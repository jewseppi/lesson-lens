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
#   ./start-local.sh           start (or restart) everything
#   ./start-local.sh --stop    stop the server
#   ./start-local.sh --logs    follow the server log
#   ./start-local.sh --status  is it running, and where?
#   ./start-local.sh --no-git  don't switch branches or pull
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

port_is_free() {
  # A bind test is the only honest check: lsof may be absent, and macOS system
  # services (AirPlay Receiver owns 5000, sometimes 7000) hold ports that lsof
  # shows but you cannot kill.
  "${PY:-python3}" - "$1" <<'PYEOF' 2>/dev/null
import socket, sys
s = socket.socket()
try:
    s.bind(("127.0.0.1", int(sys.argv[1])))
except OSError:
    sys.exit(1)
finally:
    s.close()
PYEOF
}

# Report exactly what is and isn't running, so "refused to connect" always has an
# answer that doesn't require reading a log.
show_status() {
  bold "LessonLens status"
  local running=0
  if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
    info "process: running (pid $(cat "$PIDFILE"))"
    running=1
  else
    warn "process: NOT running"
  fi
  if curl -fsS -o /dev/null "http://127.0.0.1:$PORT/api/health" 2>/dev/null; then
    info "http:    responding at http://127.0.0.1:$PORT"
    info ""
    info "Open http://127.0.0.1:$PORT"
  else
    warn "http:    nothing answering on port $PORT"
    if [ "$running" = "1" ]; then
      warn "the process is alive but not serving — last log lines:"
      tail -15 "$LOGFILE" 2>/dev/null | sed 's/^/     /'
    else
      warn "start it with:  ./start-local.sh"
    fi
  fi
  [ -f "$LOGFILE" ] && info "log:     $LOGFILE" || warn "log:     none yet (the server has never started here)"
  return 0
}

NO_GIT=0
for arg in "$@"; do
  case "$arg" in
    --stop)   bold "Stopping LessonLens"; stop_server; exit 0 ;;
    --logs)   exec tail -f "$LOGFILE" ;;
    --status) show_status; exit 0 ;;
    --no-git) NO_GIT=1 ;;
    -h|--help)
      sed -n '3,15p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
      exit 0 ;;
    *) die "unknown option: $arg (try --help)" ;;
  esac
done

bold "LessonLens — local setup in $REPO"

# --- get onto the right branch, up to date --------------------------------
# Running from a stale feature branch is the single most confusing failure:
# everything "works", you just get old code, and `git pull` reports nothing to
# do. So this actively lands you on the default branch. Local work is stashed
# rather than refused — recoverable, and reported loudly — because leaving you
# stuck on a dead branch is the worse outcome. Skip it all with --no-git.
sync_git() {
  git rev-parse --git-dir >/dev/null 2>&1 || return 0

  local branch default
  branch="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo '?')"
  git fetch origin >/dev/null 2>&1 || warn "could not reach origin — working offline"
  default="$(git symbolic-ref --quiet --short refs/remotes/origin/HEAD 2>/dev/null | sed 's#^origin/##')"
  if [ -z "$default" ]; then
    for guess in main master; do
      git rev-parse --verify --quiet "origin/$guess" >/dev/null 2>&1 && { default="$guess"; break; }
    done
  fi
  default="${default:-main}"

  if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
    # Tracked changes only: stashing untracked files would swallow a
    # hand-dropped copy of this very script.
    if git stash push -m "start-local.sh autostash $(date '+%Y-%m-%d %H:%M')" >/dev/null 2>&1; then
      STASHED=1
      warn "stashed your uncommitted changes on '$branch' — restore with: git stash pop"
    fi
  fi

  if [ "$branch" != "$default" ]; then
    warn "on '$branch', which is not '$default' — switching so you get current code"
    if ! git checkout "$default" >/dev/null 2>&1; then
      # An untracked file that also exists on the target branch blocks checkout.
      # Move the offenders aside instead of dead-ending.
      local blocked
      blocked="$(git checkout "$default" 2>&1 | sed -n 's/^\t//p' | tr -d '\r')"
      if [ -n "$blocked" ]; then
        while IFS= read -r f; do
          [ -e "$f" ] || continue
          mv "$f" "$f.local-backup" && warn "moved untracked $f -> $f.local-backup"
        done <<< "$blocked"
      fi
      git checkout "$default" >/dev/null 2>&1 || die "could not switch to $default — resolve git by hand, then re-run"
    fi
  fi

  git pull --ff-only >/dev/null 2>&1 || warn "could not fast-forward $default (diverged or offline)"
  info "on $default: $(git log --oneline -1)"
}

if [ "${NO_GIT:-0}" = "1" ]; then
  info "--no-git: leaving the working tree exactly as it is"
else
  sync_git
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
# --- pick a port that will actually bind --------------------------------
# Do this before .env is written, so the file records the port we really use.
stop_server
if ! port_is_free "$PORT"; then
  ORIGINAL_PORT="$PORT"
  for candidate in 5002 5055 5175 8000 8080 8765; do
    if port_is_free "$candidate"; then PORT="$candidate"; break; fi
  done
  if [ "$PORT" = "$ORIGINAL_PORT" ]; then
    die "port $PORT is taken and no fallback was free — pick one: PORT=9123 ./start-local.sh"
  fi
  warn "port $ORIGINAL_PORT is in use (on macOS, AirPlay Receiver holds 5000);"
  warn "using $PORT instead — the URL below reflects it"
  # An existing .env still points at the old port; keep it consistent or the
  # updater and MCP server will aim at a server that isn't there.
  if [ -f .env ]; then
    "${PY:-python3}" - "$ORIGINAL_PORT" "$PORT" <<'PYEOF'
import pathlib, sys
old, new = sys.argv[1], sys.argv[2]
p = pathlib.Path(".env")
p.write_text(p.read_text().replace(f"127.0.0.1:{old}", f"127.0.0.1:{new}"))
PYEOF
    info "updated .env to port $PORT"
  fi
fi

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
# (stop_server already ran during port selection above.)
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
if [ "${READY:-0}" != "1" ]; then
  # Never exit quietly here: the next thing that happens is the browser saying
  # "refused to connect", with nothing on screen explaining why.
  echo
  printf '\033[31m%s\033[0m\n' "  THE SERVER DID NOT START — the app is NOT running."
  echo
  warn "last lines of $LOGFILE:"
  tail -20 "$LOGFILE" 2>/dev/null | sed 's/^/     /'
  echo
  if grep -qi "address already in use" "$LOGFILE" 2>/dev/null; then
    warn "something else is on port $PORT. Retry on another:  PORT=9123 ./start-local.sh"
  elif grep -qi "ModuleNotFoundError\|ImportError" "$LOGFILE" 2>/dev/null; then
    warn "a dependency is missing. Try:  rm -rf .venv && ./start-local.sh"
  else
    warn "re-run to see it again, or send me the lines above:  ./start-local.sh"
  fi
  exit 1
fi
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
    ./start-local.sh --status  is it running, and where?
    ./start-local.sh --logs    follow the log
    ./start-local.sh --stop    stop it
    ./start-local.sh           restart (safe to re-run any time)
EOF

if [ "${STASHED:-0}" = "1" ]; then
  echo
  warn "your uncommitted changes were stashed to get you onto the default branch."
  warn "get them back with:  git stash pop        (see them with: git stash list)"
fi

exit $DOCTOR_STATUS
