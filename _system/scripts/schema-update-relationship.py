#!/usr/bin/env python3
"""
Update an existing relationship in _system/schema/relationships.yaml.

Updatable fields: description, notes
Not updatable via this script: name or inverse (rename requires cascade across all
  meta.yaml files and the SQLite relationships table), category (moving between
  categories is a structural decision), symmetric (changing breaks bidirectionality).

At least one of description or notes must be provided.

No SQLite migration needed: the relationships table stores relationship names as
strings and doesn't need schema changes when descriptions or notes change.

Input: JSON via stdin
  {
    "name": "rated_by",         # required — identifies the forward relationship name
    "description": "...",       # optional — updated description
    "notes": "..."              # optional — updated usage notes
  }

Output: JSON to stdout
  { "success": true, "name": "rated_by", "updated": ["description", "notes"] }
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
RELATIONSHIPS_YAML = ENGINE / "_system" / "schema" / "relationships.yaml"


def fail(msg: str) -> None:
    print(json.dumps({"success": False, "error": msg}))
    sys.exit(1)


def update_fields_in_relationship_block(content: str, rel_name: str, updates: dict) -> tuple[str, list]:
    """
    Update fields within a relationship block.
    Relationships are indented 6 spaces; their fields are indented 8 spaces.
    Fields not found in the block are appended before the block exits.
    Returns (updated_content, list_of_updated_fields).
    """
    lines = content.split("\n")
    result = []
    in_block = False
    updated_fields = []
    pending = dict(updates)
    i = 0

    while i < len(lines):
        line = lines[i]
        stripped = line.lstrip()
        indent = len(line) - len(stripped)

        # Relationship name at 6-space indent
        if line.rstrip() == f"      {rel_name}:" or line.startswith(f"      {rel_name}: "):
            in_block = True
            result.append(line)
            i += 1
            continue

        if in_block:
            # Block exits when indent drops to <= 6 (next sibling or parent section)
            if stripped and indent <= 6:
                # Append any fields not yet found
                for field, value in list(pending.items()):
                    result.append(f'        {field}: "{value}"')
                    updated_fields.append(field)
                    del pending[field]
                in_block = False
                result.append(line)
                i += 1
                continue

            matched = False
            for field in list(pending.keys()):
                if line.startswith(f"        {field}: ") or line.rstrip() == f"        {field}:":
                    result.append(f'        {field}: "{pending[field]}"')
                    updated_fields.append(field)
                    del pending[field]
                    matched = True
                    break

            if not matched:
                result.append(line)
            i += 1
            continue

        result.append(line)
        i += 1

    # Handle block at end of file
    if pending and in_block:
        for field, value in pending.items():
            result.append(f'        {field}: "{value}"')
            updated_fields.append(field)

    return "\n".join(result), updated_fields


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

    updates = {}
    if "description" in data:
        desc = data["description"]
        if not isinstance(desc, str) or not desc.strip():
            fail("'description' must be a non-empty string")
        updates["description"] = desc.strip()

    if "notes" in data:
        notes = data["notes"]
        if not isinstance(notes, str) or not notes.strip():
            fail("'notes' must be a non-empty string")
        updates["notes"] = notes.strip()

    if not updates:
        fail("At least one of 'description' or 'notes' must be provided")

    # Load schema to verify existence
    try:
        with open(RELATIONSHIPS_YAML, encoding="utf-8") as f:
            existing = yaml.safe_load(f)
    except Exception as e:
        fail(f"Cannot read relationships.yaml: {e}")

    found_in_schema = False
    for cat_def in existing.get("categories", {}).values():
        if name in cat_def.get("relationships", {}):
            found_in_schema = True
            break

    if not found_in_schema:
        fail(
            f"Relationship '{name}' does not exist in schema. "
            "Note: only the forward relationship name is accepted (not the inverse)."
        )

    try:
        content = open(RELATIONSHIPS_YAML, encoding="utf-8").read()
    except Exception as e:
        fail(f"Cannot read relationships.yaml: {e}")

    content, updated_fields = update_fields_in_relationship_block(content, name, updates)

    if not updated_fields:
        fail(
            f"Relationship '{name}' found in schema but its block was not located in text — "
            "file may be malformed."
        )

    try:
        with open(RELATIONSHIPS_YAML, "w", encoding="utf-8") as f:
            f.write(content)
    except Exception as e:
        fail(f"Failed to write relationships.yaml: {e}")

    print(json.dumps({"success": True, "name": name, "updated": updated_fields}))
    sys.exit(0)


if __name__ == "__main__":
    main()
