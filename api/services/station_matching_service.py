"""
Service for matching/filtering fuel stations along routes.

TODO: Implement station filtering and corridor matching logic.
"""

from api.models import FuelStation


class StationMatchingService:
    """Handles fuel station search and filtering."""

    @staticmethod
    def eligible_stations():
        """Return only locally persisted coordinates suitable for route matching."""
        return FuelStation.objects.filter(
            geocoding_status="success",
            latitude__isnull=False,
            longitude__isnull=False,
        )

    @staticmethod
    def find_stations_near_route(start, finish, corridor_miles):
        """Find fuel stations within specified corridor of route."""
        pass

    @staticmethod
    def filter_stations(state=None, min_price=None, max_price=None):
        """Filter fuel stations by criteria."""
        pass
