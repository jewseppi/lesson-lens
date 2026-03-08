# Deployment

## Recommended Layout

Use one Python web app and one domain.

- App folder on server: `<DEPLOY_PATH>/language`
- WSGI app root: `<DEPLOY_PATH>/language/api`
- Frontend build output: `<DEPLOY_PATH>/language/web/dist`
- Database: `<DEPLOY_PATH>/language/api/lessonlens.db`
- Writable runtime folders:
  - `<DEPLOY_PATH>/language/raw-exports`
  - `<DEPLOY_PATH>/language/processed`
  - `<DEPLOY_PATH>/language/summaries`

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
- `DEPLOY_PATH`: absolute path on the server, for example `/home/USERNAME/apps`
- `PASSENGER_PYTHON_PATH`: absolute path to the Python virtualenv root on the server

The workflow uploads the repo to `<DEPLOY_PATH>/language/`, builds the frontend,
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