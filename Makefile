# LessonLens — convenience targets
#
# The headline target is `make update`: the one-touch local updater. After you
# export a LINE chat on your Mac, run `make update` to sync the chat + any new
# images to your hosted LessonLens server and generate the newest lesson.
#
# Configure the hosted server via environment variables (or a repo-root .env):
#   LESSONLENS_API_URL, LESSONLENS_EMAIL, LESSONLENS_PASSWORD

PYTHON ?= python3

.PHONY: update update-all update-dry update-sync-only schedule unschedule help

help:
	@echo "make update          Sync newest LINE export + new images to the hosted app, then generate latest"
	@echo "make update-all      Sync, then generate every session still missing a summary (backlog fill; idempotent)"
	@echo "make update-dry      Show what would be synced (no network, no changes)"
	@echo "make update-sync-only  Sync chat + images but skip generation"
	@echo "make schedule        Install the launchd job so the update runs automatically (macOS)"
	@echo "make unschedule      Remove the launchd job"

update:
	$(PYTHON) scripts/line_mac_sync.py $(ARGS)

update-all:
	$(PYTHON) scripts/line_mac_sync.py --generate-all $(ARGS)

update-dry:
	$(PYTHON) scripts/line_mac_sync.py --dry-run $(ARGS)

update-sync-only:
	$(PYTHON) scripts/line_mac_sync.py --sync-only $(ARGS)

schedule:
	bash scripts/launchd/install.sh

unschedule:
	bash scripts/launchd/install.sh --uninstall
