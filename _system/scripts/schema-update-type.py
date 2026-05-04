#!/usr/bin/env python3
"""
Update an existing entity type in _system/schema/types.yaml.

Updatable fields: description
Not updatable via this script: name (rename requires cascade across all entities),
  grouping (moving a type requires structural migration of all entities of that type).

Input: JSON via stdin
  {
    "name": "partnership",     # required — identifies the type
    "description": "..."       # required — updated description
  }

Output: JSON to stdout
  { "success": true, "name": "partnership", "updated": ["description"] }
  { "success": false, "error": "..." }

Exit codes:
  0 = success
  1 = validation or write error
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


def update_field_in_block(content: str, block_name: str, field: str, new_value: str) -> tuple[str, bool]:
    """
    Replace a field value within a named block (2-space name indent, 4-space field indent).
    Operates on the content string as passed — caller is responsible for restricting
    to the right section if the same block_name could appear elsewhere.
    Returns (updated_content, was_found).
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

        if line.rstrip() == f"  {block_name}:" or line.startswith(f"  {block_name}: "):
            in_block = True
            result.append(line)
            i += 1
            continue

        if in_block:
            if stripped and indent <= 2:
                in_block = False
                result.append(line)
                i += 1
                continue

            if not found and (
                line.startswith(f"    {field}: ") or line.rstrip() == f"    {field}:"
            ):
                result.append(f'    {field}: "{new_value}"')
                found = True
                i += 1
                continue

        result.append(line)
        i += 1

    return "\n".join(result), found


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--help":
        print(__doc__)
        sys.exit(0)

    try:
        raw = sys.stdin.read().strip()
        if not raw:
            fail("No input — expected JSON via stdin")
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        fail(f"Invalid JSON input: {e}")

    name = data.get("name", "").strip()
    if not name:
        fail("Field 'name' is required")

    description = data.get("description", "").strip()
    if not description:
        fail("Field 'description' is required and must not be empty")

    # Load schema to verify existence
    try:
        with open(TYPES_YAML) as f:
            existing = yaml.safe_load(f)
    except Exception as e:
        fail(f"Cannot read types.yaml: {e}")

    if name not in existing.get("types", {}):
        fail(f"Type '{name}' does not exist in schema")

    try:
        content = open(TYPES_YAML).read()
    except Exception as e:
        fail(f"Cannot read types.yaml: {e}")

    # Restrict surgery to the types: section (before groupings:) to avoid
    # matching a grouping with the same name.
    groupings_marker = "\ngroupings:"
    if groupings_marker not in content:
        fail("Could not find 'groupings:' section in types.yaml — file structure unexpected")

    split_pos = content.index(groupings_marker)
    types_section = content[:split_pos]
    groupings_section = content[split_pos:]

    updated_types, found = update_field_in_block(types_section, name, "description", description)

    if not found:
        fail(
            f"Could not locate description field in type '{name}' block — "
            "file may be malformed. Verify types.yaml structure."
        )

    try:
        with open(TYPES_YAML, "w") as f:
            f.write(updated_types + groupings_section)
    except Exception as e:
        fail(f"Failed to write types.yaml: {e}")

    print(json.dumps({"success": True, "name": name, "updated": ["description"]}))
    sys.exit(0)


if __name__ == "__main__":
    main()
