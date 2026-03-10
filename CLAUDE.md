# Project Instructions for Claude

## Commit Rules
- Do NOT include `Co-Authored-By` lines in commit messages.

## Project: LessonLens
- Monorepo: `web/` (React + Vite, deployed via Cloudflare Pages) and `api/` (Flask, deployed via rsync to shared host)
- Both deploy automatically on push to main
- Python venv at `.venv/`, run tests with `source .venv/bin/activate && cd api && python -m pytest tests/`
- TypeScript check: `cd web && node_modules/.bin/tsc --noEmit`
