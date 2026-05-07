#!/usr/bin/env python3
"""
Remove an entity type from _system/schema/types.yaml.

This script is the atomic primitive for type deletion via the Surface API
(DELETE /api/schema/types/:name). It:
  1. Validates the type exists
  2. Checks for existing entities of this type in SQLite (safety gate)
  3. Removes the type definition block from types: section
  4. Removes the type name from its grouping's types: list
  5. Prints a JSON result for the Express caller

Safety gate: if entities of this type exist in SQLite, the operation is blocked
unless force: true is passed. This prevents orphaning live data.

No SQLite migration needed: removing a type from schema doesn't change the
entities table structure. Existing entity rows remain (if force used) — they
become orphaned and will be surfaced by validate.py.

Input: JSON via stdin
  {
    "name": "partnership",   # type to remove
    "force": false           # optional — bypass entity existence check
  }

Output: JSON to stdout
  { "success": true, "name": "partnership" }
  { "success": false, "error": "Type 'partnership' has 3 entities. Pass force: true to remove anyway." }

Exit codes:
  0 = success
  1 = validation or write error
"""

import json
import os
import re
import sqlite3
import sys
import yaml
from pathlib import Path

WORKSPACE = Path(os.environ.get("SUBSTRATE_PATH", Path(__file__).resolve().parents[2]))
ENGINE = Path(os.environ.get("SUBSTRATE_ENGINE_PATH", WORKSPACE))
TYPES_YAML = ENGINE / "_system" / "schema" / "types.yaml"
DB_PATH = WORKSPACE / "_system" / "index" / "substrate.db"


def fail(msg: str) -> None:
    print(json.dumps({"success": False, "error": msg}))
    sys.exit(1)


def count_entities(type_name: str) -> int:
    """Return count of entities with this type in SQLite. Returns 0 if DB unavailable."""
    try:
        conn = sqlite3.connect(str(DB_PATH))
        row = conn.execute("SELECT COUNT(*) FROM entities WHERE type = ?", (type_name,)).fetchone()
        conn.close()
        return row[0] if row else 0
    except Exception:
        return 0


def delete_type_block(content: str, name: str) -> tuple[str, bool]:
    """
    Remove a type's definition block from the types: section.
    Blocks are at 2-space indent. Returns (updated_content, found).
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

        if line.rstrip() == f"  {name}:" or line.startswith(f"  {name}: "):
            in_block = True
            found = True
            # Remove preceding blank line if present
            if result and result[-1].strip() == "":
                result.pop()
            i += 1
            continue

        if in_block:
            # Block continues while indentation > 2
            if stripped and indent <= 2:
                in_block = False
                result.append(line)
            # else: skip this line (it's part of the deleted block)
            i += 1
            continue

        result.append(line)
        i += 1

    return "\n".join(result), found


def remove_from_types_list(content: str, grouping: str, name: str) -> str:
    """Remove type name from a grouping's types: [...] list."""

    def replacer(m):
        items_str = m.group(1)
        # Parse out individual quoted names
        items = re.findall(r'"([^"]+)"', items_str)
        items = [item for item in items if item != name]
        if items:
            return 'types: [' + ', '.join(f'"{item}"' for item in items) + ']'
        else:
            return 'types: []'

    # Find the grouping's section, then replace its types: list
    # Pattern: the types: line somewhere after the grouping name heading
    # We do a two-pass: first find the grouping block, then edit the types: line within it
    lines = content.split("\n")
    result = []
    in_grouping = False
    types_pattern = re.compile(r'\s*types:\s*\[([^\]]*)\]')

    for line in lines:
        stripped = line.lstrip()
        indent = len(line) - len(stripped)

        if line.rstrip() == f"  {grouping}:" or line.startswith(f"  {grouping}: "):
            in_grouping = True
            result.append(line)
            continue

        if in_grouping:
            # Grouping block continues while indentation > 2
            if stripped and indent <= 2:
                in_grouping = False
                result.append(line)
                continue
            # Update the types: list within this grouping
            if types_pattern.match(line):
                result.append(types_pattern.sub(replacer, line))
                continue

        result.append(line)

    return "\n".join(result)


def main():
    try:
        raw = sys.stdin.read().strip()
        if not raw:
            fail("No input — expected JSON via stdin: {\"name\": \"type-name\"}")
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        fail(f"Invalid JSON input: {e}")

    name = data.get("name", "").strip()
    if not name:
        fail("Field 'name' is required")

    force = data.get("force", False)

    # Load schema to find the type and its grouping
    try:
        with open(TYPES_YAML, encoding="utf-8") as f:
            existing = yaml.safe_load(f)
    except Exception as e:
        fail(f"Cannot read types.yaml: {e}")

    types_map = existing.get("types", {})
    if name not in types_map:
        fail(f"Type '{name}' does not exist in schema")

    grouping = types_map[name].get("grouping", "")

    # Safety gate: check for existing entities
    if not force:
        count = count_entities(name)
        if count > 0:
            fail(
                f"Type '{name}' has {count} entit{'y' if count == 1 else 'ies'} in the database. "
                f"Remove or retype those entities first, or pass force: true to remove the type anyway "
                f"(orphaned entities will be flagged by validate.py)."
            )

    # Read file as text for surgical edits
    try:
        content = open(TYPES_YAML, encoding="utf-8").read()
    except Exception as e:
        fail(f"Cannot read types.yaml: {e}")

    # Remove the type definition block
    content, found = delete_type_block(content, name)
    if not found:
        fail(f"Type '{name}' found in YAML parse but not in text — file may be malformed")

    # Remove the type from its grouping's types: list
    if grouping:
        content = remove_from_types_list(content, grouping, name)

    try:
        with open(TYPES_YAML, "w", encoding="utf-8") as f:
            f.write(content)
    except Exception as e:
        fail(f"Failed to write types.yaml: {e}")

    result: dict = {"success": True, "name": name}
    if force and count_entities(name) > 0:
        result["warning"] = f"Orphaned entities of type '{name}' remain in the database. Run validate.py to audit."

    print(json.dumps(result))
    sys.exit(0)


if __name__ == "__main__":
    main()
