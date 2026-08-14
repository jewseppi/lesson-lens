# LessonLens — convenience targets
#
# The headline target is `make update`: the one-touch local updater. After you
# export a LINE chat on your Mac, run `make update` to sync the chat + any new
# images to your hosted LessonLens server and generate the newest lesson.
#
# Configure the hosted server via environment variables (or a repo-root .env):
#   LESSONLENS_API_URL, LESSONLENS_EMAIL, LESSONLENS_PASSWORD

PYTHON ?= python3
# The app and the updater must agree on this; see api/app.py DEFAULT_LOCAL_PORT.
PORT ?= 5001

.PHONY: run-local serve web-build update update-all update-dry update-sync-only update-agent push schedule unschedule doctor doctor-agent test help

help:
	@echo "./update-now.sh      Update code, start the app, sync your newest LINE export (start here)"
	@echo "./start-local.sh     Everything but the sync: deps, UI build, login, server, preflight"
	@echo "make run-local       Build the web UI, then start the app locally (foreground)"
	@echo "make serve           Start the app only, on PORT (default $(PORT))"
	@echo "make web-build       Build the web UI into web/dist (the app serves it)"
	@echo "make doctor          Check config, hosted login, MCP server and agent command"
	@echo "make doctor-agent    Same, and actually run your agent command once"
	@echo "make update          Sync newest LINE export + new images to the hosted app, then generate latest"
	@echo "make update-all      Sync, then generate every session still missing a summary (backlog fill; idempotent)"
	@echo "make update-agent    Sync, then generate with your subscription CLI agent (no provider API key)"
	@echo "make update-dry      Show what would be synced (no network, no changes)"
	@echo "make update-sync-only  Sync chat + images but skip generation"
	@echo "make push            Push a LOCAL instance up to the hosted one (Mode B fallback)"
	@echo "make schedule        Install the launchd job so the update runs automatically (macOS)"
	@echo "make unschedule      Remove the launchd job"
	@echo "make test            Run the dependency-light test suite"

web-build:
	cd web && npm install && npm run build

serve:
	@echo "LessonLens on http://127.0.0.1:$(PORT)  (Ctrl-C to stop)"
	cd api && PORT=$(PORT) $(PYTHON) app.py

# One command to go from a fresh clone to a running app: the API serves the
# built UI from web/dist, so there is no second process to babysit.
run-local: web-build serve

doctor:
	$(PYTHON) scripts/doctor.py $(ARGS)

doctor-agent:
	$(PYTHON) scripts/doctor.py --check-agent $(ARGS)

update:
	$(PYTHON) scripts/line_mac_sync.py $(ARGS)

update-all:
	$(PYTHON) scripts/line_mac_sync.py --generate-all $(ARGS)

update-dry:
	$(PYTHON) scripts/line_mac_sync.py --dry-run $(ARGS)

update-sync-only:
	$(PYTHON) scripts/line_mac_sync.py --sync-only $(ARGS)

update-agent:
	$(PYTHON) scripts/line_mac_sync.py --generate-with agent $(ARGS)

push:
	$(PYTHON) scripts/line_mac_sync.py --target local --push --sync-only $(ARGS)

test:
	cd api && $(PYTHON) -m pytest tests/test_line_mac_sync.py tests/test_backup_attachments.py \
		tests/test_mcp_hosted.py tests/test_restore_points.py tests/test_doctor.py \
		-o addopts="" --noconftest -q

schedule:
	bash scripts/launchd/install.sh

unschedule:
	bash scripts/launchd/install.sh --uninstall
