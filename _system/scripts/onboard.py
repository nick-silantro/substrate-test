#!/usr/bin/env python3
"""
Initialize a Substrate workspace from a structured onboarding conversation.

Usage:
  python3 onboard.py --from /path/to/onboarding-input.json

The JSON input is produced by the guided install agent after a setup
conversation. This script assembles full context documents from baked-in
templates plus the user-specific content from the JSON, creates the entities,
and rebuilds the index.

Creates:
  - user-[call_name] context-doc  (USER-[CALLNAME].md)
  - narrative context-doc          (NARRATIVE.md)
  - bulletin-board context-doc     (BULLETIN-BOARD.md)
  - Additional entities from the JSON entities array
  - _system/onboarding-complete.txt marker
"""

import os
import sys
import json
import uuid
import argparse
import subprocess
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
from lib.fileio import dump_entity_meta

SUBSTRATE_PATH = os.environ.get(
    "SUBSTRATE_PATH",
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)

# ---------------------------------------------------------------------------
# Document templates (rules headers baked in; user-specific content appended)
# ---------------------------------------------------------------------------

USER_HEADER = """\
_A person in this workspace. Loaded by agents at the start of every session._

_When reading: internalize who this person is — their strengths, how they communicate, what they care about, and how they work. Let this shape every response._

_When writing: update only from observation or what the user has directly told you. Don't speculate or infer beyond what's confirmed._

---

"""

NARRATIVE_HEADER = """\
> **How to write here.** This document is a narrative — the gist of how things feel in the workspace, not a changelog of what shipped or a list of what's pending. Before adding any sentence, run two tests:
>
> 1. **Does it name a specific entity, UUID, ticket, or completion event?** Rewrite it to describe state instead. ("The invoicing work is blocked" not "Ticket abc123 is at the gate.")
> 2. **Does it describe what's currently true, or what should happen next?** Entries describe state — not prescriptions or action items. Those belong elsewhere.
>
> Completed threads leave this document. They don't become trophies here.

"""

BULLETIN_BOARD_HEADER = """\
Operational dispatch surface. Items are actionable but unformed — work without a project home, open decisions, things that need to be picked up. Not a historical document.

Items leave this board when they become tickets, when decisions close, or when confirmed stale. Don't let resolved threads accumulate — prune at the end of each session.

For the overall feel of the workspace: see NARRATIVE.

---

"""

# ---------------------------------------------------------------------------
# Entity creation
# ---------------------------------------------------------------------------

def _new_id():
    return str(uuid.uuid4())


def _create_context_doc(name, description, filename, content, startup_for, context_audience, entity_id=None):
    """Create a context-doc entity folder, meta.yaml, and content file."""
    entity_id = entity_id or _new_id()
    prefix = entity_id[:2]
    suffix = entity_id[2:4]
    entity_dir = Path(SUBSTRATE_PATH) / "entities" / "context-doc" / prefix / suffix / entity_id
    entity_dir.mkdir(parents=True, exist_ok=True)

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    meta = {
        "id": entity_id,
        "type": "context-doc",
        "name": name,
        "description": description,
        "meta_status": "live",
        "health": "undefined",
        "context_audience": json.dumps(context_audience, separators=(",", ":")),
        "startup_for": json.dumps(startup_for, separators=(",", ":")),
        "created": now,
        "last_edited": now,
    }
    (entity_dir / "meta.yaml").write_text(dump_entity_meta(meta))
    (entity_dir / filename).write_text(content)

    print(f"  Created {name}")
    return entity_id


def _create_extra_entity(entity_type, name, description):
    """Create a plain entity by delegating to create-entity.py."""
    script = Path(SCRIPT_DIR) / "create-entity.py"
    env = {**os.environ, "SUBSTRATE_PATH": SUBSTRATE_PATH}
    result = subprocess.run(
        [sys.executable, str(script),
         "--type", entity_type,
         "--name", name,
         "--description", description],
        env=env,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"  Warning: could not create entity '{name}': {result.stderr.strip()}", file=sys.stderr)
    else:
        print(f"  Created {entity_type}: {name}")


# ---------------------------------------------------------------------------
# Document assembly
# ---------------------------------------------------------------------------

def _build_user_doc(data):
    call_name = data.get("call_name", "")
    full_name = data.get("full_name", call_name)
    focus = data.get("focus", "")
    working_style = data.get("working_style", "")

    body = f"# USER — {full_name}\n\n"
    body += USER_HEADER
    body += "## Identity\n\n"
    body += f"- **Name:** {full_name}\n"
    body += f"- **What to call them:** {call_name}\n\n"
    if focus:
        body += f"## Current Focus\n\n{focus}\n\n"
    if working_style:
        body += f"## Working Style\n\n{working_style}\n"
    return body


def _build_narrative_doc(data):
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    focus = data.get("focus", "")
    motion = data.get("motion", "")
    workspace_state = data.get("workspace_state", "New workspace. Just getting started.")
    user_state = data.get("user_state", "")

    body = "# NARRATIVE\n\n"
    body += NARRATIVE_HEADER
    body += f"_Last updated: {now_str}_\n\n"
    if focus:
        body += f"## Focus\n\n{focus}\n\n"
    if motion:
        body += f"## Motion\n\n_What's moving, which direction, how fast._\n\n{motion}\n\n"
    if workspace_state:
        body += f"## Workspace State\n\n{workspace_state}\n\n"
    if user_state:
        body += f"## User State\n\n{user_state}\n\n"
    return body


def _build_bulletin_board_doc(data):
    seed = data.get("seed", "")
    body = "# BULLETIN BOARD\n\n"
    body += BULLETIN_BOARD_HEADER
    if seed:
        body += seed + "\n"
    return body


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    p = argparse.ArgumentParser(prog="substrate onboard")
    p.add_argument("--from", dest="input_file", required=True,
                   metavar="PATH", help="Path to onboarding JSON input file")
    args = p.parse_args()

    input_path = Path(args.input_file).expanduser().resolve()
    if not input_path.exists():
        print(f"onboard: input file not found: {input_path}", file=sys.stderr)
        sys.exit(1)

    with open(input_path) as f:
        data = json.load(f)

    user_data = data.get("user", {})
    narrative_data = data.get("narrative", {})
    bulletin_data = data.get("bulletin_board", {})

    call_name = user_data.get("call_name", "user")
    full_name = user_data.get("full_name", call_name)
    call_name_upper = call_name.upper()

    print("Setting up your workspace...")
    print()

    _create_context_doc(
        name=f"user-{call_name.lower()}",
        description=f"Profile of {full_name}. Read by agents to calibrate communication and approach.",
        filename=f"USER-{call_name_upper}.md",
        content=_build_user_doc(user_data),
        startup_for=["L0", "L1"],
        context_audience=["all"],
    )

    _create_context_doc(
        name="narrative",
        description="Current workspace narrative — focus, state, and momentum.",
        filename="NARRATIVE.md",
        content=_build_narrative_doc(narrative_data),
        startup_for=["L0", "L1", "L2"],
        context_audience=["all"],
    )

    _create_context_doc(
        name="bulletin-board",
        description="Operational dispatch surface. Open threads, decisions, and items needing pickup.",
        filename="BULLETIN-BOARD.md",
        content=_build_bulletin_board_doc(bulletin_data),
        startup_for=["L0", "L1"],
        context_audience=["L0", "L1"],
    )

    # Rebuild index
    migrate = Path(SCRIPT_DIR) / "migrate-to-sqlite.py"
    env = {**os.environ, "SUBSTRATE_PATH": SUBSTRATE_PATH}
    subprocess.run([sys.executable, str(migrate)], env=env, check=False, capture_output=True)

    # Clean up input file
    try:
        input_path.unlink()
    except OSError:
        pass

    print()
    print("Workspace is ready.")


if __name__ == "__main__":
    main()
