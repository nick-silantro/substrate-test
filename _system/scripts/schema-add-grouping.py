#!/usr/bin/env python3
"""
Add a new user-defined grouping to _system/schema-user/types.yaml.

Writes to the workspace schema-user/ directory, not the managed engine schema.
Groupings should be rare — only create one when you're introducing a category
that doesn't fit any existing managed grouping.

Input: JSON via stdin
  {
    "name": "contracts",                   # snake_case or kebab-case, unique
    "description": "...",                  # what this grouping represents
    "nature": ["object"],                  # exactly one of: "work" or "object"
    "behavior": {                          # optional behavioral flags
      "non_duplicable": false,
      "inline_display": false,
      "contextually_bound": false
    }
  }

Output: JSON to stdout
  { "success": true, "name": "contracts" }
  { "success": false, "error": "Grouping 'contracts' already exists" }

Exit codes:
  0 = success
  1 = validation or write error
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

VALID_NATURES = {"work", "object"}


def fail(msg: str) -> None:
    print(json.dumps({"success": False, "error": msg}))
    sys.exit(1)


def validate_input(data: dict) -> str | None:
    name = data.get("name", "").strip()
    if not name:
        return "Field 'name' is required"
    if not re.match(r'^[a-z][a-z0-9_-]*$', name):
        return f"Grouping name '{name}' must be lowercase with underscores or hyphens"

    nature = data.get("nature", [])
    if not nature or not isinstance(nature, list):
        return "Field 'nature' must be a non-empty list (allowed values: 'work', 'object')"
    if len(nature) > 1:
        return f"Grouping nature must be exactly one value: 'work' or 'object'. Dual-nature groupings are not permitted."
    invalid = [n for n in nature if n not in VALID_NATURES]
    if invalid:
        return f"Invalid nature values: {invalid}. Allowed: {sorted(VALID_NATURES)}"

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
    nature = data["nature"]
    description = data["description"]
    behavior = data.get("behavior", {})

    # Check conflicts in engine schema
    try:
        with open(ENGINE_TYPES_YAML) as f:
            engine_existing = yaml.safe_load(f)
    except Exception as e:
        fail(f"Cannot read engine types.yaml: {e}")

    if name in engine_existing.get("groupings", {}):
        fail(f"Grouping '{name}' already exists in the managed schema")

    # Check conflicts in user schema
    user_data = {}
    if USER_TYPES_YAML.exists():
        try:
            with open(USER_TYPES_YAML) as f:
                user_data = yaml.safe_load(f) or {}
        except Exception as e:
            fail(f"Cannot read schema-user/types.yaml: {e}")

    if name in user_data.get("groupings", {}):
        fail(f"Grouping '{name}' already exists in your schema extensions")

    # Build grouping definition dict
    g_def = {"description": description, "nature": nature, "types": []}
    if behavior and any(behavior.values()):
        g_def["behavior"] = {k: v for k, v in behavior.items() if v}

    user_data.setdefault("groupings", {})[name] = g_def

    USER_SCHEMA_DIR.mkdir(parents=True, exist_ok=True)
    try:
        with open(USER_TYPES_YAML, "w") as f:
            yaml.dump(user_data, f, default_flow_style=False, sort_keys=False, allow_unicode=True)
    except Exception as e:
        fail(f"Failed to write schema-user/types.yaml: {e}")

    print(json.dumps({"success": True, "name": name}))
    sys.exit(0)


if __name__ == "__main__":
    main()
