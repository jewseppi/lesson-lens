# LessonLens Repo Workflow

When the user provides a LINE export text file or an absolute path to one, prefer the repo-local bridge command instead of manually clicking through the UI.

Primary command:

```bash
python scripts/agent_bridge.py --file /absolute/path/to/export.txt --provider openai
```

Variants:

```bash
python scripts/agent_bridge.py --file /absolute/path/to/export.txt --sync-only
python scripts/agent_bridge.py --file /absolute/path/to/export.txt --session-id 2026-03-05 --provider anthropic
python scripts/agent_bridge.py --file /absolute/path/to/export.txt --manual-agent
python scripts/agent_bridge.py --install-lesson-json /absolute/path/to/lesson-data.json --session-id 2026-03-05
```

Behavior expectations:

- Ensure the local admin user exists.
- Sync the export into the database.
- Default to generating the latest session unless the user specifies a session.
- If summary generation fails because an API key is missing, report that clearly and stop after sync.
- Prefer OpenAI unless the user explicitly asks for Claude/Anthropic.

If the user explicitly wants the agent to do the summarization directly rather than
using an external provider, use this workflow instead:

1. Prepare the manual bundle with:

```bash
python scripts/agent_bridge.py --file /absolute/path/to/export.txt --manual-agent
```

2. Inspect the parsed session transcript.
3. Author a valid `lesson-data.json` matching the repo schema.
4. Install it with:

```bash
python scripts/agent_bridge.py --install-lesson-json /absolute/path/to/lesson-data.json --session-id YYYY-MM-DD
```

This is the preferred path when the user says the agent itself should produce the
summary and app-consumable assets.

Environment requirements for generation:

- `OPENAI_API_KEY` for OpenAI
- `ANTHROPIC_API_KEY` for Anthropic
- `GEMINI_API_KEY` or `GOOGLE_API_KEY` for Gemini

If the user asks for a summary from a newly provided file, do not tell them to run CLI scripts manually. Use the bridge script.

## One-touch hosted update (macOS)

When the user just wants to "update" from their Mac after a LINE lesson — pull
the latest chat and images into the **hosted** app in one step — prefer the
one-touch updater over the agent bridge (which targets a local test DB):

```bash
make update            # or: python scripts/line_mac_sync.py
```

It reads `LESSONLENS_API_URL` / `LESSONLENS_EMAIL` / `LESSONLENS_PASSWORD`
(env or repo `.env`), finds the newest LINE export, scans LINE's media cache for
new images, uploads both, and generates the latest session. Use
`--images-dir <folder>` if the cache scan yields nothing, `--dry-run` to
preview, and `--sync-only` to skip generation.

## Generating summaries yourself (preferred — no API key)

When the user asks you to summarize lessons, do the work yourself through the
**`lessonlens-hosted` MCP server** rather than calling
`/api/sessions/{id}/generate`. The generate endpoint bills a provider per token;
you are already paid for. The hosted MCP server talks to the user's remote
instance directly, so there is no local database to keep in sync.

Workflow:

1. `list_sessions(needs_summary_only=True)` — find what needs doing.
2. `get_session(session_id)` — read the transcript.
3. `get_retrieval_context(session_id)` — prior vocabulary/corrections, so new
   material stays consistent with earlier lessons.
4. `get_session_attachments(session_id)` — worksheets/whiteboard photos, if any.
5. `lesson_data_schema()` — the skeleton to fill in.
6. `store_summary(session_id, lesson_data_json)` — write it back.

Two rules the hosted importer enforces, which `store_summary` checks first and
will tell you about explicitly:

- `schema_version` must be exactly `"lesson-data.v1"`.
- `lesson_date` must equal the `session_id`.

If the user is working against a **local** instance instead, use the original
`lessonlens` MCP server (same tool names, local SQLite) and then
`make push` to send it up to hosted.