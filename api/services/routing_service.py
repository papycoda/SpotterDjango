"""
Service for route planning and geometry using OSRM.

Provides route calculations between coordinates for fuel trip planning.
"""

from dataclasses import dataclass
from decimal import Decimal

from api.clients.osrm_client import OSRMClient, OSRMError, OSRMTransientError, OSRMPermanentError


@dataclass(frozen=True)
class RouteGeometry:
    """Structured route data for fuel trip planning."""
    distance_miles: Decimal
    duration_minutes: Decimal
    coordinates: list[tuple[Decimal, Decimal]]  # List of (lat, lon) points


class RoutingService:
    """Handles route planning and geometry calculations."""

    def __init__(self, *, osrm_client=None):
        self.osrm_client = osrm_client or OSRMClient()

    def get_route_geometry(self, start, finish):
        """
        Get route geometry and distance between two points.

        Args:
            start: tuple (lat, lon) - starting coordinate
            finish: tuple (lat, lon) - ending coordinate

        Returns:
            RouteGeometry with distance in miles, duration in minutes,
            and list of (lat, lon) coordinate tuples

        Raises:
            RoutingTransientError: Network or server error that can be retried
            RoutingPermanentError: Invalid coordinates or no route found
        """
        # OSRM expects (lon, lat) order, so we convert
        start_osrm = (start[1], start[0])  # (lon, lat)
        finish_osrm = (finish[1], finish[0])  # (lon, lat)

        try:
            result = self.osrm_client.get_route(start_osrm, finish_osrm)
        except OSRMTransientError as exc:
            raise RoutingTransientError(str(exc)) from exc
        except OSRMPermanentError as exc:
            raise RoutingPermanentError(str(exc)) from exc
        except OSRMError as exc:
            # Any other OSRM error is treated as permanent
            raise RoutingPermanentError(str(exc)) from exc

        # Convert meters to miles (1 meter = 0.000621371 miles)
        distance_miles = (result.distance_meters * Decimal("0.000621371")).quantize(
            Decimal("0.01")
        )

        # Convert seconds to minutes
        duration_minutes = (result.duration_seconds / Decimal("60")).quantize(
            Decimal("0.1")
        )

        # Return coordinates in (lat, lon) order for consistency
        return RouteGeometry(
            distance_miles=distance_miles,
            duration_minutes=duration_minutes,
            coordinates=result.geometry,  # Already (lat, lon) from decoder
        )

    def calculate_distance(self, start, finish):
        """
        Calculate distance between two points along the route.

        Args:
            start: tuple (lat, lon) - starting coordinate
            finish: tuple (lat, lon) - ending coordinate

        Returns:
            Distance in miles as Decimal

        Raises:
            RoutingTransientError: Network or server error that can be retried
            RoutingPermanentError: Invalid coordinates or no route found
        """
        geometry = self.get_route_geometry(start, finish)
        return geometry.distance_miles


class RoutingError(Exception):
    """Base error for routing operations."""


class RoutingTransientError(RoutingError):
    """A network or server error that can be retried."""


class RoutingPermanentError(RoutingError):
    """A request or response that should not be retried."""
