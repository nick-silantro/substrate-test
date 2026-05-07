#!/usr/bin/env python3
"""
Remove a dimension from _system/schema/attributes.yaml.

This script is the atomic primitive for dimension deletion via the Surface API
(DELETE /api/schema/dimensions/:name). It:
  1. Validates the dimension exists and is not a core HIP/FLAIR dimension
  2. Removes the dimension block from the dimensions: section
  3. Prints a JSON result for the Express caller

Important limitation: SQLite columns cannot be easily dropped (SQLite < 3.35 doesn't
support ALTER TABLE DROP COLUMN). The column will remain in the entities table as an
orphaned column but will have no schema meaning. This is harmless — the column is simply
ignored by all schema-aware code after removal. You can confirm by running validate.py
(no drift errors will appear since Check 5 only detects schema → SQLite direction).

HIP/FLAIR dimensions (health, importance_strategic, phase, focus, life_stage,
assessment, importance_tactical, resolution) are protected and cannot be removed
via this script — they are structural to the dimensional model.

Input: JSON via stdin
  { "name": "pipeline_status" }

Output: JSON to stdout
  { "success": true, "name": "pipeline_status", "warning": "SQLite column remains..." }
  { "success": false, "error": "Dimension 'health' is a protected HIP/FLAIR dimension" }
"""

import json
import os
import sys
import yaml
from pathlib import Path

WORKSPACE = Path(os.environ.get("SUBSTRATE_PATH", Path(__file__).resolve().parents[2]))
ENGINE = Path(os.environ.get("SUBSTRATE_ENGINE_PATH", WORKSPACE))
ATTRIBUTES_YAML = ENGINE / "_system" / "schema" / "attributes.yaml"

# Core dimensional model — these cannot be removed
PROTECTED_DIMENSIONS = {
    # HIP (object-nature)
    "health", "importance_strategic", "phase",
    # FLAIR (work-nature)
    "focus", "life_stage", "assessment", "importance_tactical", "resolution",
}


def fail(msg: str) -> None:
    print(json.dumps({"success": False, "error": msg}))
    sys.exit(1)


def delete_dimension_block(content: str, name: str) -> tuple[str, bool]:
    """
    Remove a dimension's block from attributes.yaml.
    Dimension entries are at 2-space indent (under the top-level dimensions: key).
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

        if line.rstrip() == f"  {name}:" or line.startswith(f"  {name}: "):
            in_block = True
            found = True
            # Remove preceding blank line if present
            if result and result[-1].strip() == "":
                result.pop()
            i += 1
            continue

        if in_block:
            # Block ends when we hit something at ≤ 2-space indent
            if stripped and indent <= 2:
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
            fail("No input — expected JSON via stdin: {\"name\": \"dimension-name\"}")
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        fail(f"Invalid JSON input: {e}")

    name = data.get("name", "").strip()
    if not name:
        fail("Field 'name' is required")

    # Protect core dimensions
    if name in PROTECTED_DIMENSIONS:
        fail(
            f"Dimension '{name}' is a protected HIP/FLAIR dimension and cannot be removed. "
            f"Protected dimensions are structural to the dimensional status model."
        )

    # Load schema to verify existence
    try:
        with open(ATTRIBUTES_YAML, encoding="utf-8") as f:
            existing = yaml.safe_load(f)
    except Exception as e:
        fail(f"Cannot read attributes.yaml: {e}")

    dims = existing.get("dimensions", {})
    if name not in dims:
        fail(f"Dimension '{name}' does not exist in schema")

    # Read as text for surgical edit
    try:
        content = open(ATTRIBUTES_YAML, encoding="utf-8").read()
    except Exception as e:
        fail(f"Cannot read attributes.yaml: {e}")

    content, found = delete_dimension_block(content, name)
    if not found:
        fail(f"Dimension '{name}' found in YAML parse but not in text — file may be malformed")

    try:
        with open(ATTRIBUTES_YAML, "w", encoding="utf-8") as f:
            f.write(content)
    except Exception as e:
        fail(f"Failed to write attributes.yaml: {e}")

    print(json.dumps({
        "success": True,
        "name": name,
        "warning": (
            f"Dimension '{name}' removed from schema. "
            f"The SQLite column '{name}' remains in the entities table as an orphaned column — "
            f"this is harmless but can be cleaned up by recreating the database from scratch."
        ),
    }))
    sys.exit(0)


if __name__ == "__main__":
    main()
