#!/usr/bin/env python3
"""
Change Data Capture (CDC) for Substrate.

Writes every entity mutation directly to the changelog table in substrate.db.
Agent identity is read from the SUBSTRATE_AGENT environment variable (optional).

Entry format (stored as raw_json):
  {
    "timestamp": "2026-03-03T14:22:31Z",
    "operation": "create" | "update" | "delete" | "cascade",
    "entity_id": "uuid",
    "entity_type": "task",
    "entity_name": "Analyze job listing",
    "agent": "alpha",                          # optional, from SUBSTRATE_AGENT
    "changes": [...],                          # attribute-level diffs
    "relationships": [...],                    # relationship mutations
    "triggered_by": "uuid"                     # for cascade events
  }

Changes format (updates):  {"attribute": "focus", "old": "idle", "new": "active"}
Changes format (creates):  {"attribute": "focus", "value": "active"}
Relationships format:      {"action": "add"|"remove"|"change", "type": "belongs_to",
                            "target_id": "uuid", "target_name": "Some Entity"}
"""

import os
import json
import sqlite3
from datetime import datetime, timezone


def _substrate_path():
    return os.environ.get(
        "SUBSTRATE_PATH",
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    )


def _db_path():
    return os.path.join(_substrate_path(), "_system", "index", "substrate.db")


def ensure_changelog_table(conn):
    """Create the changelog table and indexes if they don't exist."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS changelog (
            rowid INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            operation TEXT NOT NULL,
            entity_id TEXT NOT NULL,
            entity_type TEXT NOT NULL,
            entity_name TEXT,
            agent TEXT,
            triggered_by TEXT,
            raw_json TEXT NOT NULL
        )
    """)
    for idx_name, idx_col in [
        ("idx_changelog_entity", "entity_id"),
        ("idx_changelog_agent", "agent"),
        ("idx_changelog_timestamp", "timestamp"),
        ("idx_changelog_operation", "operation"),
    ]:
        conn.execute(f"CREATE INDEX IF NOT EXISTS {idx_name} ON changelog({idx_col})")
    conn.commit()


def log_change(operation, entity_id, entity_type, entity_name,
               changes=None, relationships=None, triggered_by=None):
    """Write a change event to the changelog table in substrate.db.

    Args:
        operation: "create", "update", "delete", or "cascade"
        entity_id: UUID of the entity
        entity_type: type string (e.g., "task", "document")
        entity_name: human-readable name
        changes: list of attribute-level change dicts (see module docstring)
        relationships: list of relationship change dicts (see module docstring)
        triggered_by: entity UUID that caused this change (for cascade events)
    """
    entry = {
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "operation": operation,
        "entity_id": entity_id,
        "entity_type": entity_type,
        "entity_name": entity_name,
    }

    agent = os.environ.get("SUBSTRATE_AGENT")
    if agent:
        entry["agent"] = agent

    if changes:
        entry["changes"] = changes
    if relationships:
        entry["relationships"] = relationships
    if triggered_by:
        entry["triggered_by"] = triggered_by

    raw_json = json.dumps(entry)

    db = _db_path()
    if not os.path.exists(db):
        return  # No database yet — skip silently
    try:
        conn = sqlite3.connect(db)
        ensure_changelog_table(conn)
        conn.execute("""
            INSERT INTO changelog (timestamp, operation, entity_id, entity_type,
                                   entity_name, agent, triggered_by, raw_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            entry["timestamp"],
            entry["operation"],
            entry["entity_id"],
            entry["entity_type"],
            entry.get("entity_name"),
            entry.get("agent"),
            entry.get("triggered_by"),
            raw_json,
        ))
        conn.commit()
        conn.close()
    except Exception:
        pass  # Non-fatal — don't break entity operations
