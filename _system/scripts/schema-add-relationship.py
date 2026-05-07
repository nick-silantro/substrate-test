#!/usr/bin/env python3
"""
Add a new user-defined relationship to _system/schema-user/relationships.yaml.

Writes to the workspace schema-user/ directory, not the managed engine schema.
The four canonical categories are fixed (containment, origin, causal, associative).
New relationships must be added to one of these.

Input: JSON via stdin
  {
    "name": "rated_by",                    # snake_case, the forward relationship
    "inverse": "rates",                    # snake_case, the inverse relationship
    "category": "associative",             # containment | origin | causal | associative
    "description": "...",                  # what this relationship means
    "notes": "...",                        # optional — expected types, usage guidance
    "symmetric": false                     # optional — true if forward == inverse
  }

Output: JSON to stdout
  { "success": true, "name": "rated_by", "inverse": "rates" }
  { "success": false, "error": "Relationship 'rated_by' already exists" }

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
ENGINE_RELATIONSHIPS_YAML = ENGINE / "_system" / "schema" / "relationships.yaml"
USER_SCHEMA_DIR = WORKSPACE / "_system" / "schema-user"
USER_RELATIONSHIPS_YAML = USER_SCHEMA_DIR / "relationships.yaml"

VALID_CATEGORIES = {"containment", "origin", "causal", "associative"}


def fail(msg: str) -> None:
    print(json.dumps({"success": False, "error": msg}))
    sys.exit(1)


def validate_input(data: dict) -> str | None:
    name = data.get("name", "").strip()
    if not name:
        return "Field 'name' is required"
    if not re.match(r'^[a-z][a-z0-9_]*$', name):
        return f"Relationship name '{name}' must be snake_case"

    inverse = data.get("inverse", "").strip()
    if not inverse:
        return "Field 'inverse' is required (use same value as 'name' for symmetric relationships)"
    if not re.match(r'^[a-z][a-z0-9_]*$', inverse):
        return f"Inverse name '{inverse}' must be snake_case"

    category = data.get("category", "")
    if category not in VALID_CATEGORIES:
        return f"category must be one of: {sorted(VALID_CATEGORIES)} (got '{category}')"

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
    inverse = data["inverse"]
    category = data["category"]
    symmetric = data.get("symmetric", name == inverse)

    # Collect all names from engine schema to check for conflicts
    try:
        with open(ENGINE_RELATIONSHIPS_YAML, encoding="utf-8") as f:
            engine_existing = yaml.safe_load(f)
    except Exception as e:
        fail(f"Cannot read engine relationships.yaml: {e}")

    engine_rel_names = set()
    for cat_def in engine_existing.get("categories", {}).values():
        for rel_name, rel_def in cat_def.get("relationships", {}).items():
            engine_rel_names.add(rel_name)
            engine_rel_names.add(rel_def.get("inverse", ""))

    if name in engine_rel_names:
        fail(f"Relationship name '{name}' already exists in the managed schema")
    if inverse != name and inverse in engine_rel_names:
        fail(f"Inverse name '{inverse}' already exists in the managed schema")

    # Collect all names from user schema for conflict check
    user_data = {}
    if USER_RELATIONSHIPS_YAML.exists():
        try:
            with open(USER_RELATIONSHIPS_YAML, encoding="utf-8") as f:
                user_data = yaml.safe_load(f) or {}
        except Exception as e:
            fail(f"Cannot read schema-user/relationships.yaml: {e}")

    user_rel_names = set()
    for cat_def in user_data.get("categories", {}).values():
        for rel_name, rel_def in cat_def.get("relationships", {}).items():
            user_rel_names.add(rel_name)
            user_rel_names.add(rel_def.get("inverse", ""))

    if name in user_rel_names:
        fail(f"Relationship name '{name}' already exists in your schema extensions")
    if inverse != name and inverse in user_rel_names:
        fail(f"Inverse name '{inverse}' already exists in your schema extensions")

    # Build the relationship definition dict
    rel_def: dict = {"inverse": inverse, "description": data["description"]}
    if symmetric:
        rel_def["symmetric"] = True
    if data.get("notes", "").strip():
        rel_def["notes"] = data["notes"].strip()

    # Write to schema-user/relationships.yaml
    cats = user_data.setdefault("categories", {})
    cats.setdefault(category, {"relationships": {}})["relationships"][name] = rel_def

    USER_SCHEMA_DIR.mkdir(parents=True, exist_ok=True)
    try:
        with open(USER_RELATIONSHIPS_YAML, "w", encoding="utf-8") as f:
            yaml.dump(user_data, f, default_flow_style=False, sort_keys=False, allow_unicode=True)
    except Exception as e:
        fail(f"Failed to write schema-user/relationships.yaml: {e}")

    print(json.dumps({"success": True, "name": name, "inverse": inverse}))
    sys.exit(0)


if __name__ == "__main__":
    main()
