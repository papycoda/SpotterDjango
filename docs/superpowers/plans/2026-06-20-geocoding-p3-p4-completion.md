# Geocoding, P3, and P4 Completion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make station coordinates conservative and diagnosable, complete the fixed-vehicle fuel optimizer, and finish the assignment endpoint and delivery gates.

**Architecture:** Keep Nominatim access in the client and database transitions in the geocoding service. Keep route acquisition, local station filtering, and fuel optimization separate, with the destination represented as a non-purchasing terminal node. Integrate only after focused suites pass, then apply migrations and reconcile documentation against fresh verification.

**Tech Stack:** Python 3.14, Django 6, Django REST Framework, SQLite/PostgreSQL-compatible ORM, requests, Decimal, mocked Nominatim/OSRM tests.

---

## File responsibility map

- `api/clients/nominatim_client.py`: build bounded station queries, validate structured candidates, and expose typed results/errors.
- `api/services/geocoding_service.py`: job and station state transitions plus persistence of success/failure metadata.
- `api/migrations/0004_geocoding_failure_classification.py`: schema fields only.
- `api/migrations/0005_invalidate_low_confidence_geocodes.py`: forward-only data migration clearing unsafe stage-3/low-confidence results; its reverse is intentionally a no-op because deleted coordinates cannot be reconstructed.
- `api/services/fuel_optimization_service.py`: fixed-vehicle reachability, stop selection, purchases, and Decimal cost calculation.
- `api/views.py` and `api/serializers.py`: HTTP validation, service orchestration, stable errors, and decimal-string response serialization.
- `api/tests/test_clients.py`, `test_geocoding_service.py`, `test_geocoding_worker.py`: offline geocoding behavior.
- `api/tests/test_fuel_optimization.py`: optimizer regression behavior.
- `api/tests/test_route_fuel_plan.py`, `test_schema.py`: integrated endpoint contract, call budget, query count, and Swagger.
- `README.md`, `docs/P4-DELIVERY.md`, `TODO.md`: operational instructions and verified status.

### Task 1: Conservative Nominatim candidate matching

**Files:**
- Modify: `api/tests/test_clients.py`
- Modify: `api/clients/nominatim_client.py`

- [ ] **Step 1: Replace legacy client expectations with failing behavior tests**

Add focused tests that assert:

```python
def test_high_confidence_requires_identity_and_address_evidence(self):
    result = self.client.geocode(
        name="Love's Travel Stop #429",
        address="100 Main St",
        city="Dallas",
        state="TX",
    )
    self.assertEqual(result.confidence, "high")
    self.assertEqual(result.stage, 1)

def test_medium_confidence_accepts_identity_without_address_evidence(self):
    result = self.client.geocode(
        name="Pilot Travel Center #10",
        address="I-40 Exit 1",
        city="Memphis",
        state="TN",
    )
    self.assertEqual(result.confidence, "medium")

def test_never_accepts_an_unrelated_same_city_station(self):
    with self.assertRaises(NominatimPermanentError) as raised:
        self.client.geocode(
            name="Independent Truck Stop",
            address="US-50 Exit 2",
            city="Florence",
            state="KS",
        )
    self.assertEqual(raised.exception.reason, "no_match_osm")

def test_network_and_429_failures_remain_typed_transient_errors(self):
    with self.assertRaises(NominatimTransientError) as raised:
        self.client.geocode(name="Station", address="100 Main", city="Dallas", state="TX")
    self.assertIn(raised.exception.reason, {"network_error", "rate_limited", "upstream_error"})
```

Mock payloads must include structured `address`, `namedetails`, and `extratags`, and must separately cover state mismatch, locality mismatch, non-fuel features, malformed JSON, and invalid coordinates.

- [ ] **Step 2: Run the client tests and verify RED**

Run: `python manage.py test api.tests.test_clients.NominatimClientTests`

Expected: failures proving the current city-only fallback, collapsed transient errors, missing evidence fields, and old return contract.

- [ ] **Step 3: Implement the minimal typed client contract**

Use exception types carrying sanitized reasons:

```python
class NominatimError(Exception):
    def __init__(self, reason):
        self.reason = reason
        super().__init__(reason)

class NominatimNoMatchError(NominatimPermanentError):
    pass
```

Every request includes:

```python
{
    "countrycodes": "us",
    "format": "jsonv2",
    "addressdetails": 1,
    "namedetails": 1,
    "extratags": 1,
    "limit": 10,
}
```

Implement two strategies only: full identity/address/locality/state, then normalized identity/locality/state. Remove `_stage3_city_state_fallback`. Validate structured state and locality first, then fuel evidence, then normalized identity. Return `high` only when address/highway evidence also matches; otherwise return `medium`. When all candidates fail, raise the most specific deterministic no-match reason.

- [ ] **Step 4: Run client tests and verify GREEN**

Run: `python manage.py test api.tests.test_clients.NominatimClientTests`

Expected: all client tests pass with no network access.

- [ ] **Step 5: Commit the client task**

```powershell
git add api/clients/nominatim_client.py api/tests/test_clients.py
git commit -m "fix: make station geocoding conservative"
```

### Task 2: Persist classifications and invalidate unsafe coordinates

**Files:**
- Modify: `api/tests/test_geocoding_service.py`
- Modify: `api/tests/test_geocoding_worker.py`
- Modify: `api/services/geocoding_service.py`
- Modify: `api/migrations/0004_geocoding_failure_classification.py`
- Create: `api/migrations/0005_invalidate_low_confidence_geocodes.py`

- [ ] **Step 1: Write failing service and migration tests**

Add tests proving:

```python
def test_permanent_no_match_persists_specific_reason(self):
    client.geocode.side_effect = NominatimNoMatchError("city_mismatch")
    GeocodingService.process_job(job.id, client=client, worker_id="worker-1")
    station.refresh_from_db()
    self.assertEqual(station.geocoding_status, "failed")
    self.assertEqual(station.geocoding_failure_reason, "city_mismatch")

def test_transient_error_restores_claim_without_incrementing_counts(self):
    client.geocode.side_effect = NominatimTransientError("network_error")
    with self.assertRaises(NominatimTransientError):
        GeocodingService.process_job(job.id, client=client, worker_id="worker-1")
    station.refresh_from_db()
    self.assertEqual(station.geocoding_status, "claimed")
```

Add a migration test that seeds high, medium, low, stage-3, and legacy-null successes at migration state `0004`, migrates to `0005`, and asserts only low/stage-3 rows have null coordinates and `pending` status.

- [ ] **Step 2: Run focused tests and verify RED**

Run: `python manage.py test api.tests.test_geocoding_service api.tests.test_geocoding_worker api.tests.test_migrations`

Expected: failures for permanent-exception classification and missing invalidation migration.

- [ ] **Step 3: Implement persistence and forward invalidation**

Keep `0004` as schema-only. In `0005`, use historical models:

```python
def invalidate_unsafe_geocodes(apps, schema_editor):
    FuelStation = apps.get_model("api", "FuelStation")
    FuelStation.objects.filter(
        geocoding_status="success",
    ).filter(
        models.Q(geocoding_confidence="low") | models.Q(geocoding_stage=3)
    ).update(
        latitude=None,
        longitude=None,
        geocoding_status="pending",
        geocoding_failure_reason=None,
        geocoding_confidence=None,
        geocoding_stage=None,
        geocode_job=None,
    )
```

The reverse migration is a no-op because deleted coordinates cannot be reconstructed. Update `process_job` so typed permanent errors become failed rows with their exact allowed reason, transient errors restore `claimed`, and successful results persist only `high` or `medium` confidence.

- [ ] **Step 4: Run focused geocoding tests and verify GREEN**

Run: `python manage.py test api.tests.test_clients api.tests.test_geocoding_service api.tests.test_geocoding_worker api.tests.test_geocoding_endpoints api.tests.test_migrations`

Expected: all focused geocoding and migration tests pass.

- [ ] **Step 5: Commit the workflow task**

```powershell
git add api/services/geocoding_service.py api/migrations/0004_geocoding_failure_classification.py api/migrations/0005_invalidate_low_confidence_geocodes.py api/tests/test_geocoding_service.py api/tests/test_geocoding_worker.py api/tests/test_migrations.py
git commit -m "fix: persist safe geocoding outcomes"
```

### Task 3: Correct P3 fuel optimization

**Files:**
- Modify: `api/tests/test_fuel_optimization.py`
- Modify: `api/services/fuel_optimization_service.py`

- [ ] **Step 1: Normalize the failing optimizer tests around the assignment contract**

Ensure fixtures express route progress and total route distance in the same units. Required assertions include:

```python
def test_destination_is_terminal_and_never_a_purchase_stop(self):
    result = service.optimize_fuel_stops(route_700_miles, stations_at_400)
    self.assertEqual([stop.station.id for stop in result.fuel_stops], ["400"])
    self.assertTrue(all(stop.route_progress_miles < Decimal("700") for stop in result.fuel_stops))

def test_total_is_sum_of_half_up_rounded_stop_costs(self):
    result = service.optimize_fuel_stops(route, stations)
    self.assertEqual(result.total_cost_usd, sum(stop.cost_usd for stop in result.fuel_stops))
    self.assertEqual(result.fuel_stops[0].gallons_purchased.as_tuple().exponent, -3)
    self.assertEqual(result.fuel_stops[0].cost_usd.as_tuple().exponent, -2)
```

Retain explicit cases for under/exactly 500 miles, 700 miles, 1,200 miles, cheaper downstream stations, duplicate prices, and unbridgeable gaps.

- [ ] **Step 2: Run optimizer tests and verify RED**

Run: `python manage.py test api.tests.test_fuel_optimization`

Expected: the six previously observed route-gap errors plus new rounding/terminal-node failures.

- [ ] **Step 3: Implement a destination-aware greedy optimizer**

Use `Decimal` constants:

```python
MILES_PER_METER = Decimal("0.000621371192237334")
GALLON_QUANTUM = Decimal("0.001")
MONEY_QUANTUM = Decimal("0.01")
```

Represent start at mile `0`, stations at their route progress, and destination at `route.distance_miles`. At each station, calculate arrival fuel from the previous node; buy enough to reach the first cheaper station reachable with a full tank, otherwise enough to reach the next feasibility-preserving node or destination. Quantize gallons and stop cost with `ROUND_HALF_UP`, cap purchases at tank capacity, and sum rounded stop costs. Never emit start or destination as a fuel stop.

- [ ] **Step 4: Run optimizer tests and verify GREEN**

Run: `python manage.py test api.tests.test_fuel_optimization`

Expected: all optimizer tests pass.

- [ ] **Step 5: Commit P3**

```powershell
git add api/services/fuel_optimization_service.py api/tests/test_fuel_optimization.py
git commit -m "fix: complete fixed-vehicle fuel optimization"
```

### Task 4: Integrate the endpoint contract and Swagger

**Files:**
- Modify: `api/serializers.py`
- Modify: `api/views.py`
- Create: `api/tests/test_route_fuel_plan.py`
- Modify: `api/tests/test_schema.py`

- [ ] **Step 1: Write failing endpoint integration tests**

Mock only the external/service boundaries and assert:

```python
def test_returns_decimal_strings_and_geojson(self):
    response = self.client.post(self.url, {"start": "Dallas, TX", "finish": "Denver, CO"}, format="json")
    self.assertEqual(response.status_code, 200)
    self.assertEqual(response.data["route_geometry"]["type"], "LineString")
    self.assertRegex(response.data["total_fuel_cost"], r"^\d+\.\d{2}$")
    self.assertRegex(response.data["total_fuel_purchased"], r"^\d+\.\d{3}$")
```

Add separate tests for invalid input `400`, unresolved location `404`, infeasible corridor `422`, transient geocoder/OSRM `502`, exactly two endpoint-geocoder calls, exactly one OSRM call, and no Nominatim call during station filtering/optimization.

- [ ] **Step 2: Run endpoint/schema tests and verify RED**

Run: `python manage.py test api.tests.test_route_fuel_plan api.tests.test_schema`

Expected: failures for float serialization, missing integration coverage, or schema mismatch.

- [ ] **Step 3: Implement the minimal HTTP-boundary changes**

Keep views limited to serializer validation, service calls, and response mapping. Serialize `Decimal` values with fixed formatting:

```python
"price_per_gallon": format(stop.station.price_per_gallon, ".2f"),
"route_progress_miles": format(stop.route_progress_miles, ".3f"),
"gallons_purchased": format(stop.gallons_purchased, ".3f"),
"cost_usd": format(stop.cost_usd, ".2f"),
"total_fuel_purchased": format(fuel_plan.total_fuel_purchased, ".3f"),
"total_fuel_cost": format(fuel_plan.total_cost_usd, ".2f"),
```

Remove the stale Swagger statement that the endpoint is scaffolded. Ensure response serializers use `DecimalField(..., coerce_to_string=True)` or string fields with decimal patterns matching the actual response.

- [ ] **Step 4: Run integration and schema tests and verify GREEN**

Run: `python manage.py test api.tests.test_route_fuel_plan api.tests.test_schema api.tests.test_route_service api.tests.test_station_filtering`

Expected: all route acquisition, filtering, endpoint, and schema tests pass.

- [ ] **Step 5: Commit endpoint integration**

```powershell
git add api/serializers.py api/views.py api/tests/test_route_fuel_plan.py api/tests/test_schema.py
git commit -m "feat: complete fuel-plan API contract"
```

### Task 5: P4 performance, migration, documentation, and backlog reconciliation

**Files:**
- Modify: `api/tests/test_route_fuel_plan.py`
- Modify: `README.md`
- Modify: `docs/P4-DELIVERY.md`
- Modify: `TODO.md`

- [ ] **Step 1: Add representative offline delivery gates**

Add an endpoint test using mocked endpoint geocoding/OSRM and a bounded local station fixture. Wrap the request with `CaptureQueriesContext(connection)` and `time.perf_counter()`:

```python
with CaptureQueriesContext(connection) as queries:
    started = time.perf_counter()
    response = self.client.post(self.url, payload, format="json")
    elapsed = time.perf_counter() - started
self.assertEqual(response.status_code, 200)
self.assertLessEqual(len(queries), 12)
self.assertLess(elapsed, 0.5)
```

The threshold measures local work only and must not include real HTTP calls.

- [ ] **Step 2: Run the delivery test and verify RED or existing compliance**

Run: `python manage.py test api.tests.test_route_fuel_plan`

Expected: if the new bound fails, capture the query list and optimize only the demonstrated excess; otherwise record the passing baseline without speculative changes.

- [ ] **Step 3: Update operational documentation and TODO truthfully**

Document migration order, CSV import, automatic database worker, one-worker SQLite restriction, conservative confidence rules, explicit retry behavior, endpoint example with decimal strings, fixed vehicle assumptions, and the three-call route budget. In `TODO.md`, mark only items supported by tests/checks. Keep the approved live external smoke request unchecked if not run.

- [ ] **Step 4: Apply local migrations and verify invalidation state**

Run:

```powershell
python manage.py migrate
python manage.py showmigrations api
```

Expected: migrations through `0005` show `[X]`; any existing low-confidence/stage-3 successes have been reset with null coordinates while legacy-null/high/medium successes remain.

- [ ] **Step 5: Run all repository gates**

Run:

```powershell
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py test
python manage.py run_geocoding_worker --once
git diff --check
```

Expected: checks pass, no migration drift, all tests pass, the once-worker exits cleanly using mocked-network tests for external behavior, and no whitespace errors exist. Do not run a live worker against Nominatim merely to satisfy verification.

- [ ] **Step 6: Commit verified delivery state**

```powershell
git add README.md docs/P4-DELIVERY.md TODO.md api/tests/test_route_fuel_plan.py
git commit -m "docs: finalize verified assignment delivery"
```

### Task 6: Independent reviews and final integration audit

**Files:**
- Review all files changed by Tasks 1-5

- [ ] **Step 1: Run specification-compliance review**

Compare every requirement in `docs/superpowers/specs/2026-06-20-geocoding-p3-p4-completion-design.md` against the diff and tests. Reject city-only coordinates, float money, untyped transient failures, destination purchases, or unsupported checked TODO items.

- [ ] **Step 2: Run code-quality review**

Inspect service/client boundaries, sanitized errors, Decimal conversions, ORM portability, migration safety, deterministic tests, and accidental edits to unrelated scaffold endpoints.

- [ ] **Step 3: Re-run final verification after review fixes**

Run:

```powershell
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py test
git diff --check
git status --short
```

Expected: all automated gates pass; status contains only intentional changes. Report the live external smoke test separately as completed or explicitly unverified.
