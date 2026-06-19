# Fuel Station Geocoding Design

## Objective

Geocode imported fuel stations asynchronously so route matching can use a local coordinate dataset. Route planning must never geocode stations or call Nominatim for station coordinates.

## API contract

`POST /api/v1/admin/fuel-stations/geocode/` accepts:

- `limit`: optional integer, default `500`, minimum `1`, maximum `2000`.
- `retry_failed`: optional boolean, default `false`.

The endpoint requires an authenticated staff user, creates a persisted `GeocodeJob`, queues a Celery task with only the job ID, and returns `202 Accepted` with the serialized job. If no eligible stations exist, the job is created as completed with zero work and no task is queued.

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

Selection assigns stations to the job with status `claimed`; it does not mark the whole batch as processing. Immediately before an external call, the service atomically transitions one assigned station from `claimed` to `processing`. A valid result saves latitude and longitude and marks the station `success`. No match marks it `failed`. A transient client failure restores it to `claimed` and is re-raised so Celery can retry safely. A permanent client failure marks it `failed`.

The service processes only the stations assigned to the persisted job and updates job counters after every station, making progress visible and restarts idempotent.

### Celery task

`api/tasks.py` receives only a `GeocodeJob` ID. It atomically transitions a pending job to processing, invokes the service, and marks the job completed when all assigned work finishes. A duplicate delivery exits when the same job has a fresh processing heartbeat. The task uses late acknowledgement and rejects work on worker loss so Redis can redeliver interrupted jobs. Transient Nominatim failures use bounded Celery retry with backoff and jitter. An exhausted or unexpected failure marks the job failed with a sanitized error and marks its remaining claimed stations failed so none are stranded.

Rate limiting is enforced immediately before each Nominatim request using `NOMINATIM_MIN_INTERVAL_SECONDS` and a Redis-backed distributed limiter at `NOMINATIM_RATE_LIMIT_REDIS_URL`, which defaults to the Celery broker URL. An atomic Redis script reserves the next request slot using Redis server time; callers wait and retry until they own a slot. A short key expiry prevents abandoned limiter state from persisting. If Redis is unavailable, the task retries rather than making an unthrottled request. Geocoding tasks also use a dedicated Celery queue with worker concurrency `1` as defense in depth, but correctness does not depend on only one process being deployed.

### Stale-work recovery

`GeocodeJob` stores a heartbeat timestamp, refreshed before and after each station call. `GEOCODING_STALE_AFTER_SECONDS` must be longer than the HTTP timeout plus maximum rate-limit wait. A Celery Beat recovery task finds processing jobs whose heartbeat is older than that threshold, locks each job, resets its assigned `processing` station to `claimed`, changes the job back to `pending`, and requeues the same job ID. Claimed stations therefore remain attached to their original job rather than being stranded or claimed by a competing job.

Normal task redelivery is also idempotent: when a task starts for a pending or stale job, it resets any processing station assigned to that job back to `claimed`, derives counters from persisted station outcomes, and continues. A currently healthy job is never reset because its heartbeat is still fresh.

## Persisted job scope

The existing `GeocodeJob` model tracks totals and outcomes. Its schema gains `heartbeat_at`. `FuelStation` gains the `claimed` geocoding status and a nullable relationship to its assigned geocoding job. A later explicit retry may reassign a failed station to a new job. This avoids passing thousands of station IDs through Celery and prevents concurrent jobs from processing the same rows. The schema changes are delivered in a new migration.

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

Tests mock all external HTTP, Redis, and Celery dispatch. Coverage includes request validation, permissions, empty batches, claimed-to-processing transitions, deterministic selection, success, no result, permanent failure, transient retry, idempotent task redelivery, stale heartbeat recovery, failed-station retry selection, aggregate status, distributed rate limiting, and route matching's success-only coordinate filter.

Repository verification runs Django tests, system checks, migration drift checks, and a Celery worker against disposable Redis when available. Automated tests require no network access.
