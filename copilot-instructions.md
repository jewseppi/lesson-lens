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