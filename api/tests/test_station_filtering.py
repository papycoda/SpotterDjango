"""
Tests for station filtering service.

Tests spatial filtering of fuel stations along routes including
bounding box prefiltering and Shapely geometric operations.
"""

from decimal import Decimal
import math
from unittest.mock import Mock, patch

from django.test import TestCase

from api.models import FuelStation
from api.services.station_filtering_service import (
    StationFilteringService,
    StationFilteringError,
    InvalidRouteGeometryError,
    NearbyStation,
)


class StationFilteringServiceTests(TestCase):
    """Tests for station filtering service."""

    def setUp(self):
        """Set up test data."""
        self.service = StationFilteringService()

        # Create test stations along a route
        # Route: Dallas (32.7767, -96.7970) to Houston (29.7604, -95.3698)
        self.stations = []

        # Station on route (near Dallas)
        self.stations.append(
            FuelStation.objects.create(
                id="station_1",
                name="On Route Dallas",
                address="123 Main St",
                city="Dallas",
                state="TX",
                price_per_gallon=Decimal("3.50"),
                latitude=Decimal("32.78"),
                longitude=Decimal("-96.80"),
                geocoding_status="success",
            )
        )

        # Station on route (near Houston)
        self.stations.append(
            FuelStation.objects.create(
                id="station_2",
                name="On Route Houston",
                address="456 Main St",
                city="Houston",
                state="TX",
                price_per_gallon=Decimal("3.45"),
                latitude=Decimal("29.76"),
                longitude=Decimal("-95.37"),
                geocoding_status="success",
            )
        )

        # Station near route but not on it (closer to route for bounding box test)
        self.stations.append(
            FuelStation.objects.create(
                id="station_3",
                name="Near Route",
                address="789 Side Rd",
                city="Columbus",
                state="TX",
                price_per_gallon=Decimal("3.55"),
                latitude=Decimal("30.00"),
                longitude=Decimal("-95.80"),
                geocoding_status="success",
            )
        )

        # Station far from route
        self.stations.append(
            FuelStation.objects.create(
                id="station_4",
                name="Far From Route",
                address="321 Far Away",
                city="Austin",
                state="TX",
                price_per_gallon=Decimal("3.60"),
                latitude=Decimal("30.27"),
                longitude=Decimal("-97.74"),
                geocoding_status="success",
            )
        )

        # Station without geocoding
        self.stations.append(
            FuelStation.objects.create(
                id="station_5",
                name="Not Geocoded",
                address="555 Unknown",
                city="Unknown",
                state="TX",
                price_per_gallon=Decimal("3.40"),
                latitude=None,
                longitude=None,
                geocoding_status="pending",
            )
        )

        # Simple route geometry for testing
        self.route_geometry = [
            (Decimal("32.7767"), Decimal("-96.7970")),  # Dallas
            (Decimal("31.50"), Decimal("-96.00")),
            (Decimal("30.00"), Decimal("-95.50")),
            (Decimal("29.7604"), Decimal("-95.3698")),  # Houston
        ]

    def test_get_stations_in_bounds_filters_by_bbox(self):
        """Test that bounding box prefilter returns stations within bounds."""
        stations = self.service.get_stations_in_bounds(self.route_geometry)

        # Should include stations on route and near route
        station_ids = {s.id for s in stations}

        self.assertIn("station_1", station_ids)  # Dallas
        self.assertIn("station_2", station_ids)  # Houston
        self.assertIn("station_3", station_ids)  # Near route

        # Should NOT include station far from route
        self.assertNotIn("station_4", station_ids)

        # Should NOT include non-geocoded station
        self.assertNotIn("station_5", station_ids)

    def test_get_stations_in_bounds_with_buffer(self):
        """Test that bounding box includes buffer zone."""
        # Small route segment
        small_route = [
            (Decimal("32.7767"), Decimal("-96.7970")),
            (Decimal("32.78"), Decimal("-96.80")),
        ]

        stations = self.service.get_stations_in_bounds(small_route)

        # Should include station within buffer
        station_ids = {s.id for s in stations}
        self.assertIn("station_1", station_ids)

    def test_get_stations_in_bounds_empty_geometry_raises_error(self):
        """Test that empty route geometry raises ValueError."""
        with self.assertRaises(ValueError) as cm:
            self.service.get_stations_in_bounds([])

        self.assertIn("empty", str(cm.exception).lower())

    def test_calculate_station_route_distance(self):
        """Test distance calculation from station to route."""
        # Station at Dallas (on route)
        distance_m, progress_m = self.service.calculate_station_route_distance(
            Decimal("32.78"), Decimal("-96.80"), self.route_geometry
        )

        # Distance should be small (on route)
        self.assertLess(distance_m, 500)  # Within 500m

        # Progress should be >= 0 (at or after start)
        self.assertGreaterEqual(progress_m, 0)

    def test_calculate_station_route_distance_progress_calculation(self):
        """Test that progress increases along the route."""
        # Point near start (Dallas)
        _, progress_start = self.service.calculate_station_route_distance(
            Decimal("32.7767"), Decimal("-96.7970"), self.route_geometry
        )

        # Point near end (Houston)
        _, progress_end = self.service.calculate_station_route_distance(
            Decimal("29.7604"), Decimal("-95.3698"), self.route_geometry
        )

        # End progress should be greater than start progress
        self.assertGreater(progress_end, progress_start)

    def test_multi_state_route_uses_geodesic_distance_and_progress(self):
        route = [
            (Decimal("32.7767"), Decimal("-96.7970")),
            (Decimal("35.4676"), Decimal("-97.5164")),
            (Decimal("39.7392"), Decimal("-104.9903")),
        ]

        def independent_haversine(start, end):
            lat1, lon1 = map(math.radians, map(float, start))
            lat2, lon2 = map(math.radians, map(float, end))
            dlat, dlon = lat2 - lat1, lon2 - lon1
            a = (
                math.sin(dlat / 2) ** 2
                + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
            )
            return 6_371_008.8 * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

        expected_first_leg = independent_haversine(route[0], route[1])
        expected_total = expected_first_leg + independent_haversine(route[1], route[2])

        total = self.service.get_route_total_distance(route)
        distance, progress = self.service.calculate_station_route_distance(
            route[1][0], route[1][1], route
        )

        self.assertAlmostEqual(total, expected_total, delta=expected_total * 0.001)
        self.assertLess(distance, 1)
        self.assertAlmostEqual(progress, expected_first_leg, delta=expected_first_leg * 0.001)

    def test_five_mile_corridor_includes_inside_and_excludes_outside(self):
        route = [
            (Decimal("32.0"), Decimal("-97.0")),
            (Decimal("34.0"), Decimal("-97.0")),
        ]
        for station_id, longitude in (("inside-five", "-96.92"), ("outside-five", "-96.90")):
            FuelStation.objects.create(
                id=station_id,
                name=station_id,
                address="1 Corridor Rd",
                city="Test",
                state="TX",
                price_per_gallon=Decimal("3.50"),
                latitude=Decimal("33.0"),
                longitude=Decimal(longitude),
                geocoding_status="success",
            )

        nearby = self.service.find_nearby_stations(route, max_distance_m=5 * 1609.344)

        station_ids = {item.station.id for item in nearby}
        self.assertIn("inside-five", station_ids)
        self.assertNotIn("outside-five", station_ids)

    def test_calculate_station_route_distance_small_route(self):
        """Test distance calculation with minimal route."""
        small_route = [
            (Decimal("32.7767"), Decimal("-96.7970")),
            (Decimal("32.78"), Decimal("-96.80")),
        ]

        distance_m, progress_m = self.service.calculate_station_route_distance(
            Decimal("32.778"), Decimal("-96.798"), small_route
        )

        # Should return valid distances
        self.assertGreaterEqual(distance_m, 0)
        self.assertGreaterEqual(progress_m, 0)

    def test_calculate_station_route_distance_invalid_geometry(self):
        """Test that single-point geometry raises ValueError."""
        with self.assertRaises(ValueError) as cm:
            self.service.calculate_station_route_distance(
                Decimal("32.78"), Decimal("-96.80"), [(Decimal("32.7767"), Decimal("-96.7970"))]
            )

        self.assertIn("at least 2 points", str(cm.exception))

    def test_find_nearby_stations_filters_by_distance(self):
        """Test find_nearby_stations returns stations within max distance."""
        nearby = self.service.find_nearby_stations(
            self.route_geometry, max_distance_m=5000
        )

        # Should find some stations
        self.assertGreater(len(nearby), 0)

        # All should be within max distance
        for station in nearby:
            self.assertLessEqual(station.route_distance_m, 5000)

    def test_find_nearby_stations_marks_on_route(self):
        """Test that close stations are marked as on route."""
        nearby = self.service.find_nearby_stations(
            self.route_geometry, max_distance_m=1000
        )

        # At least the Dallas station should be "on route"
        on_route_stations = [s for s in nearby if s.is_on_route]
        self.assertGreater(len(on_route_stations), 0)

    def test_find_nearby_stations_sorted_by_progress(self):
        """Test that results are sorted by route progress."""
        nearby = self.service.find_nearby_stations(
            self.route_geometry, max_distance_m=10000
        )

        if len(nearby) > 1:
            # Check sorted by progress
            for i in range(len(nearby) - 1):
                self.assertLessEqual(
                    nearby[i].route_progress_m, nearby[i + 1].route_progress_m
                )

    def test_find_nearby_stations_returns_namedtuple(self):
        """Test that find_nearby_stations returns NearbyStation namedtuples."""
        nearby = self.service.find_nearby_stations(
            self.route_geometry, max_distance_m=10000
        )

        for station in nearby:
            self.assertIsInstance(station, NearbyStation)
            self.assertIsInstance(station.station, FuelStation)
            self.assertIsInstance(station.route_distance_m, float)
            self.assertIsInstance(station.route_progress_m, float)
            self.assertIsInstance(station.is_on_route, bool)

    def test_find_nearby_stations_ignores_null_coordinates(self):
        """Test that stations without coordinates are skipped."""
        # Create a station with null coordinates
        FuelStation.objects.create(
            id="null_coords",
            name="Null Coordinates",
            address="999 Nowhere",
            city="Nowhere",
            state="TX",
            price_per_gallon=Decimal("3.30"),
            latitude=None,
            longitude=None,
            geocoding_status="success",
        )

        nearby = self.service.find_nearby_stations(
            self.route_geometry, max_distance_m=10000
        )

        # Should not crash and null_coords should not be in results
        station_ids = [s.station.id for s in nearby]
        self.assertNotIn("null_coords", station_ids)

    def test_get_route_total_distance(self):
        """Test route distance calculation."""
        distance = self.service.get_route_total_distance(self.route_geometry)

        # Dallas to Houston is ~240 miles (~386 km)
        # Should be in reasonable range
        self.assertGreater(distance, 200000)  # At least 200 km
        self.assertLess(distance, 500000)  # At most 500 km

    def test_get_route_total_distance_small_route(self):
        """Test distance calculation with small route."""
        small_route = [
            (Decimal("32.7767"), Decimal("-96.7970")),
            (Decimal("32.80"), Decimal("-96.80")),
        ]

        distance = self.service.get_route_total_distance(small_route)

        # Should have positive distance
        self.assertGreater(distance, 0)

    def test_get_route_total_distance_invalid_geometry(self):
        """Test that invalid geometry raises ValueError."""
        with self.assertRaises(ValueError):
            self.service.get_route_total_distance([(Decimal("32.7767"), Decimal("-96.7970"))])

    def test_custom_on_route_threshold(self):
        """Test custom on-route threshold."""
        custom_service = StationFilteringService(on_route_threshold=500)

        nearby = custom_service.find_nearby_stations(
            self.route_geometry, max_distance_m=1000
        )

        # Should use custom threshold
        on_route_stations = [s for s in nearby if s.is_on_route]
        for station in on_route_stations:
            self.assertLessEqual(station.route_distance_m, 500)


class StationFilteringPerformanceTests(TestCase):
    """Performance tests for station filtering service."""

    def setUp(self):
        """Set up performance test data."""
        self.service = StationFilteringService()

        # Create many stations across Texas
        self.create_test_stations(100)

        # Realistic route geometry (Dallas to Austin to Houston)
        self.route_geometry = [
            (Decimal("32.7767"), Decimal("-96.7970")),  # Dallas
            (Decimal("32.50"), Decimal("-96.50")),
            (Decimal("32.00"), Decimal("-96.00")),
            (Decimal("31.50"), Decimal("-95.50")),
            (Decimal("30.27"), Decimal("-97.74")),  # Austin
            (Decimal("30.00"), Decimal("-96.50")),
            (Decimal("29.80"), Decimal("-95.80")),
            (Decimal("29.7604"), Decimal("-95.3698")),  # Houston
        ]

    def create_test_stations(self, count):
        """Create test stations distributed across Texas."""
        import random

        generator = random.Random(0)

        for i in range(count):
            # Random coordinates within Texas bounds
            lat = Decimal(str(generator.uniform(25.0, 36.5))).quantize(
                Decimal("0.0000001")
            )
            lon = Decimal(str(generator.uniform(-106.0, -93.0))).quantize(
                Decimal("0.0000001")
            )

            FuelStation.objects.create(
                id=f"perf_test_{i}",
                name=f"Performance Test Station {i}",
                address=f"{i} Test St",
                city="Test City",
                state="TX",
                price_per_gallon=Decimal("3.50"),
                latitude=lat,
                longitude=lon,
                geocoding_status="success",
            )

    def test_bounding_box_filter_performance(self):
        """Test that bounding box filter is fast."""
        import time

        start = time.time()
        stations = self.service.get_stations_in_bounds(self.route_geometry)
        elapsed = time.time() - start

        # Should complete in under 100ms for 100 stations
        self.assertLess(elapsed, 0.1)

        # Should return fewer than all stations
        self.assertLess(stations.count(), 100)

    def test_full_pipeline_performance(self):
        """Test performance of full filtering pipeline."""
        import time

        start = time.time()
        nearby = self.service.find_nearby_stations(
            self.route_geometry, max_distance_m=10000
        )
        elapsed = time.time() - start

        # Should complete in reasonable time (< 1 second for 100 stations)
        self.assertLess(elapsed, 1.0)

        # Should return results
        self.assertGreater(len(nearby), 0)


class StationFilteringEdgeCaseTests(TestCase):
    """Edge case tests for station filtering service."""

    def setUp(self):
        """Set up test fixtures."""
        self.service = StationFilteringService()

    def test_empty_route_geometry(self):
        """Test handling of empty route geometry."""
        with self.assertRaises(ValueError):
            self.service.get_stations_in_bounds([])

        with self.assertRaises(ValueError):
            self.service.find_nearby_stations([])

    def test_single_point_route(self):
        """Test handling of single-point route geometry."""
        single_point = [(Decimal("32.7767"), Decimal("-96.7970"))]

        with self.assertRaises(ValueError):
            self.service.calculate_station_route_distance(
                Decimal("32.78"), Decimal("-96.80"), single_point
            )

        with self.assertRaises(ValueError):
            self.service.get_route_total_distance(single_point)

    def test_no_stations_in_bounds(self):
        """Test when no stations exist in bounding box."""
        # Route in middle of ocean
        ocean_route = [
            (Decimal("0"), Decimal("0")),
            (Decimal("1"), Decimal("1")),
        ]

        nearby = self.service.find_nearby_stations(ocean_route, max_distance_m=1000)

        # Should return empty list
        self.assertEqual(len(nearby), 0)

    def test_station_exactly_on_route(self):
        """Test station exactly on route coordinate."""
        route = [
            (Decimal("32.7767"), Decimal("-96.7970")),
            (Decimal("32.80"), Decimal("-96.80")),
        ]

        # Create station exactly at first route point
        FuelStation.objects.create(
            id="exact_on_route",
            name="Exact On Route",
            address="1 On Route",
            city="Dallas",
            state="TX",
            price_per_gallon=Decimal("3.50"),
            latitude=Decimal("32.7767"),
            longitude=Decimal("-96.7970"),
            geocoding_status="success",
        )

        nearby = self.service.find_nearby_stations(route, max_distance_m=1000)

        # Should find station with very small distance
        self.assertEqual(len(nearby), 1)
        self.assertLess(nearby[0].route_distance_m, 50)  # Within 50m
        self.assertTrue(nearby[0].is_on_route)
