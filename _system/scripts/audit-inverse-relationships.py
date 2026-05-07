#!/usr/bin/env python3
"""
Audit (and optionally fix) stale inverse relationships in Substrate.

When a child entity's belongs_to is changed to a new parent, the old parent's
contains entry isn't removed. This script finds and reports those stale entries.

Usage:
  python3 audit-inverse-relationships.py           # Report only
  python3 audit-inverse-relationships.py --fix      # Report and remove stale entries
"""

import os
import sys
import re
import glob
import argparse
from datetime import datetime

SUBSTRATE_PATH = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(SUBSTRATE_PATH, "_system", "scripts"))
from lib.fileio import safe_write

# Containment inverse pairs: if P has inverse_rel: [X], then X should have forward_rel: [P]
# Only check from the inverse side (parent -> child) since that's where stale entries accumulate.
INVERSE_TO_FORWARD = {
    "contains": "belongs_to",
    "has_member": "member_of",
    "parent_of": "child_of",
    "manages": "reports_to",
}


def load_all_meta():
    """Load all meta.yaml files into a dict keyed by entity ID."""
    entities = {}
    pattern = os.path.join(SUBSTRATE_PATH, "entities", "**", "meta.yaml")
    for path in glob.glob(pattern, recursive=True):
        try:
            with open(path, 'r', encoding="utf-8") as f:
                content = f.read()
            # Extract ID
            id_match = re.search(r'^id:\s*(.+)$', content, re.MULTILINE)
            if not id_match:
                continue
            entity_id = id_match.group(1).strip()
            entities[entity_id] = {"path": path, "content": content}
        except Exception as e:
            print(f"  Warning: failed to read {path}: {e}", file=sys.stderr)
    return entities


def extract_relationship_targets(content, rel_name):
    """Extract UUIDs from a relationship list in meta.yaml content.
    Handles both '  - UUID' (two-space indent) and '- UUID' (zero indent) formats."""
    targets = []
    lines = content.split('\n')
    in_block = False
    for line in lines:
        if line.strip() == f"{rel_name}:" and not line.startswith(' '):
            in_block = True
            continue
        if in_block:
            stripped = line.strip()
            if stripped.startswith('- '):
                uuid = stripped.lstrip('- ').strip()
                if len(uuid) == 36 and '-' in uuid:
                    targets.append(uuid)
                continue
            elif line.strip() == '':
                continue
            else:
                break
    return targets


def find_stale_inverses(entities):
    """Find all stale inverse entries across the graph."""
    stale = []  # list of (parent_id, inverse_rel, child_id, forward_rel)

    for parent_id, parent_data in entities.items():
        for inverse_rel, forward_rel in INVERSE_TO_FORWARD.items():
            children = extract_relationship_targets(parent_data["content"], inverse_rel)
            for child_id in children:
                if child_id not in entities:
                    # Child doesn't exist at all — also stale
                    stale.append((parent_id, inverse_rel, child_id, forward_rel, "missing_entity"))
                    continue
                child_data = entities[child_id]
                back_refs = extract_relationship_targets(child_data["content"], forward_rel)
                if parent_id not in back_refs:
                    stale.append((parent_id, inverse_rel, child_id, forward_rel, "no_backref"))

    return stale


def remove_relationship_from_meta(content, rel_type, target_id):
    """Remove a relationship entry from meta.yaml. Removes the header if list becomes empty."""
    lines = content.rstrip('\n').split('\n')
    new_lines = []
    in_rel_block = False
    header_line = None
    remaining_items = []

    for line in lines:
        if line.strip() == f"{rel_type}:" and not line.startswith(' '):
            in_rel_block = True
            header_line = line
            remaining_items = []
            continue

        if in_rel_block:
            if line.startswith('  - '):
                item = line.strip().lstrip('- ').strip()
                if item == target_id:
                    continue
                remaining_items.append(line)
                continue
            else:
                if remaining_items:
                    new_lines.append(header_line)
                    new_lines.extend(remaining_items)
                in_rel_block = False

        new_lines.append(line)

    if in_rel_block and remaining_items:
        new_lines.append(header_line)
        new_lines.extend(remaining_items)

    return '\n'.join(new_lines) + '\n'


def fix_stale(entities, stale_entries):
    """Remove stale inverse entries from parent meta.yaml files."""
    # Group by parent to minimize file writes
    by_parent = {}
    for parent_id, inverse_rel, child_id, forward_rel, reason in stale_entries:
        if parent_id not in by_parent:
            by_parent[parent_id] = []
        by_parent[parent_id].append((inverse_rel, child_id))

    fixed = 0
    for parent_id, removals in by_parent.items():
        parent_data = entities[parent_id]

        with safe_write(parent_data["path"]) as (content, write):
            for inverse_rel, child_id in removals:
                content = remove_relationship_from_meta(content, inverse_rel, child_id)
                fixed += 1

            # Update modified timestamp. Route through quote_yaml_scalar so the
            # value is written as a quoted string, not a bare timestamp that
            # PyYAML would re-parse as a datetime object on next load.
            from lib.fileio import quote_yaml_scalar
            now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
            content = re.sub(r'^last_edited:.*$', f'last_edited: {quote_yaml_scalar(now)}', content, flags=re.MULTILINE)

            write(content)

    return fixed


def main():
    parser = argparse.ArgumentParser(description="Audit stale inverse relationships")
    parser.add_argument("--fix", action="store_true", help="Remove stale entries")
    args = parser.parse_args()

    print("Loading all entities...")
    entities = load_all_meta()
    print(f"  Loaded {len(entities)} entities.\n")

    print("Scanning for stale inverse relationships...")
    stale = find_stale_inverses(entities)

    if not stale:
        print("  No stale inverses found. Graph is clean.")
        return

    # Group by parent for reporting
    by_parent = {}
    for parent_id, inverse_rel, child_id, forward_rel, reason in stale:
        if parent_id not in by_parent:
            by_parent[parent_id] = {"name": None, "entries": []}
        by_parent[parent_id]["entries"].append((inverse_rel, child_id, forward_rel, reason))

    # Look up parent names
    for parent_id in by_parent:
        content = entities[parent_id]["content"]
        name_match = re.search(r'^name:\s*(.+)$', content, re.MULTILINE)
        if name_match:
            by_parent[parent_id]["name"] = name_match.group(1).strip().strip('"')

    print(f"\n  STALE INVERSE ENTRIES: {len(stale)} total across {len(by_parent)} parent(s)\n")

    for parent_id, info in sorted(by_parent.items(), key=lambda x: len(x[1]["entries"]), reverse=True):
        name = info["name"] or parent_id
        print(f"  {name} [{parent_id[:8]}] — {len(info['entries'])} stale entries:")
        for inverse_rel, child_id, forward_rel, reason in info["entries"]:
            child_name = ""
            if child_id in entities:
                nm = re.search(r'^name:\s*(.+)$', entities[child_id]["content"], re.MULTILINE)
                if nm:
                    child_name = f" ({nm.group(1).strip().strip(chr(34))})"
            reason_label = "entity missing" if reason == "missing_entity" else f"{forward_rel} doesn't point back"
            print(f"    {inverse_rel}: {child_id[:8]}{child_name} — {reason_label}")
        print()

    if args.fix:
        print("Fixing stale entries...")
        fixed = fix_stale(entities, stale)
        print(f"  Removed {fixed} stale entries.\n")

        # Verify
        print("Re-scanning to verify...")
        entities = load_all_meta()
        remaining = find_stale_inverses(entities)
        if remaining:
            print(f"  WARNING: {len(remaining)} stale entries remain!")
        else:
            print("  Verified: zero stale entries remain.")
    else:
        print("Run with --fix to remove stale entries.")


if __name__ == "__main__":
    main()
