#!/bin/bash
# SessionStart hook (.claude/settings.json): installs the project into Claude
# Code cloud sessions so Claude can run the tests, the app and the ingest
# scripts against Supabase. A no-op locally, where the venv already exists.
# Supabase credentials come from the cloud environment, not from here - see
# SUPABASE_SETUP.md section 17.

if [ "$CLAUDE_CODE_REMOTE" != "true" ]; then
  exit 0
fi

cd "$CLAUDE_PROJECT_DIR" || exit 1

# pip skips requirements that are already satisfied, so a resumed session is fast.
pip install --quiet -e . -r requirements.txt -r requirements-ingest.txt || true
exit 0
