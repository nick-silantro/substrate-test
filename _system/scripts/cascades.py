#!/usr/bin/env python3
"""
Reactive dimension transitions for dependency relationships.

Handles:
  - Cycle detection: prevents circular depends_on chains
  - Completion cascading: when a dependency resolves, unblock dependents
  - Dependency blocking: when a depends_on is added to an unresolved target, block the source

These functions operate on SQLite + meta.yaml together. They are called by
create-entity.py and update-entity.py — never invoked directly.

Design principles:
  - Blocking flows through depends_on relationships, not belongs_to containment
  - is_blocked: true means "has at least one unresolved dependency"
  - Unblocking happens when ALL dependencies are resolved (Completed or Superseded)
  - Cancellation/Deferral leaves dependents blocked — surfaced by query.py stuck
  - Only entities with is_blocked = true are candidates for unblocking (manual overrides respected)
  - focus is never modified by the blocking system — it tracks attention state independently
"""

import os
import sys
import sqlite3
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from schema import SubstrateSchema
from lib.fileio import safe_write

def _get_schema():
    import yaml
    schema_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "schema")
    with open(os.path.join(schema_dir, "types.yaml")) as f:
        types_data = yaml.safe_load(f)
    with open(os.path.join(schema_dir, "attributes.yaml")) as f:
        attributes_data = yaml.safe_load(f)
    with open(os.path.join(schema_dir, "relationships.yaml")) as f:
        relationships_data = yaml.safe_load(f)
    return SubstrateSchema(types_data, attributes_data, relationships_data)


RESOLVED_STATES = ("completed", "superseded")


def detect_dependency_cycle(conn, source_id, target_id):
    """Check if adding source_id depends_on target_id would create a cycle.

    A cycle exists if target_id already transitively depends_on source_id.
    Uses BFS through the depends_on graph starting from target_id.

    Returns:
        list of UUIDs forming the cycle path if found, None otherwise.
    """
    visited = set()
    queue = [target_id]
    parent = {target_id: None}

    c = conn.cursor()
    while queue:
        current = queue.pop(0)
        if current == source_id:
            # Cycle found — reconstruct path
            path = []
            node = current
            while node is not None:
                path.append(node)
                node = parent.get(node)
            return path

        if current in visited:
            continue
        visited.add(current)

        c.execute(
            "SELECT target_id FROM relationships "
            "WHERE source_id = ? AND relationship = 'depends_on'",
            (current,),
        )
        for (next_id,) in c.fetchall():
            if next_id not in visited:
                queue.append(next_id)
                if next_id not in parent:
                    parent[next_id] = current

    return None


def cascade_on_resolution(conn, entity_id, new_resolution, substrate_path):
    """Called when an entity's resolution changes.

    If resolution is Completed or Superseded, check all dependents:
    for each dependent that has is_blocked=true and is Unresolved, see if ALL
    of its depends_on targets are now resolved. If so, clear is_blocked.

    Returns list of (entity_id, entity_name, entity_type) that were unblocked.
    """
    if new_resolution not in RESOLVED_STATES:
        return []

    c = conn.cursor()
    now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

    # Find entities that depend on the just-resolved entity
    c.execute(
        "SELECT source_id FROM relationships "
        "WHERE target_id = ? AND relationship = 'depends_on'",
        (entity_id,),
    )
    dependent_ids = [row[0] for row in c.fetchall()]

    unblocked = []
    for dep_id in dependent_ids:
        # Only consider blocked, unresolved dependents
        c.execute(
            "SELECT name, type, is_blocked, resolution FROM entities WHERE id = ?",
            (dep_id,),
        )
        row = c.fetchone()
        if not row:
            continue
        dep_name, dep_type, dep_is_blocked, dep_resolution = row

        if dep_is_blocked != "true" or dep_resolution not in (None, "unresolved"):
            continue

        # Check if ALL of this dependent's dependencies are now resolved
        c.execute(
            "SELECT COUNT(*) FROM relationships r "
            "JOIN entities e ON r.target_id = e.id "
            "WHERE r.source_id = ? AND r.relationship = 'depends_on' "
            "AND e.resolution NOT IN (?, ?)",
            (dep_id, "completed", "superseded"),
        )
        unresolved_count = c.fetchone()[0]

        if unresolved_count == 0:
            # All dependencies resolved — clear is_blocked
            c.execute(
                "UPDATE entities SET is_blocked = 'false', last_edited = ? WHERE id = ?",
                (now, dep_id),
            )
            _update_meta_yaml_attr(dep_id, "is_blocked", "false", conn, substrate_path)
            unblocked.append((dep_id, dep_name, dep_type))

    return unblocked


def cascade_on_ticket_in_progress(conn, ticket_id, substrate_path):
    """Called when a ticket transitions to in_progress.

    Promotes eligible contained tasks to ready:
      - Tasks already at in_progress, under_review, or done_working are skipped
      - Tasks with any unresolved depends_on targets are left unchanged
      - Tasks with no dependencies, or all dependencies resolved, are promoted to ready

    Returns list of (entity_id, entity_name, entity_type) that were promoted.
    """
    PAST_READY = ("ready", "in_progress", "under_review", "done_working")

    c = conn.cursor()
    now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

    # Get all task children of this ticket — downward traversal uses parent-side (inverse) rels
    schema = _get_schema()
    containment_rels = sorted(schema.inverse_relationships_by_category['containment'])
    placeholders = ','.join('?' * len(containment_rels))
    c.execute(
        f"SELECT e.id, e.name, e.life_stage FROM entities e "
        f"JOIN relationships r ON r.target_id = e.id "
        f"WHERE r.source_id = ? AND r.relationship IN ({placeholders}) AND e.type = 'task'",
        [ticket_id] + containment_rels,
    )
    tasks = c.fetchall()

    promoted = []
    for task_id, task_name, task_life_stage in tasks:
        # Skip tasks already at or past ready
        if task_life_stage in PAST_READY:
            continue

        # Check for unresolved depends_on targets
        c.execute(
            "SELECT COUNT(*) FROM relationships r "
            "JOIN entities e ON r.target_id = e.id "
            "WHERE r.source_id = ? AND r.relationship = 'depends_on' "
            "AND (e.resolution IS NULL OR e.resolution NOT IN (?, ?))",
            (task_id, "completed", "superseded"),
        )
        unresolved_count = c.fetchone()[0]

        if unresolved_count == 0:
            # Eligible — promote to ready
            c.execute(
                "UPDATE entities SET life_stage = 'ready', last_edited = ? WHERE id = ?",
                (now, task_id),
            )
            _update_meta_yaml_attr(task_id, "life_stage", "ready", conn, substrate_path)
            promoted.append((task_id, task_name, "task"))

    return promoted


def cascade_ticket_ready_to_tasks(conn, ticket_id, substrate_path):
    """Called when a ticket transitions to ready.

    Promotes all eligible contained tasks to ready:
      - Tasks already at ready, in_progress, under_review, or done_working are skipped
      - All other tasks are promoted to ready regardless of depends_on status
      - is_blocked is not written — block_if_unresolved_deps already set is_blocked: true
        at relationship creation time; this cascade preserves blocking state as-is

    Returns list of (entity_id, entity_name, entity_type) that were promoted.
    """
    PAST_READY = ("ready", "in_progress", "under_review", "done_working")

    c = conn.cursor()
    now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

    schema = _get_schema()
    containment_rels = sorted(schema.inverse_relationships_by_category['containment'])
    placeholders = ','.join('?' * len(containment_rels))
    c.execute(
        f"SELECT e.id, e.name, e.life_stage FROM entities e "
        f"JOIN relationships r ON r.target_id = e.id "
        f"WHERE r.source_id = ? AND r.relationship IN ({placeholders}) AND e.type = 'task'",
        [ticket_id] + containment_rels,
    )
    tasks = c.fetchall()

    promoted = []
    for task_id, task_name, task_life_stage in tasks:
        if task_life_stage in PAST_READY:
            continue
        c.execute(
            "UPDATE entities SET life_stage = 'ready', last_edited = ? WHERE id = ?",
            (now, task_id),
        )
        _update_meta_yaml_attr(task_id, "life_stage", "ready", conn, substrate_path)
        promoted.append((task_id, task_name, "task"))

    return promoted


def cascade_task_in_progress_to_ticket(conn, task_id, substrate_path):
    """Called when a task transitions to in_progress.

    Promotes the parent ticket to in_progress if it is at ready or backlog.
    "First task wins" — if the ticket is already at in_progress or past, this is a no-op.
    Returns list of (entity_id, entity_name, entity_type) that were promoted (0 or 1 items).
    """
    c = conn.cursor()
    now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

    # Find the parent ticket — ticket is source_id, task is target_id (contains relationship)
    schema = _get_schema()
    containment_rels = sorted(schema.inverse_relationships_by_category['containment'])
    placeholders = ','.join('?' * len(containment_rels))
    c.execute(
        f"SELECT e.id, e.name, e.life_stage FROM entities e "
        f"JOIN relationships r ON r.source_id = e.id "
        f"WHERE r.target_id = ? AND r.relationship IN ({placeholders}) AND e.type = 'ticket'",
        [task_id] + containment_rels,
    )
    row = c.fetchone()

    if not row:
        return []  # No parent ticket — chore or top-level task

    ticket_id, ticket_name, ticket_life_stage = row

    if ticket_life_stage not in ("ready", "backlog"):
        return []  # Already in_progress or past — no-op

    c.execute(
        "UPDATE entities SET life_stage = 'in_progress', last_edited = ? WHERE id = ?",
        (now, ticket_id),
    )
    _update_meta_yaml_attr(ticket_id, "life_stage", "in_progress", conn, substrate_path)
    return [(ticket_id, ticket_name, "ticket")]


def block_if_unresolved_deps(conn, entity_id, substrate_path):
    """Check if entity has any unresolved dependencies. If so, set is_blocked = true.

    Called after adding a depends_on relationship (both creation and update).

    Returns True if the entity was blocked, False otherwise.
    """
    c = conn.cursor()

    # Check current state — don't block if already blocked, closed, or resolved
    c.execute(
        "SELECT is_blocked, focus, resolution FROM entities WHERE id = ?",
        (entity_id,),
    )
    row = c.fetchone()
    if not row:
        return False
    current_is_blocked, current_focus, current_resolution = row

    if current_is_blocked == "true" or current_focus == "closed":
        return False
    if current_resolution and current_resolution not in (None, "unresolved"):
        return False

    # Check if any depends_on target is unresolved
    c.execute(
        "SELECT COUNT(*) FROM relationships r "
        "JOIN entities e ON r.target_id = e.id "
        "WHERE r.source_id = ? AND r.relationship = 'depends_on' "
        "AND e.resolution NOT IN (?, ?)",
        (entity_id, "completed", "superseded"),
    )
    unresolved_count = c.fetchone()[0]

    if unresolved_count > 0:
        now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        c.execute(
            "UPDATE entities SET is_blocked = 'true', last_edited = ? WHERE id = ?",
            (now, entity_id),
        )
        _update_meta_yaml_attr(entity_id, "is_blocked", "true", conn, substrate_path)
        return True

    return False


def cascade_on_review_fail(conn, review_entity_id, substrate_path):
    """Called when a review entity's verdict is set to 'fail'.

    Two behaviors:
    1. Rolls the parent ticket back to in_progress (via belongs_to relationship).
    2. Retires (phase → retired) all other established review entities on that ticket
       where verdict = 'pass' AND gate matches the failing review's gate. A post-execution
       fail only retires post-execution passes; pre-execution passes are unaffected (and
       vice versa). Outstanding conditional/fail reviews are left as-is — they serve as
       the explicit checklist the L2 must clear before resubmitting.

    Gate-awareness matters because lifecycle stages don't re-check earlier gates: a ticket
    rolled back to in_progress won't re-trigger the ready gate (BSC), so retiring
    pre-execution passes from a post-execution fail would destroy BSC history with no
    mechanism to re-validate it.

    Returns list of (entity_id, entity_name, entity_type) affected (ticket + retired reviews).
    """
    c = conn.cursor()
    now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    affected = []

    # Read the failing review's gate from meta.yaml
    c.execute("SELECT path FROM entities WHERE id = ?", (review_entity_id,))
    fail_row = c.fetchone()
    fail_gate = None
    if fail_row:
        fail_meta_path = os.path.join(substrate_path, fail_row[0], "meta.yaml")
        if os.path.exists(fail_meta_path):
            with open(fail_meta_path, "r") as f:
                for line in f:
                    stripped = line.strip()
                    if stripped.startswith("gate:"):
                        fail_gate = stripped.split(":", 1)[1].strip().strip('"').strip("'")
                        break

    # Find the parent ticket via belongs_to
    c.execute(
        "SELECT e.id, e.name, e.type, e.life_stage FROM entities e "
        "JOIN relationships r ON r.target_id = e.id "
        "WHERE r.source_id = ? AND r.relationship = 'belongs_to' "
        "AND e.type IN ('ticket', 'chore')",
        (review_entity_id,),
    )
    ticket_row = c.fetchone()
    if not ticket_row:
        return []  # No parent ticket — review not attached to a ticket

    ticket_id, ticket_name, ticket_type, ticket_life_stage = ticket_row

    # Roll ticket back to in_progress if it's at under_review or done_working
    ROLLBACK_STAGES = ("under_review", "done_working")
    if ticket_life_stage in ROLLBACK_STAGES:
        c.execute(
            "UPDATE entities SET life_stage = 'in_progress', last_edited = ? WHERE id = ?",
            (now, ticket_id),
        )
        _update_meta_yaml_attr(ticket_id, "life_stage", "in_progress", conn, substrate_path)
        affected.append((ticket_id, ticket_name, ticket_type))

    # Find all other review entities on the same ticket and retire passing ones
    # from the same gate. Conditional/fail reviews stay — they are the checklist.
    c.execute(
        "SELECT e.id, e.name, e.path FROM entities e "
        "JOIN relationships r ON r.source_id = e.id "
        "WHERE r.target_id = ? AND r.relationship = 'belongs_to' "
        "AND e.type = 'review' AND e.id != ?",
        (ticket_id, review_entity_id),
    )
    sibling_reviews = c.fetchall()

    for sibling_id, sibling_name, sibling_path in sibling_reviews:
        # Read verdict, phase, and gate from meta.yaml
        meta_path = os.path.join(substrate_path, sibling_path, "meta.yaml")
        sibling_verdict = None
        sibling_phase = None
        sibling_gate = None
        if os.path.exists(meta_path):
            with open(meta_path, "r") as f:
                for line in f:
                    stripped = line.strip()
                    if stripped.startswith("verdict:"):
                        sibling_verdict = stripped.split(":", 1)[1].strip().strip('"').strip("'")
                    elif stripped.startswith("phase:"):
                        sibling_phase = stripped.split(":", 1)[1].strip().strip('"').strip("'")
                    elif stripped.startswith("gate:"):
                        sibling_gate = stripped.split(":", 1)[1].strip().strip('"').strip("'")

        # Only retire passing reviews from the same gate
        # (leave other gates untouched, leave conditionals/fails as the checklist)
        if (sibling_verdict and sibling_verdict.lower().startswith("pass")
                and (fail_gate is None or sibling_gate == fail_gate)):
            if sibling_phase != "retired":
                _update_meta_yaml_attr(sibling_id, "phase", "retired", conn, substrate_path)
                affected.append((sibling_id, sibling_name, "review"))

    return affected


def format_cycle_error(cycle_path, conn):
    """Format a cycle path into a readable error message."""
    c = conn.cursor()
    parts = []
    for node_id in cycle_path:
        c.execute("SELECT name, type FROM entities WHERE id = ?", (node_id,))
        row = c.fetchone()
        if row:
            parts.append(f"{row[1]} '{row[0]}' [{node_id[:8]}]")
        else:
            parts.append(f"[{node_id[:8]}]")
    return " → depends_on → ".join(parts)


# --- Internal helpers ---


def _update_meta_yaml_attr(entity_id, attr, value, conn, substrate_path):
    """Update a single attribute in an entity's meta.yaml. Values and the
    last_edited timestamp are routed through quote_yaml_scalar so emission
    matches dump_entity_meta canonical form — bare writes here silently
    un-quote values the creation path emitted quoted, see ca885d21/2b44f20e.
    """
    from lib.fileio import quote_yaml_scalar
    c = conn.cursor()
    c.execute("SELECT path FROM entities WHERE id = ?", (entity_id,))
    row = c.fetchone()
    if not row:
        return

    meta_path = os.path.join(substrate_path, row[0], "meta.yaml")
    if not os.path.exists(meta_path):
        return

    quoted_value = quote_yaml_scalar(value) if isinstance(value, str) else value

    with safe_write(meta_path) as (content, write):
        lines = content.rstrip("\n").split("\n")
        updated = False
        new_lines = []

        for line in lines:
            if line.startswith(f"{attr}:") and not line.startswith(f"{attr}s:"):
                new_lines.append(f"{attr}: {quoted_value}")
                updated = True
            else:
                new_lines.append(line)

        if not updated:
            # Insert before relationship lines (find first relationship key)
            insert_at = len(new_lines)
            new_lines.insert(insert_at, f"{attr}: {quoted_value}")

        # Also update last_edited timestamp
        now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        quoted_now = quote_yaml_scalar(now)
        final_lines = []
        mod_updated = False
        for line in new_lines:
            if line.startswith("last_edited:"):
                final_lines.append(f"last_edited: {quoted_now}")
                mod_updated = True
            else:
                final_lines.append(line)
        if not mod_updated:
            final_lines.append(f"last_edited: {quoted_now}")

        write("\n".join(final_lines) + "\n")
