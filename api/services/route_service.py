"""
Service for complete route planning with geocoding and OSRM routing.

Orchestrates the full flow from location strings to route geometry,
including USA validation, OSRM route fetching, and bounding box calculation.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import List, Tuple, Optional

from api.services.route_geocoding_service import (
    RouteGeocodingService,
    GeocodedLocation,
    LocationNotInUSAError,
    GeocodingTransientError,
)
from api.clients.osrm_client import (
    OSRMClient,
    OSRMTransientError,
    OSRMPermanentError,
    RouteResult,
)


class RouteNotFoundError(Exception):
    """Raised when OSRM cannot find a route between two points."""
    pass


class RoutingTransientError(Exception):
    """Raised when routing fails due to network/upstream error (retryable)."""
    pass


@dataclass(frozen=True)
class RoutePlan:
    """Complete route plan data for fuel optimization."""
    start_geocoded: GeocodedLocation
    end_geocoded: GeocodedLocation
    route_geometry: List[Tuple[Decimal, Decimal]]  # GeoJSON LineString coords
    total_distance_m: Decimal  # meters
    total_duration_s: Decimal  # seconds
    bounding_box: Tuple[Decimal, Decimal, Decimal, Decimal]  # (min_lat, min_lon, max_lat, max_lon)

    @property
    def total_distance_miles(self) -> Decimal:
        """Total route distance in miles."""
        return (self.total_distance_m * Decimal("0.000621371")).quantize(Decimal("0.01"))

    @property
    def total_duration_minutes(self) -> Decimal:
        """Total route duration in minutes."""
        return (self.total_duration_s / Decimal("60")).quantize(Decimal("0.1"))

    @property
    def start_coords(self) -> Tuple[Decimal, Decimal]:
        """Start coordinates as (lat, lon)."""
        return self.start_geocoded.coords

    @property
    def end_coords(self) -> Tuple[Decimal, Decimal]:
        """End coordinates as (lat, lon)."""
        return self.end_geocoded.coords

    @property
    def start_osrm_coords(self) -> Tuple[Decimal, Decimal]:
        """Start coordinates in OSRM format (lon, lat)."""
        return self.start_geocoded.osrm_coords

    @property
    def end_osrm_coords(self) -> Tuple[Decimal, Decimal]:
        """End coordinates in OSRM format (lon, lat)."""
        return self.end_geocoded.osrm_coords


class RouteService:
    """
    Service for complete route planning with geocoding and OSRM routing.

    This service orchestrates:
    1. Geocoding start and finish locations
    2. Validating locations are within the USA
    3. Fetching OSRM route between coordinates
    4. Calculating bounding box for station filtering
    """

    # External service timeout budget: 2 geocoding calls + 1 routing call
    # Each uses the configured timeout independently

    def __init__(
        self,
        *,
        geocoding_service: Optional[RouteGeocodingService] = None,
        osrm_client: Optional[OSRMClient] = None,
    ):
        """
        Initialize the route service.

        Args:
            geocoding_service: Optional geocoding service for testing
            osrm_client: Optional OSRM client for testing
        """
        self.geocoding_service = geocoding_service or RouteGeocodingService()
        self.osrm_client = osrm_client or OSRMClient()

    def plan_route(self, start_location: str, end_location: str) -> RoutePlan:
        """
        Plan a complete route from start to finish locations.

        This is the primary entry point for route planning. It:
        1. Geocodes both locations
        2. Validates they're within the USA
        3. Fetches the OSRM route
        4. Returns a complete RoutePlan

        Args:
            start_location: Free-form start location string
            end_location: Free-form end location string

        Returns:
            RoutePlan with all data needed for fuel optimization

        Raises:
            LocationNotInUSAError: Either location is outside USA
            RouteNotFoundError: OSRM cannot find a route
            RoutingTransientError: Network/upstream error (retryable)
        """
        # Step 1: Geocode both locations
        start_geocoded = self._geocode_location(start_location)
        end_geocoded = self._geocode_location(end_location)

        # Step 2: Fetch OSRM route
        route_result = self._fetch_osrm_route(
            start_geocoded.osrm_coords,
            end_geocoded.osrm_coords,
        )

        # Step 3: Calculate bounding box
        bounding_box = self._calculate_bounding_box(route_result.geometry)

        # Step 4: Return complete route plan
        return RoutePlan(
            start_geocoded=start_geocoded,
            end_geocoded=end_geocoded,
            route_geometry=route_result.geometry,
            total_distance_m=route_result.distance_meters,
            total_duration_s=route_result.duration_seconds,
            bounding_box=bounding_box,
        )

    def _geocode_location(self, location_string: str) -> GeocodedLocation:
        """
        Geocode a single location with error mapping.

        Args:
            location_string: Free-form location string

        Returns:
            GeocodedLocation with coordinates and display name

        Raises:
            LocationNotInUSAError: Location is outside USA
            RoutingTransientError: Network/upstream error
        """
        try:
            return self.geocoding_service.geocode_location(location_string)
        except GeocodingTransientError as exc:
            raise RoutingTransientError(
                f"Failed to geocode '{location_string}': {exc}"
            ) from exc

    def _fetch_osrm_route(self, start_coords, end_coords) -> RouteResult:
        """
        Fetch OSRM route between coordinates.

        Args:
            start_coords: (lon, lat) tuple
            end_coords: (lon, lat) tuple

        Returns:
            RouteResult with geometry, distance, and duration

        Raises:
            RouteNotFoundError: No valid route exists
            RoutingTransientError: Network/upstream error
        """
        try:
            return self.osrm_client.get_route(start_coords, end_coords)
        except OSRMTransientError as exc:
            raise RoutingTransientError(f"Routing service temporarily unavailable: {exc}") from exc
        except OSRMPermanentError as exc:
            # Map permanent errors to route not found or generic routing error
            error_msg = str(exc).lower()
            if "no route" in error_msg or "not found" in error_msg:
                raise RouteNotFoundError(f"No route found between locations: {exc}") from exc
            raise RouteNotFoundError(f"Routing failed: {exc}") from exc

    @staticmethod
    def _calculate_bounding_box(geometry: List[Tuple[Decimal, Decimal]]) -> Tuple[Decimal, Decimal, Decimal, Decimal]:
        """
        Calculate bounding box from route geometry.

        Args:
            geometry: List of (lat, lon) coordinate tuples

        Returns:
            Tuple of (min_lat, min_lon, max_lat, max_lon)
        """
        if not geometry:
            raise ValueError("Cannot calculate bounding box from empty geometry")

        lats = [coord[0] for coord in geometry]
        lons = [coord[1] for coord in geometry]

        return (
            min(lats).quantize(Decimal("0.0000001")),
            min(lons).quantize(Decimal("0.0000001")),
            max(lats).quantize(Decimal("0.0000001")),
            max(lons).quantize(Decimal("0.0000001")),
        )


def plan_route(start: str, finish: str) -> RoutePlan:
    """
    Convenience function for route planning.

    This is the main entry point used by views and other services.
    It creates a RouteService instance and calls plan_route.

    Args:
        start: Start location string
        finish: Finish location string

    Returns:
        RoutePlan with complete route data

    Raises:
        LocationNotInUSAError: Either location is outside USA
        RouteNotFoundError: No valid route exists
        RoutingTransientError: Network/upstream error (retryable)
    """
    service = RouteService()
    return service.plan_route(start, finish)
