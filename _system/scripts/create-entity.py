#!/usr/bin/env python3
"""
Create a new Substrate entity.

Handles: UUID generation, sharded directory creation, meta.yaml writing,
SQLite index update, and bidirectional relationship linking.

Usage:
  python3 create-entity.py --type task --name "Do the thing" --belongs_to UUID
  python3 create-entity.py --type document --name "Report" --phase "in_development"
  python3 create-entity.py --type note --name "Quick thought" --description "Something I noticed"

Dimensional status flags (set any that apply; defaults auto-populated from schema):
  --focus VALUE             Focus dimension (Idle, Active, Waiting, Blocked, Paused, Closed)
  --life-stage VALUE        Life Stage dimension (Backlog, Ready, Scheduled, In Progress, ...)
  --resolution VALUE        Resolution dimension (Unresolved, Completed, Cancelled, Deferred, Superseded)
  --assessment VALUE        Assessment dimension (Not Assessed, On Track, At Risk, ...)
  --importance-tactical V   Tactical importance (Critical, High, Medium, Low)
  --health VALUE            Health dimension (Growing, Stable, Declining, Problematic, Undefined)
  --importance-strategic V  Strategic importance (Core, Important, Peripheral)
  --phase VALUE             Phase dimension (Concept, In Development, Testing, Live, ...)

Other options:
  --type TYPE          Entity type (required)
  --name NAME          Entity name (required)
  --id UUID            Use specific UUID (default: auto-generate)
  --description DESC   Description (default: "[awaiting context]")
  --due DATE           Due date
  --attr KEY=VALUE     Extra type-specific attribute (repeatable)
  --RELATIONSHIP UUID  Any relationship, e.g. --belongs_to UUID --produced_by UUID
                       Inverse relationships are created automatically.
  --dry-run            Show what would be created without writing

Dimension defaults are auto-populated based on the type's grouping nature.
Disallowed dimensions for the type are silently ignored if specified.
"""

import os
import sys
import re
import uuid
import sqlite3
import argparse
from datetime import datetime
from pathlib import Path
from schema import load_schema
from precheck import validate_create
from cascades import detect_dependency_cycle, format_cycle_error
from triggers import TriggerEngine, TriggerEvent, EventType, validate_recurrence_config, calculate_initial_next_due
from changelog import log_change
import json
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


def lookup_entity(entity_id):
    """Look up entity name and type from SQLite."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT name, type FROM entities WHERE id = ? OR id LIKE ?", (entity_id, f"{entity_id}%"))
    row = c.fetchone()
    conn.close()
    return {"name": row[0], "type": row[1]} if row else None


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


def shard_path(entity_type, entity_id):
    """Generate sharded directory path: entities/{type}/{first2}/{next2}/{uuid}"""
    prefix = entity_id[:2]
    next2 = entity_id[2:4]
    return os.path.join("entities", entity_type, prefix, next2, entity_id)


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


def extract_recurrence_attrs(extra_attrs):
    """Extract recurrence.* attrs from extra_attrs and build a recurrence config dict.

    Returns (recurrence_config, remaining_attrs) where recurrence_config is a dict
    (or None if no recurrence attrs) and remaining_attrs is the list without recurrence entries.

    Handles dot-notation: --attr recurrence.schedule_type=interval becomes
    recurrence_config["schedule_type"] = "interval".
    """
    recurrence_config = {}
    remaining = []
    for k, v in extra_attrs:
        if k.startswith("recurrence."):
            attr_name = k[len("recurrence."):]
            # Type coercion for known attributes
            if attr_name == "lead_time_days":
                v = int(v)
            elif attr_name == "day_of_month" and v != "last":
                v = int(v)
            elif attr_name == "days":
                # Comma-separated day list: "Mon,Wed,Fri"
                v = [d.strip() for d in v.split(",")]
            elif attr_name.startswith("interval."):
                # Compound interval: recurrence.interval.value=7, recurrence.interval.unit=days
                sub_key = attr_name[len("interval."):]
                if "interval" not in recurrence_config:
                    recurrence_config["interval"] = {}
                if sub_key == "value":
                    recurrence_config["interval"]["value"] = int(v)
                else:
                    recurrence_config["interval"][sub_key] = v
                continue
            recurrence_config[attr_name] = v
        else:
            remaining.append((k, v))
    return (recurrence_config if recurrence_config else None), remaining


DAY_MAP_SINGLE = {"M": "Mon", "T": "Tue", "W": "Wed", "F": "Fri", "S": "Sat"}
DAY_MAP_TWO = {"Tu": "Tue", "Th": "Thu", "Sa": "Sat", "Su": "Sun"}


def parse_every_shorthand(value):
    """Parse --every shorthand into a recurrence config dict.

    Formats:
      Nd          -> interval days (e.g., "2d", "7d", "14d")
      Nh          -> interval hours (e.g., "4h", "8h")
      Nm          -> interval minutes (e.g., "30m", "15m")
      day abbrevs -> day_of_week (e.g., "MWF", "TuThSa", "Mon")
      Nth         -> calendar_anchored (e.g., "1st", "15th")
      "last"      -> calendar_anchored with day_of_month="last"

    Returns:
        dict with schedule_type and schedule-specific attributes
    """
    # Interval: Nd (days), Nh (hours), Nm (minutes)
    m = re.match(r'^(\d+)([dhm])$', value)
    if m:
        num = int(m.group(1))
        unit_char = m.group(2)
        unit_map = {"d": "days", "h": "hours", "m": "minutes"}
        unit = unit_map[unit_char]
        return {
            "schedule_type": "interval",
            "interval": {"value": num, "unit": unit},
            "precision": "timestamp" if unit in ("hours", "minutes") else "date",
        }

    # Calendar anchored: Nth (1st, 2nd, 3rd, 15th, etc.)
    m = re.match(r'^(\d+)(?:st|nd|rd|th)$', value)
    if m:
        return {
            "schedule_type": "calendar_anchored",
            "day_of_month": int(m.group(1)),
        }

    # Calendar anchored: "last"
    if value == "last":
        return {
            "schedule_type": "calendar_anchored",
            "day_of_month": "last",
        }

    # Full day name (Mon, Tue, etc.)
    valid_days = {"Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"}
    if value in valid_days:
        return {
            "schedule_type": "day_of_week",
            "days": [value],
        }

    # Day abbreviation combinations (MWF, TuThSa, MTWThF, etc.)
    days = []
    i = 0
    while i < len(value):
        # Try two-char match first
        if i + 1 < len(value) and value[i:i+2] in DAY_MAP_TWO:
            days.append(DAY_MAP_TWO[value[i:i+2]])
            i += 2
        elif value[i] in DAY_MAP_SINGLE:
            days.append(DAY_MAP_SINGLE[value[i]])
            i += 1
        else:
            raise ValueError(f"Cannot parse --every value: '{value}'")

    if days:
        return {
            "schedule_type": "day_of_week",
            "days": days,
        }

    raise ValueError(f"Cannot parse --every value: '{value}'")


def resolve_dimensions(entity_type, explicit_dims):
    """Resolve final dimensional values: explicit overrides + defaults.

    Returns dict of dimension -> value for all non-disallowed dimensions.
    Disallowed dimensions in explicit_dims are silently dropped.
    """
    config = schema.dimension_config(entity_type)
    defaults = schema.dimension_defaults(entity_type)

    result = dict(defaults)  # Start with defaults
    for dim, val in explicit_dims.items():
        if config.get(dim) == "disallowed":
            continue  # Silently ignore disallowed dimensions
        if val is not None:
            result[dim] = val

    return result


def build_meta_yaml(entity_id, entity_type, name, description, dims, due, relationships,
                    extra_attrs=None, recurrence_config=None, next_due=None,
                    completion_count=0, streak=0, list_attrs=None):
    """Build meta.yaml content by constructing a dict and emitting via the
    canonical dumper (lib.fileio.dump_entity_meta).

    Canonical key order:
      id, type, name, description, meta_status,
      FLAIR+HIP dimensions (focus, life_stage, assessment, importance_tactical,
        resolution, health, importance_strategic, phase) in that order,
      grouping-level dimensions (in insertion order),
      due (if set),
      list attributes (if non-empty),
      extra scalar attributes (skipping anything already written),
      recurrence block (nested; with runtime attrs only when schedule_type != none),
      created, last_edited,
      relationship groups (in insertion order).

    Previously this was a hand-rolled line-based emitter with its own
    yaml_quote(). The yaml_quote convention under-safed type-ambiguous
    scalars like "2026-05-01" (date) and "yes"/"null" (bool/None), leaving
    them unquoted so PyYAML reparsed them as the wrong Python type. Routing
    through dump_entity_meta applies the resolver-aware string representer
    (see lib/fileio.py) that quotes such scalars correctly. Output is
    semantically equivalent to the old form for common cases, plus fixes
    that under-safety for timestamps, dates, and boolean-like strings.
    """
    from lib.fileio import dump_entity_meta

    now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

    meta = {}
    meta["id"] = entity_id
    meta["type"] = entity_type
    meta["name"] = name
    meta["description"] = description
    meta["meta_status"] = "live"

    # FLAIR+HIP dimensions in canonical order. Grouping-level dimensions
    # follow in their natural insertion order.
    dim_order = ["focus", "life_stage", "assessment", "importance_tactical", "resolution",
                 "health", "importance_strategic", "phase"]
    for dim in dim_order:
        if dim in dims:
            meta[dim] = dims[dim]
    for dim, val in dims.items():
        if dim not in dim_order and dim != "meta_status":
            meta[dim] = val

    if due:
        meta["due"] = due

    # List attributes (only write non-empty lists; matches original behavior).
    if list_attrs:
        for attr_name, values in list_attrs.items():
            if values:
                meta[attr_name] = list(values)

    # Scalar extra attributes. Skip any key already written (as dimension,
    # list attribute, etc.) to avoid duplicate YAML keys.
    for k, v in (extra_attrs or []):
        if k in dims or k in meta:
            continue
        meta[k] = v

    # Recurrence block. Write config attrs first, then runtime tracking attrs
    # (only when the entity is actually recurring).
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

    # Relationships — group multiple targets of the same type under one key.
    rel_groups = {}
    for rel_type, target_id in relationships:
        rel_groups.setdefault(rel_type, []).append(target_id)
    for rel_type, targets in rel_groups.items():
        meta[rel_type] = list(targets)

    return dump_entity_meta(meta)


def update_sqlite(entity_id, entity_type, name, description, dims, due, relationships, path,
                   recurrence_config=None, next_due=None, completion_count=0, streak=0,
                   list_attrs=None, extra_attrs=None):
    """Insert entity into SQLite and handle bidirectional relationships.

    Column population model:
      1. Core cols — hardcoded identity, timestamps, and recurrence runtime state.
      2. Dimension cols — every dim resolved by resolve_dimensions().
      3. List attr cols — every list attr where storage tier has a column.
      4. Schema scalar attr cols — every attribute in attributes.yaml whose
         storage tier grants a column (indexed or column), populated from
         `extra_attrs` or derived (e.g., action_parameters JSON-serialized).

    Design note: this function previously had 9 named kwargs (theme, handle,
    is_blocked, engagement_mode, event_type, action_type, executor, condition,
    action_parameters), one per attribute that had a SQLite column. Every new
    columned attribute required changing this signature AND the caller. Under
    the storage-tier model, any attribute in attributes.yaml with storage:
    indexed or column automatically gets a SQLite column — so keeping the
    signature synchronized manually was a ticking drift bug. The `extra_attrs`
    dict + `schema.columned_scalar_attrs()` loop makes new attributes
    zero-effort on the create path.

    Args:
        extra_attrs: list of (key, value) pairs OR dict of key->value for any
                     attribute declared in attributes.yaml. Values are written
                     verbatim (with per-attr special cases like action_parameters).
                     Missing attrs simply don't appear in the INSERT.
    """
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    recurrence_json = json.dumps(recurrence_config) if recurrence_config else None

    # Normalize extra_attrs to a dict. Later values for the same key win —
    # shouldn't happen in practice but behaves safely.
    if isinstance(extra_attrs, dict):
        extra = dict(extra_attrs)
    elif extra_attrs:
        extra = {k: v for k, v in extra_attrs}
    else:
        extra = {}

    # Core cols: identity, path, timestamps, recurrence runtime. Always present,
    # always derived (not user-provided). Dimensions flow through `dims`, including meta_status.
    core_cols = [
        "id", "name", "type", "description", "path",
        "next_due", "completion_count", "streak", "recurrence_schedule",
        "due", "created", "last_edited",
    ]
    core_vals = [
        entity_id, name, entity_type, description, path,
        next_due, completion_count, streak, recurrence_json,
        due, now, now,
    ]

    all_cols = list(core_cols)
    all_vals = list(core_vals)
    written = set(all_cols)

    # Dimensions — includes meta_status (always 'live' for new entities).
    for dim_name, dim_val in dims.items():
        if dim_name in written:
            continue
        all_cols.append(dim_name)
        all_vals.append(dim_val)
        written.add(dim_name)

    # List attrs with SQLite columns (indexed + column tiers). file_only skipped.
    if list_attrs:
        for attr_name, values in list_attrs.items():
            if attr_name in written:
                continue
            config = schema.list_attr_config(attr_name)
            if not config or not config.get("has_column"):
                continue
            csv_val = ",".join(values) if values else None
            all_cols.append(attr_name)
            all_vals.append(csv_val)
            written.add(attr_name)

    # Scalar attrs declared in attributes.yaml with a SQLite column (indexed
    # or column tier). Values come from extra_attrs. Special cases:
    #   - action_parameters: YAML dict → JSON string in SQLite.
    for attr_name in schema.columned_scalar_attrs():
        if attr_name in written:
            continue
        value = extra.get(attr_name)
        if attr_name == "action_parameters" and isinstance(value, dict):
            value = json.dumps(value)
        all_cols.append(attr_name)
        all_vals.append(value)
        written.add(attr_name)

    placeholders = ", ".join(["?"] * len(all_cols))
    col_str = ", ".join(all_cols)
    c.execute(f"INSERT OR REPLACE INTO entities ({col_str}) VALUES ({placeholders})", all_vals)

    # Insert relationships (both directions)
    for rel_type, target_id in relationships:
        c.execute("INSERT OR IGNORE INTO relationships (source_id, relationship, target_id) VALUES (?, ?, ?)",
                  (entity_id, rel_type, target_id))

        inverse = schema.inverses.get(rel_type)
        if inverse:
            c.execute("INSERT OR IGNORE INTO relationships (source_id, relationship, target_id) VALUES (?, ?, ?)",
                      (target_id, inverse, entity_id))
            c.execute("UPDATE entities SET last_edited = ? WHERE id = ?", (now, target_id))

    conn.commit()
    conn.close()


def update_target_meta(target_id, inverse_rel, entity_id):
    """Add inverse relationship to target entity's meta.yaml."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT path FROM entities WHERE id = ?", (target_id,))
    row = c.fetchone()
    conn.close()

    if not row:
        print(f"  Warning: Target {target_id} not found in index — inverse not written to meta.yaml")
        return

    meta_path = os.path.join(SUBSTRATE_PATH, row[0], "meta.yaml")
    if not os.path.exists(meta_path):
        print(f"  Warning: Target meta.yaml not found: {meta_path}")
        return

    with open(meta_path, 'r', encoding="utf-8") as f:
        content = f.read()

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

    # Quote the timestamp — a bare `2026-04-22T12:00:00` parses as a datetime
    # object on the next read, which downstream stringifies differently than
    # the original (space separator instead of T). Using quote_yaml_scalar
    # keeps the emitted form consistent with dump_entity_meta.
    from lib.fileio import quote_yaml_scalar
    now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    content = re.sub(r'last_edited:.*', f'last_edited: {quote_yaml_scalar(now)}', content)

    lines = content.rstrip('\n').split('\n')
    new_lines = []
    inserted = False

    for i, line in enumerate(lines):
        new_lines.append(line)
        if line.strip() == f"{inverse_rel}:" and not inserted:
            # Detect indentation from existing list items and match it
            prefix = ""
            next_idx = i + 1
            if next_idx < len(lines):
                next_line = lines[next_idx]
                if next_line.lstrip().startswith("- "):
                    prefix = next_line[: len(next_line) - len(next_line.lstrip())]
            new_lines.append(f"{prefix}- {entity_id}")
            inserted = True

    if not inserted:
        new_lines.append(f"{inverse_rel}:")
        new_lines.append(f"  - {entity_id}")

    with safe_write(meta_path) as (_, write):
        write('\n'.join(new_lines) + '\n')
        # Post-write verification: confirm the inverse was actually persisted
        with open(meta_path, 'r', encoding="utf-8") as f:
            written = f.read()
        if f"- {entity_id}" not in written:
            print(f"  ERROR: Inverse write verification failed — {inverse_rel} → {entity_id} not found in {meta_path} after write")


def main():
    parser = argparse.ArgumentParser(description="Create a Substrate entity", add_help=False)
    parser.add_argument("--type", required=True, dest="entity_type")
    parser.add_argument("--name", required=True)
    parser.add_argument("--id", dest="entity_id", default=None)
    parser.add_argument("--description", default="[awaiting context]")
    # Dimensional flags — standard HIP/FLAIR set
    parser.add_argument("--focus", default=None)
    parser.add_argument("--life-stage", default=None, dest="life_stage")
    parser.add_argument("--resolution", default=None)
    parser.add_argument("--assessment", default=None)
    parser.add_argument("--importance-tactical", default=None, dest="importance_tactical")
    parser.add_argument("--health", default=None)
    parser.add_argument("--importance-strategic", default=None, dest="importance_strategic")
    parser.add_argument("--phase", default=None)
    # Grouping-level dimensional flags — dynamically registered from schema
    # Any dimension not in the standard set above gets a named flag automatically.
    # dest uses the raw dim name (underscored) so getattr(args, dim) works uniformly.
    _standard_dims = {"focus", "life_stage", "assessment", "importance_tactical",
                      "resolution", "health", "importance_strategic", "phase", "meta_status"}
    for _dim in schema.dimension_names:
        if _dim not in _standard_dims:
            parser.add_argument(f"--{_dim.replace('_', '-')}", default=None, dest=_dim)
    # Other
    parser.add_argument("--due", default=None)
    parser.add_argument("--next-due", default=None, dest="next_due", help="Override initial next_due date for recurring entities")
    parser.add_argument("--every", default=None, help="Recurrence shorthand: 2d (interval), MWF (day_of_week), 1st (calendar_anchored), last. Single-char days: M=Mon T=Tue W=Wed F=Fri S=Sat. Use two-char for Thu/Sat/Sun: Th Sa Su.")
    parser.add_argument("--date-basis", default=None, dest="date_basis", help="Override next_date_basis: scheduled or completion")
    parser.add_argument("--engagement-mode", default=None, dest="engagement_mode",
                        help="Engagement mode: none, wander, explore, experiment, execute (default: none for work types)")
    parser.add_argument("--domain", action="append", default=[], dest="domain",
                        help="Subject matter domain (repeatable, max 2): code, infrastructure, content, data, design, strategy, communication")
    parser.add_argument("--attr", action="append", default=[], help="Extra type-specific attribute as key=value (repeatable)")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--help", "-h", action="store_true")

    args, remainder = parser.parse_known_args()

    if args.help:
        print(__doc__)
        sys.exit(0)

    extra_attrs = parse_attr_pairs(args.attr)
    all_attrs = list(extra_attrs)  # Preserve original attrs for precheck validation

    # Extract recurrence config from dot-notation attrs (e.g., --attr recurrence.schedule_type=interval)
    recurrence_config, extra_attrs = extract_recurrence_attrs(extra_attrs)

    # --every shorthand overrides --attr recurrence.* if both provided
    if args.every:
        try:
            recurrence_config = parse_every_shorthand(args.every)
        except ValueError as e:
            print(f"Error: {e}")
            sys.exit(1)

    # --date-basis overrides default next_date_basis
    if args.date_basis and recurrence_config:
        if args.date_basis not in ("scheduled", "completion"):
            print(f"Error: --date-basis must be 'scheduled' or 'completion', got '{args.date_basis}'")
            sys.exit(1)
        recurrence_config["next_date_basis"] = args.date_basis

    # Auto-default recurrence to none for types where it's required/preferred but not specified.
    # Mirrors the engagement_mode auto-default pattern — caller shouldn't need to know the schema.
    if not recurrence_config:
        rec_access = schema.access_level("recurrence", args.entity_type, "attribute")
        if rec_access in ("required", "preferred"):
            recurrence_config = {"schedule_type": "none"}
            if not any(k == "recurrence.schedule_type" for k, _ in all_attrs):
                all_attrs.append(("recurrence.schedule_type", "none"))

    # Validate recurrence config if present
    if recurrence_config:
        rec_errors = validate_recurrence_config(recurrence_config)
        if rec_errors:
            print("Recurrence validation failed:")
            for e in rec_errors:
                print(f"  \u2717 {e}")
            sys.exit(1)

    # Calculate initial next_due for recurring entities
    from datetime import date as _date
    initial_next_due = None
    if args.next_due:
        initial_next_due = args.next_due
    elif recurrence_config:
        computed = calculate_initial_next_due(recurrence_config, _date.today())
        if computed:
            initial_next_due = computed.isoformat()

    # Resolve engagement_mode: auto-default to "none" for types where it's required/preferred
    engagement_mode = args.engagement_mode
    em_access = schema.access_level("engagement_mode", args.entity_type, "attribute")
    if engagement_mode is None and em_access in ("required", "preferred"):
        engagement_mode = schema.attr_default("engagement_mode") or "none"
    if engagement_mode is not None and em_access == "forbidden":
        engagement_mode = None  # Silently drop for types that can't use it
    # Inject engagement_mode into extra_attrs for precheck validation.
    # If the user already passed --attr engagement_mode=X, it's in extra_attrs;
    # skip the append to prevent duplicate keys in meta.yaml.
    if engagement_mode is not None:
        if not any(k == "engagement_mode" for k, v in extra_attrs):
            extra_attrs.append(("engagement_mode", engagement_mode))
        if not any(k == "engagement_mode" for k, v in all_attrs):
            all_attrs.append(("engagement_mode", engagement_mode))

    # --- Generic list attribute resolution ---
    # Collects values from both first-class flags (like --domain) and --attr pairs.
    # Validates enum values and max_items from schema. Writes YAML lists and
    # comma-separated SQLite columns for indexed list attrs.
    list_attr_values = {}  # attr_name -> [values]

    # Merge first-class list flags (currently: --domain)
    if args.domain:
        list_attr_values["domain"] = list(args.domain)

    # Collect --attr entries for list attributes, merge with flag values
    for attr_name in schema.all_list_attrs():
        attr_entries = [v for k, v in extra_attrs if k == attr_name]
        if attr_entries:
            existing = list_attr_values.get(attr_name, [])
            for v in attr_entries:
                if v not in existing:
                    existing.append(v)
            list_attr_values[attr_name] = existing

    # Remove list attrs from extra_attrs (handled separately)
    list_attr_names = set(schema.all_list_attrs())
    extra_attrs = [(k, v) for k, v in extra_attrs if k not in list_attr_names]
    all_attrs = [(k, v) for k, v in all_attrs if k not in list_attr_names]

    # Validate and filter each list attribute
    for attr_name, values in list(list_attr_values.items()):
        config = schema.list_attr_config(attr_name)
        if not config:
            continue
        # Silently drop for types that can't use it
        access = schema.access_level(attr_name, args.entity_type, "attribute")
        if access == "forbidden":
            del list_attr_values[attr_name]
            continue
        # Validate enum values
        if config["enum_values"]:
            for v in values:
                if v not in config["enum_values"]:
                    print(f"ERROR: Invalid value '{v}' for attribute '{attr_name}'. "
                          f"Valid values: {', '.join(config['enum_values'])}")
                    sys.exit(1)
        # Enforce max_items
        if config["max_items"] and len(values) > config["max_items"]:
            print(f"ERROR: Maximum {config['max_items']} values allowed for '{attr_name}' "
                  f"(got {len(values)}).")
            sys.exit(1)

    # Parse relationship args from remainder
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

    # Build explicit dimensions from flags
    explicit_dims = {}
    for dim in schema.dimension_names:
        val = getattr(args, dim, None)
        if val is not None:
            explicit_dims[dim] = val


    # Route dimension keys from --attr into explicit_dims before resolve_dimensions.
    # First-class flags already in explicit_dims take precedence.
    # resolve_dimensions handles disallowed dims internally — no guard needed here.
    _dim_extra_create = [(k, v) for k, v in extra_attrs if k in schema.dimension_names]
    extra_attrs = [(k, v) for k, v in extra_attrs if k not in schema.dimension_names]
    for dim, val in _dim_extra_create:
        if dim not in explicit_dims:
            explicit_dims[dim] = val

    # Pre-check: validate operation against schema before doing anything
    # Use all_attrs (pre-extraction) so recurrence presence check sees recurrence.schedule_type
    validation = validate_create(
        schema, args.entity_type,
        name=args.name,
        description=args.description,
        dimensions=explicit_dims,
        relationships=relationships,
        extra_attrs=all_attrs,
        due=args.due,
        db_path=DB_PATH,
    )
    for w in validation.warnings:
        print(f"  Warning: {w}")
    if not validation.valid:
        print("Validation failed:")
        for e in validation.errors:
            print(f"  ✗ {e}")
        sys.exit(1)

    # Resolve dimensions (explicit + defaults, minus disallowed)
    dims = resolve_dimensions(args.entity_type, explicit_dims)

    # Generate or use provided UUID
    entity_id = args.entity_id or str(uuid.uuid4())

    # Cycle detection for depends_on relationships
    # At creation, cycles can only form if both --depends_on and --enables are used:
    # new entity depends_on A, and B depends_on new entity (via enables inverse).
    # Cycle exists if A already transitively depends_on B.
    depends_on_targets = [tid for rt, tid in relationships if rt == "depends_on"]
    enables_targets = [tid for rt, tid in relationships if schema.inverses.get(rt) == "depends_on"]
    if depends_on_targets and enables_targets:
        conn_cycle = sqlite3.connect(DB_PATH)
        for dep_target in depends_on_targets:
            for en_target in enables_targets:
                # Would dep_target → ... → en_target → new → dep_target form a cycle?
                cycle = detect_dependency_cycle(conn_cycle, dep_target, en_target)
                if cycle:
                    print(f"ERROR: Creating this entity would form a circular dependency.")
                    print(f"   {format_cycle_error(cycle, conn_cycle)}")
                    conn_cycle.close()
                    sys.exit(1)
        conn_cycle.close()

    # Check if any depends_on targets are unresolved → override focus to Blocked
    # Note: this is a pre-creation check (entity doesn't exist in SQLite yet),
    # not a cascade. The trigger engine handles post-mutation blocking via
    # builtin:dependency_block in update-entity.py. This sets the initial state.
    if depends_on_targets:
        conn_check = sqlite3.connect(DB_PATH)
        c_check = conn_check.cursor()
        placeholders = ",".join("?" * len(depends_on_targets))
        c_check.execute(
            f"SELECT COUNT(*) FROM entities WHERE id IN ({placeholders}) "
            f"AND resolution NOT IN ('completed', 'superseded')",
            depends_on_targets,
        )
        unresolved_count = c_check.fetchone()[0]
        conn_check.close()
        if unresolved_count > 0:
            extra_attrs.append(("is_blocked", "true"))

    # Review duplication guard: an agent cannot create a new review on a ticket
    # if it already has ANY non-retired review on that ticket for the same gate.
    # This prevents pass-stacking (same agent giving multiple passes) and premature
    # re-review (reviewing again before prior concerns are addressed/retired).
    if args.entity_type == "review":
        review_gate = next((v for k, v in extra_attrs if k == "gate"), None)
        review_belongs_to = [tid for rt, tid in relationships if rt == "belongs_to"]
        review_performed_by = [tid for rt, tid in relationships if rt == "performed_by"]

        if review_belongs_to and review_performed_by:
            conn_guard = sqlite3.connect(DB_PATH)
            c_guard = conn_guard.cursor()
            ticket_id_target = review_belongs_to[0]
            agent_id_target = review_performed_by[0]

            # Find existing review entities on this ticket by this agent
            c_guard.execute("""
                SELECT e.id, e.path FROM entities e
                JOIN relationships r_bt ON r_bt.source_id = e.id
                JOIN relationships r_pb ON r_pb.source_id = e.id
                WHERE e.type = 'review'
                AND e.meta_status = 'live'
                AND r_bt.relationship = 'belongs_to' AND r_bt.target_id = ?
                AND r_pb.relationship = 'performed_by' AND r_pb.target_id = ?
            """, (ticket_id_target, agent_id_target))
            existing_reviews = c_guard.fetchall()

            for existing_id, existing_path in existing_reviews:
                # Check gate and phase from meta.yaml
                existing_meta = os.path.join(SUBSTRATE_PATH, existing_path, "meta.yaml")
                existing_gate = None
                existing_phase = None
                if os.path.exists(existing_meta):
                    with open(existing_meta, "r", encoding="utf-8") as f:
                        for line in f:
                            stripped = line.strip()
                            if stripped.startswith("gate:"):
                                existing_gate = stripped.split(":", 1)[1].strip().strip('"').strip("'")
                            elif stripped.startswith("phase:"):
                                existing_phase = stripped.split(":", 1)[1].strip().strip('"').strip("'")

                # Block if same gate and not retired
                if existing_gate == review_gate and existing_phase != "retired":
                    existing_verdict = None
                    if os.path.exists(existing_meta):
                        with open(existing_meta, "r", encoding="utf-8") as f:
                            for line in f:
                                if line.strip().startswith("verdict:"):
                                    existing_verdict = line.strip().split(":", 1)[1].strip().strip('"').strip("'")
                                    break
                    conn_guard.close()
                    print(f"ERROR: This agent already has a non-retired {review_gate} review "
                          f"on this ticket (review {existing_id[:8]}, verdict: {existing_verdict or 'pending'}).")
                    print(f"   The existing review must be retired before creating a new one.")
                    sys.exit(1)

            conn_guard.close()

    # Build paths
    rel_path = shard_path(args.entity_type, entity_id)
    abs_path = os.path.join(SUBSTRATE_PATH, rel_path)
    meta_content = build_meta_yaml(entity_id, args.entity_type, args.name, args.description,
                                   dims, args.due, relationships, extra_attrs,
                                   recurrence_config=recurrence_config,
                                   next_due=initial_next_due,
                                   list_attrs=list_attr_values or None)

    if args.dry_run:
        print(f"DRY RUN — would create:")
        print(f"  Path: {rel_path}/meta.yaml")
        print(f"  ID: {entity_id}")
        print()
        print(meta_content)
        if relationships:
            print("Inverse relationships to write:")
            for rel_type, target_id in relationships:
                inverse = schema.inverses.get(rel_type, "???")
                print(f"  {target_id} → {inverse} → {entity_id}")
        return

    # Create directory and meta.yaml
    os.makedirs(abs_path, exist_ok=True)
    meta_path = os.path.join(abs_path, "meta.yaml")
    with safe_write(meta_path, create=True) as (_, write):
        write(meta_content)

    # Build the attr dict that flows into SQLite column population.
    # Starts with every --attr pair the user passed. Engagement mode (already
    # injected into extra_attrs above), handle, theme, trigger attrs, etc. all
    # flow through this uniform path. update_sqlite will only populate columns
    # for attrs that actually have SQLite columns per their storage tier.
    sqlite_attrs = {k: v for k, v in extra_attrs}

    # action_parameters is structured (YAML dict) in meta.yaml, not a flat
    # string that --attr can pass. Re-parse from meta_content and pass the dict;
    # update_sqlite JSON-serializes it for the column.
    if args.entity_type == "trigger":
        import yaml as _yaml_ap
        try:
            _parsed = _yaml_ap.safe_load(meta_content)
            _ap = _parsed.get("action_parameters")
            if isinstance(_ap, dict):
                sqlite_attrs["action_parameters"] = _ap
        except Exception:
            pass

    # Update SQLite
    update_sqlite(entity_id, args.entity_type, args.name, args.description,
                  dims, args.due, relationships, rel_path,
                  recurrence_config=recurrence_config, next_due=initial_next_due,
                  list_attrs=list_attr_values or None,
                  extra_attrs=sqlite_attrs)

    # Generate and store embedding (silently skips if search not set up)
    try:
        from embeddings import is_search_available, generate_and_store, load_vec_extension, init_vec_table
        if is_search_available():
            emb_conn = sqlite3.connect(DB_PATH)
            if load_vec_extension(emb_conn):
                init_vec_table(emb_conn)
                generate_and_store(emb_conn, entity_id, args.entity_type, args.name, args.description)
                emb_conn.commit()
            emb_conn.close()
    except Exception:
        pass  # Embeddings are optional — entity creation succeeds regardless

    # Update target entities with inverse relationships
    for rel_type, target_id in relationships:
        inverse = schema.inverses.get(rel_type)
        if inverse:
            update_target_meta(target_id, inverse, entity_id)

    # --- Fire ENTITY_CREATED event ---
    # Wired so trigger entities listening for entity_created can respond.
    # No built-in triggers consume this yet — this is infrastructure.
    conn_trigger = sqlite3.connect(DB_PATH)
    engine = TriggerEngine(conn_trigger, SUBSTRATE_PATH)
    create_event = TriggerEvent(
        event_type=EventType.ENTITY_CREATED,
        entity_id=entity_id,
        entity_type=args.entity_type,
        entity_name=args.name,
        context={"relationships": [(rt, tid) for rt, tid in relationships]},
    )
    trigger_results = engine.evaluate_script_time(create_event)
    conn_trigger.commit()
    conn_trigger.close()
    for result in trigger_results:
        for action in result.actions_taken:
            print(f"   >> Trigger: {action}")

    # --- Change logging ---
    # meta_status is excluded: always "live" at creation, always default, always hardcoded.
    # Including it would add noise to every creation log with zero information content.
    create_changes = [{"attribute": dim, "value": val} for dim, val in dims.items()
                      if dim != "meta_status"]
    if args.description and args.description != "[awaiting context]":
        create_changes.append({"attribute": "description", "value": args.description})
    if args.due:
        create_changes.append({"attribute": "due", "value": args.due})
    for k, v in extra_attrs:
        create_changes.append({"attribute": k, "value": v})
    create_rels = []
    for rel_type, target_id in relationships:
        target_info = lookup_entity(target_id)
        create_rels.append({"action": "add", "type": rel_type, "target_id": target_id,
                            "target_name": target_info["name"] if target_info else None})
    log_change(
        "create", entity_id, args.entity_type, args.name,
        changes=create_changes or None,
        relationships=create_rels or None,
    )

    # Output
    print(f"Created {args.entity_type}: {args.name}")
    print(f"   ID: {entity_id}")
    print(f"   Path: {rel_path}/meta.yaml")
    dim_str = ", ".join(f"{k}={v}" for k, v in dims.items())
    if dim_str:
        print(f"   Dimensions: {dim_str}")
    if relationships:
        print(f"   Relationships:")
        for rel_type, target_id in relationships:
            inverse = schema.inverses.get(rel_type, "???")
            target_info = lookup_entity(target_id)
            target_label = f"{target_info['name']} ({target_info['type']})" if target_info else target_id
            print(f"     {rel_type} → {target_label} [{target_id[:8]}] (inverse: {inverse})")
            if rel_type == "relates_to" and target_info:
                print(f"     ^ Hint: If this {args.entity_type} would be meaningless without {target_info['name']}, use --belongs_to instead (independence test).")
    if any(k == "is_blocked" and v == "true" for k, v in extra_attrs) and depends_on_targets:
        print(f"   >> is_blocked set to true — has unresolved dependencies")
    if engagement_mode and engagement_mode != "none":
        print(f"   Engagement mode: {engagement_mode}")
    if recurrence_config:
        stype = recurrence_config.get("schedule_type", "none")
        print(f"   Recurrence: {stype}")
        if initial_next_due:
            print(f"   Next due: {initial_next_due}")


if __name__ == "__main__":
    main()
