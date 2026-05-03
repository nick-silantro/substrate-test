#!/usr/bin/env python3
"""
Substrate unified recurrence evaluator.

Single entry point for all time-based evaluation. Processes all due
recurring entities — chores get promoted (Backlog → Ready), schedule-fired
triggers spawn agents. One clock, one query path, polymorphic actions.

Usage:
  python3 evaluate-triggers.py                  # Process all due entities
  python3 evaluate-triggers.py --type chore     # Only chores
  python3 evaluate-triggers.py --type trigger   # Only schedule-fired triggers
  python3 evaluate-triggers.py --dry-run        # Show what would fire
  python3 evaluate-triggers.py --overdue        # List overdue without acting

Runs every 5 minutes via com.substrate.evaluate-triggers.plist.

Snooze handling:
  - No snooze set: included
  - Expired snooze (snoozed_until <= today): included
  - Future-scheduled snooze (snoozed_from > today): included (not yet active)
  - Active snooze (snoozed_until > today, not future-from): excluded
"""

import os
import sys
import sqlite3
import argparse
from datetime import datetime

from triggers import TriggerEngine
from changelog import log_change

# ─── Path resolution ──────────────────────────────────────────────────────

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SUBSTRATE_PATH = os.environ.get(
    "SUBSTRATE_PATH",
    os.path.dirname(os.path.dirname(SCRIPT_DIR)),
)
DB_PATH = os.path.join(SUBSTRATE_PATH, "_system", "index", "substrate.db")


def main():
    parser = argparse.ArgumentParser(
        description="Evaluate recurrence and promote/spawn due entities"
    )
    parser.add_argument(
        "--type", dest="entity_type", default=None,
        help="Only process entities of this type (e.g., chore, trigger)"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show what would fire without making changes"
    )
    parser.add_argument(
        "--overdue", action="store_true",
        help="List overdue entities with days-overdue count"
    )
    args = parser.parse_args()

    if not os.path.exists(DB_PATH):
        print(f"Database not found: {DB_PATH}")
        sys.exit(1)

    conn = sqlite3.connect(DB_PATH)
    engine = TriggerEngine(conn, SUBSTRATE_PATH)
    now = datetime.now()

    if args.overdue:
        _show_overdue(engine, now)
    elif args.dry_run:
        _dry_run(engine, now, args.entity_type)
    else:
        _evaluate(engine, now, args.entity_type)

    conn.close()


def _show_overdue(engine, now):
    """List overdue entities with days-overdue count."""
    overdue = engine.get_overdue_entities(now)

    if not overdue:
        print("No overdue entities.")
        return

    print(f"Overdue entities ({len(overdue)}):\n")
    # Sort by days overdue (most overdue first)
    overdue.sort(key=lambda e: e["days_overdue"], reverse=True)
    for entry in overdue:
        days = entry["days_overdue"]
        day_str = "day" if days == 1 else "days"
        stage = entry["life_stage"] or "—"
        print(f"  {entry['type']:10s} {entry['name'][:40]:40s} [{entry['id'][:8]}]  "
              f"{days} {day_str} overdue  (due: {entry['next_due']}, stage: {stage})")


def _dry_run(engine, now, entity_type):
    """Show what would fire without acting."""
    # Chore candidates
    chore_candidates = []
    if entity_type is None or entity_type != "trigger":
        chore_candidates = engine.get_due_entities(now, entity_type=entity_type)

    # Trigger candidates
    trigger_candidates = []
    if entity_type is None or entity_type == "trigger":
        trigger_candidates = _get_due_triggers(engine, now)

    total = len(chore_candidates) + len(trigger_candidates)
    if total == 0:
        type_str = f" (type: {entity_type})" if entity_type else ""
        print(f"No entities due{type_str}.")
        return

    print(f"Would process {total} entit{'y' if total == 1 else 'ies'}:\n")

    for entry in chore_candidates:
        lead = entry.get("lead_time_days", 0)
        lead_str = f"  (lead: {lead}d)" if lead else ""
        print(f"  [promote] {entry['type']:10s} {entry['name'][:40]:40s} [{entry['id'][:8]}]  "
              f"due: {entry['next_due']}{lead_str}")

    for entry in trigger_candidates:
        print(f"  [spawn]   {entry['type']:10s} {entry['name'][:40]:40s} [{entry['id'][:8]}]  "
              f"due: {entry['next_due']}  agent: {entry.get('agent', '?')}")


def _get_due_triggers(engine, now):
    """Get schedule-fired triggers that are due (for dry-run display)."""
    import json
    now_str = now.strftime("%Y-%m-%dT%H:%M:%S")
    today_str = now.date().isoformat()

    c = engine.conn.cursor()
    c.execute("""
        SELECT e.id, e.name, e.type, e.next_due, e.action_parameters
        FROM entities e
        WHERE e.event_type = 'schedule_fired'
          AND e.executor = 'agent'
          AND e.next_due IS NOT NULL
          AND e.next_due <= ?
          AND COALESCE(e.resolution, 'unresolved') = 'unresolved'
          AND e.meta_status = 'live'
          AND (
            e.snoozed_until IS NULL
            OR e.snoozed_until <= ?
            OR (e.snoozed_from IS NOT NULL AND e.snoozed_from > ?)
          )
    """, (now_str, today_str, today_str))

    results = []
    for row in c.fetchall():
        eid, ename, etype, next_due, action_params_json = row
        agent = "?"
        try:
            params = json.loads(action_params_json) if action_params_json else {}
            agent = params.get("agent", "?")
        except (json.JSONDecodeError, TypeError):
            pass
        results.append({
            "id": eid, "name": ename, "type": etype,
            "next_due": next_due, "agent": agent,
        })
    return results


def _evaluate(engine, now, entity_type):
    """Unified evaluation — promote chores and spawn agents."""
    results = engine.evaluate_recurrence(now, entity_type=entity_type)

    if not results:
        print("No entities due.")
        return

    promoted = 0
    spawned = 0

    for result in results:
        if not result.actions_taken:
            continue
        action = result.actions_taken[0]

        if action.get("spawn_agent"):
            # Agent spawn
            agent_name = action.get("agent_name", "unknown")
            print(f"  >> [spawn]   '{action.get('entity_name', '?')}' [{action.get('entity_id', '?')[:8]}] "
                  f"— spawning {agent_name}")
            log_change("trigger_spawn", action.get("entity_id", ""), action.get("entity_type", "trigger"),
                       action.get("entity_name", "unknown"),
                       changes=[{"attribute": "agent_spawned", "old": None, "new": agent_name}],
                       triggered_by="recurrence_evaluator")
            spawned += 1
        elif action.get("changes"):
            # Chore promotion
            print(f"  >> [promote] '{action['entity_name']}' [{action['entity_id'][:8]}] "
                  f"— Backlog -> Ready")
            log_change("cascade", action["entity_id"], action["entity_type"],
                       action["entity_name"],
                       changes=action.get("changes", []),
                       triggered_by="recurrence_evaluator")
            promoted += 1

    parts = []
    if promoted:
        parts.append(f"{promoted} promoted")
    if spawned:
        parts.append(f"{spawned} spawned")
    print(f"\n  Total: {', '.join(parts)}." if parts else "")


if __name__ == "__main__":
    main()
