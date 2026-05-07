#!/usr/bin/env python3
"""
Remove an attribute from _system/schema/attributes.yaml.

This script is the atomic primitive for attribute deletion via the Surface API
(DELETE /api/schema/attributes/:name). It:
  1. Validates the attribute exists in the attributes: section
  2. Removes the attribute block
  3. Prints a JSON result for the Express caller

Attributes in the `universal:` block (name, uuid, type, etc.) are protected
and cannot be removed via this script.

If the attribute had a SQLite column (storage: indexed or storage: column —
which is the default), its column remains as an orphaned column after deletion
(same limitation as dimension deletion). This is harmless.

Input: JSON via stdin
  { "name": "job_title" }

Output: JSON to stdout
  { "success": true, "name": "job_title" }
  { "success": false, "error": "Attribute 'name' is in the universal block and cannot be removed" }
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


def delete_attribute_block(content: str, name: str) -> tuple[str, bool]:
    """
    Remove an attribute's block from the attributes: section of attributes.yaml.
    Attribute entries under attributes: are at 2-space indent.
    Returns (updated_content, found).
    """
    lines = content.split("\n")
    result = []
    in_attributes_section = False
    in_block = False
    found = False
    i = 0

    while i < len(lines):
        line = lines[i]
        stripped = line.lstrip()
        indent = len(line) - len(stripped)

        # Track which top-level section we're in
        if line.startswith("attributes:") and indent == 0:
            in_attributes_section = True
            result.append(line)
            i += 1
            continue

        if in_attributes_section and indent == 0 and stripped and not line.startswith("attributes:"):
            # Entered a different top-level section
            in_attributes_section = False

        # Look for the attribute block within the attributes: section
        if in_attributes_section and (
            line.rstrip() == f"  {name}:" or line.startswith(f"  {name}: ")
        ):
            in_block = True
            found = True
            # Remove preceding blank line if present
            if result and result[-1].strip() == "":
                result.pop()
            i += 1
            continue

        if in_block:
            # Block ends when indentation drops to ≤ 2
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
            fail("No input — expected JSON via stdin: {\"name\": \"attribute-name\"}")
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        fail(f"Invalid JSON input: {e}")

    name = data.get("name", "").strip()
    if not name:
        fail("Field 'name' is required")

    # Load schema to find the attribute and check it's not universal
    try:
        with open(ATTRIBUTES_YAML, encoding="utf-8") as f:
            existing = yaml.safe_load(f)
    except Exception as e:
        fail(f"Cannot read attributes.yaml: {e}")

    universal = existing.get("universal", {})
    if name in universal:
        fail(
            f"Attribute '{name}' is in the universal: block and cannot be removed. "
            f"Universal attributes (name, uuid, type, etc.) are structural to all entities."
        )

    attributes = existing.get("attributes", {})
    if name not in attributes:
        fail(f"Attribute '{name}' does not exist in the attributes: section")

    # Check if it had a SQLite column — if so, warn about orphaned column.
    # Under the new storage-tier model: indexed and "column" tiers both have
    # a column; "file_only" does not. Legacy `index_in_sqlite: true` implies indexed.
    attr_data = attributes[name]
    _storage = attr_data.get("storage")
    if _storage in ("indexed", "column"):
        had_column = True
    elif _storage == "file_only":
        had_column = False
    else:
        # No explicit storage tier. Default is "indexed" → column present.
        # Legacy flag: index_in_sqlite: true means indexed; false is ignored.
        had_column = True

    try:
        content = open(ATTRIBUTES_YAML, encoding="utf-8").read()
    except Exception as e:
        fail(f"Cannot read attributes.yaml: {e}")

    content, found = delete_attribute_block(content, name)
    if not found:
        fail(f"Attribute '{name}' found in YAML parse but not in text — file may be malformed")

    try:
        with open(ATTRIBUTES_YAML, "w", encoding="utf-8") as f:
            f.write(content)
    except Exception as e:
        fail(f"Failed to write attributes.yaml: {e}")

    result: dict = {"success": True, "name": name}
    if had_column:
        result["warning"] = (
            f"Attribute '{name}' had a SQLite column. "
            f"The column remains as an orphan — harmless but cannot be auto-dropped."
        )

    print(json.dumps(result))
    sys.exit(0)


if __name__ == "__main__":
    main()
