#!/usr/bin/env python3
"""
Remove a relationship (and its inverse) from _system/schema/relationships.yaml.

This script is the atomic primitive for relationship deletion via the Surface API
(DELETE /api/schema/relationships/:name). It:
  1. Validates the relationship exists in the schema
  2. Optionally checks for existing usages in SQLite (safety gate)
  3. Removes both the forward relationship entry and its inverse entry
  4. Prints a JSON result for the Express caller

Safety gate: if entities are using this relationship in SQLite, the operation
is blocked unless force: true is passed.

No SQLite migration needed: relationships are stored as string names in the
relationships table — removing the schema definition doesn't drop rows.
Existing relationship rows become schema-orphaned and will be flagged by validate.py
if force is used.

Input: JSON via stdin
  {
    "name": "rated_by",    # forward relationship name to remove
    "force": false         # optional — bypass usage check
  }

Output: JSON to stdout
  { "success": true, "name": "rated_by", "inverse": "rates" }
  { "success": false, "error": "Relationship 'rated_by' is used by 5 entities. Pass force: true to remove anyway." }
"""

import json
import os
import sqlite3
import sys
import yaml
from pathlib import Path

WORKSPACE = Path(os.environ.get("SUBSTRATE_PATH", Path(__file__).resolve().parents[2]))
ENGINE = Path(os.environ.get("SUBSTRATE_ENGINE_PATH", WORKSPACE))
RELATIONSHIPS_YAML = ENGINE / "_system" / "schema" / "relationships.yaml"
DB_PATH = WORKSPACE / "_system" / "index" / "substrate.db"


def fail(msg: str) -> None:
    print(json.dumps({"success": False, "error": msg}))
    sys.exit(1)


def count_usages(rel_name: str) -> int:
    """Return count of relationship rows using this name in SQLite."""
    try:
        conn = sqlite3.connect(str(DB_PATH))
        row = conn.execute(
            "SELECT COUNT(*) FROM relationships WHERE relationship = ?", (rel_name,)
        ).fetchone()
        conn.close()
        return row[0] if row else 0
    except Exception:
        return 0


def find_relationship(schema: dict, name: str) -> tuple[str, dict] | tuple[None, None]:
    """
    Find a relationship by name in the schema.
    Returns (category_name, rel_data) or (None, None) if not found.
    """
    for cat_name, cat_data in schema.get("categories", {}).items():
        rels = cat_data.get("relationships", {})
        if name in rels:
            return cat_name, rels[name]
    return None, None


def delete_rel_block(content: str, name: str) -> tuple[str, bool]:
    """
    Remove a relationship block from relationships.yaml.
    Relationship entries are at 6-space indent (under categories.X.relationships).
    Returns (updated_content, found).
    """
    lines = content.split("\n")
    result = []
    in_block = False
    found = False
    i = 0

    while i < len(lines):
        line = lines[i]
        stripped = line.lstrip()
        indent = len(line) - len(stripped)

        if line.rstrip() == f"      {name}:" or line.startswith(f"      {name}: "):
            in_block = True
            found = True
            # Remove preceding blank line if present
            if result and result[-1].strip() == "":
                result.pop()
            i += 1
            continue

        if in_block:
            # Block continues while indentation > 6
            if stripped and indent <= 6:
                in_block = False
                result.append(line)
            # else: skip (part of deleted block)
            i += 1
            continue

        result.append(line)
        i += 1

    return "\n".join(result), found


def main():
    try:
        raw = sys.stdin.read().strip()
        if not raw:
            fail("No input — expected JSON via stdin: {\"name\": \"relationship-name\"}")
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        fail(f"Invalid JSON input: {e}")

    name = data.get("name", "").strip()
    if not name:
        fail("Field 'name' is required")

    force = data.get("force", False)

    # Load schema to find the relationship and its inverse
    try:
        with open(RELATIONSHIPS_YAML) as f:
            existing = yaml.safe_load(f)
    except Exception as e:
        fail(f"Cannot read relationships.yaml: {e}")

    category, rel_data = find_relationship(existing, name)
    if category is None:
        fail(f"Relationship '{name}' does not exist in schema")

    inverse_name = rel_data.get("inverse", "")
    is_symmetric = (name == inverse_name)

    # Safety gate: check for existing usages
    if not force:
        usage_count = count_usages(name)
        if usage_count > 0:
            fail(
                f"Relationship '{name}' is used by {usage_count} entit{'y' if usage_count == 1 else 'ies'}. "
                f"Remove those relationships first, or pass force: true to remove the schema entry anyway "
                f"(orphaned rows will be flagged by validate.py)."
            )

    try:
        content = open(RELATIONSHIPS_YAML).read()
    except Exception as e:
        fail(f"Cannot read relationships.yaml: {e}")

    # Remove the forward relationship block
    content, found = delete_rel_block(content, name)
    if not found:
        fail(f"Relationship '{name}' found in YAML parse but not in text — file may be malformed")

    # Remove the inverse relationship block (if it's a different name)
    if inverse_name and not is_symmetric and inverse_name != name:
        content, _ = delete_rel_block(content, inverse_name)

    try:
        with open(RELATIONSHIPS_YAML, "w") as f:
            f.write(content)
    except Exception as e:
        fail(f"Failed to write relationships.yaml: {e}")

    result: dict = {"success": True, "name": name}
    if inverse_name and not is_symmetric:
        result["inverse"] = inverse_name
    if force:
        result["warning"] = (
            f"Relationship '{name}' removed from schema. "
            f"Existing relationship rows in SQLite remain — run validate.py to audit."
        )

    print(json.dumps(result))
    sys.exit(0)


if __name__ == "__main__":
    main()
