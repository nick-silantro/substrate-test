#!/usr/bin/env python3
"""
PostToolUse hook — validates context-doc entity markdown files after Write/Edit.
Checks: line count limits, UUID presence in prose content.

Invoked by Claude Code automatically; receives tool event JSON on stdin.
"""
import sys
import json
import re


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        sys.exit(0)

    file_path = data.get('tool_input', {}).get('file_path', '')

    if '/entities/context-doc/' not in file_path or not file_path.endswith('.md'):
        sys.exit(0)

    try:
        with open(file_path, 'r') as f:
            content = f.read()
    except Exception:
        sys.exit(0)

    lines = content.splitlines()
    line_count = len(lines)
    filename = file_path.split('/')[-1]
    warnings = []

    # NARRATIVE has a tighter target (100 lines); everything else uses 200.
    is_narrative = 'NARRATIVE' in filename.upper()
    soft_limit = 100 if is_narrative else 150
    hard_limit = 200

    if line_count > hard_limit:
        warnings.append(
            f'Line count ({line_count}) exceeds the {hard_limit}-line hard limit — prune before adding more content'
        )
    elif line_count > soft_limit:
        label = 'target' if is_narrative else 'limit'
        warnings.append(
            f'Line count ({line_count}) is approaching the {soft_limit}-line {label}'
        )

    # UUID check — UUIDs in prose context docs indicate entity ID pollution.
    uuid_pat = re.compile(
        r'\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b',
        re.IGNORECASE,
    )
    uuids = uuid_pat.findall(content)
    if uuids:
        sample = ', '.join(list(dict.fromkeys(uuids))[:3])
        warnings.append(
            f'Contains entity UUIDs ({sample}) — context docs should not reference UUIDs directly'
        )

    if warnings:
        print(f'[context-doc] {filename}')
        for w in warnings:
            print(f'  • {w}')


if __name__ == '__main__':
    main()
