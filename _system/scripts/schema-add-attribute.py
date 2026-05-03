#!/usr/bin/env python3
"""
Add a new user-defined attribute to _system/schema-user/attributes.yaml.

Writes to the workspace schema-user/ directory, not the managed engine schema.
Attributes in the attributes: section are type-specific fields that live in
entity meta.yaml files — things like url, job_title, asset_path, config_path.

Distinction from dimensions: dimensions are status axes (focus, life_stage, etc.)
that drive agent behavior and workflow. Attributes are data fields — they store
values but don't drive state machine transitions.

SQLite behavior: Every attribute declared here gets a SQLite column by default
(storage: "indexed"). The tier you choose controls what SQLite gets:
  - "indexed"   — column + CREATE INDEX. Default. Use for filter/sort/group fields.
  - "column"    — column, no index. Use for JSON blobs or long-form sentences
                   where exact-match indexing is waste but you still want SQL access.
  - "file_only" — meta.yaml only. Use for multi-paragraph prose content fields.
A schema migration runs automatically when the attribute has a column (indexed or column).

Input: JSON via stdin
  {
    "name": "listing_url",              # snake_case, unique across attributes
    "data_type": "url",                 # string | url | text | boolean | integer |
                                        # float | date | enum | list | uuid | block
    "description": "...",              # what this attribute stores
    "required": false,                 # whether entities of preferred types must have it
    "values": ["a", "b"],              # only for data_type: enum
    "list": true,                      # attribute holds multiple values (YAML list)
    "max_items": 2,                    # max number of list values (only for list attrs)
    "access": {                        # access declaration
      "exclusive": true,
      "preferred": { "types": ["job-opportunity"] },
      "forbidden": { "natures": ["work"] }
    },
    "storage": "indexed"               # "indexed" | "column" | "file_only"
                                       # Default: "indexed"
    # Legacy: "index_in_sqlite": true maps to "indexed". Deprecated.
  }

Output: JSON to stdout
  { "success": true, "name": "listing_url" }
  { "success": false, "error": "Attribute 'listing_url' already exists" }

Exit codes:
  0 = success
  1 = validation or write error
"""

import json
import os
import re
import subprocess
import sys
import yaml
from pathlib import Path

WORKSPACE = Path(os.environ.get("SUBSTRATE_PATH", Path(__file__).resolve().parents[2]))
ENGINE = Path(os.environ.get("SUBSTRATE_ENGINE_PATH", WORKSPACE))
ENGINE_ATTRIBUTES_YAML = ENGINE / "_system" / "schema" / "attributes.yaml"
USER_SCHEMA_DIR = WORKSPACE / "_system" / "schema-user"
USER_ATTRIBUTES_YAML = USER_SCHEMA_DIR / "attributes.yaml"
SCRIPTS_DIR = ENGINE / "_system" / "scripts"

VALID_DATA_TYPES = {
    "string", "url", "text", "boolean", "integer", "float",
    "date", "enum", "list", "uuid", "block"
}


def fail(msg: str) -> None:
    print(json.dumps({"success": False, "error": msg}))
    sys.exit(1)


def validate_input(data: dict) -> str | None:
    name = data.get("name", "").strip()
    if not name:
        return "Field 'name' is required"
    if not re.match(r'^[a-z][a-z0-9_]*$', name):
        return f"Attribute name '{name}' must be snake_case"

    data_type = data.get("data_type", "")
    if data_type not in VALID_DATA_TYPES:
        return f"data_type must be one of: {sorted(VALID_DATA_TYPES)} (got '{data_type}')"

    if data_type == "enum" and not data.get("values"):
        return "Field 'values' is required for data_type: enum"

    if not data.get("description", "").strip():
        return "Field 'description' is required"

    storage = data.get("storage")
    if storage is not None and storage not in ("indexed", "column", "file_only"):
        return f"storage must be 'indexed', 'column', or 'file_only' (got {storage!r})"

    # Reject both fields set at once — forces author to pick the new vocabulary.
    # Legacy `index_in_sqlite: true` (alone) still maps to storage: "indexed".
    if storage is not None and "index_in_sqlite" in data:
        return (
            "Cannot set both 'storage' and legacy 'index_in_sqlite'. "
            "Use 'storage' only (indexed/column/file_only)."
        )

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
    # Storage tier: default "indexed". "column" = column only (no index).
    # "file_only" = meta.yaml only, no migration needed.
    storage = data.get("storage") or "indexed"
    needs_migration = storage in ("indexed", "column")

    # Check conflicts in engine schema
    try:
        with open(ENGINE_ATTRIBUTES_YAML) as f:
            engine_existing = yaml.safe_load(f)
    except Exception as e:
        fail(f"Cannot read engine attributes.yaml: {e}")

    for section in ("universal", "attributes", "dimensions"):
        if name in engine_existing.get(section, {}):
            fail(f"Name '{name}' already exists in managed schema section '{section}'")

    # Check conflicts in user schema
    user_data = {}
    if USER_ATTRIBUTES_YAML.exists():
        try:
            with open(USER_ATTRIBUTES_YAML) as f:
                user_data = yaml.safe_load(f) or {}
        except Exception as e:
            fail(f"Cannot read schema-user/attributes.yaml: {e}")

    if name in user_data.get("attributes", {}):
        fail(f"Attribute '{name}' already exists in your schema extensions")

    # Build attribute definition dict
    attr_def: dict = {"data_type": data["data_type"], "description": data["description"]}
    if data.get("required"):
        attr_def["required"] = True
    if data.get("values"):
        attr_def["values"] = data["values"]
    if data.get("list"):
        attr_def["list"] = True
    if data.get("max_items"):
        attr_def["max_items"] = data["max_items"]
    # Only emit non-default storage tier
    if storage in ("column", "file_only"):
        attr_def["storage"] = storage
    elif storage == "indexed":
        attr_def["storage"] = "indexed"
    if data.get("access"):
        attr_def["access"] = data["access"]

    user_data.setdefault("attributes", {})[name] = attr_def

    USER_SCHEMA_DIR.mkdir(parents=True, exist_ok=True)
    try:
        with open(USER_ATTRIBUTES_YAML, "w") as f:
            yaml.dump(user_data, f, default_flow_style=False, sort_keys=False, allow_unicode=True)
    except Exception as e:
        fail(f"Failed to write schema-user/attributes.yaml: {e}")

    # Run SQLite migration if the attribute needs an indexed column.
    # Under the new storage-tier model, this is everything except explicit file_only.
    if needs_migration:
        try:
            result = subprocess.run(
                [sys.executable, str(SCRIPTS_DIR / "migrate-to-sqlite.py"), str(WORKSPACE)],
                capture_output=True,
                text=True,
                cwd=str(WORKSPACE),
            )
            if result.returncode != 0:
                print(json.dumps({
                    "success": True,
                    "name": name,
                    "warning": (
                        f"Attribute '{name}' written to schema but SQLite migration failed: "
                        f"{result.stderr.strip()}. Run migrate-to-sqlite.py manually."
                    )
                }))
                sys.exit(0)
        except Exception as e:
            print(json.dumps({
                "success": True,
                "name": name,
                "warning": f"Attribute written but could not run migrate-to-sqlite.py: {e}"
            }))
            sys.exit(0)

    print(json.dumps({"success": True, "name": name}))
    sys.exit(0)


if __name__ == "__main__":
    main()
