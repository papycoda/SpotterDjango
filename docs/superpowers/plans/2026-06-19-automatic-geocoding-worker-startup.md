# Automatic Geocoding Worker Startup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Automatically drain unresolved station geocoding work while keeping the web server and database-backed worker in separate supervised processes locally and in deployment.

**Architecture:** Extend the existing worker with opt-in bounded automatic job creation. Add a cross-platform local supervisor command, while Docker Compose and Procfile run web and worker as independent deployment processes sharing the same database.

**Tech Stack:** Python 3.14, Django 6 management commands, `subprocess`, SQLite, Gunicorn, Docker Compose.

---

### Task 1: Automatic bounded job creation

**Files:**
- Modify: `api/management/commands/run_geocoding_worker.py`
- Modify: `api/tests/test_geocoding_worker.py`
- Modify: `fuelSpotter/settings.py`
- Modify: `.env.example`

- [ ] Add tests proving `--auto-queue N` creates a job only when no pending job exists, processes at most one job under `--once`, and rejects non-positive batch sizes.
- [ ] Run `python manage.py test api.tests.test_geocoding_worker -v 2` and verify the new tests fail because the argument is unknown.
- [ ] Add a positive-integer parser, use `GEOCODING_AUTO_QUEUE_BATCH_SIZE` as the configured default for supervisors, and call `GeocodingService.create_job(limit=N, retry_failed=False)` only when the persisted queue is empty.
- [ ] Run the worker tests and verify they pass without external network access.

### Task 2: Cross-platform local process supervisor

**Files:**
- Create: `api/management/commands/run_app.py`
- Create: `api/tests/test_run_app.py`

- [ ] Add tests that mock `subprocess.Popen` and prove the command launches `runserver --noreload` plus `run_geocoding_worker --watch --auto-queue N`, terminates the sibling when either child exits, and cleans up both children on `KeyboardInterrupt`.
- [ ] Run `python manage.py test api.tests.test_run_app -v 2` and verify failure because `run_app` does not exist.
- [ ] Implement `run_app` with `sys.executable`, the repository `manage.py`, configurable address/batch arguments, child polling, coordinated terminate/wait/kill cleanup, and a non-zero `CommandError` for unexpected child failure.
- [ ] Run the supervisor tests and verify they pass.

### Task 3: Deployment process definitions

**Files:**
- Create: `Dockerfile`
- Create: `docker-compose.yml`
- Create: `.dockerignore`
- Create: `Procfile`
- Modify: `requirements.txt`
- Modify: `fuelSpotter/settings.py`
- Modify: `.env.example`

- [ ] Add a settings test proving `SQLITE_PATH` overrides the development database path.
- [ ] Run the settings test and verify it fails with the current fixed `db.sqlite3` path.
- [ ] Add Gunicorn, an environment-driven SQLite path, a Python 3.14 image, a persistent database volume, a health-gated web service, and a single worker service running `--watch --auto-queue 500`.
- [ ] Add equivalent `release`, `web`, and `worker` Procfile process types.
- [ ] Run `docker compose config` when Docker is available and verify the resolved service graph contains one web service and one worker service.

### Task 4: Operational documentation and verification

**Files:**
- Create: `README.md`
- Modify: `TODO.md`

- [ ] Document local `python manage.py run_app`, separate-process commands, Docker Compose startup, production process types, rate-limit duration, retry behavior, and the rule that null coordinates remain valid for unresolved stations.
- [ ] Mark automatic worker startup complete in `TODO.md` without marking route-planning work complete.
- [ ] Run `python manage.py check`, `python manage.py makemigrations --check --dry-run`, `python manage.py test`, and `git diff --check`.
- [ ] Perform a no-network smoke test with `python manage.py run_geocoding_worker --once` when no job is queued and report any deployment-only gate that cannot run.
