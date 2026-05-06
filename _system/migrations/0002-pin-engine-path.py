#!/usr/bin/env python3
"""
Migration 0002: Pin engine path in workspace overlay.yaml.

Workspaces created before R1.2 may not have an explicit engine key in
overlay.yaml. Without it, the engine resolves via SUBSTRATE_ENGINE_PATH
(a global env var) before falling back to the default installed engine —
which can cause dev engine installs to leak into unrelated workspaces.

This migration writes engine: ~/.substrate/engine into overlay.yaml for
any workspace that doesn't already have it set.
"""

import os
import sys
from pathlib import Path

SUBSTRATE_PATH = os.environ.get("SUBSTRATE_PATH", "")
if not SUBSTRATE_PATH:
    print("SUBSTRATE_PATH not set", file=sys.stderr)
    sys.exit(1)

overlay_path = Path(SUBSTRATE_PATH) / "_system" / "overlay.yaml"
installed_engine = Path("~/.substrate/engine").expanduser()

if not overlay_path.exists():
    print("  overlay.yaml not found — skipping")
    sys.exit(0)

content = overlay_path.read_text(encoding="utf-8")

if "engine:" in content:
    print("  engine path already set — no change needed")
    sys.exit(0)

# Prepend the engine key before the first existing content line.
pinned = f"# Engine path — pinned by migration 0002.\nengine: {installed_engine}\n\n"
overlay_path.write_text(pinned + content, encoding="utf-8")
print(f"  engine path pinned to {installed_engine}")
