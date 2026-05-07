#!/usr/bin/env python3
"""
Normalize block-sequence indentation across all entity meta.yaml files.

Substrate's canonical meta.yaml format uses two-space-indented block sequences
under mapping keys:

    relates_to:
      - UUID

PyYAML's default `yaml.dump` emits zero-indent sequences (`- UUID` at parent's
indent), and that form has crept into entity files over time via ad-hoc
maintenance scripts and (historically) `migrate-object-nature-flair.py`. Both
forms are valid YAML, but mixing them broke line-based YAML editors in
delete-entity.py and update-entity.py — see delete-entity.py's
_is_block_seq_item docstring for the bug history.

This script walks every `entities/**/meta.yaml`, applies
`lib.fileio.normalize_meta_yaml_text`, and writes back any file that changed.
Round-trips are verified: the normalized text must parse to the same Python
structure as the original. If parsing fails before or after, the file is
reported and skipped (preserves bad files for manual repair — same posture
as `migrate-to-sqlite.py`'s pre-flight check).

Usage:
  python3 normalize-meta-yaml.py [--dry-run] [--verbose] [SUBSTRATE_PATH]

Idempotent. Running it twice has no effect on the second pass.
"""

import os
import sys
import glob
import argparse

import yaml

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

from lib.fileio import normalize_meta_yaml_text, safe_write


def main():
    parser = argparse.ArgumentParser(
        description="Normalize block-sequence indentation across all entity meta.yaml files."
    )
    parser.add_argument("substrate_path", nargs="?", default=os.getcwd(),
                        help="Substrate workspace root (default: cwd)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Report changes without writing")
    parser.add_argument("--verbose", action="store_true",
                        help="Print every file touched")
    args = parser.parse_args()

    pattern = os.path.join(args.substrate_path, "entities", "**", "meta.yaml")
    meta_files = glob.glob(pattern, recursive=True)

    scanned = 0
    changed = 0
    skipped = []

    for path in meta_files:
        scanned += 1
        with open(path, encoding="utf-8") as f:
            original = f.read()

        # Parse original first — if it's malformed, skip and report.
        try:
            original_parsed = yaml.safe_load(original)
        except yaml.YAMLError as e:
            skipped.append((path, f"original does not parse: {e}"))
            continue

        normalized = normalize_meta_yaml_text(original)

        if normalized == original:
            continue  # already canonical

        # Verify the normalization preserved structure — we only reformatted
        # indent, not content. If the dict differs, something went wrong;
        # skip rather than write the file.
        try:
            normalized_parsed = yaml.safe_load(normalized)
        except yaml.YAMLError as e:
            skipped.append((path, f"normalized text does not parse: {e}"))
            continue

        if normalized_parsed != original_parsed:
            skipped.append((path, "normalized parse differs from original — aborting write"))
            continue

        rel = os.path.relpath(path, args.substrate_path)
        if args.verbose or args.dry_run:
            print(f"  {'(dry-run) ' if args.dry_run else ''}normalize: {rel}")
        changed += 1

        if not args.dry_run:
            with safe_write(path) as (_, write):
                write(normalized)

    print()
    print(f"Scanned: {scanned}")
    print(f"{'Would change' if args.dry_run else 'Changed'}: {changed}")
    if skipped:
        print(f"Skipped: {len(skipped)}")
        for p, reason in skipped:
            print(f"  {os.path.relpath(p, args.substrate_path)}: {reason}")


if __name__ == "__main__":
    main()
