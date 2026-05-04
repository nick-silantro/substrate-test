#!/usr/bin/env python3
"""
Update an existing attribute in _system/schema/attributes.yaml.

Updatable fields: description, values (enum only), required
Not updatable via this script: name (rename requires cascade), data_type (would
  invalidate stored data), storage (changing tier requires SQLite migration),
  access (complex nested structure — use delete + add to replace access declarations).

At least one of description, values, or required must be provided.

No SQLite migration is needed for description/values/required changes — these
affect schema metadata only, not column definitions.

Input: JSON via stdin
  {
    "name": "listing_url",            # required — identifies the attribute
    "description": "...",             # optional — updated description
    "values": ["a", "b", "c"],        # optional — new values list (enum only; replaces existing)
    "required": true                  # optional — updated required flag (boolean)
  }

Output: JSON to stdout
  { "success": true, "name": "listing_url", "updated": ["description"] }
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
ATTRIBUTES_YAML = ENGINE / "_system" / "schema" / "attributes.yaml"


def fail(msg: str) -> None:
    print(json.dumps({"success": False, "error": msg}))
    sys.exit(1)


def update_fields_in_block(content: str, block_name: str, updates: dict) -> tuple[str, list]:
    """
    Update specific fields within a named YAML block (2-space name indent, 4-space fields).
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

        if line.rstrip() == f"  {block_name}:" or line.startswith(f"  {block_name}: "):
            in_block = True
            result.append(line)
            i += 1
            continue

        if in_block:
            if stripped and indent <= 2:
                # Insert any fields not yet found before the block closes
                for field, value in list(pending.items()):
                    result.append(_format_field(field, value))
                    updated_fields.append(field)
                    del pending[field]
                in_block = False
                result.append(line)
                i += 1
                continue

            matched = False
            for field in list(pending.keys()):
                if line.startswith(f"    {field}: ") or line.rstrip() == f"    {field}:":
                    result.append(_format_field(field, pending[field]))
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
            result.append(_format_field(field, value))
            updated_fields.append(field)

    return "\n".join(result), updated_fields


def _format_field(field: str, value) -> str:
    if isinstance(value, list):
        val_str = "[" + ", ".join(f'"{v}"' for v in value) + "]"
        return f"    {field}: {val_str}"
    elif isinstance(value, bool):
        return f"    {field}: {'true' if value else 'false'}"
    else:
        return f'    {field}: "{value}"'


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

    if "values" in data:
        values = data["values"]
        if not isinstance(values, list) or not values:
            fail("'values' must be a non-empty list")
        updates["values"] = values

    if "required" in data:
        req = data["required"]
        if not isinstance(req, bool):
            fail("'required' must be a boolean (true or false)")
        updates["required"] = req

    if not updates:
        fail("At least one of 'description', 'values', or 'required' must be provided")

    # Load schema to verify existence and validate values/data_type consistency
    try:
        with open(ATTRIBUTES_YAML) as f:
            existing = yaml.safe_load(f)
    except Exception as e:
        fail(f"Cannot read attributes.yaml: {e}")

    # Attributes live in the 'attributes' (or 'fields') section
    attrs = existing.get("attributes", existing.get("fields", {}))
    if name not in attrs:
        fail(f"Attribute '{name}' does not exist in schema (or is a universal/block/dimension)")

    existing_attr = attrs[name]

    # values update only makes sense for enum data_type
    if "values" in updates and existing_attr.get("data_type") != "enum":
        fail(
            f"Cannot update 'values' on attribute '{name}': "
            f"data_type is '{existing_attr.get('data_type')}', not 'enum'. "
            "Only enum attributes have a values list."
        )

    try:
        content = open(ATTRIBUTES_YAML).read()
    except Exception as e:
        fail(f"Cannot read attributes.yaml: {e}")

    content, updated_fields = update_fields_in_block(content, name, updates)

    if not updated_fields:
        fail(
            f"Attribute '{name}' found in schema but its block was not located in text — "
            "file may be malformed."
        )

    try:
        with open(ATTRIBUTES_YAML, "w") as f:
            f.write(content)
    except Exception as e:
        fail(f"Failed to write attributes.yaml: {e}")

    print(json.dumps({"success": True, "name": name, "updated": updated_fields}))
    sys.exit(0)


if __name__ == "__main__":
    main()
