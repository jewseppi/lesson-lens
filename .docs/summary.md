# LessonLens Summary

## Current State

LessonLens is now a working local-first MVP for LINE lesson exports.

- Flask backend for auth, upload/sync, parsing, session browsing, analytics, and summary generation.
- Vite/React frontend for login, sync, sessions, summary view, and study mode.
- SQLite database for users, uploads, parse runs, sessions, analytics, invitation tokens, and generated lesson summaries.
- Sync flow now uploads and parses in one step.
- Summary generation is available from the UI and via a repo-local bridge script.

## Launch Strategy

There are three practical product modes now:

1. Self-hosted with an agent

	A user clones the repo, runs it locally, and uses the manual-agent bridge or
	provider-backed bridge alongside an agent. This is the most flexible near-term
	mode and fits power users.

2. Hosted app with provider-backed generation

	The backend calls OpenAI, Anthropic, or Gemini directly. This is the simplest
	product path for users who should not need a local agent setup.

3. Future free/local model mode

	The summaries, vocabulary sets, review assets, and correction patterns produced
	over time can serve as a structured dataset for a smaller model, retrieval layer,
	or local inference workflow. That would let the app offer a cheaper or free
	in-app generation path later.

Recommended framing:

- Near term: manual-agent bridge is acceptable for users who pull the repo and run
  it locally with an agent.
- Medium term: provider-backed generation is the practical hosted version.
- Longer term: use accumulated structured lesson outputs as a dataset to support
  a local or low-cost model path.

Important caution:

- If summary data becomes training or evaluation data, keep the source transcripts,
  generated lesson packages, and any user-specific data clearly partitioned.
- Treat training eligibility as opt-in if this becomes a multi-user product.

## What Sync Does

Sync does these steps in one operation:

1. Accept a LINE text export.
2. Store or deduplicate it by hash.
3. Parse it into lesson sessions.
4. Save the normalized session data into the app database.
5. Expose sessions in the UI.

Important: sync alone does not create LLM summaries. Summary generation is a separate step because it requires an API key and a chosen provider.

## What Summary Generation Does

When a summary is generated for a session, the backend runs the existing LLM pipeline:

1. Pass 1: lesson summary extraction.
2. Pass 2: review and study asset generation.

The generated output includes:

- lesson summary content
- key sentences
- vocabulary
- teacher corrections
- study review assets such as flashcards, quiz, fill-in-the-blank, and translation drills

The result is stored both on disk and in the database so the UI can render it directly.

## Sample Input Notes

The provided backup file matches the expected LINE export shape:

- day headers such as `Tue, 2024-01-16`
- time, speaker, and raw message content
- mixed logistics, links, media placeholders, and lesson content
- teacher-driven vocabulary drops and correction-style fragments that are good candidates for summary extraction

This confirms the parser assumptions are aligned with your real data.

## Repo-To-Agent Bridge

The repo now includes a bridge script:

`python scripts/agent_bridge.py --file /absolute/path/to/export.txt --provider openai`

What it does:

1. Ensures the local admin user exists.
2. Logs into the Flask app using the local test client.
3. Runs sync on the file you provide.
4. Selects either the latest session or a specific `--session-id`.
5. Either generates the summary via a provider or prepares a manual-agent bundle for that session.

Because it uses Flask's test client directly, it does not need the dev server running.

## Direct Agent Authored Summary Path

There is now a second path for summary creation when the user wants the agent to do
the actual lesson synthesis directly instead of calling an external LLM provider.

Workflow:

1. Sync and parse the LINE export.
2. Prepare a manual work bundle using:

`python scripts/agent_bridge.py --file /absolute/path/to/export.txt --manual-agent`

3. Inspect the parsed session transcript.
4. Write a valid `lesson-data.json` that matches the repo schema and prompt rules.
5. Install that lesson package into the app using:

`python scripts/install_manual_summary.py --lesson-json /absolute/path/to/lesson-data.json --session-id 2026-03-05`

or the streamlined equivalent:

`python scripts/agent_bridge.py --install-lesson-json /absolute/path/to/lesson-data.json --session-id 2026-03-05`

This generates the companion files:

- `summary.md`
- `summary.html`
- `flashcards.csv`

and stores the lesson package in SQLite so the app can render it immediately.

This is the right path when:

- the user wants the coding agent to do the summarization directly
- there is no provider API key available
- the user explicitly wants app-consumable output without relying on the in-app generation button

There is also a Settings-page import path for users whose agent does not have repo access.
If they can produce a valid `lesson-data.json` elsewhere, they can upload that
file from Settings and the backend will install it into the app.

There is also a UI import path for users whose agent does not have repo access.
If they can produce a valid `lesson-data.json` elsewhere, they can upload that
file from the Summary page and the backend will install it into the app.

## Recommended Agent Workflow

In future chats, you can give the assistant either:

- an attached LINE export file, or
- an absolute file path to a LINE export

and ask for:

- sync only
- sync + generate latest summary
- sync + generate a specific session
- sync + have the agent author the summary package directly

Recommended prompts:

- `Sync this export and generate the latest lesson summary.`
- `Use /absolute/path/to/export.txt, sync it, and generate the summary for the latest session with OpenAI.`
- `Sync this export only. Do not generate summaries yet.`
- `Sync this export, inspect the latest session, and create the lesson-data payload directly for the app.`
- `Prepare the manual-agent bundle for this export, then install the completed lesson-data.json into the app.`

## Required Environment For Generation

Summary generation requires one of these environment variables before running the app or the bridge script:

- `OPENAI_API_KEY`
- `ANTHROPIC_API_KEY`
- `GEMINI_API_KEY` or `GOOGLE_API_KEY`

The repo now supports loading these from a local `.env` file at the project root.
Start by copying `.env.example` to `.env` and filling in the provider key you want to use.

Examples:

```bash
export OPENAI_API_KEY="sk-..."
python scripts/agent_bridge.py --file /absolute/path/to/export.txt --provider openai
```

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
python scripts/agent_bridge.py --file /absolute/path/to/export.txt --provider anthropic
```

## Current Gaps

- No API key is currently configured in this environment, so sync works but generation cannot complete until a key is set.
- Topic extraction is still thin; sessions currently rely mostly on date and message counts.
- There is not yet a fully automated batch generation workflow for all sessions in one click from the UI.

## Next Practical Step

Set one provider API key, then the assistant can take any future export file you provide and run the bridge command directly for you.