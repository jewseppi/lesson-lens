# LessonLens Public Readiness Plan

## 1. What We Built

LessonLens is a full-stack app for importing LINE chat exports, parsing sessions, and generating lesson study artifacts.

- Backend: Flask API with JWT auth, invite-based registration, uploads, parsing, summaries, backup import/export/sync.
- Frontend: React/Vite app with login, upload/sync, session browsing, summary view, settings.
- Storage:
  - SQLite DB (`api/lessonlens.db`) for users, uploads, parse runs, sessions, summaries, analytics.
  - File artifacts in `raw-exports/`, `processed/`, `summaries/`, `flashcards/`, `html/`.
- Deploy: GitHub Actions -> rsync + SSH + Passenger on Apache.
- Test quality: 237 tests, 100% backend coverage.

## 2. How It Works Today

1. User authenticates (JWT).
2. User uploads a LINE export (`.txt` or extensionless only).
3. Backend parses text into sessions/messages and stores metadata.
4. Optional summary generation runs on selected provider APIs.
5. App displays sessions, links, summaries, and study assets.

### Local Logic (Current)

- Local dev login is prefilled on localhost (`admin@lessonlens.local` / `adminpassword1`) in the UI for convenience.
- This is not SSO and does not bypass auth; it only autofills the form locally.
- Production auth still requires normal login.

## 3. Current Gaps Blocking Public Release

## 3.1 Security and Privacy Gaps

- Sensitive content is not encrypted at rest at the application level.
  - Raw chats and parsed sessions are stored as plaintext files and DB rows.
- Default secrets fallback exists in code (`SECRET_KEY` default) if env vars are missing.
- Single-host SQLite is acceptable for MVP but weak for durability, backups, and concurrent scale.
- No formal data retention/deletion policy exposed to end users.
- No MFA/SSO for hosted user accounts.
- No audit-grade security monitoring, intrusion detection, or alerting stack.

### 3.1.1 Hosted DB Threat Model Notes

Open source code does not automatically expose hosted user data, but it does raise the bar for operational hardening. Assume an attacker can read code, find weak defaults, and target deployment mistakes.

Primary risks to design against:

- Credential theft (DB creds, JWT secret, deploy key).
- Privilege escalation (app user can run destructive SQL).
- Data exfiltration via backups or object storage.
- Unauthorized mutation (tampering with chat history or summaries).
- Insider misuse from over-broad admin/deploy access.

Goal state is not "impossible to mess with" (no system can guarantee that), but defense in depth with strong prevention, detection, and recovery.

## 3.2 Availability and Operations Gaps

- Single server / single DB = single point of failure.
- Backup/restore is app-level, but no automated disaster recovery target or tested RTO/RPO.
- No CDN/object storage for larger assets and no queue-based processing.
- Limited runtime observability (logs/metrics/traces not centralized).

## 3.3 Product/Data Ingestion Gaps

- Upload route only accepts `.txt`/no extension.
- Media in exports is currently treated as placeholders (e.g., `[Photo]`, `[File]`).
- No ingestion pipeline for image/audio/video files.
- PDF extraction is unimplemented.

## 4. Why Your Latest Export With Images Failed

The parser currently recognizes media placeholders only and does not ingest binary attachments. If the export references media files, those are not pulled into the summary pipeline as actual content.

## 5. What To Build Next (Public Launch Path)

## Phase A: Security Baseline (Must-Have)

1. Enforce environment-only secrets in production startup.
2. Add per-environment config validation (fail fast if secrets missing).
3. Encrypt sensitive data at rest:
   - Option A (fast): full-disk encryption + strict OS permissions + encrypted backups.
   - Option B (stronger): app-level envelope encryption for raw chat payloads and summaries.
4. Add explicit retention controls:
   - user data export,
   - user data delete,
   - auto-expiry policy for raw uploads.
5. Add account protection:
   - password reset flow,
   - optional MFA,
   - session/token revocation support.

## Phase B: Reliability and Availability (Must-Have)

1. Move production DB from SQLite to managed Postgres.
2. Store raw uploads and generated artifacts in object storage (S3-compatible) with lifecycle policies.
3. Add automated backups + restore drills.
4. Introduce async job queue for parsing/generation workloads.
5. Add monitoring stack (errors, latency, queue depth, disk usage, backup health).

## Phase C: Database Hardening and Tamper Resistance (Must-Have)

1. Move to managed Postgres with private networking only (no public DB endpoint).
2. Use least-privilege DB roles:
   - app role: read/write only to needed tables,
   - migration role: schema changes only in CI/CD,
   - break-glass admin role with MFA and short-lived access.
3. Store all secrets in a secret manager; rotate regularly; never keep long-lived DB passwords in repo or workflow logs.
4. Add append-only audit events for security actions (login, password change, backup import, invite issuance, role changes).
5. Add immutable backups (WORM/object-lock) and periodic restore verification.
6. Add tamper-evident controls:
   - per-record checksum/signature for sensitive payloads,
   - periodic integrity scan job,
   - alert on mismatch.
7. Add strict migration gate:
   - signed migration files,
   - required code review,
   - auto-backup before apply.

## Phase D: Hybrid Private Data Storage (IPFS + Local/Primary DB)

Suggested hybrid model for personal chat content:

1. Keep credentials, billing/account metadata, and operational tables in primary DB (Postgres).
2. Encrypt chat payloads client-side or at app edge with per-user data keys.
3. Store encrypted blobs in content-addressed storage (IPFS pinning service or S3-compatible object store with content hash IDs).
4. Store only references/metadata in DB:
   - content hash/CID,
   - owner user_id,
   - key envelope reference,
   - retention and deletion flags.
5. Ensure deletion workflow includes:
   - key destruction (crypto-erasure),
   - unpin/delete request in storage backend,
   - tombstone marker in DB for audit.

Notes:

- IPFS helps with content addressing and portability, but by itself it does not provide privacy; encryption and key management are mandatory.
- For early production, S3 + KMS is often operationally simpler than raw IPFS, then add IPFS pinning where needed.

## Phase E: Media Ingestion (Needed for your teacher use case)

1. Add multipart upload support for attachment bundles (zip or per-file upload).
2. Extend schema with `attachments[]` metadata per message:
   - type (`image`, `audio`, `video`, `file`),
   - storage URI,
   - checksum,
   - optional transcript/caption.
3. Add extraction pipeline:
   - Image OCR + captioning,
   - Audio/video transcription (Whisper/faster-whisper or cloud API),
   - Merge extracted text into session timeline with provenance.
4. Update summarization prompts to include attachment-derived evidence.
5. Add privacy controls for media retention and redaction.

## 6. Recommended Model Strategy (Free/Low-Cost)

For free-ish operation with your dataset:

1. Keep high-quality structured outputs (`lesson-data.json`) as your canonical dataset.
2. Start with retrieval + template-assisted generation (cheaper than full fine-tune).
3. Introduce local/open models for low-cost inference (e.g., small instruct models).
4. Reserve paid APIs for difficult sessions or fallback quality tier.
5. Add evaluation harness on your existing summaries before switching default models.

## 7. Public Release Checklist

## Security

- [ ] Secrets manager + required env validation
- [ ] At-rest encryption strategy selected and implemented
- [ ] Data retention + deletion policy in product and docs
- [ ] Password reset + optional MFA
- [ ] Security review and dependency scan in CI
- [ ] Managed Postgres private network + least-privilege roles
- [ ] Immutable backups + restore drill evidence
- [ ] Tamper-evident integrity checks for sensitive payloads
- [ ] Centralized audit logging for security-sensitive actions

## Availability

- [ ] Managed Postgres migration
- [ ] Object storage migration for artifacts
- [ ] Automated backups + restore test
- [ ] Monitoring + alerting
- [ ] Incident runbook and on-call basics

## Product/Data

- [ ] Media attachment ingestion MVP (image/audio/video)
- [ ] Attachment-aware summary prompts
- [ ] End-user privacy settings for uploaded media
- [ ] Cost controls and model fallback policy

## 8. Immediate Next 2 Weeks (Concrete)

1. Lock production secret policy (no default secrets, startup checks).
2. Add password reset and token revocation.
3. Choose storage architecture: S3+KMS first or IPFS-hybrid pilot for encrypted chat blobs.
4. Implement DB role split and remove direct schema-change privileges from app runtime role.
5. Build attachment metadata schema + image OCR MVP with one pilot flow for "chat txt + image attachments".

---

This plan gets LessonLens from strong MVP to public-ready service with user-account security, privacy controls, and support for media-rich lesson data.