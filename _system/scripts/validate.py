#!/usr/bin/env python3
"""
Validate Substrate system integrity.

Checks:
1. SQLite vs disk — entity counts match, no missing/ghost entries
2. Bidirectional relationships — every relationship has its inverse
3. Referential integrity — all relationship targets exist
4. Schema compliance — valid types, dimensional values, relationship names
5. Schema/SQLite drift — every dimension in attributes.yaml has a column in SQLite

Check 5 exists because schema mutations (adding a dimension) require running
migrate-to-sqlite.py to add the new column. If that step is skipped — whether
by an agent following a checklist, a direct YAML edit, or a future schema UI —
the mismatch causes update-entity.py to crash on any entity update. This check
catches that silently before it becomes a runtime failure.

Usage:
    python3 validate.py [workspace_path]
    python3 validate.py [workspace_path] --repair

Exit codes:
    0 = All checks passed
    1 = Issues found
    2 = Error (missing files, etc.)
"""

import os
import sys
import sqlite3
import yaml
from pathlib import Path

# Add scripts dir to path for schema loader
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from schema import load_schema
from lib.fileio import safe_write


def load_db(db_path):
    """Connect to SQLite and return connection."""
    if not os.path.exists(db_path):
        return None
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def check_sqlite_vs_disk(workspace, conn):
    """Check 1: SQLite entity count matches meta.yaml files on disk."""
    issues = []

    entities_dir = workspace / "entities"
    disk_entities = {}
    for meta_file in entities_dir.rglob("meta.yaml"):
        try:
            with open(meta_file, encoding="utf-8") as f:
                meta = yaml.safe_load(f)
            if meta and isinstance(meta, dict) and 'id' in meta:
                disk_entities[meta['id']] = str(meta_file)
        except Exception as e:
            issues.append(("error", f"Cannot read {meta_file}: {e}"))

    # Get all SQLite entity IDs
    cursor = conn.execute("SELECT id, path FROM entities")
    sqlite_entities = {row['id']: row['path'] for row in cursor}

    # Find mismatches
    on_disk_only = set(disk_entities.keys()) - set(sqlite_entities.keys())
    in_sqlite_only = set(sqlite_entities.keys()) - set(disk_entities.keys())

    for eid in on_disk_only:
        issues.append(("missing_from_sqlite", f"{eid} exists on disk ({disk_entities[eid]}) but not in SQLite"))

    for eid in in_sqlite_only:
        issues.append(("ghost_in_sqlite", f"{eid} in SQLite ({sqlite_entities[eid]}) but not on disk"))

    return issues, len(disk_entities), len(sqlite_entities)


def check_bidirectional(conn, schema):
    """Check 2: Every relationship has its inverse on the target.

    Skips relationships where source or target doesn't exist in entities table —
    those are dangling references caught by Check 3, not bidirectional issues.
    """
    issues = []

    # Get set of known entity IDs for filtering
    known_ids = {row[0] for row in conn.execute("SELECT id FROM entities").fetchall()}

    cursor = conn.execute("SELECT source_id, relationship, target_id FROM relationships")
    relationships = cursor.fetchall()

    for row in relationships:
        source = row['source_id']
        rel = row['relationship']
        target = row['target_id']

        # Skip if either end is a dangling reference (caught by Check 3)
        if source not in known_ids or target not in known_ids:
            continue

        inverse = schema.inverses.get(rel)
        if not inverse:
            # No known inverse for this relationship — skip
            continue

        # Check if the inverse exists
        check = conn.execute(
            "SELECT 1 FROM relationships WHERE source_id = ? AND relationship = ? AND target_id = ?",
            (target, inverse, source)
        ).fetchone()

        if not check:
            issues.append(("missing_inverse", f"{source} --{rel}--> {target}, but {target} missing --{inverse}--> {source}"))

    return issues


def check_referential_integrity(conn):
    """Check 3: All relationship target UUIDs exist in entities table."""
    issues = []

    # Targets that don't exist as entities
    cursor = conn.execute("""
        SELECT DISTINCT r.source_id, r.relationship, r.target_id
        FROM relationships r
        LEFT JOIN entities e ON r.target_id = e.id
        WHERE e.id IS NULL
    """)
    for row in cursor:
        issues.append(("dangling_reference", f"{row['source_id']} --{row['relationship']}--> {row['target_id']} (target not found)"))

    # Sources that don't exist as entities
    cursor = conn.execute("""
        SELECT DISTINCT r.source_id, r.relationship, r.target_id
        FROM relationships r
        LEFT JOIN entities e ON r.source_id = e.id
        WHERE e.id IS NULL
    """)
    for row in cursor:
        issues.append(("dangling_source", f"{row['source_id']} --{row['relationship']}--> {row['target_id']} (source not found)"))

    return issues


def check_schema_compliance(conn, schema):
    """Check 4: Types, dimensional values, and relationship names are valid."""
    issues = []

    # Check types
    cursor = conn.execute("SELECT id, type, name FROM entities")
    for row in cursor:
        eid, etype, ename = row['id'], row['type'], row['name']

        if etype not in schema.known_types:
            issues.append(("unknown_type", f"{eid} ({ename}) has unknown type '{etype}'"))
            continue

        # Check dimensional values
        dim_config = schema.dimension_config(etype)
        entity = conn.execute("SELECT * FROM entities WHERE id = ?", (eid,)).fetchone()

        for dim_name in schema.dimension_names:
            value = entity[dim_name] if dim_name in entity.keys() else None
            category = dim_config.get(dim_name)

            if category == "disallowed" and value is not None:
                issues.append(("disallowed_dimension", f"{eid} ({ename}) type '{etype}' has disallowed dimension '{dim_name}' = '{value}'"))
            elif value is not None and category != "disallowed":
                valid_values = schema.dimension_values(dim_name)
                if valid_values and value not in valid_values:
                    issues.append(("invalid_dimension", f"{eid} ({ename}) dimension '{dim_name}' = '{value}' not in {valid_values}"))

    # Check relationship names
    cursor = conn.execute("SELECT DISTINCT relationship FROM relationships")
    for row in cursor:
        rel = row['relationship']
        if rel not in schema.relationship_names:
            issues.append(("unknown_relationship", f"Relationship '{rel}' not defined in schema"))

    return issues


def check_schema_sqlite_drift(conn, schema):
    """Check 5: Every dimension in attributes.yaml has a column in the SQLite entities table.

    This detects the gap between schema YAML and the SQLite index that occurs when
    a dimension is added to attributes.yaml but migrate-to-sqlite.py is not run.
    The symptom: update-entity.py crashes with 'no such column: <dim_name>' on any
    entity update, because the SELECT statement it builds includes all schema dimensions.

    This is a structural gap (missing column), not a data gap (wrong value). The only
    fix is migrate-to-sqlite.py, which adds the column via ALTER TABLE or full rebuild.
    Auto-repairable with --repair.
    """
    issues = []

    # PRAGMA table_info returns rows: (cid, name, type, notnull, dflt_value, pk)
    cursor = conn.execute("PRAGMA table_info(entities)")
    sqlite_columns = {row[1] for row in cursor.fetchall()}

    for dim_name in schema.dimension_names:
        if dim_name not in sqlite_columns:
            issues.append((
                "schema_sqlite_drift",
                f"Dimension '{dim_name}' in schema but missing from SQLite entities table"
                f" — run migrate-to-sqlite.py to add the column"
            ))

    return issues


def check_recurrence_drift(conn, workspace):
    """Check 6: Recurrence config in meta.yaml matches recurrence_schedule JSON in SQLite.

    Detects when an agent or session edits the recurrence block in meta.yaml
    (e.g., changing precision, interval) without syncing to the SQLite JSON blob.
    The evaluator queries SQLite, so stale JSON means wrong scheduling behavior.

    Only checks config attrs (schedule_type, interval, precision, days, day_of_month,
    next_date_basis, lead_time_days). Runtime attrs (next_due, streak, etc.) are
    expected to diverge between meta.yaml and SQLite during normal operation.
    """
    import json
    import yaml

    issues = []
    runtime_keys = {"next_due", "last_completed", "completion_count", "streak",
                    "last_fired", "fire_count"}

    cursor = conn.execute(
        "SELECT id, name, type, path, recurrence_schedule FROM entities "
        "WHERE recurrence_schedule IS NOT NULL AND meta_status = 'live'"
    )

    for row in cursor.fetchall():
        eid, ename, etype, epath, rec_json = row

        # Parse SQLite JSON
        try:
            sqlite_config = json.loads(rec_json)
        except (json.JSONDecodeError, TypeError):
            continue

        # Read meta.yaml recurrence block
        meta_path = workspace / epath / "meta.yaml"
        if not meta_path.exists():
            continue

        try:
            with open(meta_path, encoding="utf-8") as f:
                meta = yaml.safe_load(f)
        except Exception:
            continue

        yaml_rec = meta.get("recurrence") or {}
        # Extract config-only attrs from meta.yaml (same logic as migrate-to-sqlite.py)
        yaml_config = {k: v for k, v in yaml_rec.items() if k not in runtime_keys}

        # Normalize for comparison: serialize both to sorted JSON strings
        # (handles dict ordering differences)
        def _normalize(obj):
            if isinstance(obj, dict):
                return {k: _normalize(v) for k, v in sorted(obj.items())}
            if isinstance(obj, list):
                return [_normalize(i) for i in obj]
            return obj

        sqlite_norm = json.dumps(_normalize(sqlite_config), sort_keys=True)
        yaml_norm = json.dumps(_normalize(yaml_config), sort_keys=True, default=str)

        if sqlite_norm != yaml_norm:
            issues.append((
                "recurrence_drift",
                f"{etype} '{ename}' [{eid[:8]}]: meta.yaml recurrence config differs from SQLite"
            ))

    return issues


def repair_sqlite(workspace):
    """Rebuild SQLite from disk (delegates to migrate-to-sqlite.py)."""
    import subprocess
    script = workspace / "_system" / "scripts" / "migrate-to-sqlite.py"
    result = subprocess.run(
        [sys.executable, str(script), str(workspace)],
        capture_output=True, text=True
    )
    return result.returncode == 0, result.stdout, result.stderr


def repair_missing_inverse(workspace, conn, schema, source_id, rel, target_id):
    """Add missing inverse relationship to target entity's meta.yaml.

    Guards against duplicate writes: if source_id already appears in the
    target's meta.yaml, the file is not modified (the issue is SQLite
    drift only, which the post-repair rebuild handles).
    """
    inverse = schema.inverses.get(rel)
    if not inverse:
        return False, "No known inverse"

    # Find target's meta.yaml
    target_row = conn.execute("SELECT path FROM entities WHERE id = ?", (target_id,)).fetchone()
    if not target_row:
        return False, "Target entity not found in SQLite"

    meta_path = workspace / target_row['path'] / "meta.yaml"
    if not meta_path.exists():
        return False, f"meta.yaml not found at {meta_path}"

    with safe_write(str(meta_path)) as (content, write):
        # Guard: check that source_id actually appears under the inverse key,
        # not just anywhere in the file (description, name, other relationship).
        in_inverse_section = False
        for line in content.splitlines():
            if line.startswith(f"{inverse}:"):
                in_inverse_section = True
                continue
            if in_inverse_section:
                stripped = line.strip()
                if stripped.startswith("- ") and source_id in stripped:
                    return True, f"Already present in meta.yaml — SQLite drift only ({inverse}: {source_id} in {target_id})"
                if stripped and not stripped.startswith("- "):
                    break  # Left the list section without finding source_id

        # Check if inverse section exists
        lines = content.split('\n')
        inverse_line_idx = None
        for i, line in enumerate(lines):
            if line.startswith(f'{inverse}:'):
                inverse_line_idx = i
                break

        if inverse_line_idx is not None:
            # Check whether the key carries an inline scalar value (e.g., "contains: uuid")
            # vs. a bare list header (e.g., "contains:").
            key_line = lines[inverse_line_idx].strip()
            inline_value = key_line[len(f'{inverse}:'):].strip()

            if inline_value:
                # Scalar form — convert in place to a list, then append new item
                lines[inverse_line_idx] = f"{inverse}:"
                lines.insert(inverse_line_idx + 1, f"  - {inline_value}")
                lines.insert(inverse_line_idx + 2, f"  - {source_id}")
            else:
                # List header form — add to existing list after the last UUID item
                insert_idx = inverse_line_idx + 1
                while insert_idx < len(lines):
                    stripped = lines[insert_idx].strip()
                    if stripped.startswith('- ') and len(stripped) >= 38:
                        insert_idx += 1
                    else:
                        break
                # Detect indentation from existing list items and match it
                prefix = ""
                first_item_idx = inverse_line_idx + 1
                if first_item_idx < len(lines):
                    first_item = lines[first_item_idx]
                    if first_item.lstrip().startswith('- '):
                        prefix = first_item[: len(first_item) - len(first_item.lstrip())]
                    else:
                        print(f"  [warn] Unexpected content after '{inverse}:' key — defaulting to no-indent style")
                lines.insert(insert_idx, f"{prefix}- {source_id}")
        else:
            # Append new relationship section
            # Remove trailing empty lines
            while lines and lines[-1].strip() == '':
                lines.pop()
            lines.append(f"{inverse}:")
            lines.append(f"  - {source_id}")

        # Update modified timestamp. Route through quote_yaml_scalar so the
        # emission matches dump_entity_meta canonical form — bare timestamps
        # here would silently un-quote values the creation/update paths
        # emitted quoted; see ca885d21/2b44f20e.
        from datetime import datetime
        from lib.fileio import quote_yaml_scalar
        now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        quoted_now = quote_yaml_scalar(now)
        for i, line in enumerate(lines):
            if line.startswith("last_edited:"):
                lines[i] = f"last_edited: {quoted_now}"
                break

        write('\n'.join(lines) + '\n')

    return True, f"Added {inverse}: {source_id} to {target_id}"


def main():
    repair_mode = "--repair" in sys.argv
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    workspace = Path(args[0]) if args else Path(os.environ.get("SUBSTRATE_PATH", str(Path.cwd())))

    db_path = workspace / "_system" / "index" / "substrate.db"
    entities_dir = workspace / "entities"

    if not entities_dir.exists():
        print(f"Error: {entities_dir} not found", file=sys.stderr)
        sys.exit(2)

    schema = load_schema(str(workspace))
    conn = load_db(str(db_path))

    if not conn:
        print(f"Error: SQLite database not found at {db_path}", file=sys.stderr)
        print("Run: python3 _system/scripts/migrate-to-sqlite.py .")
        sys.exit(2)

    all_issues = []

    print("=" * 60)
    print("SUBSTRATE VALIDATION REPORT")
    print("=" * 60)

    # Check 1: SQLite vs disk
    print("\n--- Check 1: SQLite vs Disk ---")
    disk_issues, disk_count, sqlite_count = check_sqlite_vs_disk(workspace, conn)
    print(f"  On disk:   {disk_count}")
    print(f"  In SQLite: {sqlite_count}")
    if disk_issues:
        print(f"  Issues:    {len(disk_issues)}")
        for severity, msg in disk_issues:
            print(f"    [{severity}] {msg}")
    else:
        print("  OK")
    all_issues.extend(disk_issues)

    # Check 2: Bidirectional relationships
    print("\n--- Check 2: Bidirectional Relationships ---")
    bidi_issues = check_bidirectional(conn, schema)
    rel_count = conn.execute("SELECT COUNT(*) FROM relationships").fetchone()[0]
    print(f"  Relationships: {rel_count}")
    if bidi_issues:
        print(f"  Issues:        {len(bidi_issues)}")
        for severity, msg in bidi_issues:
            print(f"    [{severity}] {msg}")
    else:
        print("  OK")
    all_issues.extend(bidi_issues)

    # Check 3: Referential integrity
    print("\n--- Check 3: Referential Integrity ---")
    ref_issues = check_referential_integrity(conn)
    if ref_issues:
        print(f"  Issues: {len(ref_issues)}")
        for severity, msg in ref_issues:
            print(f"    [{severity}] {msg}")
    else:
        print("  OK")
    all_issues.extend(ref_issues)

    # Check 4: Schema compliance
    print("\n--- Check 4: Schema Compliance ---")
    schema_issues = check_schema_compliance(conn, schema)
    if schema_issues:
        print(f"  Issues: {len(schema_issues)}")
        for severity, msg in schema_issues:
            print(f"    [{severity}] {msg}")
    else:
        print("  OK")
    all_issues.extend(schema_issues)

    # Check 5: Schema/SQLite drift
    # Catches dimensions added to attributes.yaml without running migrate-to-sqlite.py.
    # A missing column causes update-entity.py to crash on any entity update — this
    # surfaces that silently before it becomes a runtime failure.
    print("\n--- Check 5: Schema/SQLite Drift ---")
    drift_issues = check_schema_sqlite_drift(conn, schema)
    if drift_issues:
        print(f"  Issues: {len(drift_issues)}")
        for severity, msg in drift_issues:
            print(f"    [{severity}] {msg}")
        print("  Fix: python3 _system/scripts/migrate-to-sqlite.py .")
    else:
        print("  OK")
    all_issues.extend(drift_issues)

    # Check 6: Recurrence config drift
    # Catches meta.yaml recurrence block out of sync with SQLite recurrence_schedule JSON.
    # This happens when agents edit meta.yaml directly without going through update-entity.py.
    print("\n--- Check 6: Recurrence Config Drift ---")
    rec_drift_issues = check_recurrence_drift(conn, workspace)
    if rec_drift_issues:
        print(f"  Issues: {len(rec_drift_issues)}")
        for severity, msg in rec_drift_issues:
            print(f"    [{severity}] {msg}")
        print("  Fix: python3 _system/scripts/migrate-to-sqlite.py .")
    else:
        print("  OK")
    all_issues.extend(rec_drift_issues)

    # Summary
    print("\n" + "=" * 60)
    if not all_issues:
        print("ALL CHECKS PASSED")
        conn.close()
        sys.exit(0)

    print(f"ISSUES FOUND: {len(all_issues)}")

    # Categorize
    categories = {}
    for severity, msg in all_issues:
        categories.setdefault(severity, []).append(msg)

    for cat, msgs in sorted(categories.items()):
        print(f"\n  {cat} ({len(msgs)}):")
        for msg in msgs:
            print(f"    - {msg}")

    # Repair
    if repair_mode:
        print("\n" + "=" * 60)
        print("RUNNING REPAIRS")
        print("=" * 60)

        repairs_made = 0

        # Repair SQLite drift (entity count mismatch) and schema/SQLite drift
        # (missing dimension columns) in a single migrate pass — both require
        # the same fix and there's no benefit to running migrate twice.
        sqlite_drift = [i for i in all_issues if i[0] in ("missing_from_sqlite", "ghost_in_sqlite")]
        schema_drift = [i for i in all_issues if i[0] == "schema_sqlite_drift"]
        combined_drift = sqlite_drift + schema_drift
        if combined_drift:
            drift_desc = []
            if sqlite_drift:
                drift_desc.append(f"{len(sqlite_drift)} entity sync issue(s)")
            if schema_drift:
                drift_desc.append(f"{len(schema_drift)} missing column(s)")
            print(f"\nRebuilding SQLite ({', '.join(drift_desc)})...")
            success, stdout, stderr = repair_sqlite(workspace)
            if success:
                print("  SQLite rebuilt successfully")
                repairs_made += len(combined_drift)
            else:
                print(f"  SQLite rebuild failed: {stderr}")

        # Repair missing inverses
        inverse_issues = [i for i in all_issues if i[0] == "missing_inverse"]
        if inverse_issues:
            print(f"\nRepairing {len(inverse_issues)} missing inverses...")
            # Re-connect after potential rebuild
            conn.close()
            conn = load_db(str(db_path))

            for _, msg in inverse_issues:
                # Parse: "SOURCE --REL--> TARGET, but TARGET missing --INV--> SOURCE"
                parts = msg.split(" --")
                source = parts[0].strip()
                rel = parts[1].split("-->")[0].strip()
                target = parts[1].split("--> ")[1].split(",")[0].strip()

                success, detail = repair_missing_inverse(workspace, conn, schema, source, rel, target)
                status = "repaired" if success else "failed"
                print(f"  [{status}] {detail}")
                if success:
                    repairs_made += 1

            # Rebuild SQLite after meta.yaml changes
            if repairs_made > 0:
                print("\nRebuilding SQLite after repairs...")
                success, _, stderr = repair_sqlite(workspace)
                if success:
                    print("  SQLite rebuilt successfully")
                else:
                    print(f"  SQLite rebuild failed: {stderr}")

        # Cannot auto-repair
        manual_types = ("unknown_type", "invalid_dimension", "unknown_relationship", "dangling_reference", "dangling_source", "disallowed_dimension")
        manual_issues = [i for i in all_issues if i[0] in manual_types]
        if manual_issues:
            print(f"\n{len(manual_issues)} issue(s) require manual review:")
            for severity, msg in manual_issues:
                print(f"  [{severity}] {msg}")

        print(f"\nRepairs completed: {repairs_made}")
    else:
        print(f"\nRun with --repair to fix automatically where possible.")

    conn.close()
    sys.exit(1)


if __name__ == "__main__":
    main()
