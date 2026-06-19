# FuelSpotter assignment backlog

## Fixed assignment contract

- Request: `POST /api/v1/routes/fuel-plan/` with only `start` and `finish`.
- Both locations must resolve inside the USA.
- Vehicle range is fixed at 500 miles and fuel economy at 10 MPG.
- Response: route GeoJSON, selected fuel stops, and total fuel cost.
- Runtime external-call budget: two geocoding calls plus one routing call. Station matching and optimization use local data only.
- Source data: `fuel-prices-for-be-assessment.csv`.

## P0 - API foundation

- [x] Ensure static URL paths are not shadowed by dynamic station IDs.
- [x] Protect scaffolded operational endpoints from anonymous/non-staff access.
- [x] Load secrets, hosts, CORS, and external-service configuration from environment variables.
- [x] Document the real CSV headers and duplicate behavior.
- [x] Make `start` and `finish` the complete fuel-plan request contract.
- [x] Fix vehicle assumptions at 500 miles and 10 MPG server-side.
- [x] Define the response contract: route geometry, fuel stops, and total fuel cost.
- [x] Replace the empty test module with a layered test package.

## P1 - Local station dataset

- [x] Add `rack_id` to `FuelStation` (the coordinate index already existed).
- [x] Add a management command to import the supplied CSV.
- [x] Canonicalize repeated OPIS IDs using stable location/Rack validation, the longest normalized name, and median price rounded half-up to cents.
- [x] Import 8,151 source rows into 6,738 canonical stations and report 1,413 collapsed rows.
- [x] Return paginated public station data with validated state/price filters and no internal job metadata.
- [x] Add resumable US station geocoding batches through the staff API and `run_geocoding_worker`; route matching excludes unresolved stations and never geocodes during requests.

## P2 - One route request

- [ ] Geocode start and finish with two bounded, cached requests and reject non-US results.
- [ ] Fetch one OSRM route with GeoJSON geometry, distance, and duration.
- [ ] Prefilter local stations with a route bounding box, then calculate distance to route segments.
- [ ] Determine station progress along the route without additional routing calls.

## P3 - Fuel optimization

- [ ] Assume the vehicle begins with a full 50-gallon tank; report this assumption in the response/documentation.
- [ ] Ensure no leg between the start, selected stops, and destination exceeds 500 miles.
- [ ] Prefer cheaper reachable downstream stations and avoid unnecessary stops.
- [ ] Calculate purchases and costs with `Decimal`; return total fuel purchased and total en-route fuel cost.
- [ ] Cover short trips, multiple stops, cheaper downstream fuel, unreachable gaps, and duplicate-price data.

## P4 - Delivery

- [ ] Wire the service into `/api/v1/routes/fuel-plan/` with stable `400`, `404`, `422`, and `502` errors.
- [ ] Document setup, data import, endpoint usage, assumptions, and external-service choice.
- [ ] Verify tests, migration drift, Django deployment checks, query count, and representative response time.
