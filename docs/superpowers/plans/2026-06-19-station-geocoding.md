# Fuel Station Geocoding Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a resumable, database-backed worker that geocodes imported fuel stations ahead of route planning and exposes staff-only trigger and status APIs.

**Architecture:** Staff requests create a persisted job and atomically claim a bounded set of stations. A Django management-command worker transitions one station at a time from claimed to processing, calls Nominatim through a bounded client and database slot limiter, then persists success or failure; ownership, heartbeats, and restart-time stale recovery make interrupted work resumable. Celery remains an optional thin adapter.

**Tech Stack:** Python 3.14, Django 6, Django REST Framework, requests, SQLite development/PostgreSQL production-compatible ORM; optional Celery adapter.

---

## File map

- Modify `api/models.py`: claimed station state, job assignment, and heartbeat.
- Create `api/migrations/0003_geocoding_workflow.py`: additive schema migration.
- Modify `fuelSpotter/settings.py` and `.env.example`: timeout, stale threshold, polling, and retry configuration.
- Modify `api/serializers.py`: validated trigger request and explicit admin job response.
- Create `api/services/geocoding_rate_limiter.py`: atomic database request-slot reservation.
- Replace `api/clients/nominatim_client.py`: bounded, typed Nominatim adapter.
- Replace `api/services/geocoding_service.py`: job creation, station transitions, counters, and recovery.
- Create `api/management/commands/run_geocoding_worker.py`: default queue worker and stale recovery loop.
- Replace the geocoding entry point in `api/tasks.py`: optional persisted-job Celery wrapper.
- Modify `api/views.py`: staff-only trigger and aggregate status endpoints.
- Modify `api/services/station_matching_service.py`: success-only local coordinate queryset.
- Expand the layered `api/tests/` package with network-free coverage.
- Update `TODO.md`: mark local coordinate enrichment workflow complete.

### Task 1: Persist lifecycle and validate configuration

**Files:**
- Modify: `api/models.py`
- Create: `api/migrations/0003_geocoding_workflow.py`
- Modify: `fuelSpotter/settings.py`
- Modify: `.env.example`
- Modify: `api/serializers.py`
- Test: `api/tests/test_models.py`
- Test: `api/tests/test_serializers.py`
- Test: `api/tests/test_settings.py`

- [ ] **Step 1: Write failing tests for the new schema, request constraints, and settings**

Add tests asserting `claimed` is a valid `FuelStation.geocoding_status`, a station can reference a `GeocodeJob`, `GeocodeJob.heartbeat_at` is nullable, the singleton rate-limit model stores `next_allowed_at`, and `GeocodeRequestSerializer` defaults to `limit=500/retry_failed=False` while rejecting limits outside `1..2000`. Extend the environment subprocess test to assert `GEOCODING_STALE_AFTER_SECONDS`, `GEOCODING_POLL_SECONDS`, and `NOMINATIM_TIMEOUT_SECONDS`.

```python
serializer = GeocodeRequestSerializer(data={})
self.assertTrue(serializer.is_valid(), serializer.errors)
self.assertEqual(serializer.validated_data, {"limit": 500, "retry_failed": False})

for invalid in (0, 2001):
    serializer = GeocodeRequestSerializer(data={"limit": invalid})
    self.assertFalse(serializer.is_valid())
```

- [ ] **Step 2: Run tests and verify RED**

Run: `python manage.py test api.tests.test_models api.tests.test_serializers api.tests.test_settings -v 2`

Expected: failures because the fields, status, serializer, and settings do not exist.

- [ ] **Step 3: Implement the additive schema and validation**

Add `('claimed', 'Claimed')`, `FuelStation.geocode_job = ForeignKey(GeocodeJob, null=True, blank=True, on_delete=SET_NULL, related_name='stations')` after moving `GeocodeJob` above `FuelStation` or using a string model reference, and `GeocodeJob.heartbeat_at = DateTimeField(null=True, blank=True)`. Add:

```python
class GeocodeRequestSerializer(serializers.Serializer):
    limit = serializers.IntegerField(min_value=1, max_value=2000, default=500)
    retry_failed = serializers.BooleanField(default=False)
```

Use explicit fields on `GeocodeJobSerializer`. Add settings with validation that the stale threshold exceeds the timeout plus interval:

```python
NOMINATIM_TIMEOUT_SECONDS = float(os.environ.get('NOMINATIM_TIMEOUT_SECONDS', EXTERNAL_HTTP_TIMEOUT_SECONDS))
GEOCODING_STALE_AFTER_SECONDS = float(os.environ.get('GEOCODING_STALE_AFTER_SECONDS', '120'))
GEOCODING_POLL_SECONDS = float(os.environ.get('GEOCODING_POLL_SECONDS', '5'))
if GEOCODING_STALE_AFTER_SECONDS <= NOMINATIM_TIMEOUT_SECONDS + NOMINATIM_MIN_INTERVAL_SECONDS:
    raise ImproperlyConfigured('GEOCODING_STALE_AFTER_SECONDS must exceed the Nominatim timeout and interval')
```

Add `GEOCODING_POLL_SECONDS` and bounded retry settings. Generate migration with `python manage.py makemigrations api` and inspect it rather than editing an applied migration.

- [ ] **Step 4: Run tests and migration checks GREEN**

Run: `python manage.py test api.tests.test_models api.tests.test_serializers api.tests.test_settings -v 2`

Run: `python manage.py makemigrations --check --dry-run`

Expected: tests pass and `No changes detected`.

- [ ] **Step 5: Commit**

```powershell
git add api/models.py api/migrations/0003_geocoding_workflow.py api/serializers.py api/tests/test_models.py api/tests/test_serializers.py api/tests/test_settings.py fuelSpotter/settings.py .env.example
git commit -m "feat: add geocoding job lifecycle"
```

### Task 2: Build the database limiter and Nominatim client

**Files:**
- Create: `api/services/geocoding_rate_limiter.py`
- Replace: `api/clients/nominatim_client.py`
- Replace: `api/tests/test_clients.py`

- [ ] **Step 1: Write failing client tests**

Cover structured USA search parameters, configured URL/user agent/timeout, valid decimal coordinates, empty results, malformed payload, HTTP `429`, HTTP `5xx`, other status errors, request exceptions, and database limiter invocation. Use patched `requests.Session.get` and a fake limiter; no test performs network I/O.

```python
result = client.geocode(address="100 Main St", city="Dallas", state="TX")
self.assertEqual(result.latitude, Decimal("32.7767000"))
session.get.assert_called_once_with(
    f"{settings.NOMINATIM_BASE_URL}/search",
    params={"street": "100 Main St", "city": "Dallas", "state": "TX", "countrycodes": "us", "format": "jsonv2", "limit": 1},
    headers={"User-Agent": settings.NOMINATIM_USER_AGENT},
    timeout=settings.NOMINATIM_TIMEOUT_SECONDS,
)
```

Test sequential database reservations with an injected clock and sleeper: the first reservation runs immediately, the second sleeps for the remaining interval, and the stored `next_allowed_at` advances atomically.

- [ ] **Step 2: Run tests and verify RED**

Run: `python manage.py test api.tests.test_clients -v 2`

Expected: failures because typed exceptions, coordinate result, limiter, and HTTP behavior are absent.

- [ ] **Step 3: Implement minimal typed adapters**

Create `GeocodingResult`, `NominatimError`, `NominatimTransientError`, and `NominatimPermanentError`. Map network/429/5xx to transient; malformed/other statuses to permanent; return `None` for `[]`. Quantize coordinates to seven decimal places and enforce latitude `[-90,90]` and longitude `[-180,180]`.

Create `DatabaseRateLimiter.acquire()` using `transaction.atomic()` and `select_for_update()` on the singleton `GeocodingRateLimit` row. Reserve `max(now, next_allowed_at)`, persist the following slot, commit, and sleep until the reserved time. Inject the clock and sleeper for deterministic tests.

- [ ] **Step 4: Run tests and verify GREEN**

Run: `python manage.py test api.tests.test_clients -v 2`

Expected: all client tests pass without network access.

- [ ] **Step 5: Commit**

```powershell
git add api/clients/nominatim_client.py api/services/geocoding_rate_limiter.py api/tests/test_clients.py
git commit -m "feat: add database-rate-limited Nominatim client"
```

### Task 3: Implement job claiming and station processing

**Files:**
- Replace: `api/services/geocoding_service.py`
- Create: `api/tests/test_geocoding_service.py`

- [ ] **Step 1: Write failing tests for job creation and processing**

Cover deterministic bounded claiming, default exclusion of failed stations, explicit failed retry, no-work completed job, one-at-a-time claimed-to-processing transitions, success persistence, no-result failure, permanent failure, transient reset to claimed, counter updates, and preservation of already successful coordinates.

```python
job = GeocodingService.create_job(limit=2, retry_failed=False)
self.assertEqual(job.total_stations, 2)
self.assertEqual(
    list(job.stations.values_list("geocoding_status", flat=True)),
    ["claimed", "claimed"],
)

GeocodingService.process_job(job.id, client=fake_client)
station.refresh_from_db()
self.assertEqual(station.geocoding_status, "success")
self.assertEqual(station.latitude, Decimal("32.7767000"))
```

- [ ] **Step 2: Run tests and verify RED**

Run: `python manage.py test api.tests.test_geocoding_service -v 2`

Expected: failures because the service methods do not exist.

- [ ] **Step 3: Implement transactional lifecycle methods**

Implement `create_job(limit, retry_failed)`, `start_or_resume_job(job_id)`, `process_job(job_id, client=None)`, `finish_job(job_id)`, `fail_job(job_id, message)`, and `recover_stale_jobs(cutoff)`. Claim with `transaction.atomic()`, deterministic `order_by('pk')`, `select_for_update(skip_locked=True)` only when the backend supports it, and conditional updates so concurrent workers cannot process the same station.

Refresh `heartbeat_at` immediately before and after each client call. Derive processed/success/failed counts from the assigned stations so redelivery cannot double-increment counters. Sanitize job error messages to fixed application text.

- [ ] **Step 4: Run tests and verify GREEN**

Run: `python manage.py test api.tests.test_geocoding_service -v 2`

Expected: all lifecycle tests pass.

- [ ] **Step 5: Commit**

```powershell
git add api/services/geocoding_service.py api/tests/test_geocoding_service.py
git commit -m "feat: process resumable geocoding jobs"
```

### Task 4: Implement the database worker and optional Celery adapter

**Files:**
- Modify: `api/tasks.py`
- Create: `api/management/commands/run_geocoding_worker.py`
- Create: `api/tests/test_geocoding_worker.py`
- Replace: `api/tests/test_tasks.py`

- [ ] **Step 1: Write failing task tests**

Cover oldest-job selection, `--once`, watch polling, transient bounded retry, terminal failure cleanup, stale recovery before polling, SQLite live-worker exclusion, and fresh-job exclusion. Separately test that the optional Celery wrapper accepts only a job ID and calls the same service; no Redis or Celery worker is required.

```python
call_command("run_geocoding_worker", "--once")
process.assert_called_once_with(job.id)

with patch("api.tasks.GeocodingService.process_job") as process:
    geocode_stations_task.run(job.id)
process.assert_called_once_with(job.id)
```

- [ ] **Step 2: Run tests and verify RED**

Run: `python manage.py test api.tests.test_tasks -v 2`

Expected: failures from placeholder task behavior.

- [ ] **Step 3: Implement bounded retry and recovery tasks**

Implement the command loop with a unique worker token, stale recovery before each poll, oldest-pending-job selection, bounded exponential sleep for transient failures, `--once`, and `--watch`. On SQLite, reject a second live owner; PostgreSQL relies on row locks and conditional ownership. Keep `geocode_stations_task(job_id)` as a thin optional wrapper around `GeocodingService.process_job(job_id)` without endpoint dispatch or Redis-specific behavior.

- [ ] **Step 4: Run tests and verify GREEN**

Run: `python manage.py test api.tests.test_geocoding_worker api.tests.test_tasks -v 2`

Expected: all task tests pass.

- [ ] **Step 5: Commit**

```powershell
git add api/tasks.py api/management/commands/run_geocoding_worker.py api/tests/test_geocoding_worker.py api/tests/test_tasks.py
git commit -m "feat: run database geocoding worker"
```

### Task 5: Expose staff trigger and status APIs

**Files:**
- Modify: `api/views.py`
- Create: `api/tests/test_geocoding_endpoints.py`
- Modify: `api/tests/test_admin_permissions.py`

- [ ] **Step 1: Write failing endpoint tests**

Cover `202` job creation, `200` zero-work completion, invalid input `400`, explicit retry flag, aggregate counts including claimed, latest serialized job, and existing anonymous/non-staff authorization behavior. Assert the endpoint never calls Celery.

```python
response = self.client.post(reverse("admin-geocode-stations"), {"limit": 25}, format="json")
self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
self.assertEqual(response.data["status"], "pending")

response = self.client.get(reverse("admin-geocode-status"))
self.assertEqual(response.data["counts"]["claimed"], 1)
```

- [ ] **Step 2: Run tests and verify RED**

Run: `python manage.py test api.tests.test_geocoding_endpoints api.tests.test_admin_permissions -v 2`

Expected: placeholder endpoint responses fail the contract.

- [ ] **Step 3: Implement thin HTTP boundaries**

Validate POST through `GeocodeRequestSerializer`, call `GeocodingService.create_job`, and serialize with `GeocodeJobSerializer`; do not dispatch threads, Celery, or HTTP work from the request. GET aggregates status counts with one grouped ORM query and returns the latest job or `null`. Keep `IsOperationsAdmin` on both endpoints and do not call clients from views.

- [ ] **Step 4: Run tests and verify GREEN**

Run: `python manage.py test api.tests.test_geocoding_endpoints api.tests.test_admin_permissions -v 2`

Expected: endpoint and permission tests pass.

- [ ] **Step 5: Commit**

```powershell
git add api/views.py api/tests/test_geocoding_endpoints.py api/tests/test_admin_permissions.py
git commit -m "feat: expose geocoding operations API"
```

### Task 6: Enforce the route-planning boundary and finish delivery

**Files:**
- Modify: `api/services/station_matching_service.py`
- Create: `api/tests/test_station_matching_service.py`
- Modify: `TODO.md`

- [ ] **Step 1: Write a failing local-data-only station query test**

Create success, pending, failed, and coordinate-null stations. Assert `eligible_stations()` returns only success rows with both coordinates and performs no client call.

```python
ids = list(StationMatchingService.eligible_stations().values_list("pk", flat=True))
self.assertEqual(ids, [successful.pk])
```

- [ ] **Step 2: Run the test and verify RED**

Run: `python manage.py test api.tests.test_station_matching_service -v 2`

Expected: failure because `eligible_stations` is absent.

- [ ] **Step 3: Implement the explicit boundary and update the backlog**

Implement:

```python
@staticmethod
def eligible_stations():
    return FuelStation.objects.filter(
        geocoding_status="success",
        latitude__isnull=False,
        longitude__isnull=False,
    )
```

Make future corridor filtering start from this queryset. Mark the P1 coordinate-enrichment item complete in `TODO.md`, noting that batches are populated through the admin endpoint and never during route requests.

- [ ] **Step 4: Run complete verification**

Run:

```powershell
python manage.py test -v 2
python manage.py check
python manage.py makemigrations --check --dry-run
python -m compileall -q api fuelSpotter
git diff --check
```

Expected: all tests pass, no Django issues, no migration drift, successful compilation, and no whitespace errors.

Run the management-command worker against a mocked/local Nominatim endpoint for a one-station job, terminate it during processing, restart it after the stale threshold, and verify the same job resumes. This integration path must not require Redis or a Celery worker.

- [ ] **Step 5: Commit**

```powershell
git add api/services/station_matching_service.py api/tests/test_station_matching_service.py TODO.md
git commit -m "feat: restrict route matching to geocoded stations"
```
