#!/usr/bin/env python3
"""
Remove a grouping from _system/schema/types.yaml.

This script is the atomic primitive for grouping deletion via the Surface API
(DELETE /api/schema/groupings/:name). It:
  1. Validates the grouping exists
  2. Blocks if the grouping still has types assigned to it
  3. Removes the grouping block from the groupings: section
  4. Prints a JSON result for the Express caller

Safety gate: a grouping with types cannot be removed. Remove or retype all
member types first (using DELETE /api/schema/types/:name or moving types to
another grouping).

No SQLite migration needed.

Input: JSON via stdin
  { "name": "opportunities" }

Output: JSON to stdout
  { "success": true, "name": "opportunities" }
  { "success": false, "error": "Grouping 'opportunities' still has 3 types: [...]" }
"""

import json
import os
import sys
import yaml
from pathlib import Path

WORKSPACE = Path(os.environ.get("SUBSTRATE_PATH", Path(__file__).resolve().parents[2]))
ENGINE = Path(os.environ.get("SUBSTRATE_ENGINE_PATH", WORKSPACE))
TYPES_YAML = ENGINE / "_system" / "schema" / "types.yaml"


def fail(msg: str) -> None:
    print(json.dumps({"success": False, "error": msg}))
    sys.exit(1)


def delete_grouping_block(content: str, name: str) -> tuple[str, bool]:
    """
    Remove a grouping's block from the groupings: section.
    Grouping entries are at 2-space indent.
    Returns (updated_content, found).
    """
    lines = content.split("\n")
    result = []
    in_groupings_section = False
    in_block = False
    found = False
    i = 0

    while i < len(lines):
        line = lines[i]
        stripped = line.lstrip()
        indent = len(line) - len(stripped)

        if line.startswith("groupings:") and indent == 0:
            in_groupings_section = True
            result.append(line)
            i += 1
            continue

        if in_groupings_section and indent == 0 and stripped and not line.startswith("groupings:"):
            in_groupings_section = False

        if in_groupings_section and (
            line.rstrip() == f"  {name}:" or line.startswith(f"  {name}: ")
        ):
            in_block = True
            found = True
            if result and result[-1].strip() == "":
                result.pop()
            i += 1
            continue

        if in_block:
            if stripped and indent <= 2:
                in_block = False
                result.append(line)
            i += 1
            continue

        result.append(line)
        i += 1

    return "\n".join(result), found


def main():
    try:
        raw = sys.stdin.read().strip()
        if not raw:
            fail("No input — expected JSON via stdin: {\"name\": \"grouping-name\"}")
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        fail(f"Invalid JSON input: {e}")

    name = data.get("name", "").strip()
    if not name:
        fail("Field 'name' is required")

    try:
        with open(TYPES_YAML, encoding="utf-8") as f:
            existing = yaml.safe_load(f)
    except Exception as e:
        fail(f"Cannot read types.yaml: {e}")

    groupings = existing.get("groupings", {})
    if name not in groupings:
        fail(f"Grouping '{name}' does not exist in schema")

    grouping_data = groupings[name]
    member_types = grouping_data.get("types", [])
    if member_types:
        fail(
            f"Grouping '{name}' still has {len(member_types)} type(s): {member_types}. "
            f"Remove or move all member types before deleting the grouping."
        )

    try:
        content = open(TYPES_YAML, encoding="utf-8").read()
    except Exception as e:
        fail(f"Cannot read types.yaml: {e}")

    content, found = delete_grouping_block(content, name)
    if not found:
        fail(f"Grouping '{name}' found in YAML parse but not in text — file may be malformed")

    try:
        with open(TYPES_YAML, "w", encoding="utf-8") as f:
            f.write(content)
    except Exception as e:
        fail(f"Failed to write types.yaml: {e}")

    print(json.dumps({"success": True, "name": name}))
    sys.exit(0)


if __name__ == "__main__":
    main()
