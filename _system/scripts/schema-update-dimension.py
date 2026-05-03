#!/usr/bin/env python3
"""
Update an existing dimension in _system/schema/attributes.yaml.

This script is the atomic primitive for dimension updates via the Surface API
(PATCH /api/schema/dimensions/:name). It handles partial updates — only fields
included in the input are changed.

Updatable fields: values, default, question
Not updatable via this script: name (rename requires cascade), category,
grouping (moving a dimension requires structural analysis).

Common use case: adding a new status value to an existing grouping dimension
(e.g., adding "on_hold" to pipeline_status's values list).

No SQLite migration needed for these updates — the dimension column already
exists. Only the allowed values and default change.

Input: JSON via stdin
  {
    "name": "pipeline_status",          # required — identifies the dimension
    "values": ["active", "withdrawn"],  # optional — replaces the full values list
    "default": "active",               # optional — new default (must be in values)
    "question": "..."                  # optional — updated question text
  }

  At least one of values, default, or question must be provided.

Output: JSON to stdout
  { "success": true, "name": "pipeline_status", "updated": ["values", "default"] }
  { "success": false, "error": "..." }
"""

import json
import os
import sys
import yaml
from pathlib import Path

WORKSPACE = Path(os.environ.get("SUBSTRATE_PATH", Path(__file__).resolve().parents[2]))
ENGINE = Path(os.environ.get("SUBSTRATE_ENGINE_PATH", WORKSPACE))
ATTRIBUTES_YAML = ENGINE / "_system" / "schema" / "attributes.yaml"

# Protected HIP/FLAIR dimensions — their values are part of the dimensional model
# and should not be changed without careful cascade analysis.
PROTECTED_DIMENSIONS = {
    "health", "importance_strategic", "phase",
    "focus", "life_stage", "assessment", "importance_tactical", "resolution",
}


def fail(msg: str) -> None:
    print(json.dumps({"success": False, "error": msg}))
    sys.exit(1)


def update_fields_in_dimension_block(content: str, dim_name: str, updates: dict) -> tuple[str, list]:
    """
    Update specific fields within a dimension's block in attributes.yaml.
    Dimension blocks are at 2-space indent; fields are at 4-space indent.

    updates: dict of {field_name: new_value} for fields to update.
    Returns (updated_content, list_of_updated_fields).

    For fields not found in the block (e.g., adding 'question' where none existed),
    the field is appended within the block before the next sibling entry.
    """
    lines = content.split("\n")
    result = []
    in_block = False
    updated_fields = []
    pending_inserts = dict(updates)  # fields not yet found (need to be inserted)
    i = 0

    while i < len(lines):
        line = lines[i]
        stripped = line.lstrip()
        indent = len(line) - len(stripped)

        # Detect block entry
        if line.rstrip() == f"  {dim_name}:" or line.startswith(f"  {dim_name}: "):
            in_block = True
            result.append(line)
            i += 1
            continue

        if in_block:
            # Detect block exit (indent drops back to ≤ 2 on a non-blank line)
            if stripped and indent <= 2:
                # Before exiting, insert any fields that were never found in the block
                for field, value in list(pending_inserts.items()):
                    if isinstance(value, list):
                        val_str = "[" + ", ".join(f'"{v}"' for v in value) + "]"
                        result.append(f"    {field}: {val_str}")
                    else:
                        result.append(f'    {field}: "{value}"')
                    updated_fields.append(field)
                    del pending_inserts[field]
                in_block = False
                result.append(line)
                i += 1
                continue

            # Check if this line matches one of our update fields
            matched = False
            for field in list(pending_inserts.keys()):
                field_prefix = f"    {field}: "
                if line.startswith(field_prefix) or line.rstrip() == f"    {field}:":
                    new_value = pending_inserts[field]
                    if isinstance(new_value, list):
                        val_str = "[" + ", ".join(f'"{v}"' for v in new_value) + "]"
                        result.append(f"    {field}: {val_str}")
                    else:
                        result.append(f'    {field}: "{new_value}"')
                    updated_fields.append(field)
                    del pending_inserts[field]
                    matched = True
                    break

            if not matched:
                result.append(line)
            i += 1
            continue

        result.append(line)
        i += 1

    # Handle case where block was at end of file
    if pending_inserts and in_block:
        for field, value in pending_inserts.items():
            if isinstance(value, list):
                val_str = "[" + ", ".join(f'"{v}"' for v in value) + "]"
                result.append(f"    {field}: {val_str}")
            else:
                result.append(f'    {field}: "{value}"')
            updated_fields.append(field)

    return "\n".join(result), updated_fields


def main():
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

    # Collect which fields to update
    updates = {}
    if "values" in data:
        values = data["values"]
        if not isinstance(values, list) or not values:
            fail("'values' must be a non-empty list")
        updates["values"] = values
    if "default" in data:
        updates["default"] = data["default"]
    if "question" in data:
        if not data["question"].strip():
            fail("'question' must not be empty")
        updates["question"] = data["question"]

    if not updates:
        fail("At least one of 'values', 'default', or 'question' must be provided")

    # Protected dimensions warning
    if name in PROTECTED_DIMENSIONS:
        fail(
            f"Dimension '{name}' is a protected HIP/FLAIR dimension. "
            f"Modifying its values could break the dimensional model across all entities. "
            f"This requires a full cascade migration — do not use this endpoint for core dimensions."
        )

    # Load schema to validate existence and cross-check default/values
    try:
        with open(ATTRIBUTES_YAML) as f:
            existing = yaml.safe_load(f)
    except Exception as e:
        fail(f"Cannot read attributes.yaml: {e}")

    dims = existing.get("dimensions", {})
    if name not in dims:
        fail(f"Dimension '{name}' does not exist in schema")

    existing_dim = dims[name]
    existing_values = existing_dim.get("values", [])
    existing_default = existing_dim.get("default")

    # Cross-validate: the effective default must be in the effective values list
    effective_values = updates.get("values", existing_values)
    effective_default = updates.get("default", existing_default)
    if effective_default and effective_default not in effective_values:
        fail(
            f"default '{effective_default}' must be one of the values: {effective_values}. "
            f"If you're adding a new value and changing the default to it, include "
            f"both 'values' (with the new value) and 'default' in the same request."
        )

    try:
        content = open(ATTRIBUTES_YAML).read()
    except Exception as e:
        fail(f"Cannot read attributes.yaml: {e}")

    content, updated_fields = update_fields_in_dimension_block(content, name, updates)

    if not updated_fields:
        fail(f"Dimension '{name}' found in schema but its block was not found in text — file may be malformed")

    try:
        with open(ATTRIBUTES_YAML, "w") as f:
            f.write(content)
    except Exception as e:
        fail(f"Failed to write attributes.yaml: {e}")

    print(json.dumps({"success": True, "name": name, "updated": updated_fields}))
    sys.exit(0)


if __name__ == "__main__":
    main()
