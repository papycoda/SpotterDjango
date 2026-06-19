"""Tests for routing service."""

from decimal import Decimal
from unittest.mock import Mock

from django.test import SimpleTestCase

from api.clients.osrm_client import RouteResult
from api.services.routing_service import (
    RoutingService,
    RoutingTransientError,
    RoutingPermanentError,
    RouteGeometry,
)


class RoutingServiceTests(SimpleTestCase):
    """Tests for routing service."""

    def setUp(self):
        self.osrm_client = Mock()
        self.service = RoutingService(osrm_client=self.osrm_client)

    def test_get_route_geometry_converts_coordinates(self):
        """Test that coordinates are converted from (lat,lon) to OSRM's (lon,lat)."""
        # Mock OSRM result with coordinates in (lat, lon) order from decoder
        self.osrm_client.get_route.return_value = RouteResult(
            distance_meters=Decimal("5000"),
            duration_seconds=Decimal("300"),
            geometry=[(Decimal("32.7767"), Decimal("-96.7970"))],
        )

        # Service expects (lat, lon) order
        start = (Decimal("32.7767"), Decimal("-96.7970"))  # Dallas
        finish = (Decimal("29.7604"), Decimal("-95.3698"))  # Houston

        result = self.service.get_route_geometry(start, finish)

        # Verify OSRM client was called with (lon, lat) order
        call_args = self.osrm_client.get_route.call_args
        osrm_start, osrm_end = call_args[0]

        # First point: start converted to (lon, lat)
        self.assertEqual(osrm_start, (Decimal("-96.7970"), Decimal("32.7767")))

        # Second point: finish converted to (lon, lat)
        self.assertEqual(osrm_end, (Decimal("-95.3698"), Decimal("29.7604")))

    def test_get_route_geometry_converts_units(self):
        """Test that distance and duration are converted to miles and minutes."""
        self.osrm_client.get_route.return_value = RouteResult(
            distance_meters=Decimal("5000"),  # 5 km
            duration_seconds=Decimal("300"),  # 5 minutes
            geometry=[(Decimal("32.7767"), Decimal("-96.7970"))],
        )

        start = (Decimal("32.7767"), Decimal("-96.7970"))
        finish = (Decimal("29.7604"), Decimal("-95.3698"))

        result = self.service.get_route_geometry(start, finish)

        # 5000 meters ≈ 3.11 miles
        expected_miles = (Decimal("5000") * Decimal("0.000621371")).quantize(
            Decimal("0.01")
        )
        self.assertAlmostEqual(result.distance_miles, expected_miles, places=2)

        # 300 seconds = 5.0 minutes
        self.assertEqual(result.duration_minutes, Decimal("5.0"))

    def test_get_route_geometry_returns_structure(self):
        """Test that RouteGeometry structure is returned correctly."""
        geometry_points = [
            (Decimal("32.7767"), Decimal("-96.7970")),
            (Decimal("32.5000"), Decimal("-96.5000")),
            (Decimal("29.7604"), Decimal("-95.3698")),
        ]

        self.osrm_client.get_route.return_value = RouteResult(
            distance_meters=Decimal("5000"),
            duration_seconds=Decimal("300"),
            geometry=geometry_points,
        )

        start = (Decimal("32.7767"), Decimal("-96.7970"))
        finish = (Decimal("29.7604"), Decimal("-95.3698"))

        result = self.service.get_route_geometry(start, finish)

        # Verify structure
        self.assertIsInstance(result, RouteGeometry)
        self.assertIsInstance(result.distance_miles, Decimal)
        self.assertIsInstance(result.duration_minutes, Decimal)
        self.assertIsInstance(result.coordinates, list)

        # Verify geometry is returned in (lat, lon) order
        self.assertEqual(result.coordinates, geometry_points)

    def test_calculate_distance_returns_distance_only(self):
        """Test that calculate_distance returns just the distance in miles."""
        self.osrm_client.get_route.return_value = RouteResult(
            distance_meters=Decimal("5000"),
            duration_seconds=Decimal("300"),
            geometry=[(Decimal("32.7767"), Decimal("-96.7970"))],
        )

        start = (Decimal("32.7767"), Decimal("-96.7970"))
        finish = (Decimal("29.7604"), Decimal("-95.3698"))

        distance = self.service.calculate_distance(start, finish)

        # Should return distance in miles
        expected_miles = (Decimal("5000") * Decimal("0.000621371")).quantize(
            Decimal("0.01")
        )
        self.assertAlmostEqual(distance, expected_miles, places=2)

    def test_maps_transient_osrm_errors(self):
        """Test that OSRM transient errors are mapped to RoutingTransientError."""
        from api.clients.osrm_client import OSRMTransientError

        self.osrm_client.get_route.side_effect = OSRMTransientError("Network error")

        start = (Decimal("32.7767"), Decimal("-96.7970"))
        finish = (Decimal("29.7604"), Decimal("-95.3698"))

        with self.assertRaises(RoutingTransientError) as cm:
            self.service.get_route_geometry(start, finish)

        self.assertIn("Network error", str(cm.exception))

    def test_maps_permanent_osrm_errors(self):
        """Test that OSRM permanent errors are mapped to RoutingPermanentError."""
        from api.clients.osrm_client import OSRMPermanentError

        self.osrm_client.get_route.side_effect = OSRMPermanentError("No route")

        start = (Decimal("32.7767"), Decimal("-96.7970"))
        finish = (Decimal("29.7604"), Decimal("-95.3698"))

        with self.assertRaises(RoutingPermanentError) as cm:
            self.service.get_route_geometry(start, finish)

        self.assertIn("No route", str(cm.exception))

    def test_maps_base_osrm_errors_to_permanent(self):
        """Test that base OSRM errors are mapped to RoutingPermanentError."""
        from api.clients.osrm_client import OSRMError

        self.osrm_client.get_route.side_effect = OSRMError("Unknown error")

        start = (Decimal("32.7767"), Decimal("-96.7970"))
        finish = (Decimal("29.7604"), Decimal("-95.3698"))

        with self.assertRaises(RoutingPermanentError) as cm:
            self.service.get_route_geometry(start, finish)

        self.assertIn("Unknown error", str(cm.exception))

    def test_coordinate_precision_handling(self):
        """Test that coordinates maintain proper precision."""
        # OSRM returns coordinates with 7 decimal places
        self.osrm_client.get_route.return_value = RouteResult(
            distance_meters=Decimal("1000"),
            duration_seconds=Decimal("60"),
            geometry=[
                (Decimal("32.7767000"), Decimal("-96.7970000")),
                (Decimal("32.8000000"), Decimal("-96.8000000")),
            ],
        )

        start = (Decimal("32.7767000"), Decimal("-96.7970000"))
        finish = (Decimal("32.8000000"), Decimal("-96.8000000"))

        result = self.service.get_route_geometry(start, finish)

        # Verify precision is maintained
        for lat, lon in result.coordinates:
            # Should have 7 decimal places (0.0000001 precision)
            self.assertEqual(
                lat.as_tuple().exponent,
                Decimal("0.0000001").as_tuple().exponent
            )
            self.assertEqual(
                lon.as_tuple().exponent,
                Decimal("0.0000001").as_tuple().exponent
            )

    def assertAlmostEqual(self, first, second, places=None):
        """Helper for Decimal comparison."""
        if places is not None:
            quantize_to = Decimal("0.1") ** places
            first = first.quantize(quantize_to)
            second = second.quantize(quantize_to)
        self.assertEqual(first, second)
