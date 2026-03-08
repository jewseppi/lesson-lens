# Language Lesson Summarizer

Turns exported LINE/chat lesson transcripts into structured study packages:
lesson summaries, flashcards, review exercises, and a mobile-friendly viewer.

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

## GoDaddy Deployment

This app can be deployed the same basic way as xlsvc on shared hosting with
Passenger.

Recommended production shape:

- `language/api/` runs the Flask app under Passenger
- `language/web/dist/` contains the built React frontend
- Flask serves the built frontend and the `/api/*` routes from the same domain
- SQLite stays as `api/lessonlens.db`
- Runtime folders such as `raw-exports/`, `processed/`, and `summaries/` stay
	on the server as writable directories

For deployment:

1. Build the frontend with `npm run build` in `web/`
2. Install Python dependencies from `api/requirements.txt`
3. Run Passenger with `api/passenger_wsgi.py`
4. Initialize the database with `python -c "from app import init_db; init_db()"`
5. Ensure `api/`, `raw-exports/`, `processed/`, and `summaries/` are writable by the app user

The Flask app now serves the built SPA directly, so you can point a single app
at a single domain such as `lens.jsilverman.ca`.

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
# Parse an export file into structured sessions
python scripts/parse_line_export.py --input raw-exports/DOC-20260307-WA0006 --run-id 2026-03-07_01

# Validate parser output
python scripts/validate_sessions.py --run-id 2026-03-07_01

# Generate lesson package (summary, flashcards, HTML)
python scripts/generate_outputs.py --run-id 2026-03-07_01 --session 2024-01-16

# Run quality checks
python scripts/quality_check.py --run-id 2026-03-07_01 --session 2024-01-16

# Or run everything end-to-end
python scripts/run_all.py --input raw-exports/DOC-20260307-WA0006 --run-id 2026-03-07_01
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

All commands accept `--input`, `--run-id`, and `--config` flags.

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
