#!/usr/bin/env python3
"""
Update trigger entity recurrence state after confirmed execution.

Called by agent-run.sh after acquiring a concurrency slot. Updates:
  - last_fired → current datetime
  - fire_count → increment
  - next_due → calculated from last_fired + interval

Also supports --bootstrap mode to initialize next_due on all schedule_fired
triggers (set next_due = now + interval, as if they just ran).

Usage:
  python3 update-trigger-state.py --trigger-id UUID
  python3 update-trigger-state.py --bootstrap
"""

import os
import sys
import json
import sqlite3
import argparse
from datetime import datetime

# Add scripts dir to path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from triggers import calculate_next_due, _get_precision
from lib.fileio import safe_write

# ─── Path resolution ──────────────────────────────────────────────────────

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SUBSTRATE_PATH = os.environ.get(
    "SUBSTRATE_PATH",
    os.path.dirname(os.path.dirname(SCRIPT_DIR)),
)
DB_PATH = os.path.join(SUBSTRATE_PATH, "_system", "index", "substrate.db")


def update_trigger_state(conn, trigger_id, substrate_path):
    """Update a single trigger entity's recurrence state after firing.

    Args:
        conn: sqlite3 connection
        trigger_id: UUID of the trigger entity
        substrate_path: workspace root path

    Returns:
        True if updated, False if trigger not found or has no recurrence config
    """
    now = datetime.now()
    now_str = now.strftime("%Y-%m-%dT%H:%M:%S")

    c = conn.cursor()
    c.execute(
        "SELECT recurrence_schedule, fire_count, path FROM entities WHERE id = ?",
        (trigger_id,),
    )
    row = c.fetchone()
    if not row:
        print(f"Trigger entity {trigger_id} not found.", file=sys.stderr)
        return False

    recurrence_json, old_fire_count, entity_path = row
    if not recurrence_json:
        print(f"Trigger entity {trigger_id} has no recurrence config.", file=sys.stderr)
        return False

    config = json.loads(recurrence_json)
    old_fire_count = old_fire_count or 0
    new_fire_count = old_fire_count + 1

    # Calculate next_due from now (completion basis — interval from firing time)
    precision = _get_precision(config)
    from_date = now if precision == "timestamp" else now.date()
    new_next_due = calculate_next_due(config, from_date)
    new_next_due_str = new_next_due.isoformat()

    # Update SQLite
    conn.execute(
        """UPDATE entities SET
            last_fired = ?,
            fire_count = ?,
            next_due = ?,
            last_edited = ?
        WHERE id = ?""",
        (now_str, new_fire_count, new_next_due_str, now_str, trigger_id),
    )
    conn.commit()

    # Update meta.yaml
    _update_trigger_meta_yaml(substrate_path, entity_path, now_str, new_fire_count, new_next_due_str)

    return True


def bootstrap_all_triggers(conn, substrate_path):
    """Initialize next_due on all schedule_fired triggers.

    Sets next_due = now + interval for each operational trigger entity,
    as if they just ran. This prepares them for the unified evaluator.

    Args:
        conn: sqlite3 connection
        substrate_path: workspace root path

    Returns:
        list of (trigger_id, trigger_name, new_next_due) tuples
    """
    now = datetime.now()
    now_str = now.strftime("%Y-%m-%dT%H:%M:%S")

    c = conn.cursor()
    c.execute("""
        SELECT id, name, recurrence_schedule, path
        FROM entities
        WHERE type = 'trigger'
          AND event_type = 'schedule_fired'
          AND meta_status = 'live'
          AND COALESCE(resolution, 'unresolved') = 'unresolved'
          AND recurrence_schedule IS NOT NULL
    """)

    results = []
    for row in c.fetchall():
        trigger_id, trigger_name, recurrence_json, entity_path = row

        try:
            config = json.loads(recurrence_json)
        except (json.JSONDecodeError, TypeError):
            continue

        if config.get("schedule_type") in (None, "none"):
            continue

        precision = _get_precision(config)
        from_date = now if precision == "timestamp" else now.date()
        new_next_due = calculate_next_due(config, from_date)
        new_next_due_str = new_next_due.isoformat()

        conn.execute(
            """UPDATE entities SET
                next_due = ?,
                last_edited = ?
            WHERE id = ?""",
            (new_next_due_str, now_str, trigger_id),
        )

        # Update meta.yaml
        _update_trigger_meta_yaml(substrate_path, entity_path, now_str, None, new_next_due_str)

        results.append((trigger_id, trigger_name, new_next_due_str))

    conn.commit()
    return results


def _update_trigger_meta_yaml(substrate_path, entity_path, now_str, fire_count, next_due_str):
    """Update trigger meta.yaml with recurrence state.

    Updates last_fired, fire_count, next_due as indented sub-attributes
    under the recurrence: block, plus last_edited at top level.
    """
    if not entity_path:
        return

    meta_path = os.path.join(substrate_path, entity_path, "meta.yaml")
    if not os.path.exists(meta_path):
        return

    with safe_write(meta_path) as (content, write):
        lines = content.rstrip("\n").split("\n")
        new_lines = []
        updated_attrs = set()

        # Attributes to update
        recurrence_updates = {"next_due": next_due_str}
        if fire_count is not None:
            recurrence_updates["last_fired"] = now_str
            recurrence_updates["fire_count"] = str(fire_count)

        # Route timestamps and recurrence values through quote_yaml_scalar so
        # emission matches dump_entity_meta canonical form. Bare timestamps
        # here would silently un-quote values the creation/update paths emitted
        # quoted — see ca885d21/2b44f20e commit chain.
        from lib.fileio import quote_yaml_scalar
        quoted_now = quote_yaml_scalar(now_str)
        quoted_recurrence = {
            k: quote_yaml_scalar(v) if isinstance(v, str) else v
            for k, v in recurrence_updates.items()
        }

        for line in lines:
            matched = False
            # Top-level: last_edited
            if line.startswith("last_edited:"):
                new_lines.append(f"last_edited: {quoted_now}")
                updated_attrs.add("last_edited")
                matched = True
            else:
                # Recurrence runtime attributes (indented, 2-space)
                stripped = line.lstrip()
                if line.startswith("  ") and not line.startswith("   "):
                    for attr_name, quoted_value in quoted_recurrence.items():
                        if stripped.startswith(f"{attr_name}:") and not stripped.startswith(f"{attr_name}s:"):
                            new_lines.append(f"  {attr_name}: {quoted_value}")
                            updated_attrs.add(attr_name)
                            matched = True
                            break
            if not matched:
                new_lines.append(line)

        # Insert missing recurrence attributes inside recurrence block
        missing = {k: v for k, v in quoted_recurrence.items() if k not in updated_attrs}
        if missing:
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
                insert_lines = [f"  {k}: {v}" for k, v in missing.items()]
                new_lines = new_lines[:last_indented + 1] + insert_lines + new_lines[last_indented + 1:]

        if "last_edited" not in updated_attrs:
            new_lines.append(f"last_edited: {quoted_now}")

        write("\n".join(new_lines) + "\n")


def main():
    parser = argparse.ArgumentParser(description="Update trigger entity recurrence state")
    parser.add_argument("--trigger-id", help="UUID of the trigger entity to update")
    parser.add_argument("--bootstrap", action="store_true",
                        help="Initialize next_due on all schedule_fired triggers")
    args = parser.parse_args()

    if not args.trigger_id and not args.bootstrap:
        parser.error("Either --trigger-id or --bootstrap is required")

    if not os.path.exists(DB_PATH):
        print(f"Database not found: {DB_PATH}", file=sys.stderr)
        sys.exit(1)

    conn = sqlite3.connect(DB_PATH)

    if args.bootstrap:
        results = bootstrap_all_triggers(conn, SUBSTRATE_PATH)
        if results:
            print(f"Bootstrapped {len(results)} trigger(s):")
            for tid, tname, next_due in results:
                print(f"  {tname} [{tid[:8]}] -> next_due: {next_due}")
        else:
            print("No operational triggers found to bootstrap.")
    else:
        success = update_trigger_state(conn, args.trigger_id, SUBSTRATE_PATH)
        if not success:
            sys.exit(1)

    conn.close()


if __name__ == "__main__":
    main()
