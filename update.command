#!/bin/bash
# LessonLens one-touch update — double-click this file in Finder to run.
#
# It changes into the repo directory (the folder this file lives in) and runs
# the macOS LINE sync updater against your hosted LessonLens server.
#
# Before first use:
#   1. Copy .env.example to .env and set LESSONLENS_API_URL / LESSONLENS_EMAIL /
#      LESSONLENS_PASSWORD, or export those in your shell profile.
#   2. In Finder, right-click this file → Open (once) to clear Gatekeeper, or run
#      `chmod +x update.command` in Terminal.
#
# The Terminal window stays open at the end so you can read the summary.
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

PYTHON="${PYTHON:-python3}"

echo "LessonLens update — $(date)"
echo "Repo: $DIR"
echo

# Pass any extra args through, e.g. double-clicking won't pass any, but running
# `./update.command --sync-only` from Terminal will.
"$PYTHON" scripts/line_mac_sync.py "$@"
STATUS=$?

echo
if [ "$STATUS" -eq 0 ]; then
  echo "Done. You can close this window."
else
  echo "Update exited with status $STATUS. See messages above."
fi

# Keep the window open when launched by double-click.
read -r -p "Press Return to close..." _ || true
exit "$STATUS"
