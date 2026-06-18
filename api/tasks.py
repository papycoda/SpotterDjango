from celery import shared_task


@shared_task
def import_fuel_prices_task(file_path):
    """
    Async task to import fuel prices from CSV file.

    TODO: Implement CSV parsing and database import logic.
    """
    pass


@shared_task
def geocode_stations_task(limit=500, retry_failed=False):
    """
    Async task to geocode fuel stations without coordinates.

    TODO: Implement geocoding logic using GeocodingService.
    """
    pass
