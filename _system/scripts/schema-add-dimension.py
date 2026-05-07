#!/usr/bin/env python3
"""
Add a new dimension to _system/schema/attributes.yaml and sync SQLite.

This script is the atomic primitive for schema mutation via the Surface API
(POST /api/schema/dimensions). It handles the full operation:
  1. Validate the new dimension definition
  2. Check for name conflicts in the existing schema
  3. Append the new dimension to attributes.yaml (preserving all existing content)
  4. Run migrate-to-sqlite.py to add the new SQLite column
  5. Print a JSON result for the Express caller

Why a script rather than JS YAML manipulation in the Express route:
  - Python's yaml library is already the canonical parser for this codebase
  - Append-only write preserves all existing YAML comments (js-yaml would strip them)
  - Keeps the mutation logic testable independently of the web server

Input: JSON via stdin
  {
    "name": "pipeline_status",          # snake_case, must end in _status for grouping dims
    "category": "grouping",             # "HIP", "FLAIR", or "grouping"
    "grouping": "opportunities",        # required if category == "grouping"
    "question": "Where is this?",       # what the dimension answers
    "values": ["a", "b", "c"],          # allowed states (non-empty)
    "default": "a",                     # must be in values
    "access": {                         # access declaration (see attributes.yaml conventions)
      "exclusive": true,
      "preferred": { "groupings": ["opportunities"] },
      "forbidden": { "types": ["receipt"] }
    }
  }

Output: JSON to stdout
  { "success": true, "name": "pipeline_status" }
  { "success": false, "error": "Dimension 'pipeline_status' already exists" }

Exit codes:
  0 = success
  1 = validation or write error
  2 = usage error
"""

import json
import os
import sys
import subprocess
import yaml
from pathlib import Path

WORKSPACE = Path(os.environ.get("SUBSTRATE_PATH", Path(__file__).resolve().parents[2]))
ENGINE = Path(os.environ.get("SUBSTRATE_ENGINE_PATH", WORKSPACE))
ATTRIBUTES_YAML = ENGINE / "_system" / "schema" / "attributes.yaml"
SCRIPTS_DIR = ENGINE / "_system" / "scripts"


def fail(msg: str) -> None:
    print(json.dumps({"success": False, "error": msg}))
    sys.exit(1)


def validate_input(data: dict) -> str | None:
    """Return error message if input is invalid, None if valid."""
    name = data.get("name", "").strip()
    if not name:
        return "Field 'name' is required"
    if not name.replace("_", "").isalnum():
        return f"Name '{name}' must be snake_case alphanumeric"
    if " " in name:
        return f"Name '{name}' must not contain spaces — use snake_case"

    category = data.get("category", "")
    if category not in ("HIP", "FLAIR", "grouping"):
        return f"category must be one of: HIP, FLAIR, grouping (got '{category}')"

    if category == "grouping":
        if not data.get("grouping"):
            return "Field 'grouping' is required when category is 'grouping'"
        # Naming rule: grouping-level dimensions must use the _status suffix
        # (see schema-evolution skill and attributes.yaml conventions)
        if not name.endswith("_status"):
            return (
                f"Grouping-level dimensions must use the '_status' suffix "
                f"(e.g., '{name}_status'). This prevents namespace collisions with "
                f"HIP/FLAIR dimension names and is enforced by schema convention."
            )

    values = data.get("values", [])
    if not values or not isinstance(values, list):
        return "Field 'values' must be a non-empty list"

    default = data.get("default")
    if default and default not in values:
        return f"default '{default}' must be one of: {values}"

    if not data.get("question"):
        return "Field 'question' is required"

    return None


def build_dimension_yaml(data: dict) -> str:
    """
    Build the YAML text block for the new dimension, matching the formatting
    conventions used throughout attributes.yaml. Indented two spaces (dimensions
    are nested under the top-level 'dimensions:' key).
    """
    name = data["name"]
    lines = [f"  {name}:"]

    if data.get("category"):
        lines.append(f'    category: "{data["category"]}"')
    if data.get("grouping"):
        lines.append(f'    grouping: "{data["grouping"]}"')
    if data.get("question"):
        lines.append(f'    question: "{data["question"]}"')

    # Values as inline YAML array to match existing convention
    values_str = "[" + ", ".join(f'"{v}"' for v in data["values"]) + "]"
    lines.append(f"    values: {values_str}")

    if data.get("default"):
        lines.append(f'    default: "{data["default"]}"')

    # Access block
    access = data.get("access")
    if access:
        lines.append("    access:")
        if "exclusive" in access:
            lines.append(f'      exclusive: {"true" if access["exclusive"] else "false"}')
        for key in ("required", "preferred", "forbidden"):
            if key in access:
                lines.append(f"      {key}:")
                constraint = access[key]
                for scope in ("types", "natures", "groupings"):
                    if scope in constraint:
                        items = "[" + ", ".join(f'"{i}"' for i in constraint[scope]) + "]"
                        lines.append(f"        {scope}: {items}")

    return "\n".join(lines)


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--help":
        print(__doc__)
        sys.exit(0)

    # Read JSON from stdin
    try:
        raw = sys.stdin.read().strip()
        if not raw:
            fail("No input provided — expected JSON via stdin")
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        fail(f"Invalid JSON input: {e}")

    # Validate input structure
    err = validate_input(data)
    if err:
        fail(err)

    name = data["name"]

    # Load existing schema to check for name conflicts
    try:
        with open(ATTRIBUTES_YAML, encoding="utf-8") as f:
            existing = yaml.safe_load(f)
    except Exception as e:
        fail(f"Cannot read attributes.yaml: {e}")

    existing_dims = existing.get("dimensions", {})
    if name in existing_dims:
        fail(f"Dimension '{name}' already exists in schema")

    # Build the YAML block for the new dimension
    new_block = build_dimension_yaml(data)

    # Append to attributes.yaml — preserving all existing content and comments.
    # dimensions: is always the last top-level section in attributes.yaml, so
    # a simple append is safe. We ensure exactly one blank line between entries.
    try:
        with open(ATTRIBUTES_YAML, "r", encoding="utf-8") as f:
            content = f.read()

        # Normalize: strip trailing whitespace/newlines, then append with blank separator
        content = content.rstrip()
        updated = content + "\n\n" + new_block + "\n"

        with open(ATTRIBUTES_YAML, "w", encoding="utf-8") as f:
            f.write(updated)
    except Exception as e:
        fail(f"Failed to write attributes.yaml: {e}")

    # Run migrate-to-sqlite.py to add the new column to the SQLite entities table.
    # This is the step that was previously manual — the script adds the column via
    # ALTER TABLE and rebuilds entity rows from disk.
    try:
        result = subprocess.run(
            [sys.executable, str(SCRIPTS_DIR / "migrate-to-sqlite.py"), str(WORKSPACE)],
            capture_output=True,
            text=True,
            cwd=str(WORKSPACE),
        )
        if result.returncode != 0:
            # Migration failed — the dimension was written to YAML but SQLite is out of sync.
            # Report this clearly so the caller knows the partial state.
            fail(
                f"Dimension '{name}' written to attributes.yaml but SQLite migration failed: "
                f"{result.stderr.strip()}. Run migrate-to-sqlite.py manually to complete."
            )
    except Exception as e:
        fail(f"Failed to run migrate-to-sqlite.py: {e}")

    print(json.dumps({"success": True, "name": name}))
    sys.exit(0)


if __name__ == "__main__":
    main()
