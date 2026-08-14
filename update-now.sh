#!/usr/bin/env bash
#
# update-now.sh — get the latest code AND pull your newest LINE export in.
#
#   ./update-now.sh            do everything
#   ./update-now.sh --no-git   don't switch branches or pull (use the code as-is)
#   ./update-now.sh --status   is the app running, and where?
#   ./update-now.sh --stop     stop the app
#   ./update-now.sh --logs     follow the server log
#
# One command, no decisions. It updates the code, starts the app, makes sure the
# tooling is pointed at the account that actually holds your lessons, finds your
# newest LINE export, and syncs it. Existing sessions are never overwritten, and
# the server takes a restore point before it writes anything.
#
# The one thing it cannot do is export the chat out of LINE — macOS LINE has no
# scripting hook. Do that first: open the lesson chat, chat menu (top right),
# Save chat history, save the .txt to Downloads.
#
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"

bold() { printf '\033[1m%s\033[0m\n' "$*"; }
info() { printf '  %s\n' "$*"; }
warn() { printf '\033[33m  ! %s\033[0m\n' "$*"; }

trap 'status=$?; [ $status -ne 0 ] && printf "\033[31m\n  x update-now.sh stopped at line %s (exit %s).\033[0m\n" "$LINENO" "$status" >&2; exit $status' ERR

# --- arguments -------------------------------------------------------------
# The control flags are start-local.sh's job and they all exit early, so hand
# them straight over. Passing them through blindly with "$@" was a bug: --status
# exits 0 inside start-local.sh, and this script then cheerfully carried on to
# sync data the user had only asked to inspect.
START_ARGS=()
while [ $# -gt 0 ]; do
  case "$1" in
    --stop|--status|--logs)
      exec ./start-local.sh "$1" ;;
    -h|--help)
      sed -n '3,19p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
      exit 0 ;;
    --no-git)
      START_ARGS+=("$1") ;;
    --import)
      shift
      [ $# -gt 0 ] || { warn "--import needs a path to a backup zip"; exit 1; }
      START_ARGS+=(--import "$1") ;;
    *)
      warn "unknown option: $1 (try --help)"; exit 1 ;;
  esac
  shift
done

# --- 1. latest code + a running app ---------------------------------------
bold "1/3  Updating and starting the app"
# Deliberately NOT piped through sed for indentation: on a first run
# start-local.sh prompts for a password, and a pipe buffers the prompt so the
# script looks hung on a question you cannot see.
./start-local.sh ${START_ARGS+"${START_ARGS[@]}"}

set -a; . ./.env; set +a
PY="$( [ -x .venv/bin/python ] && echo .venv/bin/python || echo python3 )"

# --- 2. aim at the account that holds the lessons -------------------------
# Syncing into an empty account splits the data in two: nothing is lost, but
# half your lessons are behind one login and half behind another, which is worse
# than either half. Rather than ask, find the account with the most sessions.
echo
bold "2/3  Checking which account holds your lessons"
BEST="$("$PY" - <<'PYEOF'
import os, sqlite3, sys
db = os.path.join("api", "lessonlens.db")
if not os.path.exists(db):
    sys.exit(0)
conn = sqlite3.connect(db)
conn.row_factory = sqlite3.Row
try:
    rows = [
        (r["email"], conn.execute(
            "SELECT COUNT(*) FROM sessions WHERE user_id = ?", (r["id"],)).fetchone()[0])
        for r in conn.execute("SELECT id, email FROM users")
    ]
except sqlite3.Error:
    sys.exit(0)
finally:
    conn.close()
rows.sort(key=lambda t: -t[1])
if rows and rows[0][1] > 0:
    # Newline-separated: an email cannot contain one, so this cannot be
    # mis-split the way a delimiter character could.
    print(rows[0][0])
    print(rows[0][1])
PYEOF
)"

BEST_EMAIL="$(printf '%s\n' "$BEST" | sed -n 1p)"
BEST_COUNT="$(printf '%s\n' "$BEST" | sed -n 2p)"

if [ -z "$BEST_EMAIL" ]; then
  info "no sessions in the database yet — this will be the first sync"
elif [ "$BEST_EMAIL" = "${LESSONLENS_EMAIL:-}" ]; then
  info "already using $BEST_EMAIL ($BEST_COUNT sessions)"
else
  warn "'${LESSONLENS_EMAIL:-}' is not the account with your data"
  warn "'$BEST_EMAIL' holds $BEST_COUNT session(s) — pointing everything at it"
  # Reuse the password already in .env rather than generating one. You have been
  # typing it at the login screen; a fresh random string would mean the account
  # you now need to log into has a password you would have to go read out of a
  # file. Only invent one if .env somehow has none.
  NEW_PASS="${LESSONLENS_PASSWORD:-}"
  if [ ${#NEW_PASS} -lt 16 ]; then
    NEW_PASS="$("$PY" -c 'import secrets,string; a=string.ascii_letters+string.digits; print("".join(secrets.choice(a) for _ in range(24)))')"
    warn "no usable password in .env — generated one and stored it there"
  fi
  "$PY" account.py --use "$BEST_EMAIL" --password "$NEW_PASS"
  set -a; . ./.env; set +a
  info "log in as $BEST_EMAIL with the password in .env"
  # No restart needed: the server authenticates against the database, not .env.
  # .env only tells the updater and the MCP server who to log in as.
fi

# --- 3. sync the newest export --------------------------------------------
echo
bold "3/3  Syncing your newest LINE export"
count_sessions() { "$PY" scripts/count_sessions.py 2>/dev/null || echo '?'; }
before="$(count_sessions)"

set +e
LESSONLENS_TARGET=local "$PY" scripts/line_mac_sync.py --target local --generate-with none
sync_status=$?
set -e

echo
if [ "$sync_status" -ne 0 ]; then
  warn "the sync reported a problem — the app is still running and your data is untouched"
  warn "no export found? In LINE: open the chat -> chat menu -> Save chat history -> Downloads"
  exit "$sync_status"
fi

after="$(count_sessions)"
bold "Done — open ${LESSONLENS_LOCAL_URL:-http://127.0.0.1:5001}"
cat <<EOF

  Sessions: $before  ->  $after   (account: ${LESSONLENS_EMAIL:-unknown})
  A restore point was taken before writing; roll back from Settings if needed.

  Next, to turn new lessons into review material:
    LESSONLENS_TARGET=local make update-all
EOF
