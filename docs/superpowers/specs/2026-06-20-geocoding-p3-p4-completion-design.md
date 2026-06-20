# Geocoding, P3, and P4 Completion Design

## Objective

Finish the assignment without inflating geocoding coverage through incorrect coordinates. Repair station geocoding first, complete the fuel optimizer and delivery checks, then reconcile `TODO.md` with verified behavior.

## Geocoding correctness

Station geocoding remains an offline database-backed workflow. Route requests never geocode stations.

The Nominatim client will use bounded, rate-limited query strategies that preserve the existing shared database limiter. A candidate is accepted only when it:

- is a fuel/service-station feature;
- is in the expected US state;
- matches the requested locality or a documented equivalent locality field; and
- has sufficient station-identity or address evidence.

The client requests Nominatim `addressdetails`, `namedetails`, and `extratags`. Valid fuel evidence is `category=amenity` with `type=fuel`; `highway=services` only when `name`, `brand`, or `operator` matches the source station; or `shop=convenience` only when extra tags explicitly identify fuel service. State and locality are validated from structured address fields. Display text is supporting evidence, not a substitute for structured state and locality validation.

Confidence is deterministic:

- **High:** correct state and locality, valid fuel evidence, matching normalized station identity, and matching postal-address or highway/exit evidence.
- **Medium:** correct state and locality, valid fuel evidence, and matching normalized name, brand, or operator, but insufficient address evidence.
- **Low:** city/state-only or otherwise ambiguous identity evidence. New low-confidence results are rejected rather than persisted as successes.

The city-only fallback is removed because it can assign an unrelated station in the same city. A forward data migration clears latitude and longitude and resets the status for rows matching `geocoding_status=success AND (geocoding_confidence=low OR geocoding_stage=3)`. Legacy successes with null confidence came from the older strict station/state/locality matcher and remain unchanged. Existing high- and medium-confidence results remain unless they fail the new validation rules.

The client returns successful coordinates or raises typed transient/permanent exceptions. It does not convert network and upstream failures into a generic rate-limit result. Permanent no-match outcomes carry a specific reason such as no OSM match, state mismatch, city mismatch, non-fuel feature, or invalid response. The service persists these reasons, leaves transient work claimed for retry, and never repeatedly retries a deterministic failed query without changing strategy.

Migration `0004` remains the forward schema change and must be applied before running the revised worker. Tests use migrations through Django's test database and require no network.

## P3 fuel optimization

The optimizer uses the fixed assignment assumptions: 500-mile maximum range, 10 MPG, 50-gallon tank, and departure with a full tank. It tracks route position and fuel using `Decimal` values. The destination is a terminal node at the route's total distance: the vehicle must be able to reach it, but no purchase occurs there.

At every decision point it determines which stations are reachable with current fuel. It prefers a cheaper reachable downstream station; otherwise it selects a safe reachable station that preserves route feasibility. Fuel purchases cover the required downstream leg without exceeding tank capacity. Every leg from start through selected stops to the terminal destination must be at most 500 miles. An unbridgeable corridor produces the stable 422-domain error.

Prices, gallons, per-stop costs, and totals use `Decimal` throughout the optimizer. Gallons are quantized to `0.001` and each stop cost to `0.01` with `ROUND_HALF_UP`; total cost is the sum of the rounded stop costs. Decimal monetary values are serialized as strings at the API boundary to avoid binary-float loss.

Required regression coverage includes short and exact-range trips, one- and multi-stop trips, cheaper downstream stations, duplicate prices, decimal cost precision, and unreachable gaps.

## Endpoint and delivery completion

`POST /api/v1/routes/fuel-plan/` remains the sole assignment endpoint with `start` and `finish`. It performs two endpoint-geocoding calls and one OSRM routing call, then uses only persisted successful station coordinates for local matching and optimization.

The response contains GeoJSON route geometry, distance and duration, selected fuel stops, total fuel purchased, total en-route cost, and vehicle assumptions. Errors remain stable at 400 for invalid input, 404 for unresolved locations, 422 for an infeasible route/fuel plan, and 502 for transient upstream failures. Swagger must match the real request, response, and errors.

P4 verification includes:

- `python manage.py check`;
- `python manage.py makemigrations --check --dry-run`;
- the complete offline Django test suite;
- focused worker stale/retry tests with mocked Nominatim responses;
- representative query-count and mocked response-time assertions;
- migration status and application to the local database; and
- a line-by-line `TODO.md` reconciliation based only on verified results.

A live external smoke request is optional and must not be required by automated tests. If it cannot be safely run, it remains explicitly unverified rather than being marked complete.

## Work division and integration

After the implementation plan is approved, independent subagents will handle:

1. geocoding client/service correctness and migration-safe invalidation;
2. P3 optimizer correctness and regression tests; and
3. P4 endpoint/schema/documentation/performance verification after the first two streams settle.

All production changes follow test-first development. The primary agent reviews each stream, resolves integration conflicts, runs the full verification gates, and updates `TODO.md` only after evidence supports each checkbox.

## Non-goals

- Redis or Celery as required infrastructure;
- geocoding stations during route requests;
- city-centre or arbitrary same-city coordinates;
- paid geocoding providers;
- configurable vehicle profiles; and
- unrelated scaffold endpoints outside the assignment contract.
