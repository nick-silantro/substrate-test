#!/bin/bash
# auto-commit.sh
#
# Commits all uncommitted workspace changes on a schedule, then pushes to GitHub.
# Runs via cron — see crontab entry in _system/config/crontab.md.
#
# Scope: all files not excluded by .gitignore (entities, skills, schema,
# docs, config, agent memory, scripts).
#
# Safe to run when nothing has changed — exits 0 with no commit or push.
#
# Repo path resolution (priority order):
#   1. $SUBSTRATE_PATH from the environment — canonical, portable.
#      Cron should export it in the crontab entry.
#   2. Two levels up from this script (.../_system/scripts/ → .../).
#      Lets the script work in ad-hoc invocations without env setup.

set -euo pipefail

REPO="${SUBSTRATE_PATH:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
LOG="$REPO/_system/logs/auto-commit.log"
TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')

cd "$REPO"

# Verify we're in a git repo
if ! git rev-parse --git-dir > /dev/null 2>&1; then
    echo "[$TIMESTAMP] ERROR: Not a git repo at $REPO" >> "$LOG"
    exit 1
fi

# Check if there's anything to commit (unstaged, staged, or new untracked)
UNSTAGED=$(git diff --name-only 2>/dev/null | wc -l | tr -d ' ')
STAGED=$(git diff --staged --name-only 2>/dev/null | wc -l | tr -d ' ')
UNTRACKED=$(git ls-files --others --exclude-standard 2>/dev/null | wc -l | tr -d ' ')
TOTAL=$(( UNSTAGED + STAGED + UNTRACKED ))

if [ "$TOTAL" -eq 0 ]; then
    echo "[$TIMESTAMP] Nothing to commit." >> "$LOG"
    exit 0
fi

# Stage everything not excluded by .gitignore
git add -A

# Commit
git commit -m "Auto-commit: $TIMESTAMP

Scheduled commit of unticketed workspace changes.
Files: $UNSTAGED modified, $UNTRACKED new untracked (pre-stage counts)." \
    --no-gpg-sign 2>&1 | tee -a "$LOG"

# Push to GitHub
git push >> "$LOG" 2>&1 && \
    echo "[$TIMESTAMP] Pushed to origin." >> "$LOG" || \
    echo "[$TIMESTAMP] WARNING: push failed — commits are local only." >> "$LOG"

echo "[$TIMESTAMP] Done." >> "$LOG"
