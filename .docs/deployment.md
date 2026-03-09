# Deployment

## Recommended Layout

Use one Python web app and one domain.

- App root on server: `<APP_ROOT>`
- WSGI app root: `<APP_ROOT>`
- API code: `<APP_ROOT>/api`
- Frontend build output: `<APP_ROOT>/web/dist`
- Database: `<APP_ROOT>/api/lessonlens.db`
- Writable runtime folders:
  - `<APP_ROOT>/raw-exports`
  - `<APP_ROOT>/processed`
  - `<APP_ROOT>/summaries`

The Flask app serves both the React frontend and the `/api/*` routes.

## Hosting Setup

1. Create or choose a domain or subdomain for the app.
2. Create a Python web application in your hosting control panel.
3. Set the application root to the server path for `language/api`.
4. Set the startup file to `passenger_wsgi.py` or your host's equivalent WSGI entrypoint.
5. Point the app URL to `/` for the chosen domain.
6. Create or attach a Python virtual environment for the app.
7. Make sure the app user can write to:
   - `language/api`
   - `language/raw-exports`
   - `language/processed`
   - `language/summaries`

## GitHub Actions Secrets

Add these repository secrets:

- `HOST`: SSH host
- `USERNAME`: SSH username
- `SSH_PRIVATE_KEY`: private key used for deployment access
- `PASSENGER_APP_ROOT`: absolute application root on the server, for example `/home/USERNAME/public_html/api/lens`
- `PASSENGER_PYTHON_PATH`: absolute path to the Python virtualenv root on the server

The workflow uploads the repo to `<PASSENGER_APP_ROOT>/`, builds the frontend,
installs Python requirements, initializes the SQLite schema, writes `.htaccess`,
and restarts the Python app.

## First Deploy Checklist

1. Create the Python app in your hosting control panel first.
2. Note the app root path and virtualenv path.
3. Add the GitHub Actions secrets.
4. Run the `Deploy LessonLens` workflow manually.
5. Open `/api/health` on the deployed domain.
6. Open `/` on the deployed domain.

## Notes

- SQLite is expected and supported in this setup.
- Do not commit the live database file.
- If you later want separate frontend and API domains, the current code can be split again, but the single-domain deployment is the simplest path right now.

## Database Security Baseline (Hosted)

For public launch, apply these controls even if the code is open:

1. Remove default secret fallbacks in production and fail startup when required secrets are missing.
2. Put DB behind private networking; do not expose DB port publicly.
3. Use separate DB roles:
  - runtime app role (minimum read/write),
  - migration role (DDL only in CI/CD),
  - admin break-glass role (MFA, short-lived).
4. Keep credentials in a secret manager; rotate on schedule and after incidents.
5. Enable encrypted backups and immutable retention (object lock/WORM where possible).
6. Enable centralized audit logs for login/admin/backup/import actions.
7. Test restore regularly with a documented RTO/RPO target.

## Hybrid Storage Option (Encrypted Personal Data)

If you choose a hybrid model for sensitive chat history:

1. Store account/operational metadata in Postgres.
2. Encrypt chat payloads with per-user keys.
3. Store encrypted blobs in content-addressed storage (IPFS pinning or S3-compatible object storage).
4. Keep only metadata and content references (CID/hash, owner, key envelope ref) in DB.
5. Implement crypto-erasure and unpin/delete for user data deletion workflows.

Important: IPFS does not provide confidentiality by itself. Encryption and key lifecycle management are the security boundary.