# Language Lesson Summarizer

Turns exported LINE/chat lesson transcripts into structured study packages:
lesson summaries, flashcards, review exercises, and a mobile-friendly viewer.

## Run it locally

```bash
./start-local.sh
```

One command, from a fresh machine or an existing checkout. It clones the repo if
needed, switches to the default branch if you're sitting on a stale one, creates
a virtualenv, installs dependencies, builds the web UI, creates your login if the
database is new, starts the server **in the background**, and finishes by running
the preflight — so you end on the real state of your setup rather than a wall of
build output. Safe to re-run; that's also how you restart it.

```bash
./start-local.sh --logs    # follow the server log
./start-local.sh --stop    # stop it
PORT=8000 ./start-local.sh # somewhere other than 5001
```

If you'd rather drive it yourself, `make run-local` builds the UI and runs the
app in the foreground (`make serve` skips the rebuild). Either way the API serves
the built UI from `web/dist`, so there is no second process to babysit.

The database upgrades itself on start — new tables are created in place and
existing sessions, summaries, and images are untouched, so there is no migration
step after pulling.

Then point the updater at it instead of a hosted server:

```bash
LESSONLENS_TARGET=local make doctor
LESSONLENS_TARGET=local make update-all
```

## Check your setup first

```bash
make doctor          # config, hosted login, sessions, restore points, MCP, agent command
make doctor-agent    # ...and actually run your agent command once
```

`doctor` walks the whole chain — **including the LINE side on your Mac** — and,
when something is wrong, prints the fix rather than a stack trace:

- **LINE chat export**: which file it will use, or every directory it searched
  and how to find yours (`mdfind`) if there isn't one.
- **LINE image cache**: how many images are visible, or the `--images-dir`
  fallback when LINE's encrypted storage yields nothing.
- Hosted login, sessions, restore points, MCP server, agent command.

**Run it before your first export.** On a fresh Mac there is no export yet, and
doctor says so as a `TODO` with the steps to make one — not a failure. It only
reports `FAIL` for things that are actually wrong. If LINE isn't installed at
all, it says *that* instead, because LINE only ever talks to the machine it runs
on: the updater has to run on your Mac, and no server or container can reach it.

The export search covers the **sandboxed** Mac App Store LINE (whose Downloads
lives inside its container, not your home folder) as well as iCloud Desktop and
Documents. Exit code is 0 only when every required check passes, so it can gate a
scheduled run; `--skip-line` drops the Mac-side checks when running on a server.

## One-touch update from your Mac

If you take lessons over LINE on a Mac, `make update` is the fast path to keep
your hosted LessonLens in sync:

```bash
# 1) One-time: copy .env.example to .env and set the hosted-server vars
#    LESSONLENS_API_URL / LESSONLENS_EMAIL / LESSONLENS_PASSWORD
# 2) In LINE for Mac, open the lesson chat -> chat menu (top-right) ->
#    Save chat history -> save the .txt to Downloads or Desktop.
#    This is the one manual step: macOS LINE exposes no scripting hook, so
#    nothing can trigger it for you. `make doctor` lists it as a TODO until
#    you've done it once; after that, re-exporting is all that's ever needed.
# 3) Then, whenever you want to refresh:
make update            # sync newest export + any new images, generate latest lesson
make update-dry        # preview what would sync (no network, no changes)
make update-sync-only  # sync but skip generation
```

`make update` runs `scripts/line_mac_sync.py`, which finds the newest LINE
export, scans LINE's on-disk media cache for images you haven't synced yet
(deduped by content hash, so re-runs are cheap and idempotent), uploads both to
your hosted server, and generates the newest session. If the encrypted cache
doesn't yield usable images on your machine, point it at a folder where you save
images instead:

```bash
python scripts/line_mac_sync.py --images-dir ~/Pictures/line-lessons
```

### How images find their lesson

Each image is matched to a session by capture time. LINE strips EXIF from photos
you *receive*, so the updater sends every file's original modification time
alongside the bytes — an upload transmits content only, and the server's copy of
a file is always stamped "just now", which would match nothing.

Images also routinely arrive *before* the chat export that explains them: LINE
caches a photo the moment it lands, but you take the export later. An image
uploaded in that gap has no session to match yet, so after a sync that adds new
sessions the updater re-runs the match (`POST /api/attachments/rematch`) and
those images fall into place on their own. Anything still unmatched — a photo
from outside any lesson window — stays in the Unmatched list to assign or ignore.

Prefer clicking to typing? Double-click **`update.command`** in Finder (run
`chmod +x update.command` once).

To backfill older lessons after a model upgrade, `make update-all` syncs and
then generates every session still missing a summary (idempotent — it never
re-generates ones you already have). To make the update run on its own,
`make schedule` installs a `launchd` job (daily at 20:00 by default;
`bash scripts/launchd/install.sh --hour 7` to change it, `make unschedule` to
remove it). The one step it can't automate is the LINE export itself — macOS
LINE has no scripting hook — but a scheduled run harmlessly no-ops until there's
a new export to pick up.

### Generating with your agent subscription (no API key)

Provider-backed generation bills per token. If you already pay for a coding-agent
subscription (Claude Code, Copilot, Codex CLI), the agent can author the lesson
package itself — that path costs nothing extra and needs no API key on the
server. Two ways to use it:

**1. Drive it interactively.** Point your agent at the hosted MCP server, which
exposes the same tools as the local one but reads and writes your *hosted*
instance directly — no local database, no sync-up step:

```jsonc
// .mcp.json already contains this entry; fill in the env vars (or use .env)
"lessonlens-hosted": {
  "command": "python3",
  "args": ["api/mcp_server_hosted.py"],
  "env": { "LESSONLENS_API_URL": "", "LESSONLENS_EMAIL": "", "LESSONLENS_PASSWORD": "" }
}
```

Then ask your agent: *"list the sessions that need summaries, then write one for
the newest."* It uses `list_sessions(needs_summary_only=True)` → `get_session` →
`get_retrieval_context` → `store_summary`. It needs only Python's standard
library plus `mcp` — no Flask, no provider SDK.

**2. Let the updater invoke it.** Set `LESSONLENS_AGENT_CMD` in `.env` and run
`make update-agent`. The updater finds every session needing a summary and runs
your command once per session (`{session_id}` is substituted).

`.env.example` has ready-to-paste presets for Claude Code (`claude -p`), Copilot
CLI (`copilot -p`), and Codex (`codex exec`) — pick one and uncomment it. Verify
with `make doctor` (checks the binary resolves) then `make doctor-agent` (runs it
once for real).

> With `LESSONLENS_AGENT_CMD` unset, agent mode is deliberately **prepare-only**:
> it reports what needs summarizing and runs nothing. `--max-sessions` (default
> 10) caps how many run in one pass.

### Restore points (automatic safety net)

Every operation that rewrites your parsed data — chat sync, backup import, and
re-parse — takes a **restore point** first: a full snapshot (chat, summaries, and
images) captured immediately before the change.

- Kept for **7 days**, then deleted automatically, and capped at **20 per user**
  (`LESSONLENS_RESTORE_RETENTION_DAYS` / `LESSONLENS_RESTORE_MAX_POINTS`) — the
  cap matters because snapshots include images.
- Re-syncing an unchanged export is a no-op and does **not** create a snapshot,
  so the scheduled updater doesn't fill your disk.
- Summaries have their own, cheaper history: they're append-only, so the summary
  page has a **History** toggle to restore an earlier version without touching a
  snapshot.
- **Settings → Restore Points** lists them with what each contains and when it
  expires, and gives you a confirm-guarded **Roll Back** button plus a download.
- Rolling back **takes a snapshot of the current state first**, so a rollback is
  itself undoable.
- Capture is best-effort and never blocks the operation it protects: a brand new
  account has nothing to snapshot, and a snapshot failure won't turn a working
  sync into a failed one.

This is the guardrail for changing the sync paths: if a bug slips in, the
previous state is already on disk before the mutation runs.

### If the hosted app isn't reachable

The older local-first flow still works: sync into a local instance, generate
there, then push up.

```bash
make update ARGS="--target local"   # sync into a local LessonLens
make push                           # push local -> hosted via /api/backup/sync-remote
```

Backups now carry **images** as well as chat and summaries, so the push no longer
silently drops your lesson photos (it used to).

See [docs/APP_REVIEW_2026.md](docs/APP_REVIEW_2026.md) for the design and the
wider review of the app's state and gaps.

## Daily Review

A single queue across every lesson, built for the five minutes before class.

The app already generated excellent study material and it went unused, because
reviewing meant picking a session, then a mode, then facing a whole deck. Three
decisions and an unbounded pile. Daily Review removes all three: the Dashboard
shows **one card** (`12 due · 🔥 5`) and one button, and the first item is
already on screen.

- **Time-boxed, not deck-boxed.** You pick 5 or 10 minutes; the queue fills that
  budget. Finishing is the goal, so it ends when the time does.
- **Ordered for you.** Your own corrections first — the highest-value thing to
  re-see — then whole sentences, then vocabulary. Most overdue first within each.
- **Two buttons.** *Again* / *Got it*. Four would be a decision.
- **Volume that earns its growth.** Starts at 5 items a day; every 3 consecutive
  days it steps up by 2, to a cap of 30. Miss two days and it eases back one
  step instead of collapsing — so a busy week doesn't cost you the habit.
- **Difficulty that earns its growth too.** A new item is asked
  中文 → English (*Recall*) — the easy direction, and the one lessons already
  exercise. Get it right twice and it flips to English → 中文 (*Produce*), which
  is the skill actually missing when your own turns in class are in English.
  Production is roughly three times harder, so leading with it on brand-new
  material is how a review habit dies in week one; each item graduates instead.
  Answer *Again* and it drops back to Recall rather than staying in the
  direction it just failed. Corrections are exempt — "you said X, what should it
  have been?" is production whichever way you turn it.

There is nothing to import or migrate: review joins `user_retrieval_items`,
which every generated summary already populates, so **every lesson you have is
in the pool the moment this ships**. A term taught across three lessons is one
item to remember, not three.

Items are scheduled with an SM-2-lite curve (`api/review_scheduler.py`): a
correct answer pushes the next showing out by the item's ease factor, *Again*
brings it back tomorrow and slows future growth. Suspend anything you never want
to see again from the review settings endpoint.

## Agent Bridge

If you want the repo to take a LINE export file path and run the full local workflow,
use:

```bash
python scripts/agent_bridge.py --file /absolute/path/to/export.txt --provider openai
```

This will sync the export into the app and generate summary/study materials for
the latest session. Use `--sync-only` to skip generation.

For the direct agent-authored path, prepare a manual work bundle with:

```bash
python scripts/agent_bridge.py --file /absolute/path/to/export.txt --manual-agent
```

That creates `source-session.json`, `transcript.txt`, and `lesson-data.template.json`
for the selected session.

Then install the finished lesson package with:

```bash
python scripts/agent_bridge.py --install-lesson-json /absolute/path/to/lesson-data.json --session-id 2026-03-05
```

If the lesson package is created outside the repo, users can also upload the
finished `lesson-data.json` from the Settings page in the UI. That path imports
the summary into SQLite and generates the companion files server-side.

If you want to author the lesson package manually from the parsed transcript and
install it into the app without calling an external provider, use:

```bash
python scripts/install_manual_summary.py --lesson-json /absolute/path/to/lesson-data.json --session-id 2026-03-05
```

That command writes `summary.md`, `summary.html`, and `flashcards.csv`, then stores
the lesson package in SQLite so the app can render it.

Before generating summaries, copy `.env.example` to `.env` and add either an
`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, or `GEMINI_API_KEY`.

Provider selection also lives in the Settings page.

The Settings page also includes a bulk action to generate summaries for all
parsed sessions that do not already have one.

## Deployment

This app can be deployed as a single Flask application that also serves the
built React frontend.

Recommended production shape:

- `language/api/` runs the Flask app
- `language/web/dist/` contains the built React frontend
- Flask serves the built frontend and the `/api/*` routes from the same domain
- SQLite stays as `api/lessonlens.db`
- Runtime folders such as `raw-exports/`, `processed/`, and `summaries/` stay
	on the server as writable directories

For deployment:

1. Build the frontend with `npm run build` in `web/`
2. Install Python dependencies from `api/requirements.txt`
3. Run the Flask app with your WSGI entrypoint, such as `api/passenger_wsgi.py`
4. Initialize the database with `python -c "from app import init_db; init_db()"`
5. Ensure `api/`, `raw-exports/`, `processed/`, and `summaries/` are writable by the app user

The Flask app serves the built SPA directly, so one deployment can handle both
the frontend and the API.

## Launch Modes

The repo now supports three realistic operating modes:

1. Self-hosted + agent bridge

	A user pulls the code, runs the app locally, and uses an agent alongside the
	repo. The agent bridge handles sync, provider generation, or manual-agent
	bundle prep/install.

2. Provider-backed app

	The app uses OpenAI, Anthropic, or Gemini for summary generation through the
	existing backend generation endpoint.

3. Future free/local-model mode

	Over time, the structured lesson packages produced by this repo can become a
	dataset for a smaller local or low-cost model. That future mode would reduce
	dependency on paid provider APIs and make the in-app generation experience
	cheaper and easier to ship.

Important constraint:

- The manual-agent bridge is a good launch path for advanced users who can run
  the repo locally with an agent.
- For broader public launch, the likely long-term path is either a provider-backed
  experience or a local/free model fine-tuned or prompted from the accumulated
  summary dataset.

## Quick Start

```bash
# Run everything end-to-end (parse → validate → generate → quality-check)
python scripts/run_all.py --input raw-exports/DOC-20260307-WA0006 --run-id 2026-03-07_01

# ...or run the stages individually:

# Parse an export file into structured sessions → processed/<run-id>/sessions.json
python scripts/parse_line_export.py --input raw-exports/DOC-20260307-WA0006 --run-id 2026-03-07_01

# Validate parser output
python scripts/validate_sessions.py --input processed/2026-03-07_01/sessions.json

# Generate lesson package (summary, flashcards, HTML) → summaries/<run-id>/<session>/
python scripts/generate_outputs.py --sessions processed/2026-03-07_01/sessions.json \
    --session-id 2024-01-16 --run-id 2026-03-07_01

# Run quality checks against the generated package
python scripts/quality_check.py --input summaries/2026-03-07_01/2024-01-16/lesson-data.json \
    --sessions processed/2026-03-07_01/sessions.json
```

## Commands

| Command                | Description                                                       |
| ---------------------- | ----------------------------------------------------------------- |
| `parse_line_export.py` | Parse chat export → `sessions.json` + `normalized_messages.jsonl` |
| `validate_sessions.py` | Validate `sessions.json` against schema + integrity rules         |
| `generate_outputs.py`  | Generate lesson package (`.md`, `.json`, `.csv`, `.html`)         |
| `agent_bridge.py` | Sync exports, call provider generation, or prepare/install manual-agent bundles |
| `install_manual_summary.py` | Install a manual `lesson-data.json` into app assets + SQLite |
| `quality_check.py`     | Check correction coverage, pinyin completeness, source refs       |
| `run_all.py`           | Full pipeline: parse → validate → generate → quality-check        |
| `line_mac_sync.py`     | macOS one-touch updater: sync newest LINE export + new images to the hosted app, then generate (`make update`) |

The pipeline scripts accept `--input`/`--sessions`, `--run-id`, and `--config`
flags (see each command's `--help`). `line_mac_sync.py` targets the hosted
server via `LESSONLENS_API_URL` / `LESSONLENS_EMAIL` / `LESSONLENS_PASSWORD`.

## Project Structure

```
language/
├── config/          # Pipeline configuration (pipeline.yaml)
├── schemas/         # JSON schemas for sessions.json + lesson-data.json
├── prompts/         # LLM prompt templates for summarization + study assets
├── scripts/         # Python pipeline scripts
├── raw-exports/     # Drop chat export files here
├── processed/       # Parser output (sessions, normalized messages)
├── summaries/       # Generated lesson summaries (.md)
├── flashcards/      # Anki-compatible CSV exports
├── html/            # Static mobile-friendly review pages
└── web/             # React/Vite viewer (Phase 5)
```

## Configuration

Edit `config/pipeline.yaml` for:

- Speaker alias mapping (teacher/student names)
- Timezone, lesson gap threshold
- Pinyin policy, output toggles
- LLM provider/model selection

## Output Formats

Each lesson generates:

- `lesson-summary.md` — Human-readable study notes
- `lesson-data.json` — Structured data for UI/app consumption
- `flashcards.csv` — Anki-compatible import
- `review.html` — Phone-friendly review page

## Defaults

- **Script**: Traditional Chinese
- **Pinyin**: Shown on every Chinese line
- **Translation**: English
- **Lesson boundaries**: Date/time heuristics with configurable gap threshold
