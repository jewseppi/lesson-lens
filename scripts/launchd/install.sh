#!/bin/bash
# Install (or remove) a launchd job that runs the LessonLens updater daily.
#
#   bash scripts/launchd/install.sh            # install, runs daily at 20:00
#   bash scripts/launchd/install.sh --hour 7   # install, runs daily at 07:00
#   bash scripts/launchd/install.sh --uninstall
#
# The job runs `line_mac_sync.py --generate-all`, which is idempotent — a run
# with no new export/images just no-ops and fills any session still missing a
# summary. Configure the hosted server in the repo's .env (LESSONLENS_API_URL /
# LESSONLENS_EMAIL / LESSONLENS_PASSWORD) before scheduling.
set -euo pipefail

LABEL="com.lessonlens.update"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
TEMPLATE="$HERE/${LABEL}.plist"
DEST_DIR="$HOME/Library/LaunchAgents"
DEST="$DEST_DIR/${LABEL}.plist"
PYTHON_BIN="${PYTHON:-$(command -v python3 || echo python3)}"
HOUR=20
UNINSTALL=0

while [ $# -gt 0 ]; do
  case "$1" in
    --hour) HOUR="$2"; shift 2 ;;
    --uninstall) UNINSTALL=1; shift ;;
    *) echo "Unknown argument: $1" >&2; exit 2 ;;
  esac
done

uninstall() {
  launchctl unload "$DEST" 2>/dev/null || true
  rm -f "$DEST"
  echo "Removed launchd job $LABEL"
}

if [ "$UNINSTALL" -eq 1 ]; then
  uninstall
  exit 0
fi

if [ ! -f "$TEMPLATE" ]; then
  echo "Template not found: $TEMPLATE" >&2
  exit 1
fi

mkdir -p "$DEST_DIR" "$REPO/logs"

# Substitute placeholders. Use a delimiter unlikely to appear in paths.
sed \
  -e "s|__REPO__|$REPO|g" \
  -e "s|__PYTHON__|$PYTHON_BIN|g" \
  -e "s|__HOUR__|$HOUR|g" \
  "$TEMPLATE" > "$DEST"

# Reload if already loaded, then load fresh.
launchctl unload "$DEST" 2>/dev/null || true
launchctl load "$DEST"

echo "Installed launchd job $LABEL"
echo "  runs daily at ${HOUR}:00 -> $PYTHON_BIN scripts/line_mac_sync.py --generate-all"
echo "  repo:  $REPO"
echo "  plist: $DEST"
echo "  log:   $REPO/logs/update.log"
echo
echo "Run it once now to verify:  launchctl start $LABEL"
echo "Remove it later with:       make unschedule"
