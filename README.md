# FuelSpotter API

FuelSpotter is a Django REST API that plans USA routes against a local fuel-price dataset. Swagger is available at `/api/v1/swagger/` after startup.

## Fuel-plan API

Send only the start and finish locations. Both must resolve within the USA:

```http
POST /api/v1/routes/fuel-plan/
Content-Type: application/json

{
  "start": "Dallas, TX",
  "finish": "Denver, CO"
}
```

The response contains GeoJSON route geometry for map rendering, cost-effective fuel stops along the route, the gallons and cost at each stop, total en-route fuel cost, and the fixed vehicle assumptions:

```json
{
  "start": "Dallas, TX, USA",
  "finish": "Denver, CO, USA",
  "distance_miles": 700.125,
  "duration_minutes": 650,
  "route_geometry": {
    "type": "LineString",
    "coordinates": [[-96.797, 32.7767], [-104.9903, 39.7392]]
  },
  "fuel_stops": [
    {
      "station_id": "station-1",
      "name": "Example Fuel",
      "address": "100 Main St",
      "city": "Amarillo",
      "state": "TX",
      "price_per_gallon": "3.49",
      "route_progress_miles": "400.000",
      "gallons_purchased": "20.125",
      "cost_usd": "70.24"
    }
  ],
  "total_fuel_purchased": "20.125",
  "total_fuel_cost": "70.24",
  "vehicle_assumptions": {
    "range_miles": 500,
    "mpg": 10,
    "tank_gallons": 50
  }
}
```

The vehicle starts with a full 50-gallon tank. At 10 MPG this gives a maximum range of 500 miles. For longer routes, the optimizer selects as many stops as needed so that no driving leg exceeds 500 miles, preferring cheaper reachable downstream stations. Monetary and fuel calculations use `Decimal`; JSON decimal values are returned as fixed-precision strings.

### Request speed and external-call budget

Each fuel-plan request has a fixed external-call budget:

- two Nominatim calls, one for the start and one for the finish (cached after first request);
- one OSRM call that returns the complete route, distance, duration, and geometry (cached after first request);
- zero external calls for station lookup or fuel optimization.

Therefore a first request makes exactly three external HTTP calls. Repeated requests for the same locations use DB-backed caches and make zero external calls. Fuel stations are matched and optimized from the local database. Station geocoding is an offline worker workflow and never runs during an API request.

Response time is normally dominated by the two geocoding calls and the single routing call on first request. All external calls have bounded timeouts; local station filtering first applies a database bounding box and then evaluates only corridor candidates within 5 miles of the route.

Run the network-free performance and contract checks with:

```powershell
python manage.py test api.tests.test_station_filtering.StationFilteringPerformanceTests
python manage.py test api.tests.test_route_fuel_plan
```

## Local setup

```powershell
python -m pip install -r requirements.txt
python manage.py migrate
python manage.py import_fuel_prices fuel-prices-for-be-assessment.csv
python manage.py run_app
```

`run_app` starts the Django development server and the database-backed geocoding worker as separate child processes. The worker automatically creates bounded jobs for pending stations and continues processing them in the background. Press `Ctrl+C` once to stop both processes.

Useful options:

```powershell
python manage.py run_app --addrport 127.0.0.1:8080 --auto-queue 250
```

The equivalent manual processes are:

```powershell
python manage.py runserver
python manage.py run_geocoding_worker --watch --auto-queue 500
```

## Geocoding behavior

Station geocoding never runs inside an HTTP request or route-planning operation. The worker uses persisted jobs, station claims, heartbeats, stale-work recovery, and a shared database rate limiter. It processes calls sequentially to respect Nominatim limits.

Geocoding results are cached in the database. Once a location or station has been successfully geocoded, the app reuses the cached coordinates and does not call Nominatim again for the same normalized input.

The complete dataset can take roughly two hours at one request per second. Configure the identifying user agent in `.env`:

```dotenv
NOMINATIM_USER_AGENT=FuelSpotter/1.0 (https://github.com/papycoda/SpotterDjango)
```

Failed rows are not retried automatically because repeated requests for an unresolvable highway-style address waste the public service quota. Retry them explicitly through the staff geocoding endpoint after improving their source data.

Latitude and longitude remain null while a station is pending and when no reliable station match exists. Route matching uses only `success` stations with both coordinates populated.

## Docker deployment

Set at least `SECRET_KEY` and an identifying `NOMINATIM_USER_AGENT` in `.env`, then run:

```powershell
docker-compose up --build
```

Compose starts one Gunicorn web service and one automatic worker service. Both mount the same persistent SQLite volume. The worker waits for web migrations and health checks before processing jobs.

### Important: SQLite limitations

This deployment uses SQLite and is suitable for:
- single-host demo/assessment environments
- development and testing
- learning and exploration

SQLite is NOT suitable for production use because:
- it does not support concurrent writes from multiple hosts
- it has limited write concurrency compared to PostgreSQL
- it lacks many production database features

For production deployment, you must:
1. Migrate to PostgreSQL
2. Configure proper secrets management
3. Enable TLS/HTTPS
4. Use managed services for Nominatim and OSRM (or self-host with proper scaling)
5. Implement proper monitoring, logging, and backup strategies

For process-based hosting, `Procfile` exposes `release`, `web`, and `worker` process types. Provision exactly one worker and ensure web and worker use the same durable database. Separate hosts cannot use independent SQLite files; use a shared database or the supplied single-host Compose deployment.

## Configuration

Copy `.env.example` to `.env` and adjust it. Relevant worker settings include:

- `GEOCODING_AUTO_QUEUE_BATCH_SIZE` — stations claimed per automatic job, default `500`
- `GEOCODING_POLL_SECONDS` — idle queue polling interval
- `NOMINATIM_MIN_INTERVAL_SECONDS` — shared minimum delay between requests
- `SQLITE_PATH` — durable SQLite database location
- `RUN_APP_ADDRPORT` — local development bind address

## Verification

```powershell
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py test
```

## Management commands

### `import_fuel_prices`

Import fuel price data from CSV:

```powershell
python manage.py import_fuel_prices fuel-prices-for-be-assessment.csv
```

### `run_geocoding_worker`

Process station geocoding jobs:

```powershell
python manage.py run_geocoding_worker --watch --auto-queue 500
```

Options:
- `--watch`: Keep worker running and polling for new jobs
- `--auto-queue N`: Automatically create jobs for N pending stations

### `run_app`

Convenience wrapper to start both web server and geocoding worker:

```powershell
python manage.py run_app
```

## API endpoints

### Public endpoints

- `GET /api/v1/health/` — Health check
- `GET /api/v1/fuel-stations/` — List stations with state/price filters and pagination
- `GET /api/v1/fuel-stations/{station_id}/` — Get station details
- `POST /api/v1/routes/fuel-plan/` — Plan route with fuel optimization
- `POST /api/v1/routes/preview/` — Preview route without fuel optimization

### Staff endpoints

- `GET /api/v1/admin/fuel-stations/geocode/status/` — View geocoding progress
- `POST /api/v1/admin/fuel-stations/geocode/` — Queue stations for geocoding
- `GET /api/v1/admin/fuel-stations/geocoding/report/` — Generate geocoding report

### Documentation

- `/api/v1/swagger/` — Interactive Swagger UI
- `/api/v1/redoc/` — ReDoc documentation
