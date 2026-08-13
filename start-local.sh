#!/usr/bin/env bash
#
# start-local.sh — get LessonLens running locally in one command.
#
# Safe to re-run: every step checks before acting, so this doubles as "restart
# it" and as "did my setup drift?". It clones the repo if needed, creates a
# virtualenv, installs dependencies, builds the web UI, makes sure a login
# exists, starts the server in the background, and finishes by running the
# preflight so you see the real state rather than a wall of build output.
#
#   ./start-local.sh          start (or restart) everything
#   ./start-local.sh --stop   stop the server
#   ./start-local.sh --logs   follow the server log
#
set -euo pipefail

REPO_URL="https://github.com/jewseppi/lesson-lens.git"
PORT="${PORT:-5001}"
DEFAULT_EMAIL="${LESSONLENS_EMAIL:-me@example.com}"

bold() { printf '\033[1m%s\033[0m\n' "$*"; }
info() { printf '  %s\n' "$*"; }
warn() { printf '\033[33m  ! %s\033[0m\n' "$*"; }
die()  { printf '\033[31m  x %s\033[0m\n' "$*" >&2; exit 1; }

# --- locate the repo -------------------------------------------------------
# Prefer the checkout this script lives in; otherwise look where people
# actually put things, and clone only as a last resort.
find_repo() {
  local here
  here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  if [ -f "$here/api/app.py" ]; then echo "$here"; return; fi
  # The checkout is not necessarily named after the repo — this one lives at
  # ~/dev/language — so scan the usual roots for anything that looks like it.
  for root in "$HOME" "$HOME/dev" "$HOME/code" "$HOME/projects" \
              "$HOME/src" "$HOME/Developer" "$HOME/work"; do
    [ -d "$root" ] || continue
    for candidate in "$root"/*; do
      [ -f "$candidate/api/app.py" ] && [ -f "$candidate/scripts/doctor.py" ] \
        && { echo "$candidate"; return; }
    done
  done
  if command -v mdfind >/dev/null 2>&1; then
    local hit
    hit="$(mdfind -name lesson-lens -onlyin "$HOME" 2>/dev/null \
           | while read -r p; do [ -f "$p/api/app.py" ] && echo "$p" && break; done)"
    [ -n "$hit" ] && { echo "$hit"; return; }
  fi
  echo ""
}

REPO="$(find_repo)"
if [ -z "$REPO" ]; then
  REPO="$HOME/lesson-lens"
  bold "Cloning LessonLens into $REPO"
  git clone "$REPO_URL" "$REPO" >/dev/null 2>&1 || die "clone failed — check your network or clone it by hand"
fi
cd "$REPO"

# Absolute: the launch below runs from api/, and relative paths there resolved
# against the wrong directory — the pidfile landed outside the repo entirely,
# so --stop could never find the server it had just started.
PIDFILE="$REPO/.lessonlens-server.pid"
LOGFILE="$REPO/.lessonlens-server.log"

stop_server() {
  if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
    kill "$(cat "$PIDFILE")" 2>/dev/null || true
    sleep 1
    info "stopped server (pid $(cat "$PIDFILE"))"
  fi
  rm -f "$PIDFILE"
  # Anything else squatting on the port would make the restart look broken.
  if command -v lsof >/dev/null 2>&1; then
    local squatter
    squatter="$(lsof -ti :"$PORT" 2>/dev/null || true)"
    [ -n "$squatter" ] && { kill $squatter 2>/dev/null || true; info "freed port $PORT"; }
  fi
  # Explicit success: with nothing to stop the last test above is false, and
  # under `set -e` that would abort the whole script right before startup.
  return 0
}

case "${1:-}" in
  --stop) bold "Stopping LessonLens"; stop_server; exit 0 ;;
  --logs) exec tail -f "$LOGFILE" ;;
esac

bold "LessonLens — local setup in $REPO"

# --- update ---------------------------------------------------------------
if git rev-parse --git-dir >/dev/null 2>&1; then
  BRANCH="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo '?')"
  DEFAULT_BRANCH="$(git symbolic-ref --quiet --short refs/remotes/origin/HEAD 2>/dev/null | sed 's#^origin/##')"
  DEFAULT_BRANCH="${DEFAULT_BRANCH:-main}"

  if [ -n "$(git status --porcelain)" ]; then
    warn "uncommitted changes on '$BRANCH' — not touching git. Commit or stash them,"
    warn "then re-run. To discard them:  git checkout -- . && git clean -fd"
  elif [ "$BRANCH" != "$DEFAULT_BRANCH" ]; then
    # A stale feature branch is the likeliest reason for "I pulled but nothing
    # changed" — say so and move, rather than silently serving old code.
    warn "on branch '$BRANCH', not '$DEFAULT_BRANCH' — switching"
    git checkout "$DEFAULT_BRANCH" >/dev/null 2>&1 || die "could not switch to $DEFAULT_BRANCH"
    git pull --ff-only >/dev/null 2>&1 || true
    info "now on $DEFAULT_BRANCH: $(git log --oneline -1)"
  else
    git pull --ff-only >/dev/null 2>&1 && info "updated to $(git log --oneline -1)" \
      || warn "git pull skipped (branch may not track a remote)"
  fi
fi

# --- python ---------------------------------------------------------------
command -v python3 >/dev/null 2>&1 || die "python3 not found — install it, then re-run"
if [ ! -x ".venv/bin/python" ]; then
  info "creating .venv"
  python3 -m venv .venv || die "could not create a virtualenv"
fi
PY=".venv/bin/python"
# A venv keeps this off the system Python, which recent macOS marks as
# externally managed and refuses to install into.
"$PY" -m pip install --quiet --upgrade pip >/dev/null 2>&1 || true
info "installing python dependencies (quiet, may take a minute)"
"$PY" -m pip install --quiet -r api/requirements.txt || die "pip install failed — see the error above"

# --- web ------------------------------------------------------------------
if command -v npm >/dev/null 2>&1; then
  if [ ! -d web/dist ] || [ -n "$(find web/src -newer web/dist -type f -print -quit 2>/dev/null)" ]; then
    info "building the web UI"
    (cd web && npm install --silent >/dev/null 2>&1 && npm run build >/dev/null 2>&1) \
      || warn "web build failed — the API will still run, but the UI may be stale"
  else
    info "web UI is up to date"
  fi
else
  warn "npm not found — skipping the UI build (API only). Install Node to get the web app."
fi

# --- config ---------------------------------------------------------------
if [ ! -f .env ]; then
  bold "First run — creating .env"
  if [ -t 0 ]; then
    printf '  Email for your local login [%s]: ' "$DEFAULT_EMAIL"
    read -r EMAIL_IN || true
    EMAIL="${EMAIL_IN:-$DEFAULT_EMAIL}"
    printf '  Password (16+ characters, hidden): '
    read -rs PASS_IN || true
    echo
  else
    # Non-interactive (CI, a pipe, a wrapper script): take it from the
    # environment rather than blocking forever on a prompt nobody can answer.
    EMAIL="$DEFAULT_EMAIL"
    PASS_IN="${LESSONLENS_PASSWORD:-}"
    [ -n "$PASS_IN" ] || die "no TTY and LESSONLENS_PASSWORD is unset — set it, or run this in a terminal"
    info "non-interactive: using LESSONLENS_EMAIL/LESSONLENS_PASSWORD from the environment"
  fi
  [ ${#PASS_IN} -ge 16 ] || die "password must be at least 16 characters (the app enforces this)"
  cat > .env <<EOF
LESSONLENS_TARGET=local
LESSONLENS_LOCAL_URL=http://127.0.0.1:$PORT
# The MCP server always resolves the HOSTED target, so it needs this too —
# pointed at the local app.
LESSONLENS_API_URL=http://127.0.0.1:$PORT
LESSONLENS_EMAIL=$EMAIL
LESSONLENS_PASSWORD=$PASS_IN
EOF
  chmod 600 .env
  info "wrote .env (chmod 600)"
else
  info "using existing .env"
fi
set -a; . ./.env; set +a

# --- account --------------------------------------------------------------
# Registration needs an invitation, so a fresh database has no way in. Seed the
# first account directly rather than leaving you at a login screen you cannot pass.
"$PY" - <<'PYEOF'
import os, sys
sys.path.insert(0, "api")
os.chdir("api")
import app
from werkzeug.security import generate_password_hash

email = os.environ.get("LESSONLENS_EMAIL", "")
password = os.environ.get("LESSONLENS_PASSWORD", "")
conn = app.get_db()
row = conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
if row:
    print(f"  account ok: {email}")
else:
    conn.execute(
        "INSERT INTO users (email, password_hash, display_name, is_admin, status)"
        " VALUES (?, ?, ?, 1, 'active')",
        (email, generate_password_hash(password), email.split("@")[0]),
    )
    conn.commit()
    print(f"  created account: {email}")
conn.close()
PYEOF

# --- server ---------------------------------------------------------------
stop_server
info "starting the server on port $PORT"
(
  cd api
  PORT="$PORT" nohup "$REPO/$PY" app.py > "$LOGFILE" 2>&1 &
  echo $! > "$PIDFILE"
)

for _ in $(seq 1 40); do
  if curl -fsS -o /dev/null "http://127.0.0.1:$PORT/api/health" 2>/dev/null; then
    READY=1; break
  fi
  sleep 0.5
done
[ "${READY:-0}" = "1" ] || { warn "server did not come up — last lines of $LOGFILE:"; tail -20 "$LOGFILE"; exit 1; }
info "server ready at http://127.0.0.1:$PORT"

# --- preflight ------------------------------------------------------------
echo
bold "Preflight"
set +e
"$PY" scripts/doctor.py
DOCTOR_STATUS=$?
set -e

echo
bold "Open http://127.0.0.1:$PORT"
cat <<EOF

  Next:
    1. In LINE for Mac: open the lesson chat -> chat menu (top right)
       -> Save chat history -> save the .txt to Downloads or Desktop.
    2. Pull it in:   LESSONLENS_TARGET=local make update-all
    3. Daily Review is at the top of the Dashboard.

  Server control:
    ./start-local.sh --logs    follow the log
    ./start-local.sh --stop    stop it
    ./start-local.sh           restart (safe to re-run any time)
EOF

exit $DOCTOR_STATUS
