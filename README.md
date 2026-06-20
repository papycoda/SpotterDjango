# FuelSpotter API

FuelSpotter is a Django REST API that plans USA routes against a local fuel-price dataset. Swagger is available at `/api/v1/swagger/` after startup.

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

The complete dataset can take roughly two hours at one request per second. Configure the identifying user agent in `.env`:

```dotenv
NOMINATIM_USER_AGENT=FuelSpotter/1.0 (contact: opeyemi655@gmail.com)
```

Failed rows are not retried automatically because repeated requests for an unresolvable highway-style address waste the public service quota. Retry them explicitly through the staff geocoding endpoint after improving their source data.

Latitude and longitude remain null while a station is pending and when no reliable station match exists. Route matching uses only `success` stations with both coordinates populated.

## Docker deployment

Set at least `SECRET_KEY` and an identifying `NOMINATIM_USER_AGENT` in `.env`, then run:

```powershell
docker-compose up --build
```

Compose starts one Gunicorn web service and one automatic worker service. Both mount the same persistent SQLite volume. The worker waits for web migrations and health checks before processing jobs.

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
