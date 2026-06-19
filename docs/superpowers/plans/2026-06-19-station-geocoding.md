# Fuel Station Geocoding Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a resumable, rate-limited Celery workflow that geocodes imported fuel stations ahead of route planning and exposes staff-only trigger and status APIs.

**Architecture:** Staff requests create a persisted job and atomically claim a bounded set of stations. A Celery worker transitions one station at a time from claimed to processing, calls Nominatim through a bounded client and Redis slot limiter, then persists success or failure; late acknowledgement, heartbeats, and periodic stale recovery make interrupted work resumable.

**Tech Stack:** Python 3.14, Django 6, Django REST Framework, Celery 5.6, Redis 8, requests, SQLite/PostgreSQL-compatible ORM.

---

## File map

- Modify `api/models.py`: claimed station state, job assignment, and heartbeat.
- Create `api/migrations/0003_geocoding_workflow.py`: additive schema migration.
- Modify `fuelSpotter/settings.py` and `.env.example`: timeout, stale threshold, Redis limiter, queue, and Beat configuration.
- Modify `api/serializers.py`: validated trigger request and explicit admin job response.
- Create `api/clients/rate_limiter.py`: atomic Redis request-slot reservation.
- Replace `api/clients/nominatim_client.py`: bounded, typed Nominatim adapter.
- Replace `api/services/geocoding_service.py`: job creation, station transitions, counters, and recovery.
- Replace geocoding entry points in `api/tasks.py`: retryable work and stale-job recovery.
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

Add tests asserting `claimed` is a valid `FuelStation.geocoding_status`, a station can reference a `GeocodeJob`, `GeocodeJob.heartbeat_at` is nullable, and `GeocodeRequestSerializer` defaults to `limit=500/retry_failed=False` while rejecting limits outside `1..2000`. Extend the environment subprocess test to assert `NOMINATIM_RATE_LIMIT_REDIS_URL`, `GEOCODING_STALE_AFTER_SECONDS`, and `NOMINATIM_TIMEOUT_SECONDS`.

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
NOMINATIM_RATE_LIMIT_REDIS_URL = os.environ.get('NOMINATIM_RATE_LIMIT_REDIS_URL', CELERY_BROKER_URL)
GEOCODING_STALE_AFTER_SECONDS = float(os.environ.get('GEOCODING_STALE_AFTER_SECONDS', '120'))
if GEOCODING_STALE_AFTER_SECONDS <= NOMINATIM_TIMEOUT_SECONDS + NOMINATIM_MIN_INTERVAL_SECONDS:
    raise ImproperlyConfigured('GEOCODING_STALE_AFTER_SECONDS must exceed the Nominatim timeout and interval')
```

Configure `api.tasks.geocode_stations_task` on queue `geocoding` and a one-minute Beat entry for stale recovery. Generate migration with `python manage.py makemigrations api` and inspect it rather than editing an applied migration.

- [ ] **Step 4: Run tests and migration checks GREEN**

Run: `python manage.py test api.tests.test_models api.tests.test_serializers api.tests.test_settings -v 2`

Run: `python manage.py makemigrations --check --dry-run`

Expected: tests pass and `No changes detected`.

- [ ] **Step 5: Commit**

```powershell
git add api/models.py api/migrations/0003_geocoding_workflow.py api/serializers.py api/tests/test_models.py api/tests/test_serializers.py api/tests/test_settings.py fuelSpotter/settings.py .env.example
git commit -m "feat: add geocoding job lifecycle"
```

### Task 2: Build the distributed limiter and Nominatim client

**Files:**
- Create: `api/clients/rate_limiter.py`
- Replace: `api/clients/nominatim_client.py`
- Replace: `api/tests/test_clients.py`

- [ ] **Step 1: Write failing client tests**

Cover structured USA search parameters, configured URL/user agent/timeout, valid decimal coordinates, empty results, malformed payload, HTTP `429`, HTTP `5xx`, other status errors, request exceptions, and Redis limiter invocation. Use patched `requests.Session.get` and a fake limiter; no test performs network I/O.

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

Test the limiter's Lua result behavior with a fake Redis object: zero wait returns immediately; positive milliseconds call the injected sleeper and retry; Redis errors raise `RateLimiterUnavailable`.

- [ ] **Step 2: Run tests and verify RED**

Run: `python manage.py test api.tests.test_clients -v 2`

Expected: failures because typed exceptions, coordinate result, limiter, and HTTP behavior are absent.

- [ ] **Step 3: Implement minimal typed adapters**

Create `GeocodingResult`, `NominatimError`, `NominatimTransientError`, and `NominatimPermanentError`. Map network/429/5xx to transient; malformed/other statuses to permanent; return `None` for `[]`. Quantize coordinates to seven decimal places and enforce latitude `[-90,90]` and longitude `[-180,180]`.

Create `RedisRateLimiter.acquire()` using `redis.Redis.from_url(...).eval(...)` with a Lua script that reads Redis `TIME`, atomically sets the next permitted millisecond timestamp with `PSETEX`, and returns required wait milliseconds. Inject `sleep` for tests. Never fall back to an unthrottled call.

- [ ] **Step 4: Run tests and verify GREEN**

Run: `python manage.py test api.tests.test_clients -v 2`

Expected: all client tests pass without network access.

- [ ] **Step 5: Commit**

```powershell
git add api/clients/nominatim_client.py api/clients/rate_limiter.py api/tests/test_clients.py
git commit -m "feat: add rate-limited Nominatim client"
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

### Task 4: Implement Celery execution and stale recovery

**Files:**
- Modify: `api/tasks.py`
- Replace: `api/tests/test_tasks.py`

- [ ] **Step 1: Write failing task tests**

Cover job-ID-only arguments, successful completion, missing job as a safe no-op, transient `self.retry`, terminal failure cleanup, late-ack task options, stale recovery requeue, and fresh-job exclusion. Patch services and `.delay`; do not require Redis in unit tests.

```python
with patch("api.tasks.GeocodingService.process_job") as process:
    geocode_stations_task.run(job.id)
process.assert_called_once_with(job.id)

recovered = recover_stale_geocode_jobs_task.run()
self.assertEqual(recovered, 1)
geocode_stations_task.delay.assert_called_once_with(job.id)
```

- [ ] **Step 2: Run tests and verify RED**

Run: `python manage.py test api.tests.test_tasks -v 2`

Expected: failures from placeholder task behavior.

- [ ] **Step 3: Implement bounded retry and recovery tasks**

Define the bound task with `acks_late=True`, `reject_on_worker_lost=True`, `max_retries=5`, queue `geocoding`, and exponential countdown capped at 300 seconds. Retry only `NominatimTransientError` and `RateLimiterUnavailable`; terminal errors call `fail_job`. The recovery task computes `timezone.now() - timedelta(seconds=settings.GEOCODING_STALE_AFTER_SECONDS)`, calls `recover_stale_jobs`, and enqueues each recovered job ID.

- [ ] **Step 4: Run tests and verify GREEN**

Run: `python manage.py test api.tests.test_tasks -v 2`

Expected: all task tests pass.

- [ ] **Step 5: Commit**

```powershell
git add api/tasks.py api/tests/test_tasks.py
git commit -m "feat: run and recover geocoding tasks"
```

### Task 5: Expose staff trigger and status APIs

**Files:**
- Modify: `api/views.py`
- Create: `api/tests/test_geocoding_endpoints.py`
- Modify: `api/tests/test_admin_permissions.py`

- [ ] **Step 1: Write failing endpoint tests**

Cover `202` creation and dispatch, `200` zero-work completion without dispatch, invalid input `400`, explicit retry flag, aggregate counts including claimed, latest serialized job, and existing anonymous/non-staff authorization behavior.

```python
response = self.client.post(reverse("admin-geocode-stations"), {"limit": 25}, format="json")
self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
geocode_stations_task.delay.assert_called_once_with(response.data["id"])

response = self.client.get(reverse("admin-geocode-status"))
self.assertEqual(response.data["counts"]["claimed"], 1)
```

- [ ] **Step 2: Run tests and verify RED**

Run: `python manage.py test api.tests.test_geocoding_endpoints api.tests.test_admin_permissions -v 2`

Expected: placeholder endpoint responses fail the contract.

- [ ] **Step 3: Implement thin HTTP boundaries**

Validate POST through `GeocodeRequestSerializer`, call `GeocodingService.create_job`, dispatch only non-empty jobs after transaction commit, and serialize with `GeocodeJobSerializer`. GET aggregates status counts with one grouped ORM query and returns the latest job or `null`. Keep `IsOperationsAdmin` on both endpoints and do not call clients from views.

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

If disposable Redis is available, run a Celery worker limited to the geocoding queue and Celery Beat, enqueue a one-station job against a mocked/local Nominatim endpoint, terminate the worker during processing, and verify redelivery or stale recovery. If Redis is unavailable, report this integration gate explicitly rather than implying it passed.

- [ ] **Step 5: Commit**

```powershell
git add api/services/station_matching_service.py api/tests/test_station_matching_service.py TODO.md
git commit -m "feat: restrict route matching to geocoded stations"
```
