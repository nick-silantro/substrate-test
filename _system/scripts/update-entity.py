#!/usr/bin/env python3
"""
Update an existing Substrate entity.

Handles: meta.yaml modification, SQLite sync, and bidirectional relationship management.

Usage:
  python3 update-entity.py UUID --resolution Completed --focus Closed
  python3 update-entity.py UUID --focus Active --life-stage "in_progress"
  python3 update-entity.py UUID --name "New Name" --description "Updated desc"
  python3 update-entity.py UUID --importance-tactical High
  python3 update-entity.py UUID --remove-rel "belongs_to:TARGET_UUID"
  python3 update-entity.py UUID --change-rel "belongs_to:TARGET_UUID:relates_to"

Dimensional status flags:
  --focus VALUE             Focus dimension
  --life-stage VALUE        Life Stage dimension
  --resolution VALUE        Resolution dimension
  --assessment VALUE        Assessment dimension
  --importance-tactical V   Tactical importance
  --health VALUE            Health dimension
  --importance-strategic V  Strategic importance
  --phase VALUE             Phase dimension
  --meta-status VALUE       Entity visibility (live, archived, nascent)

Concurrency control:
  --claim AGENT_NAME       Atomically claim this entity (fails if already claimed)
  --unclaim                Release claim on this entity
  --expect-life-stage V    Only update if life_stage matches V (exit 1 if not)
  --expect-resolution V    Only update if resolution matches V
  --expect-focus V         Only update if focus matches V

  Claims are SQLite-only (not stored in meta.yaml) — transient coordination state.
  --expect flags compose with --claim: both conditions must be true for update to proceed.
  Exit code 1 = precondition failed (already claimed, or expected state didn't match).

Other options:
  UUID                 Entity UUID (required, first positional arg)
  --name NAME          Update name
  --description DESC   Update description
  --priority PRIORITY  Alias for --importance-tactical (low→Low, medium→Medium, high→High, urgent→Critical)
  --due DATE           Update due date
  --attr KEY=VALUE     Extra type-specific attribute update (repeatable)
  --RELATIONSHIP UUID  Add relationship, e.g. --produces UUID --belongs_to UUID
                       Inverse relationships are created automatically.
  --bring-to-today       Reset next_due to first valid scheduled date >= today
  --mark-processed AGENT   Append AGENT to processed_by list (no duplicates)
  --remove-rel REL:UUID    Remove relationship (repeatable), e.g. --remove-rel "belongs_to:UUID"
  --change-rel OLD:UUID:NEW Change relationship type (repeatable), e.g. --change-rel "belongs_to:UUID:relates_to"

Only specified attributes are changed; everything else is preserved.
"""

import os
import sys
import re
import sqlite3
import argparse
from datetime import datetime
from pathlib import Path
from schema import load_schema
from precheck import validate_update
from cascades import detect_dependency_cycle, format_cycle_error, cascade_on_review_fail
from triggers import TriggerEngine, TriggerEvent, EventType, calculate_initial_next_due, validate_recurrence_config
from changelog import log_change
from lib.fileio import safe_write
from lib.overlay import load_overlay_aliases, resolve_args_aliases

SUBSTRATE_PATH = os.environ.get("SUBSTRATE_PATH", os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
DB_PATH = os.path.join(SUBSTRATE_PATH, "_system", "index", "substrate.db")
schema = load_schema(SUBSTRATE_PATH)

# Apply alias resolution to sys.argv before argparse sees it
_overlay_aliases = load_overlay_aliases(SUBSTRATE_PATH)
sys.argv[1:] = resolve_args_aliases(
    sys.argv[1:],
    _overlay_aliases.get("attributes", {}),
    _overlay_aliases.get("relationships", {}),
)


def _get_sqlite_syncable_cols(db_path):
    """Return the set of entity columns that can be synced from --attr updates.

    Derives the set at runtime from PRAGMA table_info(entities), excluding
    system columns with dedicated handling. New indexed attributes auto-sync
    without requiring manual updates to a hard-coded set.
    """
    conn_tmp = sqlite3.connect(db_path)
    c_tmp = conn_tmp.cursor()
    c_tmp.execute("PRAGMA table_info(entities)")
    all_cols = {row[1] for row in c_tmp.fetchall()}
    conn_tmp.close()
    _exclude = {
        "id", "type", "path", "created", "last_edited",
        "claimed_by", "claimed_at", "processed_by",
        "recurrence_schedule", "next_due", "last_completed",
        "completion_count", "streak", "snoozed_from", "snoozed_until",
        "delivery_status", "payment_status",
    }
    return all_cols - _exclude


ENTITY_CLAIM_TTL_MINUTES = 30

PRIORITY_MAP = {
    "low": "low",
    "medium": "medium",
    "high": "high",
    "urgent": "critical",
}


def find_entity(entity_id):
    """Look up entity path from SQLite."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT path, name, type, meta_status FROM entities WHERE id = ?", (entity_id,))
    row = c.fetchone()
    conn.close()
    if not row:
        return None
    return {"path": row[0], "name": row[1], "type": row[2], "meta_status": row[3]}


def resolve_uuid(entity_id):
    """Resolve a short or full UUID to its canonical full UUID.

    If entity_id is already a full UUID (36 chars, 4 hyphens), return it as-is.
    Otherwise, do a prefix search in SQLite and return the matching full UUID.
    Warns and returns the original if not found or ambiguous.
    """
    if len(entity_id) == 36 and entity_id.count('-') == 4:
        return entity_id
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT id FROM entities WHERE id LIKE ?", (f"{entity_id}%",))
    rows = c.fetchall()
    conn.close()
    if len(rows) == 1:
        return rows[0][0]
    if len(rows) > 1:
        print(f"  Warning: Short UUID '{entity_id}' is ambiguous ({len(rows)} matches) — using as-is")
    else:
        print(f"  Warning: Short UUID '{entity_id}' not found in index — using as-is")
    return entity_id


def parse_attr_pairs(attr_args):
    """Parse repeated --attr key=value args into a list of (key, value)."""
    pairs = []
    for item in (attr_args or []):
        if "=" not in item:
            print(f"Invalid --attr '{item}'. Expected key=value")
            sys.exit(1)
        k, v = item.split("=", 1)
        k = k.strip()
        v = v.strip()
        if not k:
            print(f"Invalid --attr '{item}'. Key cannot be empty")
            sys.exit(1)
        pairs.append((k, v))
    return pairs


# yaml_quote was previously defined here with hand-rolled rules that under-safed
# type-ambiguous scalars (timestamps, dates, "yes"/"null") — those round-tripped
# as datetime/bool/None instead of str. Routed through lib.fileio.quote_yaml_scalar
# which uses PyYAML's resolver to detect type-ambiguous plain scalars and quote
# them correctly. Imported at module scope below.
from lib.fileio import quote_yaml_scalar


# Recurrence attribute sets — mutually exclusive.
# schedule_type is the control attribute (always present, outside both sets).
# Union of both sets = all recurrence sub-attributes for write routing.
_RECURRENCE_CONFIG_ATTRS = {"interval", "precision", "days", "day_of_month", "next_date_basis", "lead_time_days", "clock_time"}
_RECURRENCE_RUNTIME_ATTRS = {"next_due", "last_completed", "completion_count", "streak"}
_RECURRENCE_ALL_ATTRS = _RECURRENCE_CONFIG_ATTRS | _RECURRENCE_RUNTIME_ATTRS | {"schedule_type"}


def update_meta_attr(content, attr_name, value):
    """Update or add a simple attribute in YAML.

    For recurrence sub-attributes (schedule_type, interval, precision, days, day_of_month,
    next_date_basis, lead_time_days, next_due, last_completed, completion_count, streak, last_fired, fire_count),
    matches the indented form under the recurrence: block.
    For all other attributes, matches at the top level.

    Handles multi-line values: when replacing an attribute whose old value spans multiple
    lines (block scalars with |/>, or wrapped flow scalars), the continuation lines
    from the old value are removed before inserting the new single-line value.
    """
    if isinstance(value, str):
        value = quote_yaml_scalar(value)

    is_recurrence_attr = attr_name in _RECURRENCE_ALL_ATTRS
    lines = content.rstrip('\n').split('\n')
    updated = False
    new_lines = []
    skip_continuation = False

    i = 0
    while i < len(lines):
        line = lines[i]

        # Skip continuation lines from a previously replaced multi-line value
        if skip_continuation:
            if is_recurrence_attr:
                # Under recurrence block: continuation = 3+ spaces (deeper than 2-space attr)
                if line.startswith("   "):
                    i += 1
                    continue
                else:
                    skip_continuation = False
            else:
                # Top-level attribute: continuation = indented or blank line within value
                if line.strip() == "" or line[0] in (' ', '\t'):
                    i += 1
                    continue
                else:
                    skip_continuation = False

        if is_recurrence_attr:
            # Match 2-space-indented sub-attribute under recurrence block
            stripped = line.lstrip()
            if (line.startswith("  ") and not line.startswith("   ")
                    and stripped.startswith(f"{attr_name}:")
                    and not stripped.startswith(f"{attr_name}s:")):
                new_lines.append(f"  {attr_name}: {value}")
                updated = True
                skip_continuation = True
            else:
                new_lines.append(line)
        else:
            if line.startswith(f"{attr_name}:") and not line.startswith(f"{attr_name}s:"):
                new_lines.append(f"{attr_name}: {value}")
                updated = True
                skip_continuation = True
            else:
                new_lines.append(line)

        i += 1

    if not updated:
        if is_recurrence_attr:
            # Insert inside the recurrence block
            rec_start = None
            for i, line in enumerate(new_lines):
                if line.startswith("recurrence:"):
                    rec_start = i
                    break
            if rec_start is not None:
                last_indented = rec_start
                for i in range(rec_start + 1, len(new_lines)):
                    if new_lines[i].startswith(" ") or new_lines[i].startswith("\t"):
                        last_indented = i
                    else:
                        break
                new_lines.insert(last_indented + 1, f"  {attr_name}: {value}")
            else:
                new_lines.append(f"  {attr_name}: {value}")
        else:
            # Insert before relationship lines
            insert_at = len(new_lines)
            for i, line in enumerate(new_lines):
                if ':' in line and not line.startswith(' ') and not line.startswith('#'):
                    key = line.split(':')[0].strip()
                    if key in schema.relationship_names:
                        insert_at = i
                        break
            new_lines.insert(insert_at, f"{attr_name}: {value}")

    return '\n'.join(new_lines) + '\n'


def remove_meta_attr(content, attr_name):
    """Remove an attribute entirely from YAML content, including multi-line continuation."""
    lines = content.rstrip('\n').split('\n')
    new_lines = []
    skip_continuation = False
    i = 0
    while i < len(lines):
        line = lines[i]
        if skip_continuation:
            if line.strip() == "" or line[0] in (' ', '\t'):
                i += 1
                continue
            else:
                skip_continuation = False
        if line.startswith(f"{attr_name}:") and not line.startswith(f"{attr_name}s:"):
            skip_continuation = True
            i += 1
            continue
        new_lines.append(line)
        i += 1
    return '\n'.join(new_lines) + '\n'


def remove_recurrence_sub_attr(content, attr_name):
    """Remove a 2-space-indented sub-attribute from inside the recurrence: block."""
    lines = content.rstrip('\n').split('\n')
    new_lines = []
    skip_continuation = False
    i = 0
    while i < len(lines):
        line = lines[i]
        if skip_continuation:
            # Continuation = 3+ spaces (deeper than 2-space attribute indent)
            if line.startswith("   "):
                i += 1
                continue
            else:
                skip_continuation = False
        stripped = line.lstrip()
        if (line.startswith("  ") and not line.startswith("   ")
                and stripped.startswith(f"{attr_name}:")
                and not stripped.startswith(f"{attr_name}s:")):
            skip_continuation = True
            i += 1
            continue
        new_lines.append(line)
        i += 1
    return '\n'.join(new_lines) + '\n'


def add_relationship_to_meta(content, rel_type, target_id):
    """Add a relationship entry to meta.yaml."""
    # Precise idempotency: check if target_id is already listed under this
    # specific relationship header (not just anywhere in the file).
    in_section = False
    for line in content.split('\n'):
        stripped = line.strip()
        if stripped == f"{rel_type}:":
            in_section = True
        elif in_section and stripped.startswith('- '):
            if target_id in stripped:
                return content
        elif in_section and stripped and not stripped.startswith('- '):
            in_section = False

    lines = content.rstrip('\n').split('\n')
    new_lines = []
    inserted = False

    for line in lines:
        new_lines.append(line)
        if line.strip() == f"{rel_type}:" and not inserted:
            new_lines.append(f"  - {target_id}")
            inserted = True

    if not inserted:
        new_lines.append(f"{rel_type}:")
        new_lines.append(f"  - {target_id}")

    return '\n'.join(new_lines) + '\n'


def _extract_relationship_targets(content, rel_name):
    """Extract UUIDs from a relationship list in meta.yaml content.
    Handles both '  - UUID' (two-space indent) and '- UUID' (zero indent) formats."""
    targets = []
    lines = content.split('\n')
    in_block = False
    for line in lines:
        if line.strip() == f"{rel_name}:" and not line.startswith(' '):
            in_block = True
            continue
        if in_block:
            stripped = line.strip()
            if stripped.startswith('- '):
                uuid = stripped.lstrip('- ').strip()
                if len(uuid) == 36 and '-' in uuid:
                    targets.append(uuid)
                continue
            elif stripped == '':
                continue
            else:
                break
    return targets


def remove_relationship_from_meta(content, rel_type, target_id):
    """Remove a relationship entry from meta.yaml. Removes the header if list becomes empty."""
    lines = content.rstrip('\n').split('\n')
    new_lines = []
    in_rel_block = False
    removed = False

    for line in lines:
        if line.strip() == f"{rel_type}:" and not line.startswith(' '):
            in_rel_block = True
            # Don't add yet — we'll add it back only if items remain
            header_line = line
            remaining_items = []
            continue

        if in_rel_block:
            if line.startswith('  - '):
                item = line.strip().lstrip('- ').strip()
                if item == target_id:
                    removed = True
                    continue
                remaining_items.append(line)
                continue
            else:
                # End of block — emit header + remaining items if any
                if remaining_items:
                    new_lines.append(header_line)
                    new_lines.extend(remaining_items)
                in_rel_block = False

        new_lines.append(line)

    # Handle case where rel block was the last thing in the file
    if in_rel_block and remaining_items:
        new_lines.append(header_line)
        new_lines.extend(remaining_items)

    return '\n'.join(new_lines) + '\n'


def main():
    parser = argparse.ArgumentParser(description="Update a Substrate entity", add_help=False)
    parser.add_argument("entity_id", nargs="?", default=None)
    parser.add_argument("--sweep-stale-claims", action="store_true", dest="sweep_stale_claims",
                        help=f"Release entity claims older than {ENTITY_CLAIM_TTL_MINUTES} minutes and reset focus=idle")
    parser.add_argument("--name", default=None)
    parser.add_argument("--description", default=None)
    # Dimensional flags
    parser.add_argument("--focus", default=None)
    parser.add_argument("--life-stage", default=None, dest="life_stage")
    parser.add_argument("--resolution", default=None)
    parser.add_argument("--assessment", default=None)
    parser.add_argument("--importance-tactical", default=None, dest="importance_tactical")
    parser.add_argument("--health", default=None)
    parser.add_argument("--importance-strategic", default=None, dest="importance_strategic")
    parser.add_argument("--phase", default=None)
    parser.add_argument("--meta-status", default=None, dest="meta_status",
                        help="Entity visibility: live, archived, nascent")
    # Convenience aliases
    parser.add_argument("--priority", default=None, help="Alias for --importance-tactical")
    # Concurrency control
    parser.add_argument("--claim", default=None, metavar="AGENT_NAME",
                        help="Atomically claim entity (fails if already claimed)")
    parser.add_argument("--unclaim", action="store_true",
                        help="Release claim on entity")
    parser.add_argument("--expect-life-stage", default=None, dest="expect_life_stage",
                        help="Only update if life_stage matches this value")
    parser.add_argument("--expect-resolution", default=None, dest="expect_resolution",
                        help="Only update if resolution matches this value")
    parser.add_argument("--expect-focus", default=None, dest="expect_focus",
                        help="Only update if focus matches this value")
    # processed_by tracking
    parser.add_argument("--mark-processed", default=None, dest="mark_processed", metavar="AGENT",
                        help="Append agent to processed_by list (no duplicates)")
    # Other
    parser.add_argument("--due", default=None)
    parser.add_argument("--attr", action="append", default=[], help="Non-dimension attribute as key=value (repeatable). For dimensions use --life-stage, --resolution, --focus, etc.")
    parser.add_argument("--bring-to-today", action="store_true", dest="bring_to_today",
                        help="Reset next_due to first valid scheduled date >= today")
    parser.add_argument("--remove-rel", action="append", default=[], dest="remove_rels",
                        help="Remove relationship as rel_type:target_uuid (repeatable)")
    parser.add_argument("--change-rel", action="append", default=[], dest="change_rels",
                        help="Change relationship as old_type:target_uuid:new_type (repeatable)")
    parser.add_argument("--help", "-h", action="store_true")

    # Register named CLI flags for grouping-level dims dynamically from schema.
    # Mirrors create-entity.py so --delivery-status, --payment-status, etc. work
    # as first-class flags that flow through dim_updates → SQLite UPDATE.
    # Invariant: if a new dim is added with an explicit --flag above, add it here too
    # to prevent argparse from registering a conflicting dynamic flag for the same dest.
    _explicit_flag_dims = {"focus", "life_stage", "assessment", "importance_tactical",
                           "resolution", "health", "importance_strategic", "phase", "meta_status"}
    for _dim in schema.dimension_names:
        if _dim not in _explicit_flag_dims:
            parser.add_argument(f"--{_dim.replace('_', '-')}", default=None, dest=_dim)

    args, remainder = parser.parse_known_args()

    if args.help:
        print(__doc__)
        sys.exit(0)

    extra_attrs = parse_attr_pairs(args.attr)

    # Parse relationship args
    relationships = []
    i = 0
    while i < len(remainder):
        arg = remainder[i]
        if arg.startswith("--") and i + 1 < len(remainder):
            rel_name = arg[2:]
            if rel_name in schema.inverses:
                relationships.append((rel_name, remainder[i + 1]))
                i += 2
                continue
        print(f"Unknown argument: {arg}")
        sys.exit(1)

    # Resolve short UUIDs to full UUIDs before any writes
    relationships = [(rel_type, resolve_uuid(target_id)) for rel_type, target_id in relationships]

    # Handle --priority alias
    if args.priority and not args.importance_tactical:
        mapped = PRIORITY_MAP.get(args.priority.lower())
        args.importance_tactical = mapped or args.priority

    # Handle --sweep-stale-claims (no entity UUID required)
    if args.sweep_stale_claims:
        from datetime import timedelta
        cutoff = (datetime.now() - timedelta(minutes=ENTITY_CLAIM_TTL_MINUTES)).strftime("%Y-%m-%dT%H:%M:%S")
        sweep_conn = sqlite3.connect(DB_PATH)
        sc = sweep_conn.cursor()
        sc.execute("""
            SELECT id, name, path, claimed_by, claimed_at, focus
            FROM entities
            WHERE claimed_by IS NOT NULL
              AND claimed_at < ?
        """, (cutoff,))
        stale = sc.fetchall()
        if not stale:
            print("No stale claims found.")
            sweep_conn.close()
            return
        swept = 0
        sweep_now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        for eid, name, path, agent, claimed_at, focus in stale:
            sql_updates = ["claimed_by = NULL", "claimed_at = NULL", f"last_edited = '{sweep_now}'"]
            if focus == "active":
                sql_updates.append("focus = 'idle'")
            sweep_conn.execute(f"UPDATE entities SET {', '.join(sql_updates)} WHERE id = ?", (eid,))
            # Update meta.yaml for focus (files are source of truth; claimed_by/claimed_at are SQLite-only)
            if focus == "active" and path:
                meta_path = os.path.join(SUBSTRATE_PATH, path, "meta.yaml")
                if os.path.isfile(meta_path):
                    with safe_write(meta_path) as (sweep_content, write):
                        sweep_content = update_meta_attr(sweep_content, "focus", "idle")
                        write(sweep_content)
            focus_note = " (focus → idle)" if focus == "active" else ""
            print(f"  Swept: {name} [{eid[:8]}] — claimed by '{agent}' at {claimed_at}{focus_note}")
            swept += 1
        sweep_conn.commit()
        sweep_conn.close()
        print(f"Swept {swept} stale claim(s) (TTL: {ENTITY_CLAIM_TTL_MINUTES}m)")
        return

    # Find entity
    entity = find_entity(args.entity_id)
    if not entity:
        print(f"Entity not found: {args.entity_id}")
        sys.exit(1)

    if entity.get("meta_status") == "archived":
        print(f"⚠ Entity is archived (meta_status: archived). Proceeding with update.")

    # Pre-check: validate operation against schema before doing anything
    dim_inputs = {}
    for dim in schema.dimension_names:
        val = getattr(args, dim, None)
        if val is not None and val != "":  # Don't validate clears (empty string)
            dim_inputs[dim] = val
    # Determine caller: SUBSTRATE_AGENT set = agent (hard ready-gate), otherwise human (soft gate)
    caller = "agent" if os.environ.get("SUBSTRATE_AGENT") else "human"
    validation = validate_update(
        schema, args.entity_id,
        entity_type=entity["type"],
        dimensions=dim_inputs,
        relationships=relationships,
        extra_attrs=extra_attrs,
        db_path=DB_PATH,
        caller=caller,
    )
    for w in validation.warnings:
        print(f"  Warning: {w}")
    if not validation.valid:
        print("Validation failed:")
        for e in validation.errors:
            print(f"  ✗ {e}")
        sys.exit(1)

    meta_path = os.path.join(SUBSTRATE_PATH, entity["path"], "meta.yaml")
    with open(meta_path, 'r') as f:
        content = f.read()
    original_content = content  # Preserved for recurrence transition detection (section 8)

    # Capture old values for change logging and cascade detection.
    # SELECT is fully dynamic — all dims from schema, no hardcoded list.
    # Same list used for SELECT construction and row parsing: position consistency guaranteed.
    _all_dims = list(schema.dimension_names)
    _dim_select = ", ".join(_all_dims)

    conn_pre = sqlite3.connect(DB_PATH)
    c_pre = conn_pre.cursor()
    c_pre.execute(
        f"SELECT name, description, {_dim_select}, due, claimed_by, processed_by "
        f"FROM entities WHERE id = ?",
        (args.entity_id,)
    )
    _row = c_pre.fetchone()
    old_values = {"name": _row[0], "description": _row[1]}
    for _i, _dim in enumerate(_all_dims):
        old_values[_dim] = _row[2 + _i]
    _tail = 2 + len(_all_dims)
    old_values["due"] = _row[_tail]
    old_values["claimed_by"] = _row[_tail + 1]
    old_values["processed_by"] = _row[_tail + 2]
    old_resolution = old_values["resolution"]
    conn_pre.close()

    now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    changes = []
    structured_changes = []
    structured_rels = []

    # Warn if focus is being set to active without a claim
    if getattr(args, 'focus', None) == "active" and not args.claim:
        if not old_values.get("claimed_by"):
            print("  Warning: focus set to active without a claim — stale focus won't self-clear")

    # Update simple attributes
    for attr, value in [("name", args.name), ("description", args.description), ("due", args.due)]:
        if value is not None:
            content = update_meta_attr(content, attr, value)
            changes.append(f"{attr} → {value}")
            if old_values.get(attr) != value:
                structured_changes.append({"attribute": attr, "old": old_values.get(attr), "new": value})

    # Update dimensions
    dim_updates = {}
    dim_clears = []
    for dim in schema.dimension_names:
        val = getattr(args, dim, None)
        if val is not None:
            config = schema.dimension_config(entity["type"])
            is_disallowed = config.get(dim) == "disallowed"
            is_clear = val == ""

            if is_disallowed and not is_clear:
                continue  # Pre-check already warned about this

            if is_clear:
                # Clear the dimension (remove from meta.yaml, NULL in SQLite)
                content = remove_meta_attr(content, dim)
                dim_clears.append(dim)
                changes.append(f"{dim} → (cleared)")
                if old_values.get(dim) is not None:
                    structured_changes.append({"attribute": dim, "old": old_values.get(dim), "new": None})
            else:
                content = update_meta_attr(content, dim, val)
                dim_updates[dim] = val
                changes.append(f"{dim} → {val}")
                if old_values.get(dim) != val:
                    structured_changes.append({"attribute": dim, "old": old_values.get(dim), "new": val})

    # Route dimension keys from --attr into dim_updates.
    # Keys already set by first-class flags are not overwritten (first-class wins).
    # Disallowed dimensions are silently skipped — consistent with first-class flag loop.
    # Removes dimension keys from extra_attrs to prevent double-write.
    _dim_extra = [(k, v) for k, v in extra_attrs if k in schema.dimension_names]
    extra_attrs = [(k, v) for k, v in extra_attrs if k not in schema.dimension_names]
    _type_dim_config = schema.dimension_config(entity["type"])
    for dim, val in _dim_extra:
        if _type_dim_config.get(dim) == "disallowed":
            continue
        if dim not in dim_updates:
            dim_updates[dim] = val
            content = update_meta_attr(content, dim, val)
            changes.append(f"{dim} → {val}")
            if old_values.get(dim) != val:
                structured_changes.append({"attribute": dim, "old": old_values.get(dim), "new": val})

    # --- Generic list attribute handling ---
    # Collects duplicate --attr keys for list-typed attributes, validates,
    # writes as YAML list, and syncs comma-separated to SQLite if indexed.
    import re as _re_list
    list_attr_names = set(schema.all_list_attrs())
    list_attr_updates = {}  # attr_name -> [values]
    for attr_name in list_attr_names:
        values = [v for k, v in extra_attrs if k == attr_name]
        if values:
            list_attr_updates[attr_name] = values
    # Remove list attrs from extra_attrs (handled separately)
    extra_attrs = [(k, v) for k, v in extra_attrs if k not in list_attr_names]

    for attr_name, values in list_attr_updates.items():
        config = schema.list_attr_config(attr_name)
        if not config:
            continue
        # Validate enum values
        if config["enum_values"]:
            for v in values:
                if v not in config["enum_values"]:
                    print(f"Error: Invalid value '{v}' for attribute '{attr_name}'. "
                          f"Valid values: {', '.join(config['enum_values'])}")
                    sys.exit(1)
        # Enforce max_items
        if config["max_items"] and len(values) > config["max_items"]:
            print(f"Error: Maximum {config['max_items']} values allowed for '{attr_name}' "
                  f"(got {len(values)}).")
            sys.exit(1)
        # Remove existing block from content (YAML list form or scalar form)
        content = _re_list.sub(rf'{attr_name}:\n(?:  - [^\n]+\n)*', '', content)
        content = _re_list.sub(rf'{attr_name}: [^\n]+\n', '', content)
        # Insert YAML list before created: line
        list_yaml = f"{attr_name}:\n" + "".join(f"  - {v}\n" for v in values)
        content = content.replace("\ncreated:", f"\n{list_yaml}created:")
        changes.append(f"{attr_name} → {values}")
        structured_changes.append({"attribute": attr_name, "old": old_values.get(attr_name), "new": values})

    # Update extra type-specific attrs (with immutability check)
    # Note: schema validation for unknown attributes is handled by precheck.py's
    # check_attrs(), which runs via validate_update() above and warns there.
    for attr, value in extra_attrs:
        if schema.is_immutable(attr):
            import yaml as _yaml_imm
            current_meta = _yaml_imm.safe_load(content)
            current_val = current_meta.get(attr)
            if current_val is not None and str(current_val) != str(value):
                print(f"Error: Attribute '{attr}' is immutable and cannot be changed after creation "
                      f"(current: {current_val}, attempted: {value})")
                sys.exit(1)
            elif current_val is not None:
                continue  # Same value, no-op
        content = update_meta_attr(content, attr, value)
        changes.append(f"{attr} → {value}")
        structured_changes.append({"attribute": attr, "old": None, "new": value})

    # Handle --bring-to-today: re-initialize next_due to first valid date >= today
    if args.bring_to_today:
        import yaml as _yaml_bt
        import json as _json_bt
        from datetime import date as _date_bt

        meta_data = _yaml_bt.safe_load(content)
        recurrence_config = meta_data.get("recurrence")
        if not recurrence_config:
            # Try SQLite recurrence_schedule column
            conn_bt = sqlite3.connect(DB_PATH)
            c_bt = conn_bt.cursor()
            c_bt.execute("SELECT recurrence_schedule FROM entities WHERE id = ?", (args.entity_id,))
            row_bt = c_bt.fetchone()
            conn_bt.close()
            if row_bt and row_bt[0]:
                recurrence_config = _json_bt.loads(row_bt[0])

        if not recurrence_config or recurrence_config.get("schedule_type") == "none":
            print("Error: --bring-to-today requires a recurrence config with schedule_type != 'none'")
            sys.exit(1)

        new_next_due = calculate_initial_next_due(recurrence_config, _date_bt.today())
        if new_next_due:
            new_next_due_str = new_next_due.isoformat()
            args._bring_to_today_next_due = new_next_due_str  # Store for SQLite update later
            content = update_meta_attr(content, "next_due", new_next_due_str)
            # Also reset life_stage to Backlog so heartbeat can re-promote
            content = update_meta_attr(content, "life_stage", "backlog")
            changes.append(f"next_due → {new_next_due_str} (bring-to-today)")
            changes.append(f"life_stage → Backlog (bring-to-today)")
            # Get old next_due
            conn_bt2 = sqlite3.connect(DB_PATH)
            c_bt2 = conn_bt2.cursor()
            c_bt2.execute("SELECT next_due, life_stage FROM entities WHERE id = ?", (args.entity_id,))
            row_bt2 = c_bt2.fetchone()
            conn_bt2.close()
            old_next_due = row_bt2[0] if row_bt2 else None
            old_ls = row_bt2[1] if row_bt2 else None
            structured_changes.append({"attribute": "next_due", "old": old_next_due, "new": new_next_due_str})
            if old_ls != "backlog":
                structured_changes.append({"attribute": "life_stage", "old": old_ls, "new": "backlog"})
                dim_updates["life_stage"] = "backlog"
        else:
            print("Error: could not calculate next_due from recurrence config")
            sys.exit(1)

    # Handle --mark-processed: append agent to processed_by list
    processed_by_updated = None
    if args.mark_processed:
        import yaml as _yaml
        meta_data = _yaml.safe_load(content)
        current_list = meta_data.get("processed_by", []) or []
        if not isinstance(current_list, list):
            current_list = [str(current_list)]
        if args.mark_processed not in current_list:
            current_list.append(args.mark_processed)
            # Rebuild the processed_by block in meta.yaml
            # First remove existing processed_by lines
            lines = content.rstrip('\n').split('\n')
            new_lines = []
            in_processed_block = False
            for line in lines:
                if line.startswith("processed_by:") and not line.startswith("processed_bys:"):
                    in_processed_block = True
                    continue
                if in_processed_block and line.startswith("  - "):
                    continue
                if in_processed_block:
                    in_processed_block = False
                new_lines.append(line)
            # Add the updated list
            new_lines.append("processed_by:")
            for agent in current_list:
                new_lines.append(f"  - {agent}")
            content = '\n'.join(new_lines) + '\n'
            changes.append(f"processed_by += {args.mark_processed}")
            structured_changes.append({"attribute": "processed_by", "old": old_values.get("processed_by"), "new": ",".join(current_list)})
        else:
            changes.append(f"processed_by already includes {args.mark_processed}")
        processed_by_updated = ','.join(current_list)

    # Always update modified
    content = update_meta_attr(content, "last_edited", now)

    # Cycle detection for depends_on relationships
    if any(rt == "depends_on" for rt, _ in relationships) or any(
        schema.inverses.get(rt) == "depends_on" for rt, _ in relationships
    ):
        conn_cycle = sqlite3.connect(DB_PATH)
        for rel_type, target_id in relationships:
            # depends_on: source depends on target. Check target→source path.
            if rel_type == "depends_on":
                cycle = detect_dependency_cycle(conn_cycle, args.entity_id, target_id)
                if cycle:
                    print(f"ERROR: Circular dependency detected.")
                    print(f"   {format_cycle_error(cycle, conn_cycle)}")
                    conn_cycle.close()
                    sys.exit(1)
            # enables: source enables target → target depends_on source.
            # Check source→target path (would target depending on source create a cycle?).
            elif schema.inverses.get(rel_type) == "depends_on":
                cycle = detect_dependency_cycle(conn_cycle, target_id, args.entity_id)
                if cycle:
                    print(f"ERROR: Circular dependency detected.")
                    print(f"   {format_cycle_error(cycle, conn_cycle)}")
                    conn_cycle.close()
                    sys.exit(1)
        conn_cycle.close()

    # Containment reassignment: when adding a containment-forward relationship and
    # the entity already has a different target for the same relationship, treat it
    # as a reassignment — remove the old forward + inverse before adding the new one.
    # This prevents stale inverse accumulation (e.g., old parent keeps a `contains`
    # entry for an entity that moved to a new parent).
    old_targets_to_clean = []
    containment_forward = schema.forward_relationships_by_category.get('containment', set())
    for rel_type, target_id in relationships:
        if rel_type in containment_forward:
            existing = _extract_relationship_targets(content, rel_type)
            for old_target in existing:
                if old_target != target_id:
                    old_targets_to_clean.append((rel_type, old_target))
                    old_info = find_entity(old_target)
                    old_label = f"{old_info['name']} ({old_info['type']})" if old_info else old_target
                    print(f"  Reassigning {rel_type}: removing old target {old_label} [{old_target[:8]}]")
                    # Remove old forward from source meta.yaml
                    content = remove_relationship_from_meta(content, rel_type, old_target)

    # Add relationships to meta.yaml
    relates_to_hints = []
    for rel_type, target_id in relationships:
        content = add_relationship_to_meta(content, rel_type, target_id)
        target_info = find_entity(target_id)
        target_label = f"{target_info['name']} ({target_info['type']})" if target_info else target_id
        changes.append(f"{rel_type} → {target_label} [{target_id[:8]}]")
        structured_rels.append({"action": "add", "type": rel_type, "target_id": target_id,
                                "target_name": target_info["name"] if target_info else None})
        if rel_type == "relates_to" and target_info:
            relates_to_hints.append(
                f"   ^ Hint: If this {entity['type']} would be meaningless without {target_info['name']}, use --belongs_to instead (independence test)."
            )

    # Update SQLite
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    updates = []
    params = []
    for attr, value in [("name", args.name), ("description", args.description), ("due", args.due)]:
        if value is not None:
            updates.append(f"{attr} = ?")
            params.append(value)

    for dim, val in dim_updates.items():
        updates.append(f"{dim} = ?")
        params.append(val)

    for dim in dim_clears:
        updates.append(f"{dim} = NULL")

    # Handle processed_by update in SQLite
    if processed_by_updated is not None:
        updates.append("processed_by = ?")
        params.append(processed_by_updated)

    # Handle bring-to-today SQLite update (next_due value was computed earlier)
    if args.bring_to_today and hasattr(args, '_bring_to_today_next_due'):
        updates.append("next_due = ?")
        params.append(args._bring_to_today_next_due)

    # Sync SQLite-indexed attributes updated via --attr
    # Derived at runtime from PRAGMA table_info — no hard-coded set to maintain.
    # Dot-notation attrs (e.g., recurrence.next_due) bypass the syncable exclusion
    # list — the prefix is an explicit signal that the user is intentionally setting
    # a sub-attribute, so the corresponding flat column should sync. Bare attrs
    # (e.g., --attr next_due=X) still go through the exclusion filter.
    _syncable_cols = _get_sqlite_syncable_cols(DB_PATH)
    _all_cols = _syncable_cols  # fallback for dot-notation resolution
    if any('.' in a for a, _ in extra_attrs):
        _tmp = sqlite3.connect(DB_PATH)
        _all_cols = {r[1] for r in _tmp.execute("PRAGMA table_info(entities)").fetchall()}
        _tmp.close()
    # Note: `action_parameters` is a structured YAML dict in meta.yaml but a
    # flat JSON string in SQLite. `--attr` can only pass flat key=value pairs,
    # so structured updates to action_parameters must go through meta.yaml
    # editing followed by `migrate-to-sqlite.py`. The path below syncs a
    # pre-serialized JSON string as-is without re-encoding.
    for attr, value in extra_attrs:
        col = attr
        if attr not in _syncable_cols and '.' in attr:
            col = attr.rsplit('.', 1)[-1]
            # Dot-notation: check all columns, not just syncable
            if col in _all_cols:
                updates.append(f"{col} = ?")
                params.append(value)
            continue
        if col in _syncable_cols:
            updates.append(f"{col} = ?")
            params.append(value)

    # Sync list attributes with SQLite columns (comma-separated CSV).
    # File_only list attrs are in meta.yaml only and skip this block.
    for attr_name, values in list_attr_updates.items():
        config = schema.list_attr_config(attr_name)
        if config and config.get("has_column"):
            updates.append(f"{attr_name} = ?")
            params.append(",".join(values))

    # Sync recurrence_schedule JSON when any recurrence config attr changes.
    # Prevents meta.yaml / SQLite drift — the JSON blob is re-serialized from
    # the meta.yaml recurrence block after attr writes have been applied.
    _rec_attrs_changed = {a for a, _ in extra_attrs if a in _RECURRENCE_CONFIG_ATTRS}
    if _rec_attrs_changed:
        import yaml as _yaml_rec_sync
        import json as _json_rec_sync
        from datetime import date as _date_rec_sync, datetime as _dt_rec_sync
        _meta_rec_sync = _yaml_rec_sync.safe_load(content)
        _rec_block = _meta_rec_sync.get("recurrence") or {}
        _cfg_only = {k: v for k, v in _rec_block.items() if k not in _RECURRENCE_RUNTIME_ATTRS}
        def _rec_sync_default(obj):
            if isinstance(obj, (_date_rec_sync, _dt_rec_sync)):
                return obj.isoformat()
            raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")
        _rec_json = _json_rec_sync.dumps(_cfg_only, default=_rec_sync_default) if _cfg_only else None
        updates.append("recurrence_schedule = ?")
        params.append(_rec_json)

    # Handle claim/unclaim
    if args.claim:
        updates.append("claimed_by = ?")
        params.append(args.claim)
        updates.append("claimed_at = ?")
        params.append(now)
        changes.append(f"claimed_by → {args.claim}")
        structured_changes.append({"attribute": "claimed_by", "old": old_values.get("claimed_by"), "new": args.claim})
    if args.unclaim:
        updates.append("claimed_by = NULL")
        updates.append("claimed_at = NULL")
        changes.append("claim released")
        structured_changes.append({"attribute": "claimed_by", "old": old_values.get("claimed_by"), "new": None})

    updates.append("last_edited = ?")
    params.append(now)

    # Build WHERE clause with preconditions
    where_clauses = ["id = ?"]
    where_params = [args.entity_id]

    if args.claim:
        where_clauses.append("claimed_by IS NULL")

    expect_checks = [
        ("expect_life_stage", "life_stage"),
        ("expect_resolution", "resolution"),
        ("expect_focus", "focus"),
    ]
    expect_descriptions = []
    for attr_name, col_name in expect_checks:
        expected_val = getattr(args, attr_name, None)
        if expected_val is not None:
            where_clauses.append(f"{col_name} = ?")
            where_params.append(expected_val)
            expect_descriptions.append(f"{col_name}={expected_val}")

    where_sql = " AND ".join(where_clauses)
    all_params = params + where_params

    c.execute(f"UPDATE entities SET {', '.join(updates)} WHERE {where_sql}", all_params)

    # Check if preconditions were met
    if c.rowcount == 0 and (args.claim or any(getattr(args, a, None) for a, _ in expect_checks)):
        conn.close()
        # Diagnose why it failed
        reasons = []
        c2 = sqlite3.connect(DB_PATH)
        cur = c2.cursor()
        cur.execute("SELECT claimed_by, life_stage, resolution, focus FROM entities WHERE id = ?",
                     (args.entity_id,))
        row = cur.fetchone()
        c2.close()
        if row:
            current_claimed, current_ls, current_res, current_focus = row
            if args.claim and current_claimed:
                reasons.append(f"already claimed by '{current_claimed}'")
            for attr_name, col_name in expect_checks:
                expected_val = getattr(args, attr_name, None)
                if expected_val is not None:
                    current_val = {"life_stage": current_ls, "resolution": current_res, "focus": current_focus}[col_name]
                    if current_val != expected_val:
                        reasons.append(f"{col_name} is '{current_val}', expected '{expected_val}'")
        reason_str = "; ".join(reasons) if reasons else "precondition not met"
        print(f"CLAIM FAILED: {entity['type']} '{entity['name']}' [{args.entity_id[:8]}] — {reason_str}")
        # Revert meta.yaml changes (don't write the modified content)
        sys.exit(1)

    # Clean up old containment targets (reassignment: remove old forward + inverse from SQLite and meta)
    for old_rel_type, old_target_id in old_targets_to_clean:
        # Remove forward from SQLite
        c.execute("DELETE FROM relationships WHERE source_id = ? AND relationship = ? AND target_id = ?",
                  (args.entity_id, old_rel_type, old_target_id))
        # Remove inverse from SQLite and target meta.yaml
        old_inverse = schema.inverses.get(old_rel_type)
        if old_inverse:
            c.execute("DELETE FROM relationships WHERE source_id = ? AND relationship = ? AND target_id = ?",
                      (old_target_id, old_inverse, args.entity_id))
            old_target = find_entity(old_target_id)
            if old_target:
                old_target_meta = os.path.join(SUBSTRATE_PATH, old_target["path"], "meta.yaml")
                if os.path.exists(old_target_meta):
                    with safe_write(old_target_meta) as (old_target_content, write):
                        old_target_content = remove_relationship_from_meta(old_target_content, old_inverse, args.entity_id)
                        old_target_content = update_meta_attr(old_target_content, "last_edited", now)
                        write(old_target_content)
        old_target_info = find_entity(old_target_id)
        structured_rels.append({"action": "remove", "type": old_rel_type, "target_id": old_target_id,
                                "target_name": old_target_info["name"] if old_target_info else None})

    # Add relationships to SQLite + inverse to target meta.yaml
    for rel_type, target_id in relationships:
        c.execute("INSERT OR IGNORE INTO relationships (source_id, relationship, target_id) VALUES (?, ?, ?)",
                  (args.entity_id, rel_type, target_id))
        inverse = schema.inverses.get(rel_type)
        if inverse:
            c.execute("INSERT OR IGNORE INTO relationships (source_id, relationship, target_id) VALUES (?, ?, ?)",
                      (target_id, inverse, args.entity_id))
            target = find_entity(target_id)
            if target:
                target_meta = os.path.join(SUBSTRATE_PATH, target["path"], "meta.yaml")
                if os.path.exists(target_meta):
                    with safe_write(target_meta) as (target_content, write):
                        target_content = add_relationship_to_meta(target_content, inverse, args.entity_id)
                        target_content = update_meta_attr(target_content, "last_edited", now)
                        write(target_content)
                        # Post-write verification: confirm the inverse was actually persisted
                        if f"- {args.entity_id}" not in target_content:
                            print(f"  ERROR: Inverse write verification failed — {inverse} → {args.entity_id} not found in {target_meta} after write")

    # Remove relationships
    for spec in args.remove_rels:
        parts = spec.split(":")
        if len(parts) != 2:
            print(f"Invalid --remove-rel format: '{spec}'. Expected rel_type:target_uuid")
            sys.exit(1)
        rel_type, target_id = parts
        target_id = resolve_uuid(target_id)
        if rel_type not in schema.inverses:
            print(f"Unknown relationship type: {rel_type}")
            sys.exit(1)

        # Remove from source meta.yaml
        content = remove_relationship_from_meta(content, rel_type, target_id)

        # Remove from SQLite (both directions)
        inverse = schema.inverses.get(rel_type)
        c.execute("DELETE FROM relationships WHERE source_id = ? AND relationship = ? AND target_id = ?",
                  (args.entity_id, rel_type, target_id))
        if inverse:
            c.execute("DELETE FROM relationships WHERE source_id = ? AND relationship = ? AND target_id = ?",
                      (target_id, inverse, args.entity_id))
            # Remove inverse from target meta.yaml
            target = find_entity(target_id)
            if target:
                target_meta = os.path.join(SUBSTRATE_PATH, target["path"], "meta.yaml")
                if os.path.exists(target_meta):
                    with safe_write(target_meta) as (target_content, write):
                        target_content = remove_relationship_from_meta(target_content, inverse, args.entity_id)
                        target_content = update_meta_attr(target_content, "last_edited", now)
                        write(target_content)

        changes.append(f"removed {rel_type} → {target_id}")
        _rem_target = find_entity(target_id)
        structured_rels.append({"action": "remove", "type": rel_type, "target_id": target_id,
                                "target_name": _rem_target["name"] if _rem_target else None})

    # Change relationships (atomic remove + add)
    for spec in args.change_rels:
        parts = spec.split(":")
        if len(parts) != 3:
            print(f"Invalid --change-rel format: '{spec}'. Expected old_type:target_uuid:new_type")
            sys.exit(1)
        old_type, target_id, new_type = parts
        target_id = resolve_uuid(target_id)
        for rt in (old_type, new_type):
            if rt not in schema.inverses:
                print(f"Unknown relationship type: {rt}")
                sys.exit(1)

        # Remove old relationship from source meta.yaml
        content = remove_relationship_from_meta(content, old_type, target_id)
        # Add new relationship to source meta.yaml
        content = add_relationship_to_meta(content, new_type, target_id)

        # SQLite: remove old, add new (both directions)
        old_inverse = schema.inverses.get(old_type)
        new_inverse = schema.inverses.get(new_type)

        c.execute("DELETE FROM relationships WHERE source_id = ? AND relationship = ? AND target_id = ?",
                  (args.entity_id, old_type, target_id))
        c.execute("INSERT OR IGNORE INTO relationships (source_id, relationship, target_id) VALUES (?, ?, ?)",
                  (args.entity_id, new_type, target_id))

        if old_inverse:
            c.execute("DELETE FROM relationships WHERE source_id = ? AND relationship = ? AND target_id = ?",
                      (target_id, old_inverse, args.entity_id))
        if new_inverse:
            c.execute("INSERT OR IGNORE INTO relationships (source_id, relationship, target_id) VALUES (?, ?, ?)",
                      (target_id, new_inverse, args.entity_id))

        # Update target meta.yaml (remove old inverse, add new inverse)
        target = find_entity(target_id)
        if target:
            target_meta = os.path.join(SUBSTRATE_PATH, target["path"], "meta.yaml")
            if os.path.exists(target_meta):
                with safe_write(target_meta) as (target_content, write):
                    if old_inverse:
                        target_content = remove_relationship_from_meta(target_content, old_inverse, args.entity_id)
                    if new_inverse:
                        target_content = add_relationship_to_meta(target_content, new_inverse, args.entity_id)
                    target_content = update_meta_attr(target_content, "last_edited", now)
                    write(target_content)

        changes.append(f"changed {old_type} → {new_type} for {target_id}")
        structured_rels.append({"action": "change", "old_type": old_type, "new_type": new_type,
                                "target_id": target_id, "target_name": target["name"] if target else None})

    # Re-write source meta.yaml (may have been modified by remove/change ops)
    with safe_write(meta_path) as (_, write):
        write(content)

    conn.commit()
    conn.close()

    # --- Change logging ---
    if structured_changes or structured_rels:
        log_change(
            "update", args.entity_id, entity["type"],
            args.name or entity["name"],
            changes=structured_changes or None,
            relationships=structured_rels or None,
        )

    # Regenerate embedding if name or description changed (silently skips if search not set up)
    if args.name is not None or args.description is not None:
        from embeddings import is_search_available, generate_and_store, load_vec_extension, init_vec_table
        if is_search_available():
            emb_conn = sqlite3.connect(DB_PATH)
            if load_vec_extension(emb_conn):
                init_vec_table(emb_conn)
                # Re-read current entity data for embedding
                c_emb = emb_conn.cursor()
                c_emb.execute("SELECT name, type, description FROM entities WHERE id = ?", (args.entity_id,))
                emb_row = c_emb.fetchone()
                if emb_row:
                    generate_and_store(emb_conn, args.entity_id, emb_row[1], emb_row[0], emb_row[2])
                    emb_conn.commit()
            emb_conn.close()

    # Output
    print(f"Updated {entity['type']}: {args.name or entity['name']}")
    print(f"   ID: {args.entity_id}")
    for change in changes:
        print(f"   {change}")
    for hint in relates_to_hints:
        print(hint)

    # --- Reactive dimension transitions (routed through trigger engine) ---

    # 0. If a review entity's verdict changed to 'fail', cascade:
    #    - Roll parent ticket back to in_progress
    #    - Retire all passing sibling review entities (leave conditional/fail as checklist)
    # verdict is a plain attribute (not a dimension), so read from extra_attrs and meta.yaml.
    new_verdict = dict(extra_attrs).get("verdict")
    _old_verdict_match = None
    for _line in original_content.splitlines():
        _s = _line.strip()
        if _s.startswith("verdict:"):
            _old_verdict_match = _s.split(":", 1)[1].strip().strip('"').strip("'")
    old_verdict = _old_verdict_match
    if (entity["type"] == "review"
            and new_verdict == "fail"
            and new_verdict != old_verdict):
        conn_cascade = sqlite3.connect(DB_PATH)
        affected = cascade_on_review_fail(conn_cascade, args.entity_id, SUBSTRATE_PATH)
        conn_cascade.commit()
        conn_cascade.close()
        for eid, ename, etype in affected:
            if etype in ("ticket", "chore"):
                print(f"   >> Fail cascade: {etype} '{ename}' [{eid[:8]}] rolled back to in_progress")
                log_change("cascade", eid, etype, ename,
                           changes=[{"attribute": "life_stage", "old": "under_review", "new": "in_progress"}],
                           triggered_by=args.entity_id)
            elif etype == "review":
                print(f"   >> Fail cascade: review '{ename}' [{eid[:8]}] retired (passing review from prior cycle)")
                log_change("cascade", eid, etype, ename,
                           changes=[{"attribute": "phase", "old": "established", "new": "retired"}],
                           triggered_by=args.entity_id)

    # 1. If resolution changed, evaluate triggers (completion unblock)
    new_resolution = dim_updates.get("resolution")
    if new_resolution and new_resolution != old_resolution:
        conn_cascade = sqlite3.connect(DB_PATH)
        engine = TriggerEngine(conn_cascade, SUBSTRATE_PATH)
        event = TriggerEvent(
            event_type=EventType.RESOLUTION_CHANGED,
            entity_id=args.entity_id,
            entity_type=entity["type"],
            entity_name=args.name or entity["name"],
            context={"old_resolution": old_resolution, "new_resolution": new_resolution},
        )
        results = engine.evaluate_script_time(event)
        conn_cascade.commit()
        conn_cascade.close()
        for result in results:
            if result.trigger_id == "builtin:recurrence_reset" and result.actions_taken:
                action = result.actions_taken[0]
                new_due = None
                for ch in action.get("changes", []):
                    if ch.get("attribute") == "next_due":
                        new_due = ch.get("new")
                print(f"   >> Recurrence reset: {action['entity_type']} '{action['entity_name']}' [{action['entity_id'][:8]}] — next due {new_due or 'calculated'}")
                log_change("cascade", action["entity_id"], action["entity_type"],
                           action["entity_name"],
                           changes=action.get("changes", []),
                           triggered_by=args.entity_id)
            else:
                for action in result.actions_taken:
                    print(f"   >> Unblocked {action['entity_type']} '{action['entity_name']}' [{action['entity_id'][:8]}] — all dependencies resolved")
                    log_change("cascade", action["entity_id"], action["entity_type"],
                               action["entity_name"],
                               changes=[{"attribute": "is_blocked", "old": "true", "new": "false"}],
                               triggered_by=args.entity_id)

    # 1b. If resolution changed to non-unresolved, release file claims for this task
    if new_resolution and new_resolution != "unresolved":
        try:
            claim_conn = sqlite3.connect(DB_PATH)
            cursor = claim_conn.execute(
                "DELETE FROM file_claims WHERE task_id = ?",
                (args.entity_id,)
            )
            released = cursor.rowcount
            claim_conn.commit()
            claim_conn.close()
            if released > 0:
                print(f"   >> Released {released} file claim(s) for resolved task")
                log_change("file_claim_cleanup", args.entity_id, entity["type"],
                           args.name or entity["name"],
                           changes=[{"attribute": "file_claims", "old": f"{released} claims", "new": "released"}],
                           triggered_by=args.entity_id)
        except sqlite3.OperationalError:
            # file_claims table may not exist yet (pre-migration)
            pass

    # 2. If ticket life_stage changed to in_progress, promote eligible tasks to ready
    new_life_stage = dim_updates.get("life_stage")
    new_focus = dim_updates.get("focus")
    old_life_stage = old_values.get("life_stage")
    if (new_life_stage == "in_progress"
            and new_life_stage != old_life_stage
            and entity["type"] == "ticket"):
        conn_cascade = sqlite3.connect(DB_PATH)
        engine = TriggerEngine(conn_cascade, SUBSTRATE_PATH)
        event = TriggerEvent(
            event_type=EventType.DIMENSION_CHANGED,
            entity_id=args.entity_id,
            entity_type=entity["type"],
            entity_name=args.name or entity["name"],
            context={"attribute": "life_stage", "old_value": old_life_stage, "new_value": new_life_stage},
        )
        results = engine.evaluate_script_time(event)
        conn_cascade.commit()
        conn_cascade.close()
        for result in results:
            for action in result.actions_taken:
                print(f"   >> Ready: task '{action['entity_name']}' [{action['entity_id'][:8]}] — ticket in_progress, dependencies clear")
                log_change("cascade", action["entity_id"], action["entity_type"],
                           action["entity_name"],
                           changes=[{"attribute": "life_stage", "old": "backlog", "new": "ready"}],
                           triggered_by=args.entity_id)

    # 3. If ticket life_stage changed to ready, cascade ready to all child tasks
    if (new_life_stage == "ready"
            and new_life_stage != old_life_stage
            and entity["type"] == "ticket"):
        conn_cascade = sqlite3.connect(DB_PATH)
        engine = TriggerEngine(conn_cascade, SUBSTRATE_PATH)
        event = TriggerEvent(
            event_type=EventType.DIMENSION_CHANGED,
            entity_id=args.entity_id,
            entity_type=entity["type"],
            entity_name=args.name or entity["name"],
            context={"attribute": "life_stage", "old_value": old_life_stage, "new_value": new_life_stage},
        )
        results = engine.evaluate_script_time(event)
        conn_cascade.commit()
        conn_cascade.close()
        for result in results:
            for action in result.actions_taken:
                print(f"   >> Ready: task '{action['entity_name']}' [{action['entity_id'][:8]}] — ticket ready, promoted")
                log_change("cascade", action["entity_id"], action["entity_type"],
                           action["entity_name"],
                           changes=[{"attribute": "life_stage", "old": "backlog", "new": "ready"}],
                           triggered_by=args.entity_id)

    # 4. If task life_stage changed to in_progress, cascade in_progress to parent ticket
    if (new_life_stage == "in_progress"
            and new_life_stage != old_life_stage
            and entity["type"] == "task"):
        conn_cascade = sqlite3.connect(DB_PATH)
        engine = TriggerEngine(conn_cascade, SUBSTRATE_PATH)
        event = TriggerEvent(
            event_type=EventType.DIMENSION_CHANGED,
            entity_id=args.entity_id,
            entity_type=entity["type"],
            entity_name=args.name or entity["name"],
            context={"attribute": "life_stage", "old_value": old_life_stage, "new_value": new_life_stage},
        )
        results = engine.evaluate_script_time(event)
        conn_cascade.commit()
        conn_cascade.close()
        for result in results:
            for action in result.actions_taken:
                print(f"   >> In progress: ticket '{action['entity_name']}' [{action['entity_id'][:8]}] — first task started")
                log_change("cascade", action["entity_id"], action["entity_type"],
                           action["entity_name"],
                           changes=[{"attribute": "life_stage", "old": "ready", "new": "in_progress"}],
                           triggered_by=args.entity_id)

    # 5. If depends_on relationships were added, evaluate triggers (dependency block)
    has_new_deps = any(rt == "depends_on" for rt, _ in relationships) or any(
        schema.inverses.get(rt) == "depends_on" for rt, _ in relationships
    )
    if has_new_deps:
        conn_block = sqlite3.connect(DB_PATH)
        engine = TriggerEngine(conn_block, SUBSTRATE_PATH)

        # Check the source entity
        event = TriggerEvent(
            event_type=EventType.DEPENDENCY_ADDED,
            entity_id=args.entity_id,
            entity_type=entity["type"],
            entity_name=args.name or entity["name"],
            context={},
        )
        results = engine.evaluate_script_time(event)
        was_blocked = any(r.actions_taken for r in results)

        # Also check targets of enables (they gain a depends_on inverse)
        enables_targets = [tid for rt, tid in relationships if schema.inverses.get(rt) == "depends_on"]
        for target_id in enables_targets:
            c_target = conn_block.cursor()
            c_target.execute("SELECT type, name FROM entities WHERE id = ?", (target_id,))
            target_row = c_target.fetchone()
            if target_row:
                target_event = TriggerEvent(
                    event_type=EventType.DEPENDENCY_ADDED,
                    entity_id=target_id,
                    entity_type=target_row[0],
                    entity_name=target_row[1],
                    context={},
                )
                engine.evaluate_script_time(target_event)

        conn_block.commit()
        conn_block.close()
        if was_blocked:
            print(f"   >> is_blocked set to true — has unresolved dependencies")
            log_change("cascade", args.entity_id, entity["type"],
                       args.name or entity["name"],
                       changes=[{"attribute": "is_blocked", "old": "false", "new": "true"}])

    # 5b. Fire RELATIONSHIP_ADDED for non-dependency relationships
    # (depends_on already fires DEPENDENCY_ADDED above)
    non_dep_rels = [(rt, tid) for rt, tid in relationships
                    if rt != "depends_on" and schema.inverses.get(rt) != "depends_on"]
    if non_dep_rels:
        conn_rel_add = sqlite3.connect(DB_PATH)
        engine_rel = TriggerEngine(conn_rel_add, SUBSTRATE_PATH)
        for rel_type, target_id in non_dep_rels:
            rel_event = TriggerEvent(
                event_type=EventType.RELATIONSHIP_ADDED,
                entity_id=args.entity_id,
                entity_type=entity["type"],
                entity_name=args.name or entity["name"],
                context={"relationship": rel_type, "target_id": target_id},
            )
            engine_rel.evaluate_script_time(rel_event)
        conn_rel_add.commit()
        conn_rel_add.close()

    # 5c. Fire RELATIONSHIP_REMOVED for removed relationships
    if args.remove_rels:
        conn_rel_rm = sqlite3.connect(DB_PATH)
        engine_rel_rm = TriggerEngine(conn_rel_rm, SUBSTRATE_PATH)
        for spec in args.remove_rels:
            parts = spec.split(":")
            if len(parts) == 2:
                rm_rel_type, rm_target_id = parts
                rm_event = TriggerEvent(
                    event_type=EventType.RELATIONSHIP_REMOVED,
                    entity_id=args.entity_id,
                    entity_type=entity["type"],
                    entity_name=args.name or entity["name"],
                    context={"relationship": rm_rel_type, "target_id": rm_target_id},
                )
                engine_rel_rm.evaluate_script_time(rm_event)
        conn_rel_rm.commit()
        conn_rel_rm.close()

    # 6. Auto-sync resolution, life_stage, and focus on terminal transitions.
    #    A single function enforces the invariant: when any terminal dimension
    #    is set (life_stage=done_working, focus=closed, resolution=terminal),
    #    all three converge. When any reverses, the others reset to defaults.
    TERMINAL_RESOLUTIONS = ("completed", "cancelled", "deferred", "superseded")

    entity_type = entity["type"]

    def _sync_allowed(dim):
        """Return True if dim is not forbidden for this entity's type."""
        return schema.access_level(dim, entity_type, "dimension") != "forbidden"

    def _apply_terminal_sync():
        changes = []

        eff_resolution = dim_updates.get("resolution", old_values.get("resolution"))
        eff_life_stage = dim_updates.get("life_stage", old_values.get("life_stage"))
        eff_focus      = dim_updates.get("focus",      old_values.get("focus"))

        old_resolution = old_values.get("resolution")
        old_ls         = old_values.get("life_stage")
        old_focus_val  = old_values.get("focus")

        # --- Forward: any terminal trigger drives all three to terminal ---
        going_terminal = (
            (new_resolution and new_resolution in TERMINAL_RESOLUTIONS) or
            (new_life_stage == "done_working") or
            (new_focus == "closed")
        )

        # --- Reverse: moving a terminal dimension back to non-terminal ---
        reversal = (
            (new_resolution is not None and
             old_resolution in TERMINAL_RESOLUTIONS and
             new_resolution not in TERMINAL_RESOLUTIONS) or
            (new_life_stage is not None and
             old_ls == "done_working" and
             new_life_stage != "done_working") or
            (new_focus is not None and
             old_focus_val == "closed" and
             new_focus != "closed")
        )

        if going_terminal and not reversal:
            if eff_life_stage != "done_working" and _sync_allowed("life_stage"):
                changes.append(("life_stage", eff_life_stage, "done_working"))
            if eff_focus != "closed" and _sync_allowed("focus"):
                changes.append(("focus", eff_focus, "closed"))
            if eff_resolution == "unresolved" and _sync_allowed("resolution"):
                changes.append(("resolution", "unresolved", "completed"))

        elif reversal:
            # Reset dimensions not explicitly set by the user in this call.
            # Defaults: backlog (not ready — reopened work needs L1 recommitment),
            # idle (not blocked — no dependency issue, just no agent attending),
            # unresolved (not completed — if work is being reopened, it is not done).
            # These are the most conservative defaults: nothing auto-picks this up.
            if eff_life_stage == "done_working" and "life_stage" not in dim_updates and _sync_allowed("life_stage"):
                changes.append(("life_stage", eff_life_stage, "backlog"))
            if eff_focus == "closed" and "focus" not in dim_updates and _sync_allowed("focus"):
                changes.append(("focus", eff_focus, "idle"))
            if eff_resolution in TERMINAL_RESOLUTIONS and "resolution" not in dim_updates and _sync_allowed("resolution"):
                changes.append(("resolution", eff_resolution, "unresolved"))

        return changes, reversal

    sync_changes, reversal_detected = _apply_terminal_sync()

    if sync_changes:
        entity_path = os.path.join(SUBSTRATE_PATH, entity.get("path", ""), "meta.yaml")
        if os.path.isfile(entity_path):
            with safe_write(entity_path) as (content, write):
                for dim, _old, new_val in sync_changes:
                    content = update_meta_attr(content, dim, new_val)
                write(content)

        sync_conn = sqlite3.connect(DB_PATH)
        for dim, _old, new_val in sync_changes:
            sync_conn.execute(f"UPDATE entities SET {dim} = ? WHERE id = ?",
                              (new_val, args.entity_id))
        sync_conn.commit()
        sync_conn.close()

        for dim, old_val, new_val in sync_changes:
            if reversal_detected:
                print(f"   >> Auto-sync (reverse reset): {dim} {old_val} → {new_val}")
            else:
                print(f"   >> Auto-sync: {dim} {old_val} → {new_val}")
        log_change("auto_sync", args.entity_id, entity["type"],
                   args.name or entity["name"],
                   changes=[{"attribute": f, "old": o, "new": n} for f, o, n in sync_changes],
                   triggered_by=args.entity_id)

    # 6b. If _apply_terminal_sync auto-synced resolution to a terminal value,
    #     fire the same RESOLUTION_CHANGED cascade as the explicit path (section 1).
    #     Also release file claims — both gaps have the same cause and condition.
    _auto_resolution_change = next(
        (new_val for dim, _old, new_val in sync_changes if dim == "resolution"),
        None
    )
    if _auto_resolution_change and _auto_resolution_change in TERMINAL_RESOLUTIONS:
        # Cascade: unblock dependents (and handle recurrence reset if applicable)
        conn_cascade = sqlite3.connect(DB_PATH)
        engine = TriggerEngine(conn_cascade, SUBSTRATE_PATH)
        event = TriggerEvent(
            event_type=EventType.RESOLUTION_CHANGED,
            entity_id=args.entity_id,
            entity_type=entity["type"],
            entity_name=args.name or entity["name"],
            context={"old_resolution": old_resolution, "new_resolution": _auto_resolution_change},
        )
        results = engine.evaluate_script_time(event)
        conn_cascade.commit()
        conn_cascade.close()
        for result in results:
            if result.trigger_id == "builtin:recurrence_reset" and result.actions_taken:
                action = result.actions_taken[0]
                new_due = None
                for ch in action.get("changes", []):
                    if ch.get("attribute") == "next_due":
                        new_due = ch.get("new")
                print(f"   >> Recurrence reset: {action['entity_type']} '{action['entity_name']}' [{action['entity_id'][:8]}] — next due {new_due or 'calculated'}")
                log_change("cascade", action["entity_id"], action["entity_type"],
                           action["entity_name"],
                           changes=action.get("changes", []),
                           triggered_by=args.entity_id)
            else:
                for action in result.actions_taken:
                    print(f"   >> Unblocked {action['entity_type']} '{action['entity_name']}' [{action['entity_id'][:8]}] — all dependencies resolved")
                    log_change("cascade", action["entity_id"], action["entity_type"],
                               action["entity_name"],
                               changes=[{"attribute": "is_blocked", "old": "true", "new": "false"}],
                               triggered_by=args.entity_id)
        # File claim release
        try:
            claim_conn = sqlite3.connect(DB_PATH)
            cursor = claim_conn.execute(
                "DELETE FROM file_claims WHERE task_id = ?",
                (args.entity_id,)
            )
            released = cursor.rowcount
            claim_conn.commit()
            claim_conn.close()
            if released > 0:
                print(f"   >> Released {released} file claim(s) for resolved task")
                log_change("file_claim_cleanup", args.entity_id, entity["type"],
                           args.name or entity["name"],
                           changes=[{"attribute": "file_claims", "old": f"{released} claims", "new": "released"}],
                           triggered_by=args.entity_id)
        except sqlite3.OperationalError:
            pass

    # 7a. Auto-reset focus when life_stage transitions to under_review.
    #     Work handed off for review has no active agent — focus should be idle.
    if (new_life_stage == "under_review"
            and new_life_stage != old_life_stage
            and old_values.get("focus") == "active"
            and "focus" not in dim_updates):
        # Update meta.yaml
        ur_path = os.path.join(SUBSTRATE_PATH, entity.get("path", ""), "meta.yaml")
        if os.path.isfile(ur_path):
            with safe_write(ur_path) as (ur_content, write):
                ur_content = update_meta_attr(ur_content, "focus", "idle")
                write(ur_content)
        # Update SQLite
        ur_conn = sqlite3.connect(DB_PATH)
        ur_conn.execute("UPDATE entities SET focus = 'idle' WHERE id = ?", (args.entity_id,))
        ur_conn.commit()
        ur_conn.close()
        print(f"   >> Auto-reset: focus active → idle (life_stage → under_review)")
        log_change("cascade", args.entity_id, entity["type"],
                   args.name or entity["name"],
                   changes=[{"attribute": "focus", "old": "active", "new": "idle"}],
                   triggered_by=args.entity_id)

    # 7-pre. Evaluate AGENT executor triggers for any dimension/resolution change.
    #        CASCADE triggers are handled by the specific sections above (1-5).
    #        AGENT triggers are generic: any trigger entity with executor=agent that
    #        matches the event fires here, spawning an agent in background.
    #
    #        Short-circuit: skip if no agent-executor trigger entities exist.
    _agent_events = []

    # Short-circuit: skip engine instantiation if no agent triggers exist.
    # One connection handles both the check and (if needed) the engine.
    _conn_at = sqlite3.connect(DB_PATH)
    _agent_trigger_count = _conn_at.execute(
        "SELECT COUNT(*) FROM entities WHERE type='trigger' AND meta_status='live' AND executor='agent'"
    ).fetchone()[0]

    if _agent_trigger_count > 0:
        # Dimension changes (any dimension, any entity type)
        for dim_name in dim_updates:
            old_val = old_values.get(dim_name)
            new_val = dim_updates[dim_name]
            if old_val != new_val:
                _agent_events.append(TriggerEvent(
                    event_type=EventType.DIMENSION_CHANGED,
                    entity_id=args.entity_id,
                    entity_type=entity["type"],
                    entity_name=args.name or entity["name"],
                    context={"attribute": dim_name, "old_value": old_val, "new_value": new_val},
                ))

        # Resolution change
        if new_resolution and new_resolution != old_resolution:
            _agent_events.append(TriggerEvent(
                event_type=EventType.RESOLUTION_CHANGED,
                entity_id=args.entity_id,
                entity_type=entity["type"],
                entity_name=args.name or entity["name"],
                context={"old_resolution": old_resolution, "new_resolution": new_resolution},
            ))

    if _agent_events:
        engine_agent = TriggerEngine(_conn_at, SUBSTRATE_PATH)
        for ag_event in _agent_events:
            ag_results = engine_agent.fire_agent_triggers(ag_event)
            for result in ag_results:
                for action in result.actions_taken:
                    if action.get("spawn_agent"):
                        agent_name = action.get("agent_name", "unknown")
                        print(f"   >> Agent trigger: spawning {agent_name} "
                              f"(trigger {result.trigger_id}, event {ag_event.event_type.value})")
                        log_change("agent_trigger", args.entity_id, entity["type"],
                                   args.name or entity["name"],
                                   changes=[{
                                       "attribute": "agent_spawned",
                                       "old": None,
                                       "new": agent_name,
                                   }],
                                   triggered_by=result.trigger_id)
                    elif action.get("error"):
                        print(f"   !! Agent trigger error: {action['error']}")

    _conn_at.close()

    # 7. Strip all recurrence attributes when schedule_type transitions to none.
    #    When a recurring entity becomes non-recurring, both config attributes
    #    (interval, precision, days, etc.) and runtime attributes (next_due, streak, etc.)
    #    are removed from meta.yaml and NULLed in SQLite. schedule_type itself
    #    (the control attribute) stays — it was just set to "none" by the user.
    #    Design principle: removal is all-or-nothing per set.
    schedule_type_set_to_none = any(
        f == "schedule_type" and v == "none" for f, v in extra_attrs
    )
    if schedule_type_set_to_none:
        all_removable = _RECURRENCE_CONFIG_ATTRS | _RECURRENCE_RUNTIME_ATTRS
        entity_path = os.path.join(SUBSTRATE_PATH, entity.get("path", ""), "meta.yaml")
        stripped_attrs = []
        if os.path.isfile(entity_path):
            with safe_write(entity_path) as (rec_content, write):
                for attr in sorted(all_removable):
                    if f"  {attr}:" in rec_content:
                        rec_content = remove_recurrence_sub_attr(rec_content, attr)
                        stripped_attrs.append(attr)
                if stripped_attrs:
                    write(rec_content)

        # NULL runtime columns in SQLite and update recurrence_schedule JSON
        if stripped_attrs:
            import json as _json_rt
            rt_conn = sqlite3.connect(DB_PATH)
            # Only runtime attrs have dedicated SQLite columns
            for attr in stripped_attrs:
                if attr in _RECURRENCE_RUNTIME_ATTRS:
                    rt_conn.execute(f"UPDATE entities SET {attr} = NULL WHERE id = ?",
                                    (args.entity_id,))
            # Update recurrence_schedule JSON to reflect schedule_type = none
            rt_conn.execute(
                "UPDATE entities SET recurrence_schedule = ? WHERE id = ?",
                (_json_rt.dumps({"schedule_type": "none"}), args.entity_id))
            rt_conn.commit()
            rt_conn.close()

            for attr in stripped_attrs:
                print(f"   >> Recurrence cleanup: stripped {attr}")
            log_change("recurrence_cleanup", args.entity_id, entity["type"],
                       args.name or entity["name"],
                       changes=[{"attribute": a, "old": "removed", "new": None}
                                for a in stripped_attrs],
                       triggered_by=args.entity_id)

    # 8. Initialize recurrence runtime attributes when schedule_type transitions
    #    from none (or absent) to an active type. Complement to section 7.
    #    Only fires on first activation — not on config changes between active types,
    #    and not on reactivation when runtime attrs are already present.
    import yaml as _yaml_init
    from datetime import date as _date_init
    _new_sched = next((v for f, v in extra_attrs if f == "schedule_type"), None)
    _active_types = {"interval", "day_of_week", "calendar_anchored"}
    if _new_sched in _active_types:
        _orig_meta = _yaml_init.safe_load(original_content)
        _old_sched = (_orig_meta.get("recurrence") or {}).get("schedule_type", "none")
        if _old_sched in (None, "none", ""):
            # Transition from none → active. Check runtime attrs are absent.
            _current_meta = _yaml_init.safe_load(content)
            _rec_cfg = _current_meta.get("recurrence") or {}
            _already_initialized = any(
                _rec_cfg.get(a) is not None
                for a in ("completion_count", "streak", "next_due")
            )
            if not _already_initialized:
                import json as _json_init
                import datetime as _dt_init
                _rec_errors = validate_recurrence_config(_rec_cfg)
                if _rec_errors:
                    print(f"Error: recurrence config invalid after activation: {_rec_errors}")
                    sys.exit(1)
                _init_next_due = calculate_initial_next_due(_rec_cfg, _date_init.today())

                # Write runtime attrs to disk (file already written by main; re-read → patch → re-write,
                # mirroring section 7's pattern)
                _init_path = os.path.join(SUBSTRATE_PATH, entity.get("path", ""), "meta.yaml")
                with safe_write(_init_path) as (_init_disk, _init_write):
                    if _init_next_due:
                        _init_disk = update_meta_attr(_init_disk, "next_due", _init_next_due.isoformat())
                    _init_disk = update_meta_attr(_init_disk, "completion_count", "0")
                    _init_disk = update_meta_attr(_init_disk, "streak", "0")
                    _init_write(_init_disk)

                # Update SQLite runtime columns and recurrence_schedule JSON
                _init_conn = sqlite3.connect(DB_PATH)
                if _init_next_due:
                    _init_conn.execute(
                        "UPDATE entities SET next_due = ? WHERE id = ?",
                        (_init_next_due.isoformat(), args.entity_id))
                _init_conn.execute(
                    "UPDATE entities SET completion_count = 0, streak = 0 WHERE id = ?",
                    (args.entity_id,))
                # recurrence_schedule stores config attrs only — strip runtime attrs and
                # serialize date objects (e.g. next_date_basis parsed by YAML) to isoformat.
                _runtime_keys = _RECURRENCE_RUNTIME_ATTRS
                _full_rec = (_yaml_init.safe_load(_init_disk).get("recurrence") or {})
                _cfg_only = {k: v for k, v in _full_rec.items() if k not in _runtime_keys}
                def _json_date_default(obj):
                    if isinstance(obj, (_dt_init.date, _dt_init.datetime)):
                        return obj.isoformat()
                    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")
                _init_conn.execute(
                    "UPDATE entities SET recurrence_schedule = ? WHERE id = ?",
                    (_json_init.dumps(_cfg_only, default=_json_date_default), args.entity_id))
                _init_conn.commit()
                _init_conn.close()

                _init_nd_str = _init_next_due.isoformat() if _init_next_due else None
                if _init_next_due:
                    changes.append(f"next_due → {_init_nd_str} (recurrence init)")
                    structured_changes.append({"attribute": "next_due", "old": None, "new": _init_nd_str})
                changes.append("completion_count → 0 (recurrence init)")
                changes.append("streak → 0 (recurrence init)")
                structured_changes.append({"attribute": "completion_count", "old": None, "new": 0})
                structured_changes.append({"attribute": "streak", "old": None, "new": 0})
                print(f"   >> Recurrence init: initialized runtime attributes (next_due, completion_count, streak)")
                log_change("recurrence_init", args.entity_id, entity["type"],
                           args.name or entity["name"],
                           changes=[
                               {"attribute": "next_due", "old": None, "new": _init_nd_str},
                               {"attribute": "completion_count", "old": None, "new": 0},
                               {"attribute": "streak", "old": None, "new": 0},
                           ],
                           triggered_by=args.entity_id)


if __name__ == "__main__":
    main()
