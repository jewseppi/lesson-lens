# LessonLens Multi-User + Local Model Phased Plan

## Purpose
This plan prioritizes opening LessonLens to other users safely first, then adding local-model support, quality scoring, and language/model gating.

## Current State Snapshot (as of 2026-03-09)
- Invite-based registration exists (`/api/register` requires invitation token).
- Admin can create invitation tokens (`/api/admin/invitations`).
- User data is mostly scoped by `user_id` and by each user's latest parse run.
- No public signup request workflow yet.
- No admin user-management UI/API (list users, suspend/reactivate, reset, audit view).
- No formal model quality dashboard or policy gate for local models.

## Plan Review: Important Gaps To Add (Missed in earlier draft)
These are required to support multi-user launch and should be done before broad local-model rollout:

1. Account lifecycle management
- Signup request flow (request access -> admin review -> invite/approve/deny).
- Account status states (`pending`, `active`, `suspended`, `disabled`).
- Admin operations: suspend/reactivate user, force password reset, revoke sessions.

2. Data isolation hardening
- Add explicit isolation tests for every user-facing endpoint (cross-user access tests).
- Add `user_id` to `sessions` table (currently indirect via `run_id`) to simplify and harden query filtering.
- Add admin read-only impersonation/audit mode with explicit logging.

3. Safety + abuse controls
- Distinct per-endpoint rate limits for auth and signup-request endpoints.
- Bot/abuse throttling for signup requests (IP/email/domain controls).
- Audit log for security-sensitive actions (invite create/use, role/status changes, login failures, password changes).

4. Recovery and support
- Password reset flow (token-based) and session revocation.
- User-facing privacy controls and retention settings can come later, but account recovery cannot.

5. Docs and operator playbook
- Clear docs for API mode vs Agent mode vs Local mode.
- Admin runbook for onboarding users and handling account issues.

## Phases

## Phase 0 - Multi-User Launch Foundation (first and foremost)
Goal: safely onboard real users with per-account privacy and admin control.

### Phase 0A - Source Expansion: Image Context Ingestion (same priority window)
Goal: allow users to upload image attachments with chat exports and map images to sessions by timestamp.

### Image ingestion scope
- Support image bundle upload alongside chat import (zip or multi-file upload).
- Extract image metadata at ingest time:
  - EXIF datetime fields (`DateTimeOriginal`, `CreateDate`, `ModifyDate`),
  - timezone offset if present,
  - filename timestamp patterns,
  - filesystem `mtime` as last-resort fallback.
- Normalize all timestamps to UTC plus source timezone confidence.

### Session alignment logic
- Build deterministic matcher that maps each image to the nearest session window by time.
- Matching strategy:
  - high confidence: image timestamp within session start/end,
  - medium confidence: within configurable margin (for example +/- 2 hours),
  - low confidence: same date only,
  - unmatched: no reasonable candidate.
- Persist match confidence and reason so the UI can explain assignments.
- Add manual reassignment endpoint/UI for low-confidence or unmatched images.

### Backend schema and API additions
- Add `attachments` table:
  - `id`, `user_id`, `upload_id`, `stored_filename`, `original_filename`, `mime_type`, `sha256`,
  - `captured_at_utc`, `captured_at_local`, `timezone_hint`, `metadata_json`,
  - `ingested_at`.
- Add `session_attachments` table:
  - `id`, `user_id`, `session_id`, `attachment_id`, `match_confidence`, `match_reason`, `assigned_by`, `assigned_at`.
- Add endpoints:
  - `POST /api/sync` enhancement for optional image files,
  - `GET /api/sessions/<session_id>/attachments`,
  - `POST /api/sessions/<session_id>/attachments/<attachment_id>/assign`,
  - `POST /api/attachments/<attachment_id>/unassign`.
- Ensure all attachment reads/writes are scoped by `user_id`.

### Frontend additions
- Import flow supports selecting chat file plus optional image set.
- Session page shows assigned images and confidence badges.
- Review queue for unmatched/low-confidence attachments.

### Deliverable gates (Phase 0A)
- User can upload chat plus images in one import flow.
- At least 80% of images with valid timestamps auto-map to correct session in a pilot dataset.
- Unmatched/low-confidence images are visible and reassignable manually.
- Cross-user attachment isolation tests pass.

### Phase 0A edge cases and controls (do not skip)
- Many messaging apps strip EXIF metadata during export/share; the matcher must support no-EXIF paths.
- Add deduplication by image hash (`sha256`) to avoid duplicate storage and double assignment.
- Validate mime type and file signatures (not extension only) for upload safety.
- Limit max image count/size per import to protect server resources.
- Keep image processing asynchronous when batch size is large; surface progress in UI.

### Backend
- Add `users.status` (`pending`, `active`, `suspended`, `disabled`) and `users.last_login_at`.
- Add `signup_requests` table:
  - `id`, `email`, `display_name`, `reason`, `status`, `reviewed_by`, `reviewed_at`, `created_at`.
- Add endpoints:
  - `POST /api/signup-requests` (public).
  - `GET /api/admin/signup-requests` (admin).
  - `POST /api/admin/signup-requests/<id>/approve` (creates invite).
  - `POST /api/admin/signup-requests/<id>/deny`.
  - `GET /api/admin/users` (list/filter).
  - `POST /api/admin/users/<id>/suspend`.
  - `POST /api/admin/users/<id>/reactivate`.
- Enforce `users.status == active` at login and on protected routes.
- Add security event logging for account actions.

### Data isolation
- Add `user_id` to `sessions` with migration/backfill from `parse_runs.user_id`.
- Update reads/writes to filter sessions by both `user_id` and run/session identifiers.
- Add regression tests proving one user cannot access another user's uploads/sessions/summaries.

### Frontend
- Add signup request page.
- Add admin pages:
  - pending signup requests,
  - users list,
  - suspend/reactivate actions.

### Deliverable gates
- New user can request signup and get approved by admin.
- Non-admin user cannot view/administer other users.
- Admin can manage accounts and view audit trail.

## Phase 1 - Local Model MVP (no API key required)
Goal: users can generate summaries with a free local model.

### Model serving
- Integrate local provider via Ollama adapter (start with `qwen2.5:7b-instruct`).
- Add provider option `local` in settings and summary generation endpoint.
- Add model selector for local mode (3B/7B/14B tiers).

### Reliability
- Add local-model health check endpoint (`/api/models/local/health`).
- Add timeout, retry, and graceful fallback error messaging.

### Deliverable gates
- User with no API key can generate a summary locally.
- Clear UI warnings for expected speed/quality tradeoffs.

## Phase 2 - Evaluation Harness (no-data baseline first)
Goal: quantify local model quality by language and model.

### Benchmark inputs
- Use already summarized classes as references.
- Run local model on the same source session with no retrieval context.

### Metrics
- Schema validity (JSON compliance, required fields).
- Content coverage (overlap with reference corrections/vocab/topics).
- Hallucination proxy (unsupported entities/claims).
- Pedagogical quality checks (exercise structure and utility).
- Runtime metrics (latency, failures, token/s throughput where available).

### Storage
- Add `model_eval_runs` and `model_eval_scores` tables keyed by language/model/session.

### Deliverable gates
- Repeatable benchmark runner and persisted results.
- Baseline scorecard per language/model pair.

## Phase 3 - Dashboard + Policy Gating
Goal: operationalize results and protect users from weak model/language combos.

### Dashboard
- Admin dashboard page: model x language heatmap, trends, failure breakdown.
- Show confidence tiers (`high`, `medium`, `low`) and sample error reasons.

### Policy engine
- Add `model_language_policy` table:
  - `language`, `model_id`, `enabled_local`, `min_score`, `warning_level`, `fallback_mode`.
- Enforce policy in generation route:
  - allow,
  - allow with warning,
  - block local and require API/Agent.

### Deliverable gates
- Low-scoring language/model combinations are auto-warned or blocked.
- User sees actionable fallback guidance.

## Phase 4 - Retrieval + User Feedback Boost
Goal: improve local quality without fine-tuning.

### Retrieval
- Build per-user retrieval index from structured artifacts (corrections, vocab, exercise patterns).
- Inject top-k relevant prior examples into prompt for local generation.

### Feedback loop
- Track user edits/acceptance of generated output.
- Use accepted corrections as retrieval memory.

### Deliverable gates
- Measurable score improvement over no-data baseline.
- Better per-user personalization without sharing raw private chat data.

## Phase 5 - Optional Fine-Tuning Track
Goal: only if RAG/retrieval plateaus.

- Export opt-in, de-identified structured training records.
- Start with LoRA on a small model for output-format stability.
- Keep this optional and behind admin/dev flag.

## Model Recommendations (free/local)
- Default: Qwen2.5 7B Instruct (best quality/compute balance).
- Better hardware: Qwen2.5 14B.
- Low-end fallback: Qwen2.5 3B.

## Success Criteria
- Multi-user onboarding and account control work safely.
- User data remains private by account (admin exception logged).
- Users can attach image context to sessions with auditable, confidence-scored matching.
- Local mode works without API key.
- Dashboard provides objective language/model quality scores.
- Policy gating prevents low-quality local output in unsupported language/model combos.

## Implementation Notes
- Keep API mode as plug-and-play default.
- Keep Agent mode as pro/advanced path.
- Treat Local mode as progressive rollout with per-language guardrails.
- Defer encryption-at-rest enhancements to the existing security phases, but do not defer account isolation and admin controls.
