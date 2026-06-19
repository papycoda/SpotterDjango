# Fuel Station Geocoding Design

## Objective

Geocode imported fuel stations asynchronously so route matching can use a local coordinate dataset. Route planning must never geocode stations or call Nominatim for station coordinates.

## API contract

`POST /api/v1/admin/fuel-stations/geocode/` accepts:

- `limit`: optional integer, default `500`, minimum `1`, maximum `2000`.
- `retry_failed`: optional boolean, default `false`.

The endpoint requires an authenticated staff user, creates a persisted `GeocodeJob`, claims eligible stations, and returns `202 Accepted` with the serialized job. A separately running Django management-command worker processes queued jobs. If no eligible stations exist, the job is created as completed with zero work.

`GET /api/v1/admin/fuel-stations/geocode/status/` requires an authenticated staff user and returns aggregate station counts (`total`, `success`, `pending`, `claimed`, `processing`, and `failed`) plus the latest geocoding job, if one exists.

## Components

### Nominatim client

`api/clients/nominatim_client.py` owns all Nominatim HTTP behavior. It uses the configured base URL, identifying user agent, `NOMINATIM_TIMEOUT_SECONDS` bounded timeout, structured address parameters, JSON response format, a one-result limit, and a USA country-code restriction. It validates response shape and coordinate ranges.

The client returns coordinates for a valid match, returns `None` when Nominatim finds no match, and raises typed client exceptions for rate limiting, network errors, upstream failures, and malformed responses. Exceptions do not expose upstream bodies or credentials.

### Geocoding service

`api/services/geocoding_service.py` owns station selection and database coordination. It selects stations without coordinates in deterministic primary-key order:

- Normal jobs select `pending` stations.
- Explicit retry jobs select both `pending` and `failed` stations.
- Stations already marked `claimed`, `processing`, or `success` are not selected by another job.

Selection assigns stations to the job with status `claimed`; it does not mark the whole batch as processing. Immediately before an external call, the service atomically transitions one assigned station from `claimed` to `processing`. A valid result saves latitude and longitude and marks the station `success`. No match marks it `failed`. A transient client failure restores it to `claimed` and is re-raised so the active executor can retry safely. A permanent client failure marks it `failed`.

The service processes only the stations assigned to the persisted job and updates job counters after every station, making progress visible and restarts idempotent.

### Worker and optional Celery adapter

`python manage.py run_geocoding_worker` is the default executor. It repeatedly claims the oldest pending job, invokes the service, applies bounded exponential retry delays for transient failures, and exits cleanly with `--once` or continues polling with `--watch`. A duplicate worker exits a job attempt when the same job has a fresh processing heartbeat. An exhausted or unexpected failure marks the job failed with a sanitized error and marks its remaining claimed stations failed so none are stranded.

`api/tasks.py` remains a thin optional Celery adapter that accepts only a persisted job ID and calls the same service. The API does not require or dispatch Celery, so Redis and a Celery worker are not runtime prerequisites. A future deployment can enable the adapter without changing job or station semantics.

### Database-backed rate limiting

Rate limiting is enforced immediately before each Nominatim request using `NOMINATIM_MIN_INTERVAL_SECONDS` and a singleton database row. Inside `transaction.atomic()`, the limiter locks the row, reserves `max(now, next_allowed_at)`, advances `next_allowed_at` by the configured interval, commits, and then sleeps until its reserved slot. A crashed worker may waste one slot but cannot allow calls to run early.

PostgreSQL row locking supports multiple worker processes safely. SQLite development runs one management-command worker; the command refuses a second live worker through the same heartbeat/ownership mechanism. This avoids pretending SQLite offers production-grade concurrent queue semantics.

### Stale-work recovery

`GeocodeJob` stores a heartbeat timestamp, refreshed before and after each station call. `GEOCODING_STALE_AFTER_SECONDS` must be longer than the HTTP timeout plus maximum rate-limit wait. At startup and before each polling cycle, the management-command worker finds processing jobs whose heartbeat is older than that threshold, locks each job, resets its assigned `processing` station to `claimed`, and changes the job back to `pending`. Claimed stations remain attached to their original job rather than being stranded or claimed by a competing job.

Normal task redelivery is also idempotent: when a task starts for a pending or stale job, it resets any processing station assigned to that job back to `claimed`, derives counters from persisted station outcomes, and continues. A currently healthy job is never reset because its heartbeat is still fresh.

## Persisted job scope

The existing `GeocodeJob` model tracks totals and outcomes. Its schema gains `heartbeat_at` and worker ownership fields. `FuelStation` gains the `claimed` geocoding status and a nullable relationship to its assigned geocoding job. A singleton `GeocodingRateLimit` model stores the next reservable request time. A later explicit retry may reassign a failed station to a new job. The schema changes are delivered in a new migration.

When a job is created, eligible stations are claimed inside `transaction.atomic()` up to the validated limit. PostgreSQL uses row locking where available; SQLite retains deterministic, transaction-scoped behavior for development.

## Route-planning boundary

Station corridor matching must filter for `geocoding_status="success"`, non-null latitude, and non-null longitude. It may skip unresolved stations but may not invoke the geocoding service or Nominatim client.

## Error handling

- Invalid POST input returns `400` with field-level serializer errors.
- Anonymous users receive `401`; authenticated non-staff users receive `403`.
- Nominatim no-result responses fail only the affected station.
- HTTP `429`, network errors, and `5xx` responses are transient and retried with bounded backoff.
- Other upstream status errors and malformed successful responses are permanent station failures.
- API and job errors contain sanitized messages only.

## Tests and verification

Tests mock all external HTTP and sleeping. Coverage includes request validation, permissions, empty batches, claimed-to-processing transitions, deterministic selection, success, no result, permanent failure, transient retry, idempotent worker restart, stale heartbeat recovery, single-worker ownership on SQLite, failed-station retry selection, aggregate status, database rate-slot reservation, the optional Celery wrapper, and route matching's success-only coordinate filter.

Repository verification runs Django tests, system checks, migration drift checks, and the management-command worker against a mocked/local Nominatim endpoint. Automated tests require no network, Redis, or Celery worker.
