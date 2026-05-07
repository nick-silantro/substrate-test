#!/usr/bin/env python3
"""
Substrate Trigger Engine.

Unified event-action system that routes entity mutations through registered
triggers. Replaces direct cascade calls with a generalized engine that handles
synchronous cascades (script-time) and temporal evaluation (heartbeat-time).

Not a background process. Evaluated at two call sites:
  1. Script-time: called from update-entity.py / create-entity.py after mutations
  2. Heartbeat-time: called from evaluate-triggers.py by heartbeat agents

cascades.py is preserved and delegated to for existing cascade logic. This
module wraps it, providing a registry and evaluation framework that can grow
to include entity-attached and standalone trigger definitions.

Nomenclature: Event -> Conditional -> Branches (Branch = Action + Parameters + Executor)

Structural note: triggers are the physics of the system. Every state change is
a potential trigger event. If a matching trigger is registered, it fires.

Usage:
  engine = TriggerEngine(conn, substrate_path)
  event = TriggerEvent(EventType.RESOLUTION_CHANGED, entity_id, ...)
  results = engine.evaluate_script_time(event)
"""

import os
import ast
import json
import sqlite3
import yaml
import calendar
import subprocess
from enum import Enum
from datetime import datetime, date, timedelta
from dataclasses import dataclass, field
from typing import Any, Callable, Optional
from schema import load_schema

from cascades import (cascade_on_resolution, block_if_unresolved_deps,
                      cascade_on_ticket_in_progress,
                      cascade_ticket_ready_to_tasks,
                      cascade_task_in_progress_to_ticket)
from lib.fileio import safe_write


def get_engine_path(substrate_path: str) -> str:
    """Resolve the engine installation path from env, overlay, or default."""
    env_path = os.environ.get("SUBSTRATE_ENGINE_PATH")
    if env_path:
        return os.path.expanduser(env_path)
    overlay_path = os.path.join(substrate_path, "_system", "overlay.yaml")
    if os.path.exists(overlay_path):
        with open(overlay_path, encoding="utf-8") as f:
            overlay = yaml.safe_load(f) or {}
        engine = overlay.get("engine")
        if engine:
            return os.path.expanduser(engine)
    return os.path.expanduser("~/.substrate/engine")


# ─── Nomenclature of events recognized by the engine ────────────────────────


class EventType(Enum):
    """Events that can trigger actions.

    Each event type corresponds to a specific kind of entity mutation.
    Triggers register interest in one or more event types.
    """

    RESOLUTION_CHANGED = "resolution_changed"
    ENTITY_CREATED = "entity_created"
    SCHEDULE_FIRED = "schedule_fired"
    DIMENSION_CHANGED = "dimension_changed"
    DEPENDENCY_ADDED = "dependency_added"
    RELATIONSHIP_ADDED = "relationship_added"
    RELATIONSHIP_REMOVED = "relationship_removed"
    DATE_REACHED = "date_reached"


# ─── Invocation modes: how actions are carried out ──────────────────────────


class ExecutorType(Enum):
    """How a trigger's action is executed.

    CASCADE: synchronous, within the originating script call
    AGENT: async, queued for a heartbeat agent to pick up
    USER: surfaced to the user for manual action
    """

    CASCADE = "cascade"
    AGENT = "agent"
    USER = "user"


# ─── Canonical data structures ──────────────────────────────────────────────


@dataclass
class TriggerEvent:
    """An event that occurred in the system, to be evaluated against triggers.

    Attributes:
        event_type: what kind of mutation happened
        entity_id: UUID of the entity that changed
        entity_type: type string (e.g., "task", "chore")
        entity_name: human-readable name
        context: event-specific data (old/new values, target IDs, etc.)
    """

    event_type: EventType
    entity_id: str
    entity_type: str
    entity_name: str
    context: dict = field(default_factory=dict)


@dataclass
class TriggerAction:
    """An action to be performed when a trigger fires.

    Attributes:
        action_type: what to do (e.g., "unblock", "block", "reset_recurrence")
        parameters: action-specific configuration
        executor: how this action is carried out
    """

    action_type: str
    parameters: dict = field(default_factory=dict)
    executor: ExecutorType = ExecutorType.CASCADE


@dataclass
class TriggerResult:
    """Outcome of evaluating a single trigger against an event.

    Attributes:
        trigger_id: which trigger fired (e.g., "builtin:completion_unblock")
        source: where the trigger came from ("builtin", "entity_attached", "trigger_entity")
        entity_id: the entity that caused the event
        actions_taken: list of dicts describing what happened (for changelog integration)
    """

    trigger_id: str
    source: str
    entity_id: str
    actions_taken: list = field(default_factory=list)


@dataclass
class TriggerCondition:
    """A predicate that determines whether a trigger should fire.

    Attributes:
        predicate: callable that takes (event, conn) and returns bool
        description: human-readable explanation of the condition
    """

    predicate: Callable
    description: str


@dataclass
class Trigger:
    """Complete trigger definition.

    Attributes:
        id: unique identifier (e.g., "builtin:completion_unblock")
        name: human-readable name
        source: origin ("builtin", "entity_attached", "trigger_entity")
        event_types: which events this trigger responds to
        condition: predicate that must pass for the trigger to fire
        actions: what happens when the trigger fires
        handler: callable that performs the actual work (for built-ins)
    """

    id: str
    name: str
    source: str
    event_types: list
    condition: Optional[TriggerCondition]
    actions: list
    handler: Optional[Callable] = None


# ─── Recurrence: date calculation and validation ─────────────────────────────


VALID_SCHEDULE_TYPES = ("none", "interval", "day_of_week", "calendar_anchored")
VALID_INTERVAL_UNITS = ("seconds", "minutes", "hours", "days", "weeks", "months", "years")
VALID_DAY_ABBREVS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
DAY_ABBREV_TO_WEEKDAY = {"Mon": 0, "Tue": 1, "Wed": 2, "Thu": 3, "Fri": 4, "Sat": 5, "Sun": 6}

# Units that default to timestamp precision (sub-daily cadences)
_TIMESTAMP_UNITS = {"seconds", "minutes", "hours"}


def _get_precision(config):
    """Determine precision for a recurrence config.

    Explicit precision field wins. Otherwise, smart defaults:
    hours/minutes/seconds → timestamp, days/weeks/months/years → date.
    """
    explicit = config.get("precision")
    if explicit in ("date", "timestamp"):
        return explicit
    interval = config.get("interval", {})
    unit = interval.get("unit", "days") if isinstance(interval, dict) else "days"
    return "timestamp" if unit in _TIMESTAMP_UNITS else "date"


def _interval_to_timedelta(interval):
    """Convert a compound interval dict to a timedelta.

    Args:
        interval: dict with 'value' and 'unit' keys

    Returns:
        timedelta
    """
    value = int(interval.get("value", 1))
    unit = interval.get("unit", "days")

    if unit == "seconds":
        return timedelta(seconds=value)
    elif unit == "minutes":
        return timedelta(minutes=value)
    elif unit == "hours":
        return timedelta(hours=value)
    elif unit == "days":
        return timedelta(days=value)
    elif unit == "weeks":
        return timedelta(weeks=value)
    elif unit == "months":
        # Approximate: 30 days per month. For exact month arithmetic,
        # callers should use _next_calendar_anchored or dateutil.
        return timedelta(days=value * 30)
    elif unit == "years":
        return timedelta(days=value * 365)
    return timedelta(days=value)


def _apply_precision(dt_or_date, precision):
    """Apply precision to a datetime/date result.

    date precision: return a date (truncate to midnight).
    timestamp precision: return a datetime.
    """
    if precision == "date":
        if isinstance(dt_or_date, datetime):
            return dt_or_date.date()
        return dt_or_date
    else:
        # timestamp — ensure we return a datetime
        if isinstance(dt_or_date, date) and not isinstance(dt_or_date, datetime):
            return datetime.combine(dt_or_date, datetime.min.time())
        return dt_or_date


def calculate_next_due(config, from_date):
    """Calculate the next due date/datetime from a recurrence config and a reference point.

    For 'completion' basis: from_date is the completion date/time.
    For 'scheduled' basis: from_date is the old next_due.

    The returned value is always strictly after from_date.
    Return type depends on precision: date for 'date', datetime for 'timestamp'.

    When clock_time is present (array of "HH:MM" strings), the schedule determines
    which days fire and clock_time determines which times on those days. After firing,
    next_due is the next clock_time today (if any remain), otherwise the first
    clock_time on the next scheduled day.

    Args:
        config: dict with schedule_type and schedule-specific attributes
        from_date: date or datetime to calculate from

    Returns:
        date or datetime for the next due point
    """
    schedule_type = config.get("schedule_type", "none")
    precision = _get_precision(config)
    clock_times = _parse_clock_times(config.get("clock_time"))

    if schedule_type == "interval":
        interval = config.get("interval", {})
        if isinstance(interval, dict) and interval.get("value"):
            delta = _interval_to_timedelta(interval)
        else:
            # Fallback for legacy interval_days (should not exist after migration)
            delta = timedelta(days=int(config.get("interval_days", 1)))

        if clock_times:
            return _next_due_with_clock_time(from_date, clock_times,
                                             lambda d: d + delta)
        result = from_date + delta
        return _apply_precision(result, precision)

    elif schedule_type == "day_of_week":
        days = config.get("days", [])
        target_weekdays = sorted(DAY_ABBREV_TO_WEEKDAY[d] for d in days if d in DAY_ABBREV_TO_WEEKDAY)
        if not target_weekdays:
            return from_date + timedelta(days=1)

        def next_day_from(ref_date):
            """Find the next matching weekday strictly after ref_date's date."""
            rd = ref_date.date() if isinstance(ref_date, datetime) else ref_date
            current_wd = rd.weekday()
            for wd in target_weekdays:
                if wd > current_wd:
                    return datetime.combine(rd + timedelta(days=(wd - current_wd)),
                                            datetime.min.time())
            first_wd = target_weekdays[0]
            return datetime.combine(rd + timedelta(days=(7 - current_wd + first_wd)),
                                    datetime.min.time())

        if clock_times:
            return _next_due_with_clock_time(from_date, clock_times, next_day_from)

        current_weekday = from_date.weekday() if not isinstance(from_date, datetime) else from_date.weekday()
        for wd in target_weekdays:
            if wd > current_weekday:
                return from_date + timedelta(days=(wd - current_weekday))
        first_wd = target_weekdays[0]
        return from_date + timedelta(days=(7 - current_weekday + first_wd))

    elif schedule_type == "calendar_anchored":
        day_of_month = config.get("day_of_month", 1)

        if clock_times:
            def next_anchor(ref_date):
                rd = ref_date.date() if isinstance(ref_date, datetime) else ref_date
                return _next_calendar_anchored(rd, day_of_month, strict_after=True)
            return _next_due_with_clock_time(from_date, clock_times, next_anchor)

        return _next_calendar_anchored(from_date, day_of_month, strict_after=True)

    return from_date + timedelta(days=1)


def _parse_clock_times(raw):
    """Parse clock_time field into sorted list of (hour, minute) tuples.

    Accepts:
      - None → None
      - "HH:MM" → [(H, M)]
      - ["HH:MM", ...] → [(H, M), ...]

    Returns None if no valid times found.
    """
    if raw is None:
        return None

    if isinstance(raw, str):
        raw = [raw]

    if not isinstance(raw, list) or not raw:
        return None

    times = []
    for entry in raw:
        if isinstance(entry, str) and ":" in entry:
            parts = entry.split(":")
            try:
                times.append((int(parts[0]), int(parts[1])))
            except (ValueError, IndexError):
                continue
    return sorted(times) if times else None


def _next_due_with_clock_time(from_date, clock_times, advance_day_fn):
    """Calculate next_due when clock_time is present.

    Algorithm:
      1. Find next clock_time strictly after from_date's time on the same day.
      2. If found → return that datetime on from_date's date.
      3. If not → advance to next scheduled day (via advance_day_fn), return
         that day at the first clock_time.

    Args:
        from_date: datetime (or date) of the firing time
        clock_times: sorted list of (hour, minute) tuples
        advance_day_fn: callable(date_or_datetime) → datetime for next scheduled day

    Returns:
        datetime pinned to the next clock_time
    """
    if isinstance(from_date, date) and not isinstance(from_date, datetime):
        from_date = datetime.combine(from_date, datetime.min.time())

    from_time = (from_date.hour, from_date.minute)
    today = from_date.date()

    # Check for next clock_time today strictly after current time
    for ct in clock_times:
        if ct > from_time:
            return datetime.combine(today, datetime.min.time().replace(
                hour=ct[0], minute=ct[1]))

    # No more clock_times today — advance to next scheduled day
    next_day_dt = advance_day_fn(from_date)
    if isinstance(next_day_dt, date) and not isinstance(next_day_dt, datetime):
        next_day_dt = datetime.combine(next_day_dt, datetime.min.time())
    next_day = next_day_dt.date()

    # Pin to first clock_time on that day
    first_ct = clock_times[0]
    return datetime.combine(next_day, datetime.min.time().replace(
        hour=first_ct[0], minute=first_ct[1]))


def calculate_initial_next_due(config, today):
    """Calculate the initial next_due at entity creation time.

    Interval: now (due immediately; interval begins from first completion/firing).
    Day_of_week: today if today matches, otherwise next matching day.
    Calendar_anchored: today if today matches, otherwise next matching date.
    None: returns None (no temporal scheduling).

    Return type depends on precision: date for 'date', datetime for 'timestamp'.

    Args:
        config: dict with schedule_type and schedule-specific attributes
        today: date object for creation date

    Returns:
        date, datetime, or None
    """
    schedule_type = config.get("schedule_type", "none")
    precision = _get_precision(config)

    if schedule_type == "none":
        return None

    if schedule_type == "interval":
        if precision == "timestamp":
            return datetime.now()
        return today

    elif schedule_type == "day_of_week":
        days = config.get("days", [])
        target_weekdays = sorted(DAY_ABBREV_TO_WEEKDAY[d] for d in days if d in DAY_ABBREV_TO_WEEKDAY)
        if not target_weekdays:
            return today
        current_weekday = today.weekday()
        if current_weekday in target_weekdays:
            return today
        # Find next matching day
        for wd in target_weekdays:
            if wd > current_weekday:
                return today + timedelta(days=(wd - current_weekday))
        first_wd = target_weekdays[0]
        return today + timedelta(days=(7 - current_weekday + first_wd))

    elif schedule_type == "calendar_anchored":
        day_of_month = config.get("day_of_month", 1)
        # Check if today matches.
        # End-of-month clamping: if day_of_month > days in current month,
        # the last day of the month is treated as a match. This means
        # anchor=31 matches Feb 28 in a non-leap year, anchor=30 matches
        # Feb 28, etc. This is intentional — the user wants "as close to
        # the Nth as possible" and short months clamp down.
        if day_of_month == "last":
            last_day = calendar.monthrange(today.year, today.month)[1]
            if today.day == last_day:
                return today
        else:
            target_day = int(day_of_month)
            last_day = calendar.monthrange(today.year, today.month)[1]
            effective_day = min(target_day, last_day)
            if today.day == effective_day:
                return today
        return _next_calendar_anchored(today, day_of_month, strict_after=False)

    return None


def _next_calendar_anchored(from_date, day_of_month, strict_after=True):
    """Find the next occurrence of a calendar-anchored date.

    Args:
        from_date: reference date
        day_of_month: int (1-31) or "last"
        strict_after: if True, result must be > from_date; if False, >= from_date
    """
    if day_of_month == "last":
        # Last day of current month
        last_day = calendar.monthrange(from_date.year, from_date.month)[1]
        candidate = date(from_date.year, from_date.month, last_day)
        if (strict_after and candidate > from_date) or (not strict_after and candidate >= from_date):
            return candidate
        # Next month
        if from_date.month == 12:
            next_year, next_month = from_date.year + 1, 1
        else:
            next_year, next_month = from_date.year, from_date.month + 1
        last_day = calendar.monthrange(next_year, next_month)[1]
        return date(next_year, next_month, last_day)

    target_day = int(day_of_month)

    # Try current month
    last_day = calendar.monthrange(from_date.year, from_date.month)[1]
    effective_day = min(target_day, last_day)
    candidate = date(from_date.year, from_date.month, effective_day)
    if (strict_after and candidate > from_date) or (not strict_after and candidate >= from_date):
        return candidate

    # Next month
    if from_date.month == 12:
        next_year, next_month = from_date.year + 1, 1
    else:
        next_year, next_month = from_date.year, from_date.month + 1
    last_day = calendar.monthrange(next_year, next_month)[1]
    effective_day = min(target_day, last_day)
    return date(next_year, next_month, effective_day)


def validate_recurrence_config(config):
    """Validate a recurrence configuration block.

    Returns a list of error strings. Empty list means valid.
    """
    errors = []

    schedule_type = config.get("schedule_type")
    if not schedule_type:
        errors.append("recurrence config missing required attribute 'schedule_type'")
        return errors

    if schedule_type not in VALID_SCHEDULE_TYPES:
        errors.append(f"invalid schedule_type '{schedule_type}'; valid values: {', '.join(VALID_SCHEDULE_TYPES)}")
        return errors

    if schedule_type == "none":
        return errors

    # Validate precision if present
    precision = config.get("precision")
    if precision and precision not in ("date", "timestamp"):
        errors.append(f"invalid precision '{precision}'; valid values: date, timestamp")

    if schedule_type == "interval":
        interval = config.get("interval")
        if not interval or not isinstance(interval, dict):
            errors.append("schedule_type 'interval' requires 'interval' dict with 'value' and 'unit'")
        else:
            value = interval.get("value")
            unit = interval.get("unit")
            if not value or (isinstance(value, (int, float)) and value <= 0):
                errors.append("interval 'value' must be a positive number")
            if unit not in VALID_INTERVAL_UNITS:
                errors.append(f"invalid interval unit '{unit}'; valid: {', '.join(VALID_INTERVAL_UNITS)}")

    elif schedule_type == "day_of_week":
        days = config.get("days")
        if not days:
            errors.append("schedule_type 'day_of_week' requires 'days' list")
        elif isinstance(days, list):
            for d in days:
                if d not in VALID_DAY_ABBREVS:
                    errors.append(f"invalid day abbreviation '{d}'; valid: {', '.join(VALID_DAY_ABBREVS)}")

    elif schedule_type == "calendar_anchored":
        dom = config.get("day_of_month")
        if dom is None:
            errors.append("schedule_type 'calendar_anchored' requires 'day_of_month'")
        elif dom != "last":
            try:
                dom_int = int(dom)
                if dom_int < 1 or dom_int > 31:
                    errors.append(f"day_of_month must be 1-31 or 'last', got {dom}")
            except (ValueError, TypeError):
                errors.append(f"day_of_month must be 1-31 or 'last', got '{dom}'")

    return errors


# ─── Condition evaluation: safe expression parser for trigger conditions ─────


# Event type string to EventType enum mapping (for trigger entity loading)
EVENT_TYPE_MAP = {
    "resolution_changed": EventType.RESOLUTION_CHANGED,
    "entity_created": EventType.ENTITY_CREATED,
    "schedule_fired": EventType.SCHEDULE_FIRED,
    "dimension_changed": EventType.DIMENSION_CHANGED,
    "dependency_added": EventType.DEPENDENCY_ADDED,
    "relationship_added": EventType.RELATIONSHIP_ADDED,
    "relationship_removed": EventType.RELATIONSHIP_REMOVED,
    "date_reached": EventType.DATE_REACHED,
}

# Allowed query functions in condition expressions
ALLOWED_QUERY_FUNCTIONS = {"all_resolved", "wip_count"}


def evaluate_condition(expression, context, conn):
    """Evaluate a trigger condition expression safely using AST parsing.

    Supports:
      - Field comparisons: new == "completed", entity_type != "chore"
      - Boolean operators: and, or, not
      - 'in' operator: entity_type in ["task", "chore"]
      - Query functions: all_resolved(watched_ids), wip_count(watched_ids, statuses)

    Safety: uses Python's ast module to parse and walk the expression tree.
    Only allows Name, Constant, Compare, BoolOp, UnaryOp, List, and
    whitelisted Call nodes. Rejects imports, attribute access, and
    arbitrary function calls.

    Args:
        expression: string condition or None/empty (always fires)
        context: dict of attribute values available to the expression
        conn: sqlite3 connection (needed for query functions)

    Returns:
        True if condition is met, False if not met or expression is unsafe
    """
    if not expression or not expression.strip():
        return True

    # Normalize multiline conditions (from YAML block scalars)
    expression = expression.replace("\n", " ")

    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError:
        return False

    return _eval_node(tree.body, context, conn)


def _eval_node(node, context, conn):
    """Recursively evaluate an AST node against context.

    Returns the evaluated value (bool, str, int, list, etc.) or False on
    unsafe/unsupported patterns.
    """
    # Constant values: strings, numbers, booleans, None
    if isinstance(node, ast.Constant):
        return node.value

    # Variable references: look up in context
    if isinstance(node, ast.Name):
        if node.id.startswith("__"):
            return False
        return context.get(node.id)

    # List literals: [a, b, c]
    if isinstance(node, ast.List):
        return [_eval_node(elt, context, conn) for elt in node.elts]

    # Comparisons: ==, !=, <, >, <=, >=, in, not in
    if isinstance(node, ast.Compare):
        left = _eval_node(node.left, context, conn)
        for op, comparator in zip(node.ops, node.comparators):
            right = _eval_node(comparator, context, conn)
            if isinstance(op, ast.Eq):
                if left != right:
                    return False
            elif isinstance(op, ast.NotEq):
                if left == right:
                    return False
            elif isinstance(op, ast.Lt):
                if not (left is not None and right is not None and left < right):
                    return False
            elif isinstance(op, ast.Gt):
                if not (left is not None and right is not None and left > right):
                    return False
            elif isinstance(op, ast.LtE):
                if not (left is not None and right is not None and left <= right):
                    return False
            elif isinstance(op, ast.GtE):
                if not (left is not None and right is not None and left >= right):
                    return False
            elif isinstance(op, ast.In):
                if right is None or left not in right:
                    return False
            elif isinstance(op, ast.NotIn):
                if right is None or left in right:
                    return False
            else:
                return False
            left = right
        return True

    # Boolean operators: and, or
    if isinstance(node, ast.BoolOp):
        if isinstance(node.op, ast.And):
            for value in node.values:
                if not _eval_node(value, context, conn):
                    return False
            return True
        elif isinstance(node.op, ast.Or):
            for value in node.values:
                if _eval_node(value, context, conn):
                    return True
            return False

    # Unary operators: not
    if isinstance(node, ast.UnaryOp):
        if isinstance(node.op, ast.Not):
            return not _eval_node(node.operand, context, conn)
        return False

    # Function calls: only whitelisted query functions
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name):
            return False
        func_name = node.func.id
        if func_name not in ALLOWED_QUERY_FUNCTIONS:
            return False

        # Evaluate arguments
        args = [_eval_node(arg, context, conn) for arg in node.args]

        if func_name == "all_resolved":
            return _query_all_resolved(args[0] if args else [], conn)
        elif func_name == "wip_count":
            entity_ids = args[0] if len(args) > 0 else []
            statuses = args[1] if len(args) > 1 else []
            return _query_wip_count(entity_ids, statuses, conn)

        return False

    # Attribute access: rejected (blocks dunder traversal)
    if isinstance(node, ast.Attribute):
        return False

    # Anything else: unsafe, reject
    return False


def _query_all_resolved(entity_ids, conn):
    """Check if all entities in the list have a resolved resolution.

    Resolved means: Completed, Superseded, Abandoned, or Merged.
    """
    if not entity_ids or not conn:
        return True

    resolved_statuses = ("completed", "superseded", "Abandoned", "Merged")
    placeholders = ",".join("?" for _ in entity_ids)
    c = conn.cursor()
    c.execute(
        f"SELECT COUNT(*) FROM entities WHERE id IN ({placeholders}) "
        f"AND resolution NOT IN ({','.join('?' for _ in resolved_statuses)})",
        list(entity_ids) + list(resolved_statuses),
    )
    unresolved_count = c.fetchone()[0]
    return unresolved_count == 0


def _query_wip_count(entity_ids, statuses, conn):
    """Count entities in the given list that have a life_stage in statuses."""
    if not entity_ids or not conn or not statuses:
        return 0

    id_placeholders = ",".join("?" for _ in entity_ids)
    status_placeholders = ",".join("?" for _ in statuses)
    c = conn.cursor()
    c.execute(
        f"SELECT COUNT(*) FROM entities WHERE id IN ({id_placeholders}) "
        f"AND life_stage IN ({status_placeholders})",
        list(entity_ids) + list(statuses),
    )
    return c.fetchone()[0]


# ─── Kernel: trigger registry and evaluation ────────────────────────────────


class TriggerEngine:
    """Central registry and evaluator for all triggers in the system.

    Merges three sources:
      1. Built-in triggers (code) -- existing cascade logic
      2. Entity-attached triggers (YAML) -- recurrence attributes on meta.yaml (Phase 2)
      3. Trigger entities (data) -- type='trigger' entities in the graph (Phase 4)

    Evaluation modes:
      - evaluate_script_time(event): synchronous, CASCADE executors only
      - evaluate_heartbeat_time(now): temporal triggers, called by evaluate-triggers.py
    """

    def __init__(self, conn, substrate_path):
        """Initialize the engine with a database connection and workspace path.

        Args:
            conn: sqlite3 connection to substrate.db
            substrate_path: absolute path to the Substrate workspace root
        """
        self.conn = conn
        self.substrate_path = substrate_path
        self._valid_dimensions = set(load_schema(substrate_path).dimension_names)
        self.triggers = []
        self._register_builtins()
        self._load_and_register_trigger_entities()

    def _register_builtins(self):
        """Register built-in triggers that implement core system physics.

        These wrap existing cascades.py functions. They cannot be disabled.
        """
        # Nomenclature: completion of a dependency unblocks dependents
        self.triggers.append(Trigger(
            id="builtin:completion_unblock",
            name="Completion Unblock",
            source="builtin",
            event_types=[EventType.RESOLUTION_CHANGED],
            condition=TriggerCondition(
                predicate=lambda event, conn: (
                    event.context.get("new_resolution") in ("completed", "superseded")
                ),
                description="Resolution changed to Completed or Superseded",
            ),
            actions=[TriggerAction(
                action_type="unblock_dependents",
                executor=ExecutorType.CASCADE,
            )],
            handler=self._handle_completion_unblock,
        ))

        # Substrate rule: new dependency on unresolved target blocks the source
        self.triggers.append(Trigger(
            id="builtin:dependency_block",
            name="Dependency Block",
            source="builtin",
            event_types=[EventType.DEPENDENCY_ADDED],
            condition=None,  # Always evaluates; block_if_unresolved_deps handles the logic
            actions=[TriggerAction(
                action_type="block_source",
                executor=ExecutorType.CASCADE,
            )],
            handler=self._handle_dependency_block,
        ))

        # Ticket in_progress: promote eligible contained tasks to ready
        self.triggers.append(Trigger(
            id="builtin:ticket_in_progress_readiness",
            name="Ticket In Progress — Task Readiness",
            source="builtin",
            event_types=[EventType.DIMENSION_CHANGED],
            condition=TriggerCondition(
                predicate=lambda event, conn: (
                    event.entity_type == "ticket"
                    and event.context.get("attribute") == "life_stage"
                    and event.context.get("new_value") == "in_progress"
                ),
                description="Ticket life_stage changed to in_progress",
            ),
            actions=[TriggerAction(
                action_type="promote_eligible_tasks",
                executor=ExecutorType.CASCADE,
            )],
            handler=self._handle_ticket_in_progress_readiness,
        ))

        # Ticket ready: cascade ready down to all contained tasks
        self.triggers.append(Trigger(
            id="builtin:ticket_ready_task_promotion",
            name="Ticket Ready — Task Promotion",
            source="builtin",
            event_types=[EventType.DIMENSION_CHANGED],
            condition=TriggerCondition(
                predicate=lambda event, conn: (
                    event.entity_type == "ticket"
                    and event.context.get("attribute") == "life_stage"
                    and event.context.get("new_value") == "ready"
                ),
                description="Ticket life_stage changed to ready",
            ),
            actions=[TriggerAction(
                action_type="promote_contained_tasks_to_ready",
                executor=ExecutorType.CASCADE,
            )],
            handler=self._handle_ticket_ready_task_promotion,
        ))

        # Task in_progress: cascade in_progress up to parent ticket
        self.triggers.append(Trigger(
            id="builtin:task_in_progress_ticket_promotion",
            name="Task In Progress — Ticket Promotion",
            source="builtin",
            event_types=[EventType.DIMENSION_CHANGED],
            condition=TriggerCondition(
                predicate=lambda event, conn: (
                    event.entity_type == "task"
                    and event.context.get("attribute") == "life_stage"
                    and event.context.get("new_value") == "in_progress"
                ),
                description="Task life_stage changed to in_progress",
            ),
            actions=[TriggerAction(
                action_type="promote_parent_ticket_to_in_progress",
                executor=ExecutorType.CASCADE,
            )],
            handler=self._handle_task_in_progress_ticket_promotion,
        ))

        # Recurrence: completing a recurring entity resets it for the next cycle
        self.triggers.append(Trigger(
            id="builtin:recurrence_reset",
            name="Recurrence Reset",
            source="builtin",
            event_types=[EventType.RESOLUTION_CHANGED],
            condition=TriggerCondition(
                predicate=self._has_active_recurrence,
                description="Resolution changed to Completed AND entity has active recurrence (schedule_type != none)",
            ),
            actions=[TriggerAction(
                action_type="reset_recurrence",
                executor=ExecutorType.CASCADE,
            )],
            handler=self._handle_recurrence_reset,
        ))

    def _load_and_register_trigger_entities(self):
        """Load trigger entities from SQLite and register them."""
        entity_triggers = self._load_trigger_entities()
        self.triggers.extend(entity_triggers)

    def _load_trigger_entities(self):
        """Load trigger entities from SQLite and convert to Trigger objects.

        Reads trigger config from structured entity columns (event_type,
        action_type, executor, condition, action_parameters) — not from
        description. Description is prose, like every other entity.

        Filters:
          - type = 'trigger', meta_status = 'live'
          - COALESCE on focus/resolution for utility-nature entities (may be NULL)
          - event_type and action_type must be non-NULL (entity has valid config)

        Watched entities determined from 'watches' relationships.
        Target entities determined from 'acts_on' relationships.

        Returns:
            list of Trigger objects
        """
        c = self.conn.cursor()
        c.execute("""
            SELECT id, name, event_type, action_type, executor,
                   condition, action_parameters
            FROM entities
            WHERE type = 'trigger'
              AND meta_status = 'live'
              AND COALESCE(resolution, 'unresolved') = 'unresolved'
              AND COALESCE(focus, 'idle') IN ('idle', 'active')
              AND event_type IS NOT NULL
              AND action_type IS NOT NULL
        """)

        triggers = []
        for row in c.fetchall():
            eid, ename, event_type_str, action_type, executor_str, \
                condition_expr, action_params_raw = row

            if event_type_str not in EVENT_TYPE_MAP:
                continue

            try:
                executor = ExecutorType(executor_str or "cascade")
            except ValueError:
                executor = ExecutorType.CASCADE

            # Load watched entity IDs
            c2 = self.conn.cursor()
            c2.execute(
                "SELECT target_id FROM relationships WHERE source_id = ? AND relationship = 'watches'",
                (eid,),
            )
            watched_ids = [r[0] for r in c2.fetchall()]

            # Load acts_on target IDs
            c2.execute(
                "SELECT target_id FROM relationships WHERE source_id = ? AND relationship = 'acts_on'",
                (eid,),
            )
            acts_on_ids = [r[0] for r in c2.fetchall()]

            # Build condition
            condition = None
            if condition_expr:
                def make_condition_predicate(expr, w_ids):
                    def predicate(event, conn):
                        if w_ids and event.entity_id not in w_ids:
                            return False
                        ctx = dict(event.context)
                        ctx["entity_type"] = event.entity_type
                        ctx["entity_id"] = event.entity_id
                        ctx["entity_name"] = event.entity_name
                        ctx["watched_ids"] = w_ids
                        return evaluate_condition(expr, ctx, conn)
                    return predicate

                condition = TriggerCondition(
                    predicate=make_condition_predicate(condition_expr, watched_ids),
                    description=condition_expr,
                )
            else:
                if watched_ids:
                    def make_watch_filter(w_ids):
                        def predicate(event, conn):
                            return event.entity_id in w_ids
                        return predicate

                    condition = TriggerCondition(
                        predicate=make_watch_filter(watched_ids),
                        description=f"Event on watched entities: {watched_ids}",
                    )

            # Parse action_parameters from JSON column
            action_params = {}
            if action_params_raw:
                try:
                    action_params = json.loads(action_params_raw)
                except (json.JSONDecodeError, TypeError):
                    action_params = {}

            # Store metadata for the handler
            action_params["_trigger_entity_id"] = eid
            action_params["_watched_ids"] = watched_ids
            action_params["_acts_on_ids"] = acts_on_ids

            trigger = Trigger(
                id=f"entity:{eid}",
                name=ename,
                source="entity",
                event_types=[EVENT_TYPE_MAP[event_type_str]],
                condition=condition,
                actions=[TriggerAction(
                    action_type=action_type,
                    parameters=action_params,
                    executor=executor,
                )],
                handler=self._make_entity_trigger_handler(action_type, action_params),
            )
            triggers.append(trigger)

        return triggers

    def _make_entity_trigger_handler(self, action_type, action_params):
        """Create a handler function for an entity trigger's action.

        Args:
            action_type: the action to perform (e.g., "set_dimension")
            action_params: parameters for the action

        Returns:
            callable that takes a TriggerEvent and returns a TriggerResult
        """
        if action_type == "set_dimension":
            return lambda event: self._handle_set_dimension(event, action_params)
        elif action_type == "set_next_due":
            return lambda event: self._handle_set_next_due(event, action_params)
        elif action_type == "add_notification":
            return lambda event: self._handle_notification(event, action_params)
        elif action_type == "create_entity":
            return lambda event: self._handle_create_entity(event, action_params)
        elif action_type == "spawn_agent":
            return lambda event: self._handle_spawn_agent(event, action_params)

        # Unknown action type: return empty result
        def noop_handler(event):
            return TriggerResult(
                trigger_id=f"entity:{action_params.get('_trigger_entity_id', 'unknown')}",
                source="entity",
                entity_id=event.entity_id,
                actions_taken=[],
            )
        return noop_handler

    def _handle_set_dimension(self, event, params):
        """Handle the set_dimension action: update a target entity's dimension.

        Updates both SQLite and meta.yaml for the target entity.
        Validates dimension name against whitelist to prevent SQL injection.
        """
        target_id = params.get("target")
        dimension = params.get("dimension")
        value = params.get("value")
        trigger_entity_id = params.get("_trigger_entity_id", "unknown")

        if not target_id or not dimension or not value:
            return TriggerResult(
                trigger_id=f"entity:{trigger_entity_id}",
                source="entity",
                entity_id=event.entity_id,
                actions_taken=[{"error": "set_dimension missing target, dimension, or value"}],
            )

        # Validate dimension against whitelist (prevents SQL injection).
        # _valid_dimensions derived from schema — stays current as dims are added.
        if dimension not in self._valid_dimensions:
            return TriggerResult(
                trigger_id=f"entity:{trigger_entity_id}",
                source="entity",
                entity_id=event.entity_id,
                actions_taken=[{"error": f"invalid dimension '{dimension}'; valid: {sorted(self._valid_dimensions)}"}],
            )

        now_str = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

        # Read current value for changelog
        c = self.conn.cursor()
        c.execute(f"SELECT {dimension}, path, name, type FROM entities WHERE id = ?", (target_id,))
        row = c.fetchone()
        if not row:
            return TriggerResult(
                trigger_id=f"entity:{trigger_entity_id}",
                source="entity",
                entity_id=event.entity_id,
                actions_taken=[{"error": f"target entity {target_id} not found"}],
            )

        old_value, target_path, target_name, target_type = row

        # Update SQLite
        self.conn.execute(
            f"UPDATE entities SET {dimension} = ?, last_edited = ? WHERE id = ?",
            (value, now_str, target_id),
        )
        self.conn.commit()

        # Update meta.yaml
        self._update_meta_yaml_attr(target_path, dimension, value, now_str)

        return TriggerResult(
            trigger_id=f"entity:{trigger_entity_id}",
            source="entity",
            entity_id=event.entity_id,
            actions_taken=[{
                "entity_id": target_id,
                "entity_name": target_name,
                "entity_type": target_type,
                "changes": [{"attribute": dimension, "old": old_value, "new": value}],
            }],
        )

    def _handle_set_next_due(self, event, params):
        """Handle the set_next_due action: set a target entity's next_due date.

        Enables the series pattern — when one entity completes, the trigger
        sets the next entity's due date, making it eligible for heartbeat
        promotion.

        Parameters:
            target: entity ID to update
            value: ISO date string or "today" or "+Nd" for N days from now
        """
        target_id = params.get("target")
        value = params.get("value")
        trigger_entity_id = params.get("_trigger_entity_id", "unknown")

        if not target_id or not value:
            return TriggerResult(
                trigger_id=f"entity:{trigger_entity_id}",
                source="entity",
                entity_id=event.entity_id,
                actions_taken=[{"error": "set_next_due missing target or value"}],
            )

        now = datetime.now()
        now_str = now.strftime("%Y-%m-%dT%H:%M:%S")
        today = now.date()

        # Resolve the date value
        if value == "today":
            due_date = today
        elif value.startswith("+") and value.endswith("d"):
            try:
                days = int(value[1:-1])
                due_date = today + timedelta(days=days)
            except ValueError:
                return TriggerResult(
                    trigger_id=f"entity:{trigger_entity_id}",
                    source="entity",
                    entity_id=event.entity_id,
                    actions_taken=[{"error": f"invalid relative date '{value}'"}],
                )
        else:
            try:
                due_date = date.fromisoformat(value)
            except ValueError:
                return TriggerResult(
                    trigger_id=f"entity:{trigger_entity_id}",
                    source="entity",
                    entity_id=event.entity_id,
                    actions_taken=[{"error": f"invalid date '{value}'"}],
                )

        due_str = due_date.isoformat()

        # Read current value
        c = self.conn.cursor()
        c.execute("SELECT next_due, path, name, type FROM entities WHERE id = ?", (target_id,))
        row = c.fetchone()
        if not row:
            return TriggerResult(
                trigger_id=f"entity:{trigger_entity_id}",
                source="entity",
                entity_id=event.entity_id,
                actions_taken=[{"error": f"target entity {target_id} not found"}],
            )

        old_due, target_path, target_name, target_type = row

        # Update SQLite
        self.conn.execute(
            "UPDATE entities SET next_due = ?, last_edited = ? WHERE id = ?",
            (due_str, now_str, target_id),
        )
        self.conn.commit()

        # Update meta.yaml
        self._update_meta_yaml_attr(target_path, "next_due", due_str, now_str)

        return TriggerResult(
            trigger_id=f"entity:{trigger_entity_id}",
            source="entity",
            entity_id=event.entity_id,
            actions_taken=[{
                "entity_id": target_id,
                "entity_name": target_name,
                "entity_type": target_type,
                "changes": [{"attribute": "next_due", "old": old_due, "new": due_str}],
            }],
        )

    def _handle_notification(self, event, params):
        """Handle add_notification action (placeholder for future use)."""
        trigger_entity_id = params.get("_trigger_entity_id", "unknown")
        return TriggerResult(
            trigger_id=f"entity:{trigger_entity_id}",
            source="entity",
            entity_id=event.entity_id,
            actions_taken=[{"notification": params.get("message", "Trigger fired")}],
        )

    def _handle_create_entity(self, event, params):
        """Handle create_entity action (placeholder for future use)."""
        trigger_entity_id = params.get("_trigger_entity_id", "unknown")
        return TriggerResult(
            trigger_id=f"entity:{trigger_entity_id}",
            source="entity",
            entity_id=event.entity_id,
            actions_taken=[{"create_entity": "deferred — requires script invocation"}],
        )

    def _handle_spawn_agent(self, event, params):
        """Handle spawn_agent action: launch an agent via agent-run.sh in background.

        Does NOT perform the actual spawn — returns a TriggerResult with spawn
        parameters. The caller (fire_agent_triggers) performs the actual subprocess
        launch. This separation keeps handlers pure and testable.

        Action parameters:
            agent_name: which agent to spawn (e.g., "alpha", "carl")
            prompt: inline prompt text for the agent
            heartbeat_file: optional path to a heartbeat prompt file
                (passed via --append-system-prompt-file)
        """
        trigger_entity_id = params.get("_trigger_entity_id", "unknown")
        agent_name = params.get("agent_name")

        if not agent_name:
            return TriggerResult(
                trigger_id=f"entity:{trigger_entity_id}",
                source="entity",
                entity_id=event.entity_id,
                actions_taken=[{"error": "spawn_agent missing required 'agent_name'"}],
            )

        return TriggerResult(
            trigger_id=f"entity:{trigger_entity_id}",
            source="entity",
            entity_id=event.entity_id,
            actions_taken=[{
                "spawn_agent": True,
                "agent_name": agent_name,
                "prompt": params.get("prompt"),
                "heartbeat_file": params.get("heartbeat_file"),
                "trigger_entity_id": trigger_entity_id,
                "triggering_entity_id": event.entity_id,
                "triggering_entity_name": event.entity_name,
                "triggering_entity_type": event.entity_type,
                "event_type": event.event_type.value,
                "event_context": event.context,
            }],
        )

    def fire_agent_triggers(self, event):
        """Evaluate AGENT executor triggers and spawn matching agents in background.

        Companion to evaluate_script_time(). While that method handles CASCADE
        triggers synchronously, this method handles AGENT triggers by spawning
        agents via agent-run.sh as background processes.

        Non-blocking: each matched trigger spawns a subprocess and returns
        immediately. Respects agent-run.sh concurrency limits (the spawned
        process waits for a slot internally).

        Args:
            event: TriggerEvent describing what happened

        Returns:
            list of TriggerResult objects describing what agents were spawned
        """
        results = []
        agent_run_path = os.path.join(get_engine_path(self.substrate_path), "_system", "scripts", "agent-run.sh")

        if not os.path.exists(agent_run_path):
            return results

        for trigger in self.triggers:
            # Skip triggers that don't listen for this event type
            if event.event_type not in trigger.event_types:
                continue

            # Only AGENT executor triggers
            if not any(a.executor == ExecutorType.AGENT for a in trigger.actions):
                continue

            # Evaluate condition (includes watches filtering for entity triggers)
            if trigger.condition and not trigger.condition.predicate(event, self.conn):
                continue

            # Execute handler to get spawn parameters
            if trigger.handler:
                try:
                    result = trigger.handler(event)
                    results.append(result)

                    # Spawn agent for each action that returned spawn parameters
                    for action in result.actions_taken:
                        if action.get("spawn_agent"):
                            self._spawn_agent_process(
                                agent_run_path, action, event,
                            )

                    # Update fire stats
                    if trigger.source == "entity":
                        self._update_trigger_fire_stats(trigger.id)

                except Exception as e:
                    results.append(TriggerResult(
                        trigger_id=trigger.id,
                        source=trigger.source,
                        entity_id=event.entity_id,
                        actions_taken=[{"error": str(e), "trigger_id": trigger.id}],
                    ))

        return results

    def _spawn_agent_process(self, agent_run_path, action, event, trigger_id=None):
        """Spawn an agent via agent-run.sh as a background subprocess.

        Builds the command line from action parameters and launches it
        detached from the current process. The spawned agent-run.sh handles
        its own concurrency limiting and system lock checking.

        When trigger_id is provided, agent-run.sh updates the trigger entity's
        recurrence state (last_fired, fire_count, next_due) after acquiring a
        slot — confirmed-execution semantics.

        Args:
            agent_run_path: absolute path to agent-run.sh
            action: dict with spawn parameters from _handle_spawn_agent
            event: the triggering event (for context injection)
            trigger_id: optional UUID of the trigger entity (for state updates)
        """
        agent_name = action["agent_name"]
        prompt = action.get("prompt", "")
        heartbeat_file = action.get("heartbeat_file")

        # Build context string for the agent about what triggered it
        trigger_context = (
            f"Trigger fired: {event.event_type.value} on "
            f"{action.get('triggering_entity_type', 'unknown')} "
            f"'{action.get('triggering_entity_name', 'unknown')}' "
            f"[{action.get('triggering_entity_id', 'unknown')[:8]}]."
        )
        if event.context:
            ctx_parts = []
            for k, v in event.context.items():
                ctx_parts.append(f"{k}={v}")
            trigger_context += f" Context: {', '.join(ctx_parts)}."

        # Build the full prompt
        full_prompt = f"{trigger_context}\n\n{prompt}" if prompt else trigger_context

        cmd = [agent_run_path]

        if trigger_id:
            cmd.extend(["--trigger-id", trigger_id])

        cmd.extend(["--agent", agent_name])

        if heartbeat_file:
            cmd.extend(["--append-system-prompt-file", heartbeat_file])

        # Model and effort overrides from action_parameters
        model = action.get("model")
        if model and model != "inherit":
            cmd.extend(["--model", model])

        effort = action.get("effort")
        if effort:
            cmd.extend(["--effort", effort])

        # Required for headless operation: claude -p prompts for tool permissions
        # interactively, which hangs in a background subprocess. LaunchAgent plists
        # include this flag for the same reason. agent-run.sh does not add it —
        # the caller is responsible.
        cmd.append("--dangerously-skip-permissions")
        cmd.append(full_prompt)

        try:
            subprocess.Popen(
                cmd,
                cwd=self.substrate_path,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except Exception as e:
            # Log spawn failure but don't crash the calling script
            import sys
            print(f"   !! Agent spawn failed for {agent_name}: {e}", file=sys.stderr)

    def evaluate_script_time(self, event):
        """Evaluate triggers synchronously during a script execution.

        Only CASCADE executor triggers are eligible. Called from
        update-entity.py and create-entity.py after mutations.

        Evaluates both built-in triggers and entity triggers. Entity triggers
        are filtered by their watches relationships — they only fire for
        events on entities they watch.

        Args:
            event: TriggerEvent describing what happened

        Returns:
            list of TriggerResult objects describing what actions were taken
        """
        results = []

        for trigger in self.triggers:
            # Skip triggers that don't listen for this event type
            if event.event_type not in trigger.event_types:
                continue

            # Skip non-CASCADE triggers at script time
            if not any(a.executor == ExecutorType.CASCADE for a in trigger.actions):
                continue

            # Evaluate condition (includes watches filtering for entity triggers)
            if trigger.condition and not trigger.condition.predicate(event, self.conn):
                continue

            # Execute handler (isolated: one failing trigger doesn't prevent others)
            if trigger.handler:
                try:
                    result = trigger.handler(event)
                    results.append(result)
                    # Update fire stats for entity triggers
                    if trigger.source == "entity":
                        self._update_trigger_fire_stats(trigger.id)
                except Exception as e:
                    # Log the error but continue evaluating remaining triggers
                    results.append(TriggerResult(
                        trigger_id=trigger.id,
                        source=trigger.source,
                        entity_id=event.entity_id,
                        actions_taken=[{"error": str(e), "trigger_id": trigger.id}],
                    ))

        return results

    def _update_trigger_fire_stats(self, trigger_id):
        """Update last_fired and fire_count for a trigger entity after it fires.

        Args:
            trigger_id: prefixed trigger ID (e.g., "entity:trig-001")
        """
        # Strip the "entity:" prefix to get the raw entity ID
        entity_id = trigger_id.removeprefix("entity:")
        now_str = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

        try:
            self.conn.execute(
                """UPDATE entities SET
                    last_edited = ?
                WHERE id = ?""",
                (now_str, entity_id),
            )
            self.conn.commit()
        except Exception:
            pass  # Fire stats are informational; don't fail the trigger

    def evaluate_heartbeat_time(self, now):
        """Evaluate temporal triggers during a heartbeat run.

        Called by evaluate-triggers.py. Promotes Backlog entities to Ready
        when their next_due date (minus lead_time_days) has arrived.

        Filters:
          - resolution = Unresolved
          - meta_status = live
          - life_stage = Backlog (already-promoted entities are skipped)
          - next_due IS NOT NULL
          - Not actively snoozed
          - next_due - lead_time_days <= today

        Args:
            now: datetime representing the current evaluation time

        Returns:
            list of TriggerResult objects describing promotions
        """
        return self._evaluate_recurrence(now)

    def evaluate_heartbeat_time_filtered(self, now, entity_type=None):
        """Evaluate temporal triggers with optional type filter.

        Same as evaluate_heartbeat_time but can filter to a specific entity type.
        Used by evaluate-triggers.py --type flag.
        """
        return self._evaluate_recurrence(now, entity_type=entity_type)

    def evaluate_recurrence(self, now, entity_type=None):
        """Unified recurrence evaluation — one pass, polymorphic actions.

        Finds all due entities (chores AND schedule-fired triggers) and acts:
          - Chores in backlog → promote to Ready (state updated here)
          - Schedule-fired triggers → spawn agent (state updated in agent-run.sh)

        This is the single entry point for all time-based evaluation.
        Called by evaluate-triggers.py on its 5-minute cycle.

        Args:
            now: datetime representing the current evaluation time
            entity_type: optional type filter (e.g., "chore", "trigger")

        Returns:
            list of TriggerResult objects describing actions taken
        """
        results = []

        # Path 1: Chore promotion (existing behavior)
        chore_results = self._evaluate_recurrence(now, entity_type=entity_type)
        results.extend(chore_results)

        # Path 2: Schedule-fired trigger agent spawning
        if entity_type is None or entity_type == "trigger":
            trigger_results = self._evaluate_schedule_triggers(now)
            results.extend(trigger_results)

        return results

    def _evaluate_schedule_triggers(self, now):
        """Spawn agents for due schedule-fired triggers.

        Queries for trigger entities that are due (next_due <= now) and
        spawns agents via agent-run.sh. Trigger state (last_fired, next_due)
        is NOT updated here — agent-run.sh handles that after acquiring a
        concurrency slot (confirmed-execution semantics).

        Returns:
            list of TriggerResult objects describing spawns attempted
        """
        today = now.date() if isinstance(now, datetime) else now
        today_str = today.isoformat()
        now_str = now.strftime("%Y-%m-%dT%H:%M:%S") if isinstance(now, datetime) else today_str

        agent_run_path = os.path.join(get_engine_path(self.substrate_path), "_system", "scripts", "agent-run.sh")
        if not os.path.exists(agent_run_path):
            return []

        c = self.conn.cursor()
        c.execute("""
            SELECT e.id, e.name, e.type, e.path, e.next_due,
                   e.action_parameters, e.event_type, e.executor
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
            eid, ename, etype, epath, next_due, action_params_json, event_type, executor = row

            # Parse action_parameters
            try:
                action_params = json.loads(action_params_json) if action_params_json else {}
            except (json.JSONDecodeError, TypeError):
                action_params = {}

            agent_name = action_params.get("agent")
            if not agent_name:
                continue

            # Build action dict compatible with _spawn_agent_process
            prompt_file = action_params.get("prompt_file")
            heartbeat_file = None
            if prompt_file:
                # Resolve prompt_file relative to trigger entity folder
                heartbeat_file = os.path.join(self.substrate_path, epath, prompt_file)
                if not os.path.exists(heartbeat_file):
                    heartbeat_file = None

            action = {
                "spawn_agent": True,
                "agent_name": agent_name,
                "prompt": action_params.get("prompt", f"Run your scheduled heartbeat."),
                "heartbeat_file": heartbeat_file,
                "triggering_entity_id": eid,
                "triggering_entity_name": ename,
                "triggering_entity_type": etype,
            }

            # Add model/effort flags if specified
            model = action_params.get("model")
            effort = action_params.get("effort")

            # Build the event for context injection
            schedule_event = TriggerEvent(
                event_type=EventType.SCHEDULE_FIRED,
                entity_id=eid,
                entity_type=etype,
                entity_name=ename,
                context={"trigger": ename, "next_due": next_due},
            )

            # Spawn — trigger_id tells agent-run.sh to update state on confirmed execution
            self._spawn_agent_process(agent_run_path, action, schedule_event, trigger_id=eid)

            results.append(TriggerResult(
                trigger_id=eid,
                source="entity",
                entity_id=eid,
                actions_taken=[{
                    "entity_id": eid,
                    "entity_name": ename,
                    "entity_type": etype,
                    "spawn_agent": True,
                    "agent_name": agent_name,
                }],
            ))

        return results

    def _evaluate_recurrence(self, now, entity_type=None):
        """Promote due entities from Backlog to Ready.

        Core temporal evaluation: find all entities that are due (accounting
        for lead time and snooze) and promote them for discovery/action.
        """
        today = now.date() if isinstance(now, datetime) else now
        today_str = today.isoformat()
        now_str = now.strftime("%Y-%m-%dT%H:%M:%S") if isinstance(now, datetime) else today_str

        c = self.conn.cursor()
        # Query handles both date-precision (lead_time applies, compare against today)
        # and timestamp-precision (lead_time is 0, compare against now as datetime).
        # ISO strings sort correctly in SQLite for both date and datetime formats.
        query = """
            SELECT e.id, e.name, e.type, e.path, e.next_due,
                   json_extract(e.recurrence_schedule, '$.lead_time_days') as lead_time
            FROM entities e
            WHERE e.next_due IS NOT NULL
              AND e.resolution = 'unresolved'
              AND e.meta_status = 'live'
              AND e.life_stage = 'backlog'
              AND (
                e.snoozed_until IS NULL
                OR e.snoozed_until <= ?
                OR (e.snoozed_from IS NOT NULL AND e.snoozed_from > ?)
              )
              AND CASE WHEN COALESCE(lead_time, 0) > 0
                THEN date(e.next_due, '-' || lead_time || ' days') <= ?
                ELSE e.next_due <= ?
              END
        """
        params = [today_str, today_str, today_str, now_str]

        if entity_type:
            query += " AND e.type = ?"
            params.append(entity_type)

        c.execute(query, params)

        results = []
        for row in c.fetchall():
            eid, ename, etype, epath, next_due, lead_time = row

            # Promote: Backlog -> Ready
            self.conn.execute(
                "UPDATE entities SET life_stage = 'ready', last_edited = ? WHERE id = ?",
                (now_str, eid),
            )

            # Update meta.yaml
            self._update_meta_yaml_attr(epath, "life_stage", "ready", now_str)

            results.append(TriggerResult(
                trigger_id="builtin:recurrence_promote",
                source="builtin",
                entity_id=eid,
                actions_taken=[{
                    "entity_id": eid,
                    "entity_name": ename,
                    "entity_type": etype,
                    "changes": [
                        {"attribute": "life_stage", "old": "backlog", "new": "ready"},
                    ],
                }],
            ))

        self.conn.commit()
        return results

    def get_due_entities(self, now, entity_type=None):
        """Get entities that are due for promotion (for --dry-run and reporting).

        Same query as _evaluate_recurrence but returns data instead of acting.

        Args:
            now: datetime for evaluation
            entity_type: optional type filter (e.g., "chore")

        Returns:
            list of dicts with entity info
        """
        today = now.date() if isinstance(now, datetime) else now
        today_str = today.isoformat()
        now_str = now.strftime("%Y-%m-%dT%H:%M:%S") if isinstance(now, datetime) else today_str

        query = """
            SELECT e.id, e.name, e.type, e.next_due, e.life_stage,
                   json_extract(e.recurrence_schedule, '$.lead_time_days') as lead_time
            FROM entities e
            WHERE e.next_due IS NOT NULL
              AND e.resolution = 'unresolved'
              AND e.meta_status = 'live'
              AND e.life_stage = 'backlog'
              AND (
                e.snoozed_until IS NULL
                OR e.snoozed_until <= ?
                OR (e.snoozed_from IS NOT NULL AND e.snoozed_from > ?)
              )
              AND CASE WHEN COALESCE(lead_time, 0) > 0
                THEN date(e.next_due, '-' || lead_time || ' days') <= ?
                ELSE e.next_due <= ?
              END
        """
        params = [today_str, today_str, today_str, now_str]

        if entity_type:
            query += " AND e.type = ?"
            params.append(entity_type)

        c = self.conn.cursor()
        c.execute(query, params)

        results = []
        for row in c.fetchall():
            eid, ename, etype, next_due, life_stage, lead_time = row
            results.append({
                "id": eid,
                "name": ename,
                "type": etype,
                "next_due": next_due,
                "life_stage": life_stage,
                "lead_time_days": lead_time or 0,
            })
        return results

    def get_overdue_entities(self, now):
        """Get entities that are past their due date.

        Unlike get_due_entities, this focuses on entities where next_due < today
        (strictly past due, not just within lead time).

        Intentionally does NOT filter by snooze. Snooze suppresses promotion
        and nagging, but doesn't change whether something is overdue. The
        --overdue flag shows the full picture — snoozed items appear with
        their overdue count so the user can make informed decisions.

        Returns:
            list of dicts with entity info + days_overdue
        """
        today = now.date() if isinstance(now, datetime) else now
        today_str = today.isoformat()

        c = self.conn.cursor()
        c.execute("""
            SELECT e.id, e.name, e.type, e.next_due, e.life_stage
            FROM entities e
            WHERE e.next_due IS NOT NULL
              AND e.next_due < ?
              AND e.resolution = 'unresolved'
              AND e.meta_status = 'live'
        """, (today_str,))

        results = []
        for row in c.fetchall():
            eid, ename, etype, next_due, life_stage = row
            # Handle both date (YYYY-MM-DD) and datetime (YYYY-MM-DDTHH:MM:SS) next_due
            next_due_date = date.fromisoformat(next_due[:10])
            days_overdue = (today - next_due_date).days
            results.append({
                "id": eid,
                "name": ename,
                "type": etype,
                "next_due": next_due,
                "life_stage": life_stage,
                "days_overdue": days_overdue,
            })
        return results

    # Recurrence runtime attributes that live as indented sub-attributes under recurrence:
    _RECURRENCE_RUNTIME_ATTRS = {"next_due", "last_completed", "completion_count", "streak"}

    def _update_meta_yaml_attr(self, entity_path, attr_name, new_value, modified_str):
        """Update a single attribute in meta.yaml (and the last_edited timestamp).

        For recurrence runtime attributes (next_due, last_completed, completion_count, streak),
        matches the indented sub-attribute form ('  attr: value') under the recurrence: block.
        For all other attributes, matches at the top level.

        Timestamps and values are quoted via quote_yaml_scalar so the emission
        matches dump_entity_meta's canonical form — bare timestamps here would
        silently un-quote values the creation/update paths emitted quoted, see
        the ca885d21/2b44f20e commit chain.
        """
        from lib.fileio import quote_yaml_scalar
        quoted_modified = quote_yaml_scalar(modified_str)
        quoted_value = quote_yaml_scalar(new_value) if isinstance(new_value, str) else new_value
        meta_path = os.path.join(self.substrate_path, entity_path, "meta.yaml")
        if not os.path.exists(meta_path):
            return

        with safe_write(meta_path) as (content, write):
            lines = content.rstrip("\n").split("\n")
            new_lines = []
            updated_attr = False
            updated_modified = False
            is_recurrence_attr = attr_name in self._RECURRENCE_RUNTIME_ATTRS

            for line in lines:
                if is_recurrence_attr:
                    # Match indented sub-attribute under recurrence block (2-space indent)
                    stripped = line.lstrip()
                    if (line.startswith("  ") and not line.startswith("   ")
                            and stripped.startswith(f"{attr_name}:")
                            and not stripped.startswith(f"{attr_name}s:")):
                        new_lines.append(f"  {attr_name}: {quoted_value}")
                        updated_attr = True
                    elif line.startswith("last_edited:"):
                        new_lines.append(f"last_edited: {quoted_modified}")
                        updated_modified = True
                    else:
                        new_lines.append(line)
                else:
                    if line.startswith(f"{attr_name}:") and not line.startswith(f"{attr_name}s:"):
                        new_lines.append(f"{attr_name}: {quoted_value}")
                        updated_attr = True
                    elif line.startswith("last_edited:"):
                        new_lines.append(f"last_edited: {quoted_modified}")
                        updated_modified = True
                    else:
                        new_lines.append(line)

            if not updated_attr:
                if is_recurrence_attr:
                    # Insert inside recurrence block
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
                        new_lines.insert(last_indented + 1, f"  {attr_name}: {quoted_value}")
                    else:
                        new_lines.append(f"  {attr_name}: {quoted_value}")
                else:
                    new_lines.append(f"{attr_name}: {quoted_value}")
            if not updated_modified:
                new_lines.append(f"last_edited: {quoted_modified}")

            write("\n".join(new_lines) + "\n")

    # ─── Substrate built-in trigger handlers ──────────────────────────────

    def _handle_completion_unblock(self, event):
        """Handle the completion_unblock trigger.

        Delegates to cascades.cascade_on_resolution(). Wraps the result
        into a TriggerResult with changelog-compatible action records.
        """
        unblocked = cascade_on_resolution(
            self.conn, event.entity_id,
            event.context["new_resolution"],
            self.substrate_path,
        )

        actions_taken = []
        for uid, uname, utype in unblocked:
            actions_taken.append({
                "entity_id": uid,
                "entity_name": uname,
                "entity_type": utype,
                "changes": [{"attribute": "is_blocked", "old": "true", "new": "false"}],
            })

        return TriggerResult(
            trigger_id="builtin:completion_unblock",
            source="builtin",
            entity_id=event.entity_id,
            actions_taken=actions_taken,
        )

    def _handle_dependency_block(self, event):
        """Handle the dependency_block trigger.

        Delegates to cascades.block_if_unresolved_deps(). Wraps the result
        into a TriggerResult with changelog-compatible action records.
        """
        was_blocked = block_if_unresolved_deps(
            self.conn, event.entity_id, self.substrate_path,
        )

        actions_taken = []
        if was_blocked:
            actions_taken.append({
                "entity_id": event.entity_id,
                "entity_name": event.entity_name,
                "entity_type": event.entity_type,
                "changes": [{"attribute": "is_blocked", "old": "false", "new": "true"}],
            })

        return TriggerResult(
            trigger_id="builtin:dependency_block",
            source="builtin",
            entity_id=event.entity_id,
            actions_taken=actions_taken,
        )

    def _handle_ticket_in_progress_readiness(self, event):
        """Handle the ticket_in_progress_readiness trigger.

        Delegates to cascades.cascade_on_ticket_in_progress(). Wraps the result
        into a TriggerResult with changelog-compatible action records.
        """
        promoted = cascade_on_ticket_in_progress(
            self.conn, event.entity_id, self.substrate_path,
        )

        actions_taken = []
        for uid, uname, utype in promoted:
            actions_taken.append({
                "entity_id": uid,
                "entity_name": uname,
                "entity_type": utype,
                "changes": [{"attribute": "life_stage", "old": "backlog", "new": "ready"}],
            })

        return TriggerResult(
            trigger_id="builtin:ticket_in_progress_readiness",
            source="builtin",
            entity_id=event.entity_id,
            actions_taken=actions_taken,
        )

    def _handle_ticket_ready_task_promotion(self, event):
        """Handle the ticket_ready_task_promotion trigger.

        Delegates to cascades.cascade_ticket_ready_to_tasks(). Promotes all
        eligible contained tasks to ready, preserving existing focus state.
        """
        promoted = cascade_ticket_ready_to_tasks(
            self.conn, event.entity_id, self.substrate_path,
        )

        actions_taken = []
        for uid, uname, utype in promoted:
            actions_taken.append({
                "entity_id": uid,
                "entity_name": uname,
                "entity_type": utype,
                "changes": [{"attribute": "life_stage", "old": "backlog", "new": "ready"}],
            })

        return TriggerResult(
            trigger_id="builtin:ticket_ready_task_promotion",
            source="builtin",
            entity_id=event.entity_id,
            actions_taken=actions_taken,
        )

    def _handle_task_in_progress_ticket_promotion(self, event):
        """Handle the task_in_progress_ticket_promotion trigger.

        Delegates to cascades.cascade_task_in_progress_to_ticket(). Promotes
        the parent ticket to in_progress if it is at ready or backlog.
        """
        promoted = cascade_task_in_progress_to_ticket(
            self.conn, event.entity_id, self.substrate_path,
        )

        actions_taken = []
        for uid, uname, utype in promoted:
            actions_taken.append({
                "entity_id": uid,
                "entity_name": uname,
                "entity_type": utype,
                "changes": [{"attribute": "life_stage", "old": "ready", "new": "in_progress"}],
            })

        return TriggerResult(
            trigger_id="builtin:task_in_progress_ticket_promotion",
            source="builtin",
            entity_id=event.entity_id,
            actions_taken=actions_taken,
        )

    def _has_active_recurrence(self, event, conn):
        """Check if entity has active recurrence and resolution is Completed.

        Condition predicate for builtin:recurrence_reset trigger.
        Trigger entities are excluded — they fire-and-advance, not complete-and-reset.
        """
        if event.context.get("new_resolution") != "completed":
            return False

        # Triggers don't complete-and-reset; they fire-and-advance via the schedule evaluator
        if event.entity_type == "trigger":
            return False

        c = conn.cursor()
        c.execute("SELECT recurrence_schedule FROM entities WHERE id = ?", (event.entity_id,))
        row = c.fetchone()
        if not row or not row[0]:
            return False

        try:
            config = json.loads(row[0])
        except (json.JSONDecodeError, TypeError):
            return False

        return config.get("schedule_type") not in (None, "none")

    def _handle_recurrence_reset(self, event):
        """Handle the recurrence_reset trigger.

        When a recurring entity completes:
        1. Calculate next_due from recurrence config
        2. Update streak (increment if on-time, reset if late)
        3. Increment completion_count
        4. Set last_completed = now
        5. Reset: resolution -> Unresolved, focus -> Idle, life_stage -> Backlog, assessment -> Not Assessed
        6. Update both meta.yaml and SQLite
        """
        c = self.conn.cursor()
        now = datetime.now()
        now_str = now.strftime("%Y-%m-%dT%H:%M:%S")
        today = now.date()

        # Read current entity state
        c.execute(
            "SELECT next_due, completion_count, streak, recurrence_schedule "
            "FROM entities WHERE id = ?",
            (event.entity_id,),
        )
        row = c.fetchone()
        if not row:
            return TriggerResult(
                trigger_id="builtin:recurrence_reset",
                source="builtin",
                entity_id=event.entity_id,
                actions_taken=[],
            )

        old_next_due_str, old_count, old_streak, recurrence_json = row
        config = json.loads(recurrence_json) if recurrence_json else {}
        old_count = old_count or 0
        old_streak = old_streak or 0

        # Determine next_date_basis and calculate from_date
        next_date_basis = config.get("next_date_basis")
        if not next_date_basis:
            # Defaults per schedule type
            schedule_type = config.get("schedule_type", "none")
            if schedule_type == "interval":
                next_date_basis = "completion"
            else:
                next_date_basis = "scheduled"

        precision = _get_precision(config)

        if next_date_basis == "scheduled" and old_next_due_str:
            # Parse next_due — may be date (YYYY-MM-DD) or datetime (YYYY-MM-DDTHH:MM:SS)
            if "T" in old_next_due_str:
                from_date = datetime.fromisoformat(old_next_due_str)
            else:
                from_date = date.fromisoformat(old_next_due_str)
        else:
            from_date = now if precision == "timestamp" else today

        new_next_due = calculate_next_due(config, from_date)

        # Streak: on-time if completed on or before next_due
        if old_next_due_str:
            # Compare as date for date-precision, as datetime for timestamp-precision
            if "T" in old_next_due_str and precision == "timestamp":
                old_next_due = datetime.fromisoformat(old_next_due_str)
                new_streak = old_streak + 1 if now <= old_next_due else 0
            else:
                old_next_due = date.fromisoformat(old_next_due_str.split("T")[0])
                new_streak = old_streak + 1 if today <= old_next_due else 0
        else:
            new_streak = 0

        new_count = old_count + 1

        # Reset dimensions in SQLite
        c.execute(
            """UPDATE entities SET
                resolution = 'unresolved',
                focus = 'idle',
                life_stage = 'backlog',
                assessment = 'not_assessed',
                next_due = ?,
                last_completed = ?,
                completion_count = ?,
                streak = ?,
                last_edited = ?
            WHERE id = ?""",
            (new_next_due.isoformat(), now_str, new_count, new_streak, now_str, event.entity_id),
        )

        # Update meta.yaml (pass now_str for consistent last_edited timestamp)
        self._update_meta_yaml_recurrence(
            event.entity_id, new_next_due, now_str, new_count, new_streak, now_str,
        )

        changes = [
            {"attribute": "resolution", "old": "completed", "new": "unresolved"},
            {"attribute": "focus", "old": None, "new": "idle"},
            {"attribute": "life_stage", "old": None, "new": "backlog"},
            {"attribute": "assessment", "old": None, "new": "not_assessed"},
            {"attribute": "next_due", "old": old_next_due_str, "new": new_next_due.isoformat()},
            {"attribute": "completion_count", "old": old_count, "new": new_count},
            {"attribute": "streak", "old": old_streak, "new": new_streak},
            {"attribute": "last_completed", "old": None, "new": now_str},
        ]

        return TriggerResult(
            trigger_id="builtin:recurrence_reset",
            source="builtin",
            entity_id=event.entity_id,
            actions_taken=[{
                "entity_id": event.entity_id,
                "entity_name": event.entity_name,
                "entity_type": event.entity_type,
                "changes": changes,
            }],
        )

    def _update_meta_yaml_recurrence(self, entity_id, new_next_due, last_completed,
                                      completion_count, streak, modified_str):
        """Update meta.yaml attributes after a recurrence reset.

        Top-level dimensions (resolution, focus, life_stage, assessment, last_edited)
        are matched and updated at the top level. Recurrence runtime attributes
        (next_due, last_completed, completion_count, streak) are matched and updated
        as indented sub-attributes under the recurrence: block.
        """
        c = self.conn.cursor()
        c.execute("SELECT path FROM entities WHERE id = ?", (entity_id,))
        row = c.fetchone()
        if not row:
            return

        meta_path = os.path.join(self.substrate_path, row[0], "meta.yaml")
        if not os.path.exists(meta_path):
            return

        with safe_write(meta_path) as (content, write):
            # Top-level dimensions
            toplevel_updates = {
                "resolution": "unresolved",
                "focus": "idle",
                "life_stage": "backlog",
                "assessment": "not_assessed",
                "last_edited": modified_str,
            }
            # Recurrence runtime attributes — live as indented sub-attributes under recurrence:
            RECURRENCE_RUNTIME = {"next_due", "last_completed", "completion_count", "streak"}
            recurrence_updates = {
                "next_due": new_next_due.isoformat(),
                "last_completed": last_completed,
                "completion_count": str(completion_count),
                "streak": str(streak),
            }

            lines = content.rstrip("\n").split("\n")
            new_lines = []
            updated_attrs = set()

            for line in lines:
                matched = False
                # Match top-level attributes (unindented)
                for attr_name, new_value in toplevel_updates.items():
                    if line.startswith(f"{attr_name}:") and not line.startswith(f"{attr_name}s:"):
                        new_lines.append(f"{attr_name}: {new_value}")
                        updated_attrs.add(attr_name)
                        matched = True
                        break
                if not matched:
                    # Match recurrence runtime attributes (indented, 2-space)
                    stripped = line.lstrip()
                    if line.startswith("  ") and not line.startswith("   "):
                        for attr_name, new_value in recurrence_updates.items():
                            if stripped.startswith(f"{attr_name}:") and not stripped.startswith(f"{attr_name}s:"):
                                new_lines.append(f"  {attr_name}: {new_value}")
                                updated_attrs.add(attr_name)
                                matched = True
                                break
                if not matched:
                    new_lines.append(line)

            # Append top-level attributes that weren't found (should not happen in normal operation)
            for attr_name, new_value in toplevel_updates.items():
                if attr_name not in updated_attrs and attr_name != "last_edited":
                    new_lines.append(f"{attr_name}: {new_value}")

            # For recurrence runtime attributes not found: insert inside recurrence block
            missing_recurrence = {f: v for f, v in recurrence_updates.items() if f not in updated_attrs}
            if missing_recurrence:
                # Find insertion point at end of recurrence block
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
                    insert_at = last_indented + 1
                    insert_lines = [f"  {f}: {v}" for f, v in missing_recurrence.items()]
                    new_lines = new_lines[:insert_at] + insert_lines + new_lines[insert_at:]

            write("\n".join(new_lines) + "\n")
