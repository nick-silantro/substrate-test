#!/usr/bin/env python3
"""
Query the context stack for an agent's boot-time or accessible documents.

Sources:
  1. Engine flat files   — {engine}/_system/docs/*.md with YAML frontmatter
  2. Workspace flat files — {workspace}/_system/docs/*.md with YAML frontmatter
  3. Workspace entities  — context-doc entities in SQLite / entities/

Usage:
  python3 query-context-stack.py boot L0                    # Documents loaded at boot for L0 agents
  python3 query-context-stack.py boot L1                    # Documents loaded at boot for L1 agents
  python3 query-context-stack.py boot L2                    # Documents loaded at boot for L2 agents
  python3 query-context-stack.py boot domain:graph          # Documents loaded for graph-domain agents
  python3 query-context-stack.py boot L1 domain:ops         # Boot docs for L1 + ops domain (union)
  python3 query-context-stack.py boot                       # Docs tagged startup_for: all (generic agents)
  python3 query-context-stack.py accessible L0              # All documents L0 agents can access
  python3 query-context-stack.py accessible L2 domain:graph # Accessible to L2 or graph-domain agents

Options:
  --content       Print document content instead of paths
  --paths-only    Print only file paths, one per line (for piping)
  --format json   Machine-readable JSON output

The boot subcommand queries startup_for (auto-loaded at session start).
The accessible subcommand queries context_audience (everything the agent can see).

"all" in either attribute means every agent matches.
When no identity is specified, only documents tagged "all" are returned.

Engine docs resolution order:
  1. SUBSTRATE_ENGINE_PATH environment variable
  2. _system/overlay.yaml engine key
  3. ~/.substrate/engine (default install location)
"""

import os
import sys
import json
import sqlite3
import yaml

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
from lib.db import open_db

SUBSTRATE_PATH = os.environ.get(
    "SUBSTRATE_PATH",
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)
DB_PATH = os.path.join(SUBSTRATE_PATH, "_system", "index", "substrate.db")


def get_conn():
    return open_db(DB_PATH)


def get_engine_path():
    """Resolve the engine installation path.

    Priority: overlay.yaml (workspace-specific) > SUBSTRATE_ENGINE_PATH (global) > default.
    """
    overlay_path = os.path.join(SUBSTRATE_PATH, "_system", "overlay.yaml")
    if os.path.exists(overlay_path):
        with open(overlay_path, encoding="utf-8") as f:
            overlay = yaml.safe_load(f) or {}
        engine = overlay.get("engine")
        if engine:
            return os.path.expanduser(engine)
    env_path = os.environ.get("SUBSTRATE_ENGINE_PATH")
    if env_path:
        return os.path.expanduser(env_path)
    return os.path.expanduser("~/.substrate/engine")


def load_entity_docs():
    """Load all live context-doc entities from SQLite, enriched with meta.yaml attributes."""
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        SELECT id, name, path, description
        FROM entities
        WHERE type = 'context-doc' AND meta_status = 'live'
        ORDER BY name
    """)
    rows = c.fetchall()
    conn.close()

    docs = []
    for entity_id, name, path, description in rows:
        entity_dir = os.path.join(SUBSTRATE_PATH, path)
        meta_path = os.path.join(entity_dir, "meta.yaml")

        if not os.path.exists(meta_path):
            continue

        with open(meta_path, "r", encoding="utf-8") as f:
            meta = yaml.safe_load(f)

        # Deserialize JSON list attributes
        startup_for_raw = meta.get("startup_for", "[]")
        context_audience_raw = meta.get("context_audience", "[]")

        try:
            startup_for = json.loads(startup_for_raw) if isinstance(startup_for_raw, str) else (startup_for_raw or [])
        except (json.JSONDecodeError, TypeError):
            startup_for = []

        try:
            context_audience = json.loads(context_audience_raw) if isinstance(context_audience_raw, str) else (context_audience_raw or [])
        except (json.JSONDecodeError, TypeError):
            context_audience = []

        # Find content files (everything except meta.yaml)
        content_files = []
        if os.path.isdir(entity_dir):
            content_files = sorted([
                f for f in os.listdir(entity_dir)
                if os.path.isfile(os.path.join(entity_dir, f))
                and f != "meta.yaml"
                and not f.endswith(".lock")
            ])

        docs.append({
            "id": entity_id,
            "name": name,
            "description": description or "",
            "path": path,
            "entity_dir": entity_dir,
            "engine_file": None,
            "engine_body": None,
            "startup_for": startup_for,
            "context_audience": context_audience,
            "content_files": content_files,
            "source": "entity",
        })

    return docs


def load_engine_docs():
    """Load context docs from engine flat files (_system/docs/*.md with YAML frontmatter)."""
    engine_path = get_engine_path()
    docs_dir = os.path.join(engine_path, "_system", "docs")
    if not os.path.isdir(docs_dir):
        return []

    docs = []
    for filename in sorted(os.listdir(docs_dir)):
        if not filename.endswith(".md"):
            continue
        filepath = os.path.join(docs_dir, filename)
        with open(filepath, encoding="utf-8") as f:
            content = f.read()

        frontmatter = {}
        body = content
        if content.startswith("---\n"):
            end = content.find("\n---\n", 4)
            if end != -1:
                fm_text = content[4:end]
                try:
                    frontmatter = yaml.safe_load(fm_text) or {}
                except yaml.YAMLError:
                    pass
                body = content[end + 5:]

        startup_for = frontmatter.get("startup_for", [])
        context_audience = frontmatter.get("context_audience", [])

        # Skip docs with no routing info (e.g., agent-orientation, plain reference docs)
        if not startup_for and not context_audience:
            continue

        if isinstance(startup_for, str):
            startup_for = [startup_for]
        if isinstance(context_audience, str):
            context_audience = [context_audience]

        name = frontmatter.get("name") or filename[:-3].replace("-", " ").title()
        description = frontmatter.get("description", "")

        docs.append({
            "id": None,
            "name": name,
            "description": description,
            "path": os.path.join("_system", "docs", filename),
            "entity_dir": None,
            "engine_file": filepath,
            "engine_body": body,
            "startup_for": startup_for,
            "context_audience": context_audience,
            "content_files": [filename],
            "source": "engine",
        })

    return docs


def load_workspace_docs():
    """Load context docs from workspace flat files (_system/docs/*.md with YAML frontmatter)."""
    docs_dir = os.path.join(SUBSTRATE_PATH, "_system", "docs")
    if not os.path.isdir(docs_dir):
        return []

    docs = []
    for filename in sorted(os.listdir(docs_dir)):
        if not filename.endswith(".md"):
            continue
        filepath = os.path.join(docs_dir, filename)
        with open(filepath, encoding="utf-8") as f:
            content = f.read()

        frontmatter = {}
        body = content
        if content.startswith("---\n"):
            end = content.find("\n---\n", 4)
            if end != -1:
                fm_text = content[4:end]
                try:
                    frontmatter = yaml.safe_load(fm_text) or {}
                except yaml.YAMLError:
                    pass
                body = content[end + 5:]

        startup_for = frontmatter.get("startup_for", [])
        context_audience = frontmatter.get("context_audience", [])

        if not startup_for and not context_audience:
            continue

        if isinstance(startup_for, str):
            startup_for = [startup_for]
        if isinstance(context_audience, str):
            context_audience = [context_audience]

        name = frontmatter.get("name") or filename[:-3].replace("-", " ").title()
        description = frontmatter.get("description", "")

        docs.append({
            "id": None,
            "name": name,
            "description": description,
            "path": os.path.join("_system", "docs", filename),
            "entity_dir": None,
            "engine_file": filepath,
            "engine_body": body,
            "startup_for": startup_for,
            "context_audience": context_audience,
            "content_files": [filename],
            "source": "workspace",
        })

    return docs


def load_all_docs():
    """Load context docs from engine flat files, workspace flat files, and workspace entities.

    Entity docs come last so filter_docs preserves that order, but print_manifest
    reorders them: workspace-specific (entity/workspace) first, engine docs last.
    """
    return load_engine_docs() + load_workspace_docs() + load_entity_docs()


def matches_identity(attribute_list, identities):
    """Check if any of the agent's identities match the attribute list.

    "all" in the attribute list means every agent matches.
    If identities is empty, only matches when "all" is in the attribute list.
    """
    if "all" in attribute_list:
        return True
    if not identities:
        return False
    return bool(set(attribute_list) & set(identities))


def filter_docs(docs, mode, identities):
    """Filter documents based on mode (boot/accessible) and agent identities."""
    attr_key = "startup_for" if mode == "boot" else "context_audience"
    return [d for d in docs if matches_identity(d[attr_key], identities)]


def content_file_paths(doc):
    """Return absolute paths to content files for a document."""
    if doc.get("source") in ("engine", "workspace"):
        return [doc["engine_file"]]
    return [os.path.join(doc["entity_dir"], f) for f in doc["content_files"]]


def print_table(docs):
    """Print documents as a formatted table."""
    if not docs:
        print("No matching context documents found.")
        return

    print(f"{'Name':<45} {'Startup For':<25} {'Audience':<25} {'Source':<8} {'File'}")
    print("-" * 140)
    for doc in docs:
        startup = ", ".join(doc["startup_for"]) if doc["startup_for"] else "-"
        audience = ", ".join(doc["context_audience"]) if doc["context_audience"] else "-"
        source = doc.get("source", "entity")
        files = ", ".join(doc["content_files"]) if doc["content_files"] else "(no content)"
        print(f"{doc['name'][:45]:<45} {startup[:25]:<25} {audience[:25]:<25} {source:<8} {files}")


def print_paths(docs):
    """Print only content file paths, one per line."""
    for doc in docs:
        for path in content_file_paths(doc):
            print(path)


def print_content(docs):
    """Print full content of all matching documents."""
    for i, doc in enumerate(docs):
        if i > 0:
            print("\n" + "=" * 80 + "\n")
        print(f"# {doc['name']}")
        source = doc.get("source", "entity")
        if source in ("engine", "workspace"):
            print(f"# Source: {source}")
        else:
            print(f"# Entity: {doc['id']}")
        print(f"# Startup for: {', '.join(doc['startup_for']) or 'none'}")
        print(f"# Audience: {', '.join(doc['context_audience']) or 'none'}")
        print()
        if source in ("engine", "workspace"):
            print(doc["engine_body"], end="")
        else:
            for filepath in content_file_paths(doc):
                try:
                    with open(filepath, "r", encoding="utf-8") as f:
                        print(f.read())
                except IOError as e:
                    print(f"[Error reading {filepath}: {e}]")


def print_manifest(docs):
    """Print a compact manifest: one absolute path + description per doc.

    Designed to stay small enough to never be collapsed or truncated by the
    agent harness. The agent must read every file listed with the Read tool.
    User-specific docs (entity/workspace source) are listed first.
    """
    if not docs:
        print("No context documents found for this identity.")
        return

    user_docs = [d for d in docs if d.get("source") in ("entity", "workspace")]
    engine_docs = [d for d in docs if d.get("source") == "engine"]
    ordered = user_docs + engine_docs

    total = sum(len(content_file_paths(d)) for d in ordered)
    print(f"Context stack ({total} file{'s' if total != 1 else ''}). You MUST read every file listed using your Read tool. Do not skip any.\n")

    for doc in ordered:
        for filepath in content_file_paths(doc):
            desc = doc.get("description", "").strip()
            print(filepath)
            if desc:
                print(f"  {desc}")
            print()


def print_json(docs):
    """Print machine-readable JSON output."""
    output = []
    for doc in docs:
        output.append({
            "id": doc["id"],
            "name": doc["name"],
            "description": doc["description"],
            "source": doc.get("source", "entity"),
            "startup_for": doc["startup_for"],
            "context_audience": doc["context_audience"],
            "content_files": content_file_paths(doc),
        })
    print(json.dumps(output, indent=2))


def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print(__doc__)
        sys.exit(0)

    mode = sys.argv[1]
    if mode not in ("boot", "accessible"):
        print(f"Unknown mode: {mode}. Use 'boot' or 'accessible'.")
        sys.exit(1)

    # Parse identities and flags from remaining args
    identities = []
    output_mode = "table"  # table, paths, content, json

    i = 2
    while i < len(sys.argv):
        arg = sys.argv[i]
        if arg == "--content":
            output_mode = "content"
        elif arg == "--manifest":
            output_mode = "manifest"
        elif arg == "--paths-only":
            output_mode = "paths"
        elif arg == "--format" and i + 1 < len(sys.argv) and sys.argv[i + 1] == "json":
            output_mode = "json"
            i += 1
        else:
            identities.append(arg)
        i += 1

    # No identities = return only docs tagged "all" (generic agent default)
    # identities provided = return matching docs + "all"-tagged docs

    # Load and filter
    all_docs = load_all_docs()
    matched = filter_docs(all_docs, mode, identities)

    # Output
    if output_mode == "table":
        label = "boot" if mode == "boot" else "accessible"
        identity_label = ", ".join(identities) if identities else "all"
        print(f"\nContext stack ({label}) for: {identity_label}")
        print(f"Matched {len(matched)} of {len(all_docs)} context documents.\n")
        print_table(matched)
    elif output_mode == "paths":
        print_paths(matched)
    elif output_mode == "content":
        print_content(matched)
    elif output_mode == "manifest":
        print_manifest(matched)
    elif output_mode == "json":
        print_json(matched)


if __name__ == "__main__":
    main()
