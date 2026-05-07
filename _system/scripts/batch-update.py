#!/usr/bin/env python3
"""
Batch-update Substrate entity status from a YAML or JSON manifest.

One boot, one schema load, one DB connection for all lookups.
Each update is applied independently. Errors are collected and reported
at the end; processing continues past per-entity errors by default.

Usage:
  python3 batch-update.py --manifest updates.yaml
  python3 batch-update.py --manifest updates.json --dry-run
  python3 batch-update.py --manifest updates.yaml --fail-fast

Manifest format (YAML):
  - id: UUID or short prefix
    # Any dimensional flags:
    life_stage: in_progress
    focus: active
    resolution: completed
    assessment: on_track
    importance_tactical: high
    health: stable
    importance_strategic: core
    phase: live
    # Optional metadata:
    name: "New name"
    description: "Updated description"
    # Extra attributes:
    attrs:
      some_field: new_value

JSON manifest: same structure as YAML.

Notes:
  - 'id' can be a full UUID or the first 8+ characters (prefix match).
  - Only specified attributes are changed; everything else is preserved.
  - Exit code 1 if any entity fails; exit code 0 if all succeed.
  - --fail-fast stops on first error (default: apply all, report errors at end).
"""

import os
import sys
import re
import sqlite3
import argparse
from datetime import datetime

try:
    import yaml
except ImportError:
    print("Error: PyYAML required. Run: pip install pyyaml")
    sys.exit(1)

import json
from schema import load_schema
from precheck import validate_update
from changelog import log_change
from lib.fileio import safe_write

SUBSTRATE_PATH = os.environ.get("SUBSTRATE_PATH", os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
DB_PATH = os.path.join(SUBSTRATE_PATH, "_system", "index", "substrate.db")
schema = load_schema(SUBSTRATE_PATH)

# ---------------------------------------------------------------------------
# Manifest loading
# ---------------------------------------------------------------------------

def load_manifest(path):
    with open(path, 'r', encoding="utf-8") as f:
        content = f.read()
    if path.endswith('.json'):
        return json.loads(content)
    data = yaml.safe_load(content)
    if isinstance(data, dict):
        return data.get('updates', [data])
    return data


# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------

from lib.fileio import quote_yaml_scalar


def update_meta_attr(content, attr, value):
    if isinstance(value, str):
        value = quote_yaml_scalar(value)
    lines = content.rstrip('\n').split('\n')
    updated = False
    new_lines = []
    for line in lines:
        if line.startswith(f"{attr}:") and not line.startswith(f"{attr}s:"):
            new_lines.append(f"{attr}: {value}")
            updated = True
        else:
            new_lines.append(line)
    if not updated:
        insert_at = len(new_lines)
        for i, line in enumerate(new_lines):
            if ':' in line and not line.startswith(' ') and not line.startswith('#'):
                key = line.split(':')[0].strip()
                if key in schema.relationship_names:
                    insert_at = i
                    break
        new_lines.insert(insert_at, f"{attr}: {value}")
    return '\n'.join(new_lines) + '\n'


def find_entity(entity_id, conn):
    """Look up entity by full UUID or prefix."""
    c = conn.cursor()
    c.execute("SELECT id, path, name, type FROM entities WHERE id = ? OR id LIKE ?",
              (entity_id, f"{entity_id}%"))
    rows = c.fetchall()
    if not rows:
        return None
    if len(rows) > 1:
        return f"AMBIGUOUS:{entity_id}"  # Multiple matches for prefix
    return {"id": rows[0][0], "path": rows[0][1], "name": rows[0][2], "type": rows[0][3]}


# ---------------------------------------------------------------------------
# Apply a single update entry
# ---------------------------------------------------------------------------

def apply_update(entry, conn, dry_run=False):
    """Apply one entry from the manifest. Returns (success, message)."""
    entity_id_raw = entry.get('id')
    if not entity_id_raw:
        return False, "Entry missing 'id'"

    entity_id_raw = str(entity_id_raw)
    entity = find_entity(entity_id_raw, conn)
    if entity is None:
        return False, f"Entity not found: {entity_id_raw}"
    if isinstance(entity, str) and entity.startswith("AMBIGUOUS:"):
        return False, f"Ambiguous prefix '{entity_id_raw}' — multiple entities match"

    entity_id = entity['id']
    entity_type = entity['type']
    meta_path = os.path.join(SUBSTRATE_PATH, entity['path'], "meta.yaml")

    if not os.path.exists(meta_path):
        return False, f"meta.yaml not found: {meta_path}"

    # Collect dim updates
    dim_inputs = {}
    for dim in schema.dimension_names:
        val = entry.get(dim) or entry.get(dim.replace('_', '-'))
        if val is not None:
            dim_inputs[dim] = str(val)

    name = entry.get('name')
    description = entry.get('description')
    extra_attrs = []
    for k, v in (entry.get('attrs') or {}).items():
        extra_attrs.append((str(k), str(v)))

    # Route dimension keys from attrs into dim_inputs.
    # Explicit dimension fields in the batch entry take precedence.
    # Uses schema.dimension_names — stays current as dims are added to the schema.
    _dim_extra_batch = [(k, v) for k, v in extra_attrs if k in schema.dimension_names]
    extra_attrs = [(k, v) for k, v in extra_attrs if k not in schema.dimension_names]
    for dim, val in _dim_extra_batch:
        if dim not in dim_inputs:
            dim_inputs[dim] = val

    # Nothing to update?
    if not dim_inputs and name is None and description is None and not extra_attrs:
        return False, f"No changes specified for {entity_id[:8]}"

    # Precheck validation
    caller = "agent" if os.environ.get("SUBSTRATE_AGENT") else "human"
    validation = validate_update(
        schema, entity_id,
        entity_type=entity_type,
        dimensions=dim_inputs,
        relationships=[],
        extra_attrs=extra_attrs,
        db_path=DB_PATH,
        caller=caller,
    )
    for w in validation.warnings:
        print(f"  Warning ({entity['name']}): {w}")
    if not validation.valid:
        return False, f"Validation failed for {entity['name']}: {'; '.join(validation.errors)}"

    if dry_run:
        changes_summary = []
        if dim_inputs:
            changes_summary.extend(f"{k}={v}" for k, v in dim_inputs.items())
        if name:
            changes_summary.append(f"name={name}")
        if description:
            changes_summary.append(f"description=...")
        for k, v in extra_attrs:
            changes_summary.append(f"{k}={v}")
        return True, f"[DRY RUN] Would update {entity['name']} ({entity_id[:8]}): {', '.join(changes_summary)}"

    # Apply meta.yaml updates
    now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

    with safe_write(meta_path) as (content, write):
        for dim, val in dim_inputs.items():
            content = update_meta_attr(content, dim, val)
        if name:
            content = update_meta_attr(content, 'name', name)
        if description:
            content = update_meta_attr(content, 'description', description)
        for k, v in extra_attrs:
            content = update_meta_attr(content, k, v)

        content = re.sub(r'last_edited:.*', f'last_edited: {quote_yaml_scalar(now)}', content)
        write(content)

    # SQLite update
    c = conn.cursor()
    for dim, val in dim_inputs.items():
        c.execute(f"UPDATE entities SET {dim} = ?, last_edited = ? WHERE id = ?",
                  (val, now, entity_id))
    if name:
        c.execute("UPDATE entities SET name = ?, last_edited = ? WHERE id = ?",
                  (name, now, entity_id))
    if description:
        c.execute("UPDATE entities SET description = ?, last_edited = ? WHERE id = ?",
                  (description, now, entity_id))
    conn.commit()

    # Changelog
    changes = []
    for dim, val in dim_inputs.items():
        changes.append({"attribute": dim, "value": val})
    if name:
        changes.append({"attribute": "name", "value": name})
    if description:
        changes.append({"attribute": "description", "value": description})
    for k, v in extra_attrs:
        changes.append({"attribute": k, "value": v})
    log_change("update", entity_id, entity_type, entity['name'], changes=changes or None)

    return True, f"Updated {entity['name']} ({entity_id[:8]}): {', '.join(f'{k}={v}' for k, v in dim_inputs.items())}"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Batch-update Substrate entity status from a manifest", add_help=False)
    parser.add_argument("--manifest", "-m", required=True, help="Path to YAML or JSON manifest file")
    parser.add_argument("--dry-run", action="store_true", help="Show what would change without writing")
    parser.add_argument("--fail-fast", action="store_true", dest="fail_fast",
                        help="Stop on first error (default: apply all, report errors at end)")
    parser.add_argument("--help", "-h", action="store_true")
    args = parser.parse_args()

    if args.help:
        print(__doc__)
        sys.exit(0)

    manifest_path = args.manifest
    if not os.path.isabs(manifest_path):
        manifest_path = os.path.join(os.getcwd(), manifest_path)
    if not os.path.exists(manifest_path):
        print(f"Error: manifest not found: {manifest_path}")
        sys.exit(1)

    entries = load_manifest(manifest_path)
    if not entries:
        print("No entries found in manifest.")
        sys.exit(0)

    conn = sqlite3.connect(DB_PATH)
    errors = []
    successes = []

    for entry in entries:
        success, message = apply_update(entry, conn, dry_run=args.dry_run)
        if success:
            successes.append(message)
            print(f"  ✓ {message}")
        else:
            errors.append(message)
            print(f"  ✗ {message}")
            if args.fail_fast:
                conn.close()
                sys.exit(1)

    conn.close()

    print()
    if args.dry_run:
        print(f"Dry run complete: {len(successes)} would be updated.")
    else:
        print(f"Done: {len(successes)} updated, {len(errors)} failed.")
    if errors:
        sys.exit(1)


if __name__ == "__main__":
    main()
