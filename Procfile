release: python manage.py migrate --noinput
web: gunicorn fuelSpotter.wsgi:application --bind 0.0.0.0:$PORT
worker: python manage.py run_geocoding_worker --watch --auto-queue ${GEOCODING_AUTO_QUEUE_BATCH_SIZE:-500}
