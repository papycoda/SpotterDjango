"""
Service for spatial filtering of fuel stations along routes.

Provides bounding box prefiltering and precise distance calculations
using Shapely geometric operations to identify stations near or on routes.
"""

import math
from collections import namedtuple
from decimal import Decimal
from typing import List, Optional

from django.db.models import QuerySet
from shapely.geometry import LineString, Point

from api.models import FuelStation


# Station data with route distance information
NearbyStation = namedtuple(
    "NearbyStation",
    ["station", "route_distance_m", "route_progress_m", "is_on_route"],
)


class StationFilteringService:
    """Service for finding fuel stations along routes."""

    # Default threshold for considering a station "on route" (in meters)
    DEFAULT_ON_ROUTE_THRESHOLD = 200

    def __init__(self, *, on_route_threshold: int = DEFAULT_ON_ROUTE_THRESHOLD):
        """
        Initialize the filtering service.

        Args:
            on_route_threshold: Distance in meters for considering a station on route
        """
        self.on_route_threshold = on_route_threshold

    def get_stations_in_bounds(
        self, route_geometry: List[tuple[Decimal, Decimal]]
    ) -> QuerySet[FuelStation]:
        """
        Quick prefilter to get stations within the route's bounding box.

        This is a fast database query that eliminates most stations before
        expensive geometric distance calculations.

        Args:
            route_geometry: List of (lat, lon) tuples representing the route

        Returns:
            QuerySet of FuelStation objects within the bounding box

        Raises:
            ValueError: If route_geometry is empty
        """
        if not route_geometry:
            raise ValueError("Route geometry cannot be empty")

        # Extract latitudes and longitudes
        lats = [coord[0] for coord in route_geometry]
        lons = [coord[1] for coord in route_geometry]

        # Calculate bounding box with small buffer (0.01 degrees ≈ 1km)
        # Buffer ensures we don't miss stations near the edge
        buffer = Decimal("0.01")
        min_lat = min(lats) - buffer
        max_lat = max(lats) + buffer
        min_lon = min(lons) - buffer
        max_lon = max(lons) + buffer

        # Filter stations within bounding box that are geocoded
        return FuelStation.objects.filter(
            latitude__gte=min_lat,
            latitude__lte=max_lat,
            longitude__gte=min_lon,
            longitude__lte=max_lon,
            geocoding_status="success",
        )

    def calculate_station_route_distance(
        self,
        station_lat: Decimal,
        station_lon: Decimal,
        route_geometry: List[tuple[Decimal, Decimal]],
    ) -> tuple[float, float]:
        """
        Calculate the shortest distance from a station to the route.

        Uses Shapely to compute the perpendicular distance from a point to a line,
        which is the accurate representation of how far the station is from the route.

        Also calculates the station's position along the route (progress from start).

        Args:
            station_lat: Station latitude
            station_lon: Station longitude
            route_geometry: List of (lat, lon) tuples representing the route

        Returns:
            Tuple of (distance_to_route_m, progress_along_route_m)
            - distance_to_route_m: Perpendicular distance in meters
            - progress_along_route_m: Distance from route start to projection point

        Raises:
            ValueError: If route_geometry is empty or has only one point
        """
        if len(route_geometry) < 2:
            raise ValueError("Route geometry must have at least 2 points")

        # Convert Decimals to floats for Shapely
        station_point = Point(float(station_lon), float(station_lat))

        # Convert route coordinates to (lon, lat) for Shapely (x, y order)
        route_coords = [(float(coord[1]), float(coord[0])) for coord in route_geometry]
        route_line = LineString(route_coords)

        # Calculate shortest distance from station to route (in degrees)
        # Shapely returns distance in the same units as coordinates (degrees)
        distance_degrees = station_point.distance(route_line)

        # Convert degrees to meters (approximate, varies by latitude)
        # 1 degree ≈ 111,000 meters at equator
        # Use latitude for better accuracy: 1 degree lat ≈ 111km * cos(lat)
        avg_lat = sum(float(coord[0]) for coord in route_geometry) / len(route_geometry)
        meters_per_degree = 111000 * math.cos(math.radians(avg_lat)) if avg_lat != 0 else 111000

        distance_m = distance_degrees * meters_per_degree

        # Calculate progress along route
        # Project station point onto route line to find closest point
        # and calculate distance from start to that point
        progress_m = self._calculate_route_progress(
            station_point, route_line, meters_per_degree
        )

        return distance_m, progress_m

    def _calculate_route_progress(
        self, station_point: Point, route_line: LineString, meters_per_degree: float
    ) -> float:
        """
        Calculate the station's progress along the route.

        This finds where on the route the station is closest to (projection),
        then calculates the distance from the route start to that point.

        Args:
            station_point: Shapely Point representing the station
            route_line: Shapely LineString representing the route
            meters_per_degree: Conversion factor for distance calculations

        Returns:
            Distance in meters from route start to projected point
        """
        # Get the projected point on the route
        projected_point = route_line.interpolate(route_line.project(station_point))

        # Calculate cumulative distance along route segments to projected point
        coords = list(route_line.coords)
        total_progress = 0.0
        projected_reached = False

        for i in range(len(coords) - 1):
            if projected_reached:
                break

            segment_start = Point(coords[i])
            segment_end = Point(coords[i + 1])

            # Check if projected point is on this segment
            segment = LineString([segment_start, segment_end])

            if segment.distance(projected_point) < 0.0001:  # Tolerance for floating point
                # Calculate distance from segment start to projected point
                total_progress += segment_start.distance(projected_point) * meters_per_degree
                projected_reached = True
            else:
                # Add full segment length
                total_progress += segment.length * meters_per_degree

        return total_progress

    def find_nearby_stations(
        self,
        route_geometry: List[tuple[Decimal, Decimal]],
        max_distance_m: float = 1000,
    ) -> List[NearbyStation]:
        """
        Find all stations near a route with full distance calculations.

        This combines the bounding box prefilter with precise Shapely distance
        calculations to return stations that are actually close to the route.

        Args:
            route_geometry: List of (lat, lon) tuples representing the route
            max_distance_m: Maximum distance from route in meters

        Returns:
            List of NearbyStation namedtuples with distance and progress info

        Raises:
            ValueError: If route_geometry is empty
        """
        # Step 1: Quick bounding box prefilter
        candidates = self.get_stations_in_bounds(route_geometry)

        # Step 2: Calculate precise distances for each candidate
        nearby_stations = []

        for station in candidates:
            if station.latitude is None or station.longitude is None:
                continue

            distance_m, progress_m = self.calculate_station_route_distance(
                station.latitude, station.longitude, route_geometry
            )

            # Filter by max distance
            if distance_m <= max_distance_m:
                is_on_route = distance_m <= self.on_route_threshold
                nearby_stations.append(
                    NearbyStation(station, distance_m, progress_m, is_on_route)
                )

        # Sort by route progress for fuel optimization planning
        nearby_stations.sort(key=lambda s: s.route_progress_m)

        return nearby_stations

    def get_route_total_distance(
        self, route_geometry: List[tuple[Decimal, Decimal]]
    ) -> float:
        """
        Calculate the total distance of the route in meters.

        Args:
            route_geometry: List of (lat, lon) tuples representing the route

        Returns:
            Total route distance in meters

        Raises:
            ValueError: If route_geometry has fewer than 2 points
        """
        if len(route_geometry) < 2:
            raise ValueError("Route geometry must have at least 2 points")

        # Convert to Shapely LineString
        route_coords = [(float(coord[1]), float(coord[0])) for coord in route_geometry]
        route_line = LineString(route_coords)

        # Calculate length in degrees
        length_degrees = route_line.length

        # Convert to meters (using average latitude)
        avg_lat = sum(float(coord[0]) for coord in route_geometry) / len(route_geometry)
        meters_per_degree = 111000  # Approximate, can be refined

        return length_degrees * meters_per_degree


class StationFilteringError(Exception):
    """Base error for station filtering operations."""


class InvalidRouteGeometryError(StationFilteringError):
    """Raised when route geometry is invalid."""
