from celery import shared_task

from api.models import GeocodeJob
from api.services.geocoding_service import GeocodingService


@shared_task
def import_fuel_prices_task(file_path):
    """
    Async task to import fuel prices from CSV file.

    TODO: Implement CSV parsing and database import logic.
    """
    pass


@shared_task
def geocode_stations_task(job_id):
    """Optional Celery adapter; the management-command worker is the default."""
    if not GeocodeJob.objects.filter(pk=job_id).exists():
        return None
    return GeocodingService.process_job(job_id, worker_id=f"celery-{job_id}")
