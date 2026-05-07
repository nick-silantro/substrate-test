#!/usr/bin/env python3
"""
Batch-create Substrate entities from a YAML or JSON manifest.

One boot, one schema load, one DB connection, N inserts.
All entities are validated before any writes. If validation fails,
nothing is written (atomic: all or nothing).

Usage:
  python3 batch-create.py --manifest entities.yaml
  python3 batch-create.py --manifest entities.json --dry-run

Manifest format (YAML):
  # Optional defaults applied to every entity unless overridden
  defaults:
    belongs_to: PARENT_UUID
    engagement_mode: none

  entities:
    - type: task
      name: "Task 1"
      ref: t1                     # optional: local name for cross-entity refs
      description: "Do something"
      engagement_mode: execute
      importance_tactical: high
      belongs_to: PARENT_UUID     # relationship key = relationship type name
      recurrence:                 # nested dict for recurring entities
        schedule_type: interval
        interval:
          value: 7
          unit: days
      attrs:                      # extra type-specific attributes as key: value dict
        some_field: some_value

    - type: task
      name: "Task 2"
      depends_on: ref:t1          # ref: prefix = reference to earlier entity in manifest

JSON manifest: same structure as YAML, loaded from .json file.

Notes:
  - All UUIDs are pre-generated before any writes, so ref: references resolve correctly.
  - Relationships between entities in the same manifest are handled via
    ref: on both sides — inverse relationships are injected into both meta.yaml files.
  - For relationships to entities outside the manifest, the target's meta.yaml
    is updated with the inverse relationship (same as create-entity.py).
  - Exit code 1 if any entity fails validation (no partial writes).
"""

import os
import sys
import re
import uuid
import json
import sqlite3
import argparse
from datetime import datetime, date as _date

try:
    import yaml
except ImportError:
    print("Error: PyYAML required. Run: pip install pyyaml")
    sys.exit(1)

from schema import load_schema
from precheck import validate_create
from cascades import detect_dependency_cycle, format_cycle_error
from triggers import validate_recurrence_config, calculate_initial_next_due
from changelog import log_change
from lib.fileio import safe_write

SUBSTRATE_PATH = os.environ.get("SUBSTRATE_PATH", os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
DB_PATH = os.path.join(SUBSTRATE_PATH, "_system", "index", "substrate.db")
schema = load_schema(SUBSTRATE_PATH)

# meta_status is written explicitly in build_meta_yaml; exclude it from DIM_ORDER to avoid duplicate YAML keys.
DIM_ORDER = [d for d in schema.dimension_names if d != "meta_status"]


# ---------------------------------------------------------------------------
# Manifest loading
# ---------------------------------------------------------------------------

def load_manifest(path):
    """Load YAML or JSON manifest file. Returns parsed dict."""
    with open(path, 'r', encoding="utf-8") as f:
        content = f.read()
    if path.endswith('.json'):
        return json.loads(content)
    return yaml.safe_load(content)


# ---------------------------------------------------------------------------
# Utility functions (adapted from create-entity.py)
# ---------------------------------------------------------------------------

def shard_path(entity_type, entity_id):
    prefix = entity_id[:2]
    next2 = entity_id[2:4]
    return os.path.join("entities", entity_type, prefix, next2, entity_id)


from lib.fileio import dump_entity_meta, quote_yaml_scalar


def resolve_dimensions(entity_type, explicit_dims):
    config = schema.dimension_config(entity_type)
    defaults = schema.dimension_defaults(entity_type)
    result = dict(defaults)
    for dim, val in explicit_dims.items():
        if config.get(dim) == "disallowed":
            continue
        if val is not None:
            result[dim] = val
    return result


def build_meta_yaml(entity_id, entity_type, name, description, dims, due, relationships,
                    extra_attrs=None, recurrence_config=None, next_due=None,
                    completion_count=0, streak=0):
    """Build meta.yaml content via canonical dict + dump_entity_meta.

    Parallel implementation to create-entity.py's build_meta_yaml — differences:
      - Uses schema-derived DIM_ORDER instead of the hardcoded FLAIR+HIP order.
      - No list_attrs parameter (batch creation doesn't accept list-valued attrs
        in its manifest today).
    """
    now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

    meta = {}
    meta["id"] = entity_id
    meta["type"] = entity_type
    meta["name"] = name
    meta["description"] = description
    meta["meta_status"] = "live"

    for dim in DIM_ORDER:
        if dim in dims:
            meta[dim] = dims[dim]

    if due:
        meta["due"] = due

    for k, v in (extra_attrs or []):
        if k in dims or k in meta:
            continue
        meta[k] = v

    if recurrence_config:
        rec = {}
        for k, v in recurrence_config.items():
            if isinstance(v, list):
                rec[k] = list(v)
            elif isinstance(v, dict):
                rec[k] = dict(v)
            else:
                rec[k] = v
        if recurrence_config.get("schedule_type") not in (None, "none"):
            if next_due:
                rec["next_due"] = next_due
            rec["completion_count"] = completion_count
            rec["streak"] = streak
        meta["recurrence"] = rec

    meta["created"] = now
    meta["last_edited"] = now

    rel_groups = {}
    for rel_type, target_id in relationships:
        rel_groups.setdefault(rel_type, []).append(target_id)
    for rel_type, targets in rel_groups.items():
        meta[rel_type] = list(targets)

    return dump_entity_meta(meta)


def update_target_meta(target_id, inverse_rel, entity_id, conn):
    """Add inverse relationship to target entity's meta.yaml (external targets only)."""
    c = conn.cursor()
    c.execute("SELECT path FROM entities WHERE id = ?", (target_id,))
    row = c.fetchone()
    if not row:
        print(f"  Warning: Target {target_id} not found in index — inverse not written to meta.yaml")
        return

    meta_path = os.path.join(SUBSTRATE_PATH, row[0], "meta.yaml")
    if not os.path.exists(meta_path):
        print(f"  Warning: Target meta.yaml not found: {meta_path}")
        return

    with safe_write(meta_path) as (content, write):
        # Precise idempotency: check if entity_id is already listed under this
        # specific relationship header (not just anywhere in the file).
        in_section = False
        for line in content.split('\n'):
            stripped = line.strip()
            if stripped == f"{inverse_rel}:":
                in_section = True
            elif in_section and stripped.startswith('- '):
                if entity_id in stripped:
                    return
            elif in_section and stripped and not stripped.startswith('- '):
                in_section = False

        now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        content = re.sub(r'last_edited:.*', f'last_edited: {quote_yaml_scalar(now)}', content)

        lines = content.rstrip('\n').split('\n')
        new_lines = []
        inserted = False
        for line in lines:
            new_lines.append(line)
            if line.strip() == f"{inverse_rel}:" and not inserted:
                new_lines.append(f"  - {entity_id}")
                inserted = True
        if not inserted:
            new_lines.append(f"{inverse_rel}:")
            new_lines.append(f"  - {entity_id}")

        final_content = '\n'.join(new_lines) + '\n'
        write(final_content)

    # Post-write verification: confirm the inverse was actually persisted
    with open(meta_path, 'r', encoding="utf-8") as f:
        written = f.read()
    if f"- {entity_id}" not in written:
        print(f"  ERROR: Inverse write verification failed — {inverse_rel} → {entity_id} not found in {meta_path} after write")


# ---------------------------------------------------------------------------
# Manifest parsing
# ---------------------------------------------------------------------------

def parse_entry(entry, defaults, index, ref_map):
    """Parse a single manifest entry into a normalized entity spec.

    Returns (spec_dict, errors_list). spec_dict keys:
      type, name, ref, description, id, due, engagement_mode,
      explicit_dims, relationships, extra_attrs, recurrence_config
    """
    errors = []
    entry_label = entry.get('name', f'entry[{index}]')

    # Merge defaults (entry values take precedence)
    merged = dict(defaults or {})
    merged.update(entry)

    entity_type = merged.get('type')
    if not entity_type:
        errors.append(f"{entry_label}: 'type' is required")
    name = merged.get('name')
    if not name:
        errors.append(f"{entry_label}: 'name' is required")
    if errors:
        return None, errors

    description = str(merged.get('description', '[awaiting context]'))
    ref = merged.get('ref')
    explicit_id = merged.get('id')
    due = merged.get('due')
    engagement_mode_raw = merged.get('engagement_mode')

    # Dimensional values
    explicit_dims = {}
    for dim in schema.dimension_names:
        val = merged.get(dim)
        # Also support hyphen form (life-stage → life_stage)
        val_hyphen = merged.get(dim.replace('_', '-'))
        resolved = val or val_hyphen
        if resolved is not None:
            explicit_dims[dim] = str(resolved)
    # Also check 'life-stage', 'importance-tactical', etc.
    for key, val in merged.items():
        normalized = key.replace('-', '_')
        if normalized in schema.dimension_names and normalized not in explicit_dims and val is not None:
            explicit_dims[normalized] = str(val)

    # Relationships: any key that matches a known relationship name
    relationships = []
    known_rels = set(schema.inverses.keys())
    for key, val in merged.items():
        if key in known_rels:
            if isinstance(val, list):
                for v in val:
                    relationships.append((key, str(v)))
            elif val is not None:
                relationships.append((key, str(val)))

    # Extra attrs from 'attrs' dict
    extra_attrs = []
    for k, v in (merged.get('attrs') or {}).items():
        extra_attrs.append((str(k), str(v)))

    # Recurrence from nested 'recurrence' dict or via attrs
    recurrence_config = None
    if 'recurrence' in merged and isinstance(merged['recurrence'], dict):
        recurrence_config = dict(merged['recurrence'])
        # Type coercion
        # Compound interval: coerce value to int
        if 'interval' in recurrence_config and isinstance(recurrence_config['interval'], dict):
            recurrence_config['interval']['value'] = int(recurrence_config['interval']['value'])
        if 'lead_time_days' in recurrence_config:
            recurrence_config['lead_time_days'] = int(recurrence_config['lead_time_days'])
        if 'day_of_month' in recurrence_config and recurrence_config['day_of_month'] != 'last':
            recurrence_config['day_of_month'] = int(recurrence_config['day_of_month'])
        if 'days' in recurrence_config and isinstance(recurrence_config['days'], str):
            recurrence_config['days'] = [d.strip() for d in recurrence_config['days'].split(',')]
    else:
        # Extract recurrence.* from extra_attrs
        remaining_attrs = []
        rec = {}
        for k, v in extra_attrs:
            if k.startswith('recurrence.'):
                field_name = k[len('recurrence.'):]
                # Compound interval via dot-notation: recurrence.interval.value, recurrence.interval.unit
                if field_name.startswith('interval.'):
                    sub = field_name[len('interval.'):]
                    if 'interval' not in rec:
                        rec['interval'] = {}
                    rec['interval'][sub] = int(v) if sub == 'value' else v
                elif field_name == 'lead_time_days':
                    v = int(v)
                    rec[field_name] = v
                elif field_name == 'day_of_month' and v != 'last':
                    v = int(v)
                    rec[field_name] = v
                elif field_name == 'days':
                    v = [d.strip() for d in v.split(',')]
                    rec[field_name] = v
                else:
                    rec[field_name] = v
            else:
                remaining_attrs.append((k, v))
        if rec:
            recurrence_config = rec
            extra_attrs = remaining_attrs

    return {
        'type': entity_type,
        'name': name,
        'ref': ref,
        'description': description,
        'id': explicit_id,
        'due': due,
        'engagement_mode_raw': engagement_mode_raw,
        'explicit_dims': explicit_dims,
        'relationships': relationships,
        'extra_attrs': extra_attrs,
        'recurrence_config': recurrence_config,
    }, []


def resolve_refs(relationships, ref_map, entry_label):
    """Replace ref:NAME references with actual UUIDs. Returns (resolved, errors)."""
    resolved = []
    errors = []
    for rel_type, target in relationships:
        if str(target).startswith('ref:'):
            ref_name = target[4:]
            if ref_name not in ref_map:
                errors.append(f"{entry_label}: ref '{ref_name}' not defined (must appear earlier in manifest)")
            else:
                resolved.append((rel_type, ref_map[ref_name]))
        else:
            resolved.append((rel_type, target))
    return resolved, errors


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Batch-create Substrate entities from a manifest", add_help=False)
    parser.add_argument("--manifest", "-m", required=True, help="Path to YAML or JSON manifest file")
    parser.add_argument("--dry-run", action="store_true", help="Validate and print what would be created without writing")
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

    raw = load_manifest(manifest_path)
    if isinstance(raw, list):
        # Bare list format: no defaults
        defaults = {}
        raw_entities = raw
    else:
        defaults = raw.get('defaults', {})
        raw_entities = raw.get('entities', [])

    if not raw_entities:
        print("No entities found in manifest.")
        sys.exit(0)

    # Pass 1: parse all entries, pre-generate UUIDs, build ref_map
    ref_map = {}        # ref_name → uuid
    pre_specs = []      # (raw_spec, errors)

    for i, entry in enumerate(raw_entities):
        spec, errs = parse_entry(entry, defaults, i, ref_map)
        if spec and spec.get('ref'):
            # Assign UUID now so later entries can reference it
            spec['_uuid'] = spec['id'] or str(uuid.uuid4())
            ref_map[spec['ref']] = spec['_uuid']
        elif spec:
            spec['_uuid'] = spec['id'] or str(uuid.uuid4())
        pre_specs.append((spec, errs))

    # Pass 2: resolve refs, resolve engagement_mode, validate all entities
    all_errors = []
    specs = []

    # Track batch entity UUIDs for within-batch inverse collection
    batch_uuid_set = {s['_uuid'] for s, _ in pre_specs if s is not None}

    for i, (spec, parse_errs) in enumerate(pre_specs):
        if parse_errs:
            all_errors.extend(parse_errs)
            specs.append(None)
            continue
        if spec is None:
            specs.append(None)
            continue

        entry_label = spec['name']
        entity_type = spec['type']

        # Resolve ref: targets
        resolved_rels, ref_errs = resolve_refs(spec['relationships'], ref_map, entry_label)
        if ref_errs:
            all_errors.extend(ref_errs)
            specs.append(None)
            continue
        spec['relationships'] = resolved_rels

        # Resolve engagement_mode
        em_raw = spec['engagement_mode_raw']
        em_access = schema.access_level('engagement_mode', entity_type, 'attribute')
        if em_raw is None and em_access in ('required', 'preferred'):
            engagement_mode = schema.attr_default('engagement_mode') or 'none'
        elif em_raw is not None and em_access == 'forbidden':
            engagement_mode = None
        else:
            engagement_mode = str(em_raw) if em_raw is not None else None
        spec['engagement_mode'] = engagement_mode

        # Build all_attrs for precheck: extra_attrs + recurrence.* (so precheck sees
        # recurrence.schedule_type regardless of whether it came from attrs dict or
        # nested recurrence block) + engagement_mode
        all_attrs = list(spec['extra_attrs'])
        if spec['recurrence_config']:
            for k, v in spec['recurrence_config'].items():
                all_attrs.append((f'recurrence.{k}', str(v)))
        if engagement_mode is not None:
            all_attrs.append(('engagement_mode', engagement_mode))

        # Precheck validation: exclude within-batch relationship targets
        # (they don't exist in DB yet, but will be created in the same batch)
        external_rels = [(rt, tid) for rt, tid in resolved_rels if tid not in batch_uuid_set]
        validation = validate_create(
            schema, entity_type,
            name=spec['name'],
            description=spec['description'],
            dimensions=spec['explicit_dims'],
            relationships=external_rels,
            extra_attrs=all_attrs,
            due=spec['due'],
            db_path=DB_PATH,
        )
        for w in validation.warnings:
            print(f"  Warning ({entry_label}): {w}")
        if not validation.valid:
            for e in validation.errors:
                all_errors.append(f"{entry_label}: {e}")
            specs.append(None)
            continue

        # Recurrence validation
        if spec['recurrence_config']:
            rec_errs = validate_recurrence_config(spec['recurrence_config'])
            if rec_errs:
                for e in rec_errs:
                    all_errors.append(f"{entry_label}: {e}")
                specs.append(None)
                continue

        # Resolve dimensions
        dims = resolve_dimensions(entity_type, spec['explicit_dims'])

        # Check dependency blocking (depends_on targets that are unresolved)
        depends_on_targets = [tid for rt, tid in resolved_rels if rt == 'depends_on']
        if depends_on_targets:
            external_deps = [t for t in depends_on_targets if t not in batch_uuid_set]
            if external_deps:
                conn_check = sqlite3.connect(DB_PATH)
                c_check = conn_check.cursor()
                placeholders = ','.join('?' * len(external_deps))
                c_check.execute(
                    f"SELECT COUNT(*) FROM entities WHERE id IN ({placeholders}) "
                    f"AND resolution NOT IN ('completed', 'superseded')",
                    external_deps,
                )
                unresolved = c_check.fetchone()[0]
                conn_check.close()
                if unresolved > 0:
                    spec['extra_attrs'].append(('is_blocked', 'true'))

        # Calculate next_due for recurring entities
        next_due = None
        if spec['recurrence_config']:
            computed = calculate_initial_next_due(spec['recurrence_config'], _date.today())
            if computed:
                next_due = computed.isoformat()

        # Add engagement_mode to extra_attrs for meta.yaml
        final_extra_attrs = list(spec['extra_attrs'])
        if engagement_mode is not None:
            final_extra_attrs.append(('engagement_mode', engagement_mode))

        spec['dims'] = dims
        spec['next_due'] = next_due
        spec['final_extra_attrs'] = final_extra_attrs
        specs.append(spec)

    if all_errors:
        print(f"Validation failed ({len(all_errors)} error(s)):")
        for e in all_errors:
            print(f"  ✗ {e}")
        sys.exit(1)

    # Pass 3: collect within-batch inverse relationships
    # For each entity A with relationship → entity B where B is also in this batch,
    # add the inverse relationship to B's incoming_rels.
    incoming_rels = {spec['_uuid']: [] for spec in specs if spec is not None}
    for spec in specs:
        if spec is None:
            continue
        for rel_type, target_id in spec['relationships']:
            if target_id in batch_uuid_set:
                inverse = schema.inverses.get(rel_type)
                if inverse:
                    incoming_rels[target_id].append((inverse, spec['_uuid']))

    if args.dry_run:
        print(f"DRY RUN — would create {len([s for s in specs if s is not None])} entities:\n")
        for spec in specs:
            if spec is None:
                continue
            rel_path = shard_path(spec['type'], spec['_uuid'])
            print(f"  [{spec['type']}] {spec['name']}")
            print(f"    ID:   {spec['_uuid']}")
            print(f"    Path: {rel_path}/meta.yaml")
            if spec.get('ref'):
                print(f"    Ref:  {spec['ref']}")
            if spec['relationships']:
                for rt, tid in spec['relationships']:
                    print(f"    Rel:  {rt} → {tid[:8]}")
            if incoming_rels.get(spec['_uuid']):
                for rt, tid in incoming_rels[spec['_uuid']]:
                    print(f"    Inv:  {rt} ← {tid[:8]} (within-batch)")
            print()
        return

    # Pass 4: write files and DB
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    created_entities = []

    try:
        for spec in specs:
            if spec is None:
                continue

            entity_id = spec['_uuid']
            entity_type = spec['type']
            dims = spec['dims']
            relationships = spec['relationships']
            recurrence_config = spec['recurrence_config']
            recurrence_json = json.dumps(recurrence_config) if recurrence_config else None

            # All relationships for meta.yaml: outgoing + within-batch incoming
            all_relationships = list(relationships) + incoming_rels.get(entity_id, [])

            meta_content = build_meta_yaml(
                entity_id, entity_type, spec['name'], spec['description'],
                dims, spec['due'], all_relationships,
                extra_attrs=spec['final_extra_attrs'],
                recurrence_config=recurrence_config,
                next_due=spec['next_due'],
            )

            rel_path = shard_path(entity_type, entity_id)
            abs_path = os.path.join(SUBSTRATE_PATH, rel_path)
            os.makedirs(abs_path, exist_ok=True)
            meta_path = os.path.join(abs_path, "meta.yaml")
            with safe_write(meta_path, create=True) as (_, write):
                write(meta_content)

            # DB insert
            # Extract is_blocked from extra_attrs for SQLite column
            is_blocked_val = next((v for k, v in final_extra_attrs if k == "is_blocked"), None)

            c.execute("""
                INSERT OR REPLACE INTO entities (
                    id, name, type, description, path, meta_status,
                    focus, life_stage, assessment, importance_tactical, resolution,
                    health, importance_strategic, phase,
                    next_due, completion_count, streak, recurrence_schedule,
                    due, created, last_edited, engagement_mode, is_blocked
                ) VALUES (?, ?, ?, ?, ?, 'live', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                entity_id, spec['name'], entity_type, spec['description'], rel_path,
                dims.get('focus'), dims.get('life_stage'), dims.get('assessment'),
                dims.get('importance_tactical'), dims.get('resolution'),
                dims.get('health'), dims.get('importance_strategic'), dims.get('phase'),
                spec['next_due'], 0, 0, recurrence_json,
                spec['due'], now, now, spec.get('engagement_mode'), is_blocked_val,
            ))

            # DB relationships (outgoing only — within-batch inverses are handled symmetrically)
            for rel_type, target_id in relationships:
                c.execute(
                    "INSERT OR IGNORE INTO relationships (source_id, relationship, target_id) VALUES (?, ?, ?)",
                    (entity_id, rel_type, target_id),
                )
                inverse = schema.inverses.get(rel_type)
                if inverse:
                    c.execute(
                        "INSERT OR IGNORE INTO relationships (source_id, relationship, target_id) VALUES (?, ?, ?)",
                        (target_id, inverse, entity_id),
                    )
                    # Update modified on external targets (within-batch targets updated when their own row is inserted)
                    if target_id not in batch_uuid_set:
                        c.execute("UPDATE entities SET last_edited = ? WHERE id = ?", (now, target_id))

            created_entities.append(spec)

        conn.commit()
    except Exception as e:
        conn.rollback()
        print(f"Error during batch write: {e}")
        conn.close()
        sys.exit(1)

    # Pass 5: update external target meta.yaml files (outside batch)
    # Call update_target_meta for every (entity, external target) pair.
    # update_target_meta has its own idempotency check — do not deduplicate here,
    # or multiple entities sharing the same external target will only have the
    # first inverse written.
    for spec in created_entities:
        for rel_type, target_id in spec['relationships']:
            if target_id not in batch_uuid_set:
                inverse = schema.inverses.get(rel_type)
                if inverse:
                    update_target_meta(target_id, inverse, spec['_uuid'], conn)

    conn.close()

    # Pass 6: embeddings (silently skip if not available)
    try:
        from embeddings import is_search_available, generate_and_store, load_vec_extension, init_vec_table
        if is_search_available():
            emb_conn = sqlite3.connect(DB_PATH)
            from embeddings import load_vec_extension as lve
            if lve(emb_conn):
                init_vec_table(emb_conn)
                for spec in created_entities:
                    generate_and_store(emb_conn, spec['_uuid'], spec['type'], spec['name'], spec['description'])
                emb_conn.commit()
            emb_conn.close()
    except Exception:
        pass  # Embeddings are optional

    # Pass 7: changelog
    for spec in created_entities:
        changes = [{"attribute": dim, "value": val} for dim, val in spec['dims'].items()]
        if spec['description'] and spec['description'] != '[awaiting context]':
            changes.append({"attribute": "description", "value": spec['description']})
        if spec['due']:
            changes.append({"attribute": "due", "value": spec['due']})
        for k, v in spec['final_extra_attrs']:
            changes.append({"attribute": k, "value": v})

        rels = []
        for rel_type, target_id in spec['relationships']:
            rels.append({"action": "add", "type": rel_type, "target_id": target_id})

        log_change(
            "create", spec['_uuid'], spec['type'], spec['name'],
            changes=changes or None,
            relationships=rels or None,
        )

    # Summary
    print(f"Created {len(created_entities)} entities:")
    for spec in created_entities:
        ref_label = f" (ref: {spec['ref']})" if spec.get('ref') else ""
        print(f"  [{spec['type']}] {spec['name']}{ref_label}")
        print(f"    ID: {spec['_uuid']}")


if __name__ == "__main__":
    main()
