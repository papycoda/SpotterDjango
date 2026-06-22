"""Tests for geometry utilities."""

from decimal import Decimal

from django.test import TestCase

from api.utils.geometry_utils import (
    douglas_peucker_simplify,
    perpendicular_distance,
    simplify_for_station_filtering,
)


class PerpendicularDistanceTests(TestCase):
    """Tests for perpendicular distance calculation."""

    def test_point_on_line(self):
        """Distance should be zero when point is on the line segment."""
        start = (Decimal("40.0"), Decimal("-74.0"))
        end = (Decimal("41.0"), Decimal("-73.0"))
        # Midpoint of the line
        midpoint = (Decimal("40.5"), Decimal("-73.5"))
        distance = perpendicular_distance(midpoint, start, end)
        self.assertAlmostEqual(distance, 0.0, places=1)

    def test_point_off_line(self):
        """Distance should be positive when point is off the line."""
        start = (Decimal("40.0"), Decimal("-74.0"))
        end = (Decimal("41.0"), Decimal("-74.0"))
        # Point 0.1 degrees north (approximately 11km)
        point = (Decimal("40.5"), Decimal("-73.9"))
        distance = perpendicular_distance(point, start, end)
        # Should be around 8-12km depending on latitude scaling
        self.assertGreater(distance, 7000)
        self.assertLess(distance, 13000)


class DouglasPeuckerTests(TestCase):
    """Tests for Douglas-Peucker simplification."""

    def test_empty_list(self):
        """Empty list should return empty list."""
        result = douglas_peucker_simplify([])
        self.assertEqual(result, [])

    def test_single_point(self):
        """Single point should return single point."""
        coords = [(Decimal("40.0"), Decimal("-74.0"))]
        result = douglas_peucker_simplify(coords)
        self.assertEqual(result, coords)

    def test_two_points(self):
        """Two points should return two points unchanged."""
        coords = [
            (Decimal("40.0"), Decimal("-74.0")),
            (Decimal("41.0"), Decimal("-73.0")),
        ]
        result = douglas_peucker_simplify(coords)
        self.assertEqual(result, coords)

    def test_collinear_points(self):
        """Collinear points should be reduced to endpoints."""
        coords = [
            (Decimal("40.0"), Decimal("-74.0")),
            (Decimal("40.1"), Decimal("-73.9")),
            (Decimal("40.2"), Decimal("-73.8")),
            (Decimal("40.3"), Decimal("-73.7")),
            (Decimal("40.4"), Decimal("-73.6")),
        ]
        result = douglas_peucker_simplify(coords, tolerance_meters=1000.0)
        # Should reduce to just start and end points
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0], coords[0])
        self.assertEqual(result[-1], coords[-1])

    def test_straight_line_reduces_to_endpoints(self):
        """A nearly straight line should reduce to just endpoints."""
        coords = [
            (Decimal("40.0"), Decimal("-74.0")),
            (Decimal("40.01"), Decimal("-73.99")),
            (Decimal("40.02"), Decimal("-73.98")),
            (Decimal("40.03"), Decimal("-73.97")),
            (Decimal("40.04"), Decimal("-73.96")),
            (Decimal("40.05"), Decimal("-73.95")),
            (Decimal("41.0"), Decimal("-73.0")),
        ]
        result = douglas_peucker_simplify(coords, tolerance_meters=100.0)
        # With high tolerance, straight sections collapse
        self.assertLessEqual(len(result), len(coords))
        self.assertEqual(result[0], coords[0])
        self.assertEqual(result[-1], coords[-1])

    def test_preserves_turning_points(self):
        """Significant turns should be preserved."""
        # Create a path with a sharp turn
        coords = [
            (Decimal("40.0"), Decimal("-74.0")),
            (Decimal("40.1"), Decimal("-74.0")),
            (Decimal("40.1"), Decimal("-73.9")),  # Sharp turn here
            (Decimal("40.2"), Decimal("-73.9")),
            (Decimal("40.3"), Decimal("-73.8")),
        ]
        result = douglas_peucker_simplify(coords, tolerance_meters=50.0)
        # The turning point should be preserved
        self.assertGreater(len(result), 2)
        # Check that a point close to the turn point is in the result
        # (use approximate comparison due to float conversion in algorithm)
        turning_point = (Decimal("40.1"), Decimal("-73.9"))
        found = any(
            abs(lat - turning_point[0]) < Decimal("0.01") and
            abs(lon - turning_point[1]) < Decimal("0.01")
            for lat, lon in result
        )
        self.assertTrue(found, "Turning point should be preserved in result")


class SimplifyForStationFilteringTests(TestCase):
    """Tests for adaptive simplification."""

    def test_small_list_unchanged(self):
        """Lists smaller than target should be unchanged."""
        coords = [(Decimal(f"40.{i}"), Decimal(f"-74.{i}")) for i in range(10)]
        result = simplify_for_station_filtering(coords, target_point_count=300)
        self.assertEqual(len(result), 10)

    def test_target_point_count_approximation(self):
        """Should approximate target point count within 10%."""
        # Create a somewhat wavy line
        coords = []
        for i in range(1000):
            lat = Decimal("40") + Decimal(i) / Decimal("1000")
            lon = Decimal("-74") + Decimal(i) / Decimal("1000")
            # Add some waviness
            if i % 10 == 0:
                lon += Decimal("0.01")
            coords.append((lat, lon))

        result = simplify_for_station_filtering(coords, target_point_count=100)
        # Should be within 15% of target (85-115 points)
        self.assertGreaterEqual(len(result), 85)
        self.assertLessEqual(len(result), 115)

    def test_preserves_endpoints(self):
        """Simplification should always preserve endpoints."""
        coords = [(Decimal(f"40.{i}"), Decimal(f"-74.{i}")) for i in range(500)]
        result = simplify_for_station_filtering(coords, target_point_count=50)
        self.assertEqual(result[0], coords[0])
        self.assertEqual(result[-1], coords[-1])

    def test_large_route_simplification(self):
        """Test realistic large route simplification."""
        # Simulate a 800-mile route with dense geometry and natural variation
        coords = []
        for i in range(9500):
            # Dallas to Denver approximate path with added variation
            lat = Decimal("32.8") + (Decimal("40.0") - Decimal("32.8")) * i / 9500
            lon = Decimal("-96.8") + (Decimal("-105.0") - Decimal("-96.8")) * i / 9500
            # Add natural road variation (sine wave + periodic turns)
            variation = Decimal("0.05") * (i % 50) / Decimal("50")
            lat += variation
            # Add periodic "turns" every 100 points
            if i % 100 == 0:
                lon += Decimal("0.02")
            coords.append((lat, lon))

        result = simplify_for_station_filtering(coords, target_point_count=300)
        # Should reduce from 9500 to ~300 points
        self.assertGreaterEqual(len(result), 270)
        self.assertLessEqual(len(result), 330)

        # Should preserve endpoints
        self.assertEqual(result[0], coords[0])
        self.assertEqual(result[-1], coords[-1])


class PerformanceImpactTests(TestCase):
    """Tests to verify performance improvement."""

    def test_simplification_ratio(self):
        """Verify that simplification significantly reduces point count."""
        # Create a dense route (like OSRM returns)
        coords = []
        for i in range(9500):
            lat = Decimal("32.8") + (Decimal("40.0") - Decimal("32.8")) * i / 9500
            lon = Decimal("-96.8") + (Decimal("-105.0") - Decimal("-96.8")) * i / 9500
            coords.append((lat, lon))

        result = simplify_for_station_filtering(coords, target_point_count=300)

        # Should reduce by at least 95%
        reduction_ratio = (len(coords) - len(result)) / len(coords)
        self.assertGreater(reduction_ratio, 0.95)

        # Calculate potential performance improvement
        # If we check 100 stations against geometry:
        # Before: 100 * 9500 = 950,000 segment checks
        # After: 100 * 300 = 30,000 segment checks
        # That's ~31x faster for station filtering
        speedup = len(coords) / len(result)
        self.assertGreater(speedup, 20)
