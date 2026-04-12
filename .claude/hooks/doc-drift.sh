#!/bin/bash
# Fires from Claude Code's Stop hook. If code (not just docs) changed since
# the last commit, spawn a detached headless Claude run to repair drift in
# specs/ and README.md. The grep filter prevents the doc agent from
# re-triggering itself on its own edits.

set -e

cd "$CLAUDE_PROJECT_DIR" || exit 0

if git diff --name-only HEAD 2>/dev/null | grep -qvE '^(specs/|README\.md$)'; then
  nohup claude -p "Scan specs/ and README.md against the current code in this repo. Find and fix drift: stale file paths, stale line numbers, removed or renamed functions/classes, and behavior described in docs that no longer matches the code. Touch only files under specs/ and README.md. Do not commit. Be surgical — leave unrelated content alone." \
    > /tmp/twatch-doc-drift.log 2>&1 &
  disown 2>/dev/null || true
fi
