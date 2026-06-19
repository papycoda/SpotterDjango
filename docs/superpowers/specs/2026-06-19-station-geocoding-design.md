# Fuel Station Geocoding Design

## Objective

Geocode imported fuel stations asynchronously so route matching can use a local coordinate dataset. Route planning must never geocode stations or call Nominatim for station coordinates.

## API contract

`POST /api/v1/admin/fuel-stations/geocode/` accepts:

- `limit`: optional integer, default `500`, minimum `1`, maximum `2000`.
- `retry_failed`: optional boolean, default `false`.

The endpoint requires an authenticated staff user, creates a persisted `GeocodeJob`, queues a Celery task with only the job ID, and returns `202 Accepted` with the serialized job. If no eligible stations exist, the job is created as completed with zero work and no task is queued.

`GET /api/v1/admin/fuel-stations/geocode/status/` requires an authenticated staff user and returns aggregate station counts (`total`, `success`, `pending`, `processing`, and `failed`) plus the latest geocoding job, if one exists.

## Components

### Nominatim client

`api/clients/nominatim_client.py` owns all Nominatim HTTP behavior. It uses the configured base URL, identifying user agent, `NOMINATIM_TIMEOUT_SECONDS` bounded timeout, structured address parameters, JSON response format, a one-result limit, and a USA country-code restriction. It validates response shape and coordinate ranges.

The client returns coordinates for a valid match, returns `None` when Nominatim finds no match, and raises typed client exceptions for rate limiting, network errors, upstream failures, and malformed responses. Exceptions do not expose upstream bodies or credentials.

### Geocoding service

`api/services/geocoding_service.py` owns station selection and database coordination. It selects stations without coordinates in deterministic primary-key order:

- Normal jobs select `pending` stations.
- Explicit retry jobs select both `pending` and `failed` stations.
- Stations already marked `processing` or `success` are not selected.

Each selected station is marked `processing` before the external call. A valid result saves latitude and longitude and marks the station `success`. No match marks it `failed`. A transient client failure restores it to its prior eligible status and is re-raised so Celery can retry safely. A permanent client failure marks it `failed`.

The service processes only the stations assigned to the persisted job and updates job counters after every station, making progress visible and restarts idempotent.

### Celery task

`api/tasks.py` receives only a `GeocodeJob` ID. It atomically transitions a pending job to processing, invokes the service, and marks the job completed when all assigned work finishes. Transient Nominatim failures use bounded Celery autoretry with backoff and jitter. An exhausted or unexpected failure marks the job failed with a sanitized error.

Rate limiting is enforced immediately before each Nominatim request using `NOMINATIM_MIN_INTERVAL_SECONDS`. Only one geocoding worker should consume the geocoding queue in production so the configured interval is process-wide and Nominatim's public usage limit is respected.

## Persisted job scope

The existing `GeocodeJob` model tracks totals and outcomes. A new nullable relationship from `FuelStation` to its assigned geocoding job records which stations were claimed by a job. A later explicit retry may reassign a failed station to a new job. This avoids passing thousands of station IDs through Celery and prevents concurrent jobs from processing the same rows. The schema change is delivered in a new migration.

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

Tests mock all external HTTP and Celery dispatch. Coverage includes request validation, permissions, empty batches, job claiming, deterministic selection, success, no result, permanent failure, transient retry, idempotent task execution, failed-station retry selection, aggregate status, rate limiting, and route matching's success-only coordinate filter.

Repository verification runs Django tests, system checks, migration drift checks, and a Celery worker against disposable Redis when available. Automated tests require no network access.
