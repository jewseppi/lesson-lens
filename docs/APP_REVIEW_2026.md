# LessonLens — 2026 App Review & Streamlining

_A fresh look at what the app is for, where it stands, the gaps that made it
feel fragmented, and the changes in this branch that close them._

## The problem, restated

LessonLens turns your LINE Chinese lessons with a teacher into structured study
material: it parses a LINE chat export into per-lesson **sessions**, then
generates a lesson package per session — summary (`.md`/`.html`), structured
`lesson-data.json`, Anki-compatible `flashcards.csv`, and review drills — served
through a React viewer with a study mode. Images shared during lessons
(worksheets, whiteboard photos) attach to their session so they show up
alongside the notes.

The hard parts the app solves well: splitting a continuous chat into lessons by
date/time gaps, classifying each message (lesson content vs logistics vs media),
handling both **mobile and desktop** LINE export formats, Traditional-Chinese +
pinyin policy, and matching loose photos to sessions by EXIF/timestamp with
confidence bands and a manual-reassign fallback.

## What's actually in the repo (current state)

- **Flask API + SQLite** (`api/app.py`, ~5.3k lines): JWT auth, incremental
  `/api/sync`, image attachments with EXIF auto-matching, backup export/import,
  an eval harness, policies, fine-tune export, retrieval context, analytics, and
  an MCP server. Deployed via Passenger; a nightly GitHub Action backs it up.
- **React/Vite viewer** (`web/`): Dashboard, Sessions, SessionDetail, Summary,
  StudyMode, Upload, Settings, Eval, Admin.
- **Pipeline scripts** (`scripts/`): `parse_line_export.py` →
  `validate_sessions.py` → `generate_outputs.py` → `quality_check.py`, plus
  `agent_bridge.py` and `install_manual_summary.py`.
- **Providers**: OpenAI, Anthropic, Gemini, and local Ollama /
  OpenAI-compatible. Provider + model are selectable in Settings and per run.
- **Generation paths**: in-app (provider-backed), an Ollama GitHub-Actions
  runner, or an agent-authored manual bundle.

It is a genuinely mature app. The friction was never capability — it was the
**update path**.

## Gaps found (and what closes them here)

### 1. Models were stale — _fixed_

Defaults were `gpt-4o` / `claude-sonnet-4-20250514` / `gemini-2.0-flash`, and the
manual-agent template hardcoded a fictional `"GPT-5.4"`. Updated to current
models, with Opus 5 as the Anthropic default:

| Provider  | Was                          | Now                 |
| --------- | ---------------------------- | ------------------- |
| Anthropic | `claude-sonnet-4-20250514`   | `claude-opus-5`     |
| OpenAI    | `gpt-4o`                     | `gpt-5.6`           |
| Gemini    | `gemini-2.0-flash`           | `gemini-3.6-flash`  |

Changes: `config/pipeline.yaml` gains a `generation.provider_models` map;
`api/app.py` and `scripts/generate_outputs.py` resolve the per-provider default
from it (explicit `--model` still wins); `web/src/pages/SettingsPage.tsx`
`CLOUD_MODEL_DEFAULTS` refreshed; the fake template model replaced with a neutral
`manual-agent` marker. The defaults stay editable in Settings — refresh the map
as new models ship.

### 2. Ingestion was manual and fragmented — _the real pain, now fixed_

LINE's text export contains only `[Photo]`/`Photos` placeholders, never the
image bytes. So the old flow was: export the `.txt` by hand → save every image
out of LINE one at a time → sync the txt → upload images and hope EXIF matched →
generate per session. There was no automated image grab and no single entry
point, so the app went un-updated because the path wasn't clear.

**New: `scripts/line_mac_sync.py` + `make update`** — a one-touch macOS updater
that targets the **hosted** server (the same one the nightly backup already
talks to via `LESSONLENS_API_URL`). One command:

1. finds the newest LINE chat export `.txt` (searches Downloads/Desktop/
   Documents, prefers name-hinted files, or takes `--export-file`);
2. scans LINE's on-disk media cache for images, identifying them by **magic
   bytes** (LINE stores media with hashed, extension-less names) and skipping
   anything already synced (deduped by SHA-256, with an mtime watermark so
   re-runs are fast);
3. uploads the chat + new images to the hosted API (`/api/sync`,
   `/api/attachments/upload`), which EXIF-matches images to sessions; and
4. generates the newest session (`--sync-only` skips this, `--generate-all`
   fills every session missing a summary).

It relies on two properties already true of the server: `/api/sync` is
incremental (duplicate exports are a no-op; new sessions merge without deleting
existing summaries) and attachment upload dedupes by content hash — so running
`make update` repeatedly is safe and idempotent. The script is standard-library
only, so it runs on a stock macOS Python with no `pip install`.

### 3. No single "update button" — _fixed_

- `make update` / `make update-dry` / `make update-sync-only` targets.
- A double-clickable **`update.command`** for Finder (keeps the Terminal window
  open to show the summary).

### 4. Doc drift and rough edges — _fixed_

- The README referenced `scripts/run_all.py`, which didn't exist. It now exists
  and chains parse → validate → generate → quality-check with correct paths.
- The README Quick Start showed wrong flags for validate/generate/quality-check
  (`--run-id` where the scripts actually take `--input`/`--sessions`); corrected.
- `.env.example` documents the hosted-server vars the updater needs.

## The honest constraint

macOS LINE has **no scriptable "export chat" hook** — no AppleScript, no CLI —
so the one unavoidable manual step is hitting *Export* in LINE once per update.
Everything after that is the single `make update`. And because recent LINE
versions encrypt local storage, the media-cache scan is best-effort: if it
doesn't surface usable images on a given machine, `--images-dir <folder>` points
the updater at a folder where you save images instead, and the rest of the flow
is unchanged. This is a deliberate design choice — a reliable folder-watch
fallback over a brittle attempt to decrypt LINE's DB.

## Generation without a provider API key (second pass)

The first pass of the updater targeted the hosted server and called
`/api/sessions/{id}/generate` — which needs a **provider API key on the server**
and bills per token. That discarded the reason the agent bridge and MCP server
exist: doing generation with a flat-rate **subscription agent**.

The pieces existed but didn't connect: `api/mcp_server.py` had
`store_summary(provider="claude-agent")` — the no-API-key write path — but was
local-only (it imports `app.py` and every tool calls `get_db()`).

**Added `api/mcp_server_hosted.py`**: a sibling MCP server exposing the same tool
names against the hosted REST API over HTTP. An authenticated CLI agent now reads
and writes the hosted instance directly — no local database, no sync-up step, no
API key. It imports only the standard library plus `mcp`, so it runs on a machine
that has never installed Flask.

Two rules the hosted importer enforces are validated client-side so the agent
gets an actionable message instead of an opaque HTTP 400: `schema_version` must
be `lesson-data.v1`, and `lesson_date` must equal the session id.

The updater gained `--generate-with agent|provider|none`. Agent mode runs
`LESSONLENS_AGENT_CMD` once per session needing a summary — and with that unset
it is deliberately **prepare-only**, reporting what needs doing and running
nothing, so a misconfigured command can't fan out across the backlog
(`--max-sessions`, default 10, caps it further).

Config for both now resolves through one module, `scripts/lessonlens_config.py`,
and the HTTP client is shared via `scripts/lessonlens_client.py`.

### Backups now carry images

`_build_backup_archive` exported chat, parse artifacts, and summaries — but not
attachments. So `/api/backup/sync-remote` (the Settings "sync to remote" button)
**silently dropped every lesson photo**. Fixed in `api/backup_helpers.py`, with
the awkward part being identity: `attachments.id` is AUTOINCREMENT and
`session_attachments.session_id` stores the integer `sessions.id`, so neither is
portable. The wire format keys attachments by **sha256** and sessions by their
**session-id string**, and the import remaps both to local ids, deduping so a
re-import changes nothing. v1 archives still import (the attachment step no-ops).

Splitting these helpers out of `app.py` also made them testable without the
Flask stack — `make test` runs 48 dependency-light tests.

> **Noted, not fixed:** `GET /api/attachments` joins
> `session_attachments.session_id` against the session *string*, while the upload
> path and `GET /api/sessions/{id}/attachments` both use the integer
> `sessions.id`. That listing endpoint therefore under-reports session
> assignments. Out of scope here; the export tolerates both conventions.

## Automation added (this branch)

- **Backlog fill on Opus 5**: `make update-all` syncs and then generates every
  session still missing a summary — idempotent, so it never re-generates ones
  you already have. Use it to bring older lessons up to the stronger model
  (spot-check a couple first).
- **Scheduled runs**: `make schedule` installs a `launchd` job
  (`scripts/launchd/`, daily at 20:00 by default;
  `install.sh --hour N` to change, `make unschedule` to remove). It runs the
  gap-filling update, so a run with no new export is a cheap no-op. The one step
  that can't be automated is the LINE export itself (no macOS scripting hook).

## Suggested next steps (not in this branch)

- **Image relevance filtering**: LINE's cache holds stickers and UI chrome too;
  a lightweight size/aspect heuristic (or a vision pass) before upload would cut
  noise in attachments.
- **Confidence surfacing in the UI**: auto-matched images carry a confidence
  band already — showing low-confidence matches for one-tap confirmation would
  make the automated grab fully trustworthy.
