"""Geodesic filtering of locally geocoded fuel stations along routes."""

import math
from collections import namedtuple
from decimal import Decimal
from typing import List

from django.db.models import QuerySet

from api.models import FuelStation


NearbyStation = namedtuple(
    "NearbyStation",
    ["station", "route_distance_m", "route_progress_m", "is_on_route"],
)


class StationFilteringService:
    """Find persisted fuel-station coordinates within a route corridor."""

    EARTH_RADIUS_M = 6_371_008.8
    DEFAULT_ON_ROUTE_THRESHOLD = 200

    def __init__(self, *, on_route_threshold: int = DEFAULT_ON_ROUTE_THRESHOLD):
        self.on_route_threshold = on_route_threshold

    @staticmethod
    def _validate_route(route_geometry):
        if not route_geometry:
            raise ValueError("Route geometry cannot be empty")
        if len(route_geometry) < 2:
            raise ValueError("Route geometry must have at least 2 points")

    @classmethod
    def _haversine_m(cls, start, end):
        lat1, lon1 = map(math.radians, map(float, start))
        lat2, lon2 = map(math.radians, map(float, end))
        delta_lat = lat2 - lat1
        delta_lon = (lon2 - lon1 + math.pi) % (2 * math.pi) - math.pi
        value = (
            math.sin(delta_lat / 2) ** 2
            + math.cos(lat1) * math.cos(lat2) * math.sin(delta_lon / 2) ** 2
        )
        return cls.EARTH_RADIUS_M * 2 * math.atan2(
            math.sqrt(value), math.sqrt(max(0.0, 1 - value))
        )

    @classmethod
    def _point_to_segment(cls, point, start, end):
        """Return local-tangent distance and clamped fraction along a segment."""
        start_lat, start_lon = map(math.radians, map(float, start))
        end_lat, end_lon = map(math.radians, map(float, end))
        point_lat, point_lon = map(math.radians, map(float, point))
        reference_lat = (start_lat + end_lat) / 2

        def local_xy(latitude, longitude):
            delta_lon = (longitude - start_lon + math.pi) % (2 * math.pi) - math.pi
            return (
                cls.EARTH_RADIUS_M * delta_lon * math.cos(reference_lat),
                cls.EARTH_RADIUS_M * (latitude - start_lat),
            )

        end_x, end_y = local_xy(end_lat, end_lon)
        point_x, point_y = local_xy(point_lat, point_lon)
        squared_length = end_x * end_x + end_y * end_y
        if squared_length == 0:
            return math.hypot(point_x, point_y), 0.0
        fraction = max(
            0.0,
            min(1.0, (point_x * end_x + point_y * end_y) / squared_length),
        )
        nearest_x, nearest_y = fraction * end_x, fraction * end_y
        return math.hypot(point_x - nearest_x, point_y - nearest_y), fraction

    def get_stations_in_bounds(
        self,
        route_geometry: List[tuple[Decimal, Decimal]],
        max_distance_m: float = 1000,
    ) -> QuerySet[FuelStation]:
        self._validate_route(route_geometry)
        if max_distance_m < 0:
            raise ValueError("Maximum distance cannot be negative")

        lats = [coord[0] for coord in route_geometry]
        lons = [coord[1] for coord in route_geometry]
        latitude_padding = Decimal(
            str(math.degrees(max_distance_m / self.EARTH_RADIUS_M))
        )
        worst_latitude = max(abs(float(latitude)) for latitude in lats)
        longitude_padding = Decimal(
            str(
                math.degrees(max_distance_m / self.EARTH_RADIUS_M)
                / max(abs(math.cos(math.radians(worst_latitude))), 0.01)
            )
        )

        return FuelStation.objects.filter(
            latitude__gte=min(lats) - latitude_padding,
            latitude__lte=max(lats) + latitude_padding,
            longitude__gte=min(lons) - longitude_padding,
            longitude__lte=max(lons) + longitude_padding,
            geocoding_status="success",
            latitude__isnull=False,
            longitude__isnull=False,
        )

    def calculate_station_route_distance(
        self,
        station_lat: Decimal,
        station_lon: Decimal,
        route_geometry: List[tuple[Decimal, Decimal]],
    ) -> tuple[float, float]:
        self._validate_route(route_geometry)
        station = (station_lat, station_lon)
        closest_distance = math.inf
        closest_progress = 0.0
        cumulative_distance = 0.0

        for start, end in zip(route_geometry, route_geometry[1:]):
            segment_distance = self._haversine_m(start, end)
            distance, fraction = self._point_to_segment(station, start, end)
            if distance < closest_distance:
                closest_distance = distance
                closest_progress = cumulative_distance + fraction * segment_distance
            cumulative_distance += segment_distance

        return float(closest_distance), float(closest_progress)

    def find_nearby_stations(
        self,
        route_geometry: List[tuple[Decimal, Decimal]],
        max_distance_m: float = 1000,
    ) -> List[NearbyStation]:
        self._validate_route(route_geometry)
        candidates = self.get_stations_in_bounds(route_geometry, max_distance_m)
        nearby_stations = []
        for station in candidates:
            distance_m, progress_m = self.calculate_station_route_distance(
                station.latitude, station.longitude, route_geometry
            )
            if distance_m <= max_distance_m:
                nearby_stations.append(
                    NearbyStation(
                        station,
                        distance_m,
                        progress_m,
                        distance_m <= self.on_route_threshold,
                    )
                )
        nearby_stations.sort(key=lambda item: item.route_progress_m)
        return nearby_stations

    def get_route_total_distance(
        self, route_geometry: List[tuple[Decimal, Decimal]]
    ) -> float:
        self._validate_route(route_geometry)
        return sum(
            self._haversine_m(start, end)
            for start, end in zip(route_geometry, route_geometry[1:])
        )


class StationFilteringError(Exception):
    """Base error for station filtering operations."""


class InvalidRouteGeometryError(StationFilteringError):
    """Raised when route geometry is invalid."""
