#!/usr/bin/env python3
"""
Delete (archive) a Substrate entity.

Default behavior is soft delete: sets meta_status to 'archived' and records
the archive timestamp. The entity drops out of all active queries immediately
but remains on disk for 30 days.

Use --permanent for immediate hard deletion (removes files, SQLite records,
relationship references in other entities, and vector embeddings).

Usage:
  python3 delete-entity.py UUID                    # Soft delete (archive)
  python3 delete-entity.py UUID --permanent         # Hard delete immediately
  python3 delete-entity.py UUID1 UUID2 UUID3        # Batch soft delete
  python3 delete-entity.py --restore UUID           # Restore an archived entity
  python3 delete-entity.py --purge-expired          # Hard delete all entities archived > 30 days ago

Options:
  UUID ...           One or more entity UUIDs (positional)
  --permanent        Skip archive, hard delete immediately
  --purge-expired    Remove all entities archived > 30 days ago
  --restore UUID     Restore an archived entity to live status
  --days N           Override expiry threshold for --purge-expired (default: 30)
  --dry-run          Show what would happen without making changes
  --force            Skip confirmation prompt (for --permanent)
"""

import os
import sys
import shutil
import sqlite3
import argparse
from datetime import datetime, timezone, timedelta
from schema import load_schema
from changelog import log_change
from lib.fileio import safe_write

SUBSTRATE_PATH = os.environ.get("SUBSTRATE_PATH", os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
DB_PATH = os.path.join(SUBSTRATE_PATH, "_system", "index", "substrate.db")
schema = load_schema(SUBSTRATE_PATH)


def find_entity(entity_id):
    """Look up entity from SQLite."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT path, name, type, meta_status FROM entities WHERE id = ?", (entity_id,))
    row = c.fetchone()
    conn.close()
    if not row:
        return None
    return {"path": row[0], "name": row[1], "type": row[2], "meta_status": row[3]}


def get_relationships(entity_id):
    """Get all relationships involving this entity (both directions)."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        SELECT source_id, relationship, target_id FROM relationships
        WHERE source_id = ? OR target_id = ?
    """, (entity_id, entity_id))
    rows = c.fetchall()
    conn.close()
    return rows


def _is_block_seq_item(line):
    """True if `line` is a YAML block-sequence item under a mapping key.

    Canonical Substrate meta.yaml uses two-space-indented block sequences
    (`  - UUID`) — see lib/fileio.dump_entity_meta. This matcher also accepts
    zero-indent (`- UUID`) as defense-in-depth: if an ad-hoc script or
    maintenance operation slips through with yaml.dump's default output, the
    delete path should still correctly recognize and remove items rather
    than silently skip them.

    History: for a time both styles existed in entity files in the wild.
    That mix — combined with this matcher only accepting two-space indent —
    caused silent deletion failures and orphan bare-UUID corruption (six
    decisions corrupted during 2026-04-21 Helm curation + Nick Silhacek
    person entity corrupted during 2026-04-22 archive purge). Normalization
    pass on 2026-04-22 canonicalized all existing files; this matcher stays
    tolerant so future non-canonical writes don't produce the same damage.
    """
    stripped = line.lstrip()
    if not stripped.startswith('- '):
        return False
    prefix = line[: len(line) - len(stripped)]
    # Prefix must be all-whitespace (no content before the dash).
    return prefix == '' or prefix.isspace()


def _block_seq_item_value(line):
    """Return the value of a YAML block-sequence item line, independent of indent."""
    return line.lstrip()[2:].strip()


def remove_relationship_from_meta(content, rel_type, target_id):
    """Remove a relationship entry from meta.yaml. Removes the header if list becomes empty.

    Handles both indent styles YAML emits in the wild — see _is_block_seq_item.
    """
    lines = content.rstrip('\n').split('\n')
    new_lines = []
    in_rel_block = False
    header_line = None
    remaining_items = []

    for line in lines:
        # Block opens when we hit `<rel_type>:` at column 0.
        if line.strip() == f"{rel_type}:" and not line.startswith(' '):
            in_rel_block = True
            header_line = line
            remaining_items = []
            continue

        if in_rel_block:
            if _is_block_seq_item(line):
                if _block_seq_item_value(line) == target_id:
                    continue  # drop the target item
                remaining_items.append(line)
                continue
            # Non-item line → block ends. Flush remaining items (if any) under header.
            # If all items were removed, the header is silently dropped — correct:
            # an empty relationship block shouldn't exist.
            if remaining_items:
                new_lines.append(header_line)
                new_lines.extend(remaining_items)
            in_rel_block = False
            # Fall through to append this line below.

        new_lines.append(line)

    # Rel block may end at EOF without a follow-up key line; flush any pending items.
    if in_rel_block and remaining_items:
        new_lines.append(header_line)
        new_lines.extend(remaining_items)

    return '\n'.join(new_lines) + '\n'


def update_meta_attr(content, attr, value):
    """Update or add a simple attribute in YAML. Value is routed through
    quote_yaml_scalar so timestamps, dates, and type-ambiguous strings
    ('yes', 'null', etc.) are emitted quoted rather than bare — matches
    the canonical dump_entity_meta convention.
    """
    from lib.fileio import quote_yaml_scalar
    quoted = quote_yaml_scalar(value) if isinstance(value, str) else value

    lines = content.rstrip('\n').split('\n')
    new_lines = []
    updated = False

    for line in lines:
        if line.startswith(f"{attr}:") and not line.startswith(f"{attr}s:"):
            new_lines.append(f"{attr}: {quoted}")
            updated = True
        else:
            new_lines.append(line)

    if not updated:
        # Insert after meta_status if possible
        insert_at = len(new_lines)
        for i, line in enumerate(new_lines):
            if line.startswith("meta_status:"):
                insert_at = i + 1
                break
        new_lines.insert(insert_at, f"{attr}: {quoted}")

    return '\n'.join(new_lines) + '\n'


def clean_neighbor(neighbor_id, rel_type, entity_id, now, dry_run=False):
    """Remove a relationship reference from a neighbor entity's meta.yaml."""
    neighbor = find_entity(neighbor_id)
    if not neighbor:
        return None

    meta_path = os.path.join(SUBSTRATE_PATH, neighbor["path"], "meta.yaml")
    if not os.path.exists(meta_path):
        return neighbor

    if dry_run:
        return neighbor

    with safe_write(meta_path) as (content, write):
        content = remove_relationship_from_meta(content, rel_type, entity_id)
        content = update_meta_attr(content, "last_edited", now)
        write(content)

    return neighbor


def build_cleanup_plan(entity_id):
    """Build the list of neighbor meta.yaml edits needed for relationship cleanup."""
    relationships = get_relationships(entity_id)
    cleanup_plan = []

    for source_id, rel_type, target_id in relationships:
        if source_id == entity_id:
            inverse = schema.inverses.get(rel_type)
            if inverse:
                cleanup_plan.append({
                    "neighbor_id": target_id,
                    "rel_to_remove": inverse,
                    "display": f"{rel_type} → {target_id[:8]}",
                })
        else:
            cleanup_plan.append({
                "neighbor_id": source_id,
                "rel_to_remove": rel_type,
                "display": f"{source_id[:8]} → {rel_type}",
            })

    # Deduplicate
    seen = set()
    unique = []
    for item in cleanup_plan:
        key = (item["neighbor_id"], item["rel_to_remove"])
        if key not in seen:
            seen.add(key)
            unique.append(item)

    return unique


# --- Soft delete (archive) ---

def soft_delete(entity_id, entity, dry_run=False):
    """Archive an entity (soft delete). Returns True if archived."""
    if entity["meta_status"] == "archived":
        print(f"Already archived: {entity['type']} '{entity['name']}' [{entity_id[:8]}]")
        return False

    now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    meta_path = os.path.join(SUBSTRATE_PATH, entity["path"], "meta.yaml")

    if dry_run:
        print(f"DRY RUN — would archive:")
        print(f"   {entity['type']}: {entity['name']} [{entity_id[:8]}]")
        print(f"   meta_status: live → archived")
        print(f"   archived_at: {now_utc}")
        return True

    # Update meta.yaml
    with safe_write(meta_path) as (content, write):
        content = update_meta_attr(content, "meta_status", "archived")
        content = update_meta_attr(content, "archived_at", now_utc)
        content = update_meta_attr(content, "last_edited", now)
        write(content)

    # Update SQLite
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("UPDATE entities SET meta_status = 'archived', last_edited = ? WHERE id = ?",
              (now, entity_id))
    conn.commit()
    conn.close()

    # Log to changelog
    log_change(
        "delete", entity_id, entity["type"], entity["name"],
        changes=[
            {"attribute": "meta_status", "old": "live", "new": "archived"},
            {"attribute": "archived_at", "value": now_utc},
        ],
    )

    print(f"Archived {entity['type']}: {entity['name']}")
    print(f"   ID: {entity_id}")
    print(f"   Restore: python3 delete-entity.py --restore {entity_id}")
    return True


# --- Hard delete ---

def hard_delete(entity_id, entity, dry_run=False, force=False):
    """Permanently remove an entity. Returns True if deleted."""
    cleanup_plan = build_cleanup_plan(entity_id)
    entity_folder = os.path.join(SUBSTRATE_PATH, entity["path"])

    if dry_run:
        print(f"DRY RUN — would permanently delete:")
        print(f"   {entity['type']}: {entity['name']} [{entity_id[:8]}]")
        print(f"   Path: {entity['path']}")
        if cleanup_plan:
            print(f"   Neighbor edits ({len(cleanup_plan)}):")
            for item in cleanup_plan:
                neighbor = find_entity(item["neighbor_id"])
                label = f"{neighbor['name']} ({neighbor['type']})" if neighbor else item["neighbor_id"][:8]
                print(f"     Remove {item['rel_to_remove']} from {label} [{item['neighbor_id'][:8]}]")
        return True

    # Confirmation for interactive --permanent
    if not force:
        print(f"Permanently delete {entity['type']}: {entity['name']} [{entity_id[:8]}]?")
        print(f"   Path: {entity['path']}")
        print(f"   Relationships to clean: {len(cleanup_plan)}")
        response = input("   Confirm (y/N): ").strip().lower()
        if response != 'y':
            print("   Skipped.")
            return False

    now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

    # 1. Clean neighbor meta.yaml files
    neighbors_cleaned = 0
    for item in cleanup_plan:
        neighbor = clean_neighbor(item["neighbor_id"], item["rel_to_remove"], entity_id, now)
        if neighbor:
            neighbors_cleaned += 1

    # 2. Delete from SQLite
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM relationships WHERE source_id = ? OR target_id = ?", (entity_id, entity_id))
    rels_deleted = c.rowcount
    c.execute("DELETE FROM entities WHERE id = ?", (entity_id,))

    conn.commit()
    conn.close()

    # 3. Delete from vec_entities (needs sqlite-vec extension loaded).
    # Both failure modes — search module unavailable OR vec_entities table
    # not yet initialized — are non-fatal: the entity has already been
    # removed from the primary entities/relationships tables. Log and move on.
    try:
        from embeddings import is_search_available, load_vec_extension, remove_embedding
        if is_search_available():
            vec_conn = sqlite3.connect(DB_PATH)
            if load_vec_extension(vec_conn):
                remove_embedding(vec_conn, entity_id)
                vec_conn.commit()
            vec_conn.close()
    except ImportError:
        pass  # Search module not available — nothing to clean up
    except sqlite3.OperationalError as e:
        # remove_embedding handles missing-table internally; any other
        # OperationalError here likely means the extension load failed or
        # similar — log but don't fail the delete.
        print(f"  Warning: vec_entities cleanup skipped: {e}")

    # 4. Remove entity folder
    if os.path.exists(entity_folder):
        shutil.rmtree(entity_folder)

    # 5. Clean up empty shard directories
    parent = os.path.dirname(entity_folder)
    if os.path.exists(parent) and not os.listdir(parent):
        os.rmdir(parent)
        grandparent = os.path.dirname(parent)
        if os.path.exists(grandparent) and not os.listdir(grandparent):
            os.rmdir(grandparent)

    # 6. Log to changelog
    log_change(
        "delete", entity_id, entity["type"], entity["name"],
        changes=[{"attribute": "meta_status", "old": entity["meta_status"], "new": "permanently deleted"}],
    )

    print(f"Permanently deleted {entity['type']}: {entity['name']}")
    print(f"   ID: {entity_id}")
    print(f"   Relationships removed: {rels_deleted}")
    if neighbors_cleaned:
        print(f"   Neighbor files updated: {neighbors_cleaned}")
    return True


# --- Restore ---

def restore_entity(entity_id, dry_run=False):
    """Restore an archived entity to live status."""
    entity = find_entity(entity_id)
    if not entity:
        print(f"Entity not found: {entity_id}")
        sys.exit(1)

    if entity["meta_status"] != "archived":
        print(f"Not archived (meta_status: {entity['meta_status']}): {entity['name']} [{entity_id[:8]}]")
        sys.exit(1)

    now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    meta_path = os.path.join(SUBSTRATE_PATH, entity["path"], "meta.yaml")

    if dry_run:
        print(f"DRY RUN — would restore:")
        print(f"   {entity['type']}: {entity['name']} [{entity_id[:8]}]")
        print(f"   meta_status: archived → live")
        return True

    # Update meta.yaml
    with safe_write(meta_path) as (content, write):
        content = update_meta_attr(content, "meta_status", "live")
        content = update_meta_attr(content, "last_edited", now)
        # Remove archived_at attribute
        lines = content.rstrip('\n').split('\n')
        lines = [l for l in lines if not l.startswith("archived_at:")]
        content = '\n'.join(lines) + '\n'
        write(content)

    # Update SQLite
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("UPDATE entities SET meta_status = 'live', last_edited = ? WHERE id = ?",
              (now, entity_id))
    conn.commit()
    conn.close()

    # Log to changelog
    log_change(
        "update", entity_id, entity["type"], entity["name"],
        changes=[{"attribute": "meta_status", "old": "archived", "new": "live"}],
    )

    print(f"Restored {entity['type']}: {entity['name']}")
    print(f"   ID: {entity_id}")
    print(f"   meta_status: archived → live")
    return True


# --- Purge expired ---

def purge_expired(days=30, dry_run=False):
    """Hard delete all entities archived more than N days ago."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    cutoff_str = cutoff.strftime("%Y-%m-%dT%H:%M:%SZ")

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT id, path, name, type, meta_status FROM entities WHERE meta_status = 'archived'")
    archived = c.fetchall()
    conn.close()

    if not archived:
        print("No archived entities found.")
        return

    # Check archived_at from meta.yaml for each
    expired = []
    for eid, epath, ename, etype, estatus in archived:
        meta_path = os.path.join(SUBSTRATE_PATH, epath, "meta.yaml")
        if not os.path.exists(meta_path):
            continue
        with open(meta_path, 'r') as f:
            for line in f:
                if line.startswith("archived_at:"):
                    archived_at = line.split(":", 1)[1].strip()
                    if archived_at < cutoff_str:
                        expired.append({
                            "id": eid, "path": epath, "name": ename,
                            "type": etype, "meta_status": estatus,
                            "archived_at": archived_at,
                        })
                    break

    if not expired:
        print(f"No entities archived more than {days} days ago.")
        if archived:
            print(f"  ({len(archived)} archived entities within retention period)")
        return

    print(f"Found {len(expired)} entities archived more than {days} days ago:")
    for ent in expired:
        print(f"  [{ent['type']}] {ent['name']} [{ent['id'][:8]}] archived {ent['archived_at']}")

    if dry_run:
        print(f"\nDRY RUN — would permanently delete {len(expired)} entities.")
        return

    print()
    for ent in expired:
        hard_delete(ent["id"], ent, force=True)

    print(f"\nPurged {len(expired)} expired entities.")


# --- Main ---

def main():
    parser = argparse.ArgumentParser(description="Delete (archive) a Substrate entity", add_help=False)
    parser.add_argument("entity_ids", nargs="*")
    parser.add_argument("--permanent", action="store_true", help="Hard delete immediately")
    parser.add_argument("--purge-expired", action="store_true", dest="purge_expired",
                        help="Remove all entities archived > 30 days ago")
    parser.add_argument("--restore", metavar="UUID", default=None,
                        help="Restore an archived entity")
    parser.add_argument("--days", type=int, default=30,
                        help="Expiry threshold for --purge-expired (default: 30)")
    parser.add_argument("--dry-run", action="store_true", dest="dry_run")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--help", "-h", action="store_true")

    args = parser.parse_args()

    if args.help:
        print(__doc__)
        sys.exit(0)

    if args.purge_expired:
        purge_expired(days=args.days, dry_run=args.dry_run)
        return

    if args.restore:
        restore_entity(args.restore, dry_run=args.dry_run)
        return

    if not args.entity_ids:
        print(__doc__)
        sys.exit(0)

    deleted = 0
    for entity_id in args.entity_ids:
        entity = find_entity(entity_id)
        if not entity:
            print(f"Entity not found: {entity_id}")
            continue

        if args.permanent:
            result = hard_delete(entity_id, entity, dry_run=args.dry_run, force=args.force)
        else:
            result = soft_delete(entity_id, entity, dry_run=args.dry_run)

        if result:
            deleted += 1
        if len(args.entity_ids) > 1 and entity_id != args.entity_ids[-1]:
            print()

    if len(args.entity_ids) > 1 and not args.dry_run:
        print(f"\n{deleted}/{len(args.entity_ids)} entities processed.")


if __name__ == "__main__":
    main()
