# Automatic Geocoding Worker Startup Design

## Goal

Start station geocoding automatically in local development and deployment without Redis or Celery, without running long-lived work inside Django web-process startup, and without requiring an operator to create every batch manually.

## Architecture

The web server and geocoding worker remain separate operating-system processes. Django `AppConfig.ready()` must not start the worker because development reloaders and production web replicas can execute that hook multiple times.

For local development, a `run_app` management command supervises two child processes: Django `runserver --noreload` and `run_geocoding_worker --watch --auto-queue 500`. Stopping the supervisor terminates both children. The supervisor reports a non-zero child exit and then shuts down its sibling.

For deployment, Docker Compose defines independent `web` and `worker` services from the same image. The web service runs Gunicorn; the worker runs the database-backed management command with automatic queueing. A Procfile exposes equivalent `web` and `worker` process types for platforms that support them.

## Automatic queueing

`run_geocoding_worker` gains an optional positive `--auto-queue` batch-size argument. When no pending job exists, the worker creates a bounded job for unresolved `pending` stations. After completing a job, watch mode repeats until no eligible stations remain, then sleeps for the configured poll interval. Existing failed stations are not retried automatically; retrying them remains an explicit operation to prevent repeated requests for addresses that cannot be matched reliably.

The default worker behavior remains unchanged when `--auto-queue` is omitted. `--once --auto-queue N` creates and processes at most one bounded job, which keeps operational testing deterministic.

## Safety and data flow

The existing database job claims, station states, shared database rate limiter, heartbeat, and stale-job recovery remain authoritative. Station calls stay sequential and rate-limited. Route requests never perform station geocoding. SQLite deployments must run exactly one worker service; PostgreSQL remains the future path for controlled concurrency.

Automatic queueing may require hours for the full dataset because Nominatim is limited to roughly one request per second. A null coordinate remains valid for pending stations and for failed stations that cannot be matched confidently.

## Configuration

Environment variables configure the automatic batch size and local bind address, with defaults of 500 stations and `127.0.0.1:8000`. Deployment continues to provide Nominatim URL, identifying user agent, timeouts, and database configuration through environment variables.

## Error handling

If a local child process exits unexpectedly, `run_app` terminates the other child and exits non-zero. Keyboard interruption performs the same coordinated shutdown without leaving an orphan worker. Worker network failures continue through bounded retries and sanitized persisted job errors. Startup does not hide a failed web server or failed worker.

## Verification

Tests cover automatic job creation, no duplicate creation while a pending job exists, once-mode bounds, supervisor command construction, sibling termination after failure, and coordinated interrupt shutdown. External requests remain mocked. The full Django checks, migration drift check, and test suite must pass.
