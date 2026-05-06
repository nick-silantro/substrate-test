#!/usr/bin/env python3
"""
Run pending workspace migrations.

Scans _system/migrations/ in the engine for numbered migration scripts,
compares against the workspace's applied log, and runs any unapplied ones
in order. Stops on first failure — later migrations may depend on earlier ones.

Usage:
  python3 run-migrations.py

Environment:
  SUBSTRATE_PATH   Workspace directory (required)
"""

import json
import os
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(os.path.dirname(os.path.abspath(__file__)))
MIGRATIONS_DIR = SCRIPT_DIR.parent / "migrations"

SUBSTRATE_PATH = os.environ.get(
    "SUBSTRATE_PATH",
    str(SCRIPT_DIR.parent.parent)
)

APPLIED_LOG = Path(SUBSTRATE_PATH) / "_system" / "migrations-applied.json"


def load_applied() -> list:
    if not APPLIED_LOG.exists():
        return []
    try:
        data = json.loads(APPLIED_LOG.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def save_applied(applied: list):
    APPLIED_LOG.parent.mkdir(parents=True, exist_ok=True)
    APPLIED_LOG.write_text(json.dumps(applied, indent=2), encoding="utf-8")


def get_migrations() -> list:
    if not MIGRATIONS_DIR.exists():
        return []
    return sorted(p for p in MIGRATIONS_DIR.glob("*.py") if p.stem[0].isdigit())


def main():
    migrations = get_migrations()
    applied = load_applied()
    applied_names = set(applied)

    pending = [m for m in migrations if m.name not in applied_names]

    if not pending:
        print("Migrations: up to date.")
        return

    print(f"Running {len(pending)} migration(s)...")
    env = {**os.environ, "SUBSTRATE_PATH": SUBSTRATE_PATH}

    for migration in pending:
        print(f"  {migration.name}...", end=" ", flush=True)
        result = subprocess.run([sys.executable, str(migration)], env=env, text=True)
        if result.returncode == 0:
            applied.append(migration.name)
            save_applied(applied)
            print("done")
        else:
            print("FAILED")
            print(f"\n  Migration failed: {migration.name}", file=sys.stderr)
            print("  Resolve the issue and run 'substrate update' again.", file=sys.stderr)
            sys.exit(1)

    print(f"Migrations: {len(pending)} applied.")


if __name__ == "__main__":
    main()
