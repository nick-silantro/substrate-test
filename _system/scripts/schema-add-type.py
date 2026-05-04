#!/usr/bin/env python3
"""
Add a new user-defined entity type to _system/schema-user/types.yaml.

Writes to the workspace schema-user/ directory, not the managed engine schema.
The managed schema (engine) is read-only for end users.

Input: JSON via stdin
  {
    "name": "partnership",         # kebab-case, unique across all types
    "grouping": "opportunities",   # must be an existing grouping name
    "description": "..."           # human-readable description of this type
  }

Output: JSON to stdout
  { "success": true, "name": "partnership" }
  { "success": false, "error": "Type 'partnership' already exists" }

Exit codes:
  0 = success
  1 = validation or write error
  2 = usage error
"""

import json
import os
import re
import sys
import yaml
from pathlib import Path

WORKSPACE = Path(os.environ.get("SUBSTRATE_PATH", Path(__file__).resolve().parents[2]))
ENGINE = Path(os.environ.get("SUBSTRATE_ENGINE_PATH", WORKSPACE))
ENGINE_TYPES_YAML = ENGINE / "_system" / "schema" / "types.yaml"
USER_SCHEMA_DIR = WORKSPACE / "_system" / "schema-user"
USER_TYPES_YAML = USER_SCHEMA_DIR / "types.yaml"


def fail(msg: str) -> None:
    print(json.dumps({"success": False, "error": msg}))
    sys.exit(1)


def validate_input(data: dict) -> str | None:
    name = data.get("name", "").strip()
    if not name:
        return "Field 'name' is required"
    # Types use kebab-case (e.g., job-opportunity, video-file)
    if not re.match(r'^[a-z][a-z0-9-]*$', name):
        return f"Type name '{name}' must be kebab-case (lowercase letters, digits, hyphens)"
    if not data.get("grouping", "").strip():
        return "Field 'grouping' is required"
    if not data.get("description", "").strip():
        return "Field 'description' is required"
    return None


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--help":
        print(__doc__)
        sys.exit(0)

    try:
        raw = sys.stdin.read().strip()
        if not raw:
            fail("No input provided — expected JSON via stdin")
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        fail(f"Invalid JSON input: {e}")

    err = validate_input(data)
    if err:
        fail(err)

    name = data["name"]
    grouping = data["grouping"]
    description = data["description"]

    # Load engine schema: check for conflicts and validate the grouping exists
    try:
        with open(ENGINE_TYPES_YAML) as f:
            engine_existing = yaml.safe_load(f)
    except Exception as e:
        fail(f"Cannot read engine types.yaml: {e}")

    if name in engine_existing.get("types", {}):
        fail(f"Type '{name}' already exists in the managed schema")

    # Load user schema for additional conflict check
    user_data = {}
    if USER_TYPES_YAML.exists():
        try:
            with open(USER_TYPES_YAML) as f:
                user_data = yaml.safe_load(f) or {}
        except Exception as e:
            fail(f"Cannot read schema-user/types.yaml: {e}")

    if name in user_data.get("types", {}):
        fail(f"Type '{name}' already exists in your schema extensions")

    # Validate the grouping exists in engine OR user schema
    engine_groupings = engine_existing.get("groupings", {})
    user_groupings = user_data.get("groupings", {})
    all_groupings = {**engine_groupings, **user_groupings}
    if grouping not in all_groupings:
        fail(
            f"Grouping '{grouping}' does not exist. "
            f"Known groupings: {sorted(all_groupings.keys())}. "
            f"Create a new grouping first with: substrate schema add grouping"
        )

    # Write to schema-user/types.yaml
    user_types = user_data.setdefault("types", {})
    user_types[name] = {"grouping": grouping, "description": description}

    # If the grouping is user-defined, update its types list too
    if grouping in user_groupings:
        g_types = user_data["groupings"][grouping].setdefault("types", [])
        if name not in g_types:
            g_types.append(name)

    USER_SCHEMA_DIR.mkdir(parents=True, exist_ok=True)
    try:
        with open(USER_TYPES_YAML, "w") as f:
            yaml.dump(user_data, f, default_flow_style=False, sort_keys=False, allow_unicode=True)
    except Exception as e:
        fail(f"Failed to write schema-user/types.yaml: {e}")

    # Create the entity type folder so scripts can find it
    entity_dir = WORKSPACE / "entities" / name
    try:
        entity_dir.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        # Non-fatal — the folder might already exist or creation might fail on permissions
        print(json.dumps({
            "success": True,
            "name": name,
            "warning": f"Type added to schema but entity folder creation failed: {e}"
        }))
        sys.exit(0)

    print(json.dumps({"success": True, "name": name}))
    sys.exit(0)


if __name__ == "__main__":
    main()
