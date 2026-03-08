# GoDaddy Deployment

Target domain: `lens.jsilverman.ca`

## Recommended layout

Use one Passenger Python app and one domain.

- Domain/subdomain: `lens.jsilverman.ca`
- App folder on server: `<DEPLOY_PATH>/language`
- Passenger app root: `<DEPLOY_PATH>/language/api`
- Frontend build output: `<DEPLOY_PATH>/language/web/dist`
- Database: `<DEPLOY_PATH>/language/api/lessonlens.db`
- Writable runtime folders:
  - `<DEPLOY_PATH>/language/raw-exports`
  - `<DEPLOY_PATH>/language/processed`
  - `<DEPLOY_PATH>/language/summaries`

The Flask app serves both the React frontend and the `/api/*` routes.

## cPanel steps

1. Create a new subdomain or domain binding for `lens.jsilverman.ca`.
2. In cPanel, create a new Python application.
3. Set the application root to the server path for `language/api`.
4. Set the startup file to `passenger_wsgi.py`.
5. Set the application URL to `/` for the `lens.jsilverman.ca` domain.
6. Create or point a Python virtual environment for this app.
7. Make sure the app user can write to:
   - `language/api`
   - `language/raw-exports`
   - `language/processed`
   - `language/summaries`

## GitHub Actions secrets

Add these repository secrets:

- `HOST`: your SSH host
- `USERNAME`: your SSH username
- `SSH_PRIVATE_KEY`: private key used for deploy access
- `DEPLOY_PATH`: absolute path on the server, for example `/home/USERNAME/apps`
- `PASSENGER_PYTHON_PATH`: absolute path to the Python virtualenv root on the server

The workflow uploads the repo to `<DEPLOY_PATH>/language/`, builds the frontend,
installs Python requirements, initializes the SQLite schema, writes `.htaccess`,
and restarts Passenger.

## First deploy checklist

1. Create the Python app in cPanel first.
2. Note the app root path and virtualenv path.
3. Add the GitHub Actions secrets.
4. Run the `Deploy LessonLens` workflow manually.
5. Open `https://lens.jsilverman.ca/api/health`.
6. Open `https://lens.jsilverman.ca/`.

## Notes

- SQLite is expected and supported in this setup.
- Do not commit the live database file.
- If you later want separate frontend and API domains, the current code can be split again, but the single-domain deployment is the simplest path right now.