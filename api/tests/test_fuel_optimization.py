"""
Tests for fuel optimization service.

Tests the greedy algorithm for fuel stop optimization and edge cases.
"""

from decimal import Decimal
from unittest.mock import Mock

from django.test import SimpleTestCase

from api.models import FuelStation
from api.services.fuel_optimization_service import (
    FuelOptimizationService,
    FuelStop,
    FuelPlan,
    RouteGapTooLargeError,
)
from api.services.station_filtering_service import NearbyStation
from api.services.routing_service import RouteGeometry


class FuelOptimizationServiceTests(SimpleTestCase):
    """Tests for fuel optimization service."""

    def setUp(self):
        self.service = FuelOptimizationService()

    def test_vehicle_assumptions(self):
        """Test that vehicle assumptions match requirements."""
        self.assertEqual(self.service.range_miles, 500)
        self.assertEqual(self.service.mpg, 10)
        self.assertEqual(self.service.tank_gallons, 50)
        self.assertAlmostEqual(self.service.range_meters, 500 * 1609.344, places=1)

    def test_short_trip_no_stops_needed(self):
        """Test that short trips (< 500 miles) require no fuel stops."""
        # Create a 300-mile route
        route_geometry = RouteGeometry(
            distance_miles=Decimal("300"),
            duration_minutes=Decimal("300"),
            coordinates=[(Decimal("32.7767"), Decimal("-96.7970"))],
        )

        # Add some stations (should be ignored)
        stations = [
            self._create_nearby_station(100 * 1609.34, Decimal("3.50")),
        ]

        result = self.service.optimize_fuel_stops(route_geometry, stations)

        self.assertIsInstance(result, FuelPlan)
        self.assertEqual(len(result.fuel_stops), 0)
        self.assertEqual(result.total_fuel_purchased, Decimal("0"))
        self.assertEqual(result.total_cost_usd, Decimal("0"))
        self.assertEqual(result.route_geometry, route_geometry)

    def test_medium_trip_one_stop(self):
        """Test that medium trips (500-1000 miles) require one stop."""
        # Create a 700-mile route
        route_geometry = RouteGeometry(
            distance_miles=Decimal("700"),
            duration_minutes=Decimal("700"),
            coordinates=[(Decimal("32.7767"), Decimal("-96.7970"))],
        )

        # Place station at 400 miles
        stations = [
            self._create_nearby_station(400 * 1609.34, Decimal("3.50")),
        ]

        result = self.service.optimize_fuel_stops(route_geometry, stations)

        self.assertEqual(len(result.fuel_stops), 1)
        stop = result.fuel_stops[0]
        self.assertAlmostEqual(stop.route_progress_miles, Decimal("400"), places=0)

        # The vehicle arrives with 10 gallons from its initial full tank, so it
        # buys only the additional 20 gallons needed for the final 300 miles.
        expected_gallons = Decimal("20.000")
        self.assertEqual(stop.gallons_purchased, expected_gallons)

        # Cost: 20 * $3.50 = $70
        expected_cost = Decimal("70.00")
        self.assertEqual(stop.cost_usd, expected_cost)

    def test_long_trip_multiple_stops(self):
        """Test that long trips (1000+ miles) require multiple stops."""
        # Create a 1200-mile route
        route_geometry = RouteGeometry(
            distance_miles=Decimal("1200"),
            duration_minutes=Decimal("1200"),
            coordinates=[(Decimal("32.7767"), Decimal("-96.7970"))],
        )

        # Place stations every 300 miles
        stations = [
            self._create_nearby_station(300 * 1609.344, Decimal("3.50"), station_id="300"),
            self._create_nearby_station(600 * 1609.344, Decimal("3.60"), station_id="600"),
            self._create_nearby_station(900 * 1609.344, Decimal("3.40"), station_id="900"),
        ]

        result = self.service.optimize_fuel_stops(route_geometry, stations)

        # Should need at least 2 stops (500 mile range)
        self.assertGreaterEqual(len(result.fuel_stops), 2)

        # Verify each leg is within range
        prev_position = Decimal("0")
        for stop in result.fuel_stops:
            leg_miles = (stop.route_progress_m - prev_position) / Decimal("1609.34")
            self.assertLessEqual(leg_miles, 500, f"Leg exceeds 500 miles: {leg_miles}")
            prev_position = stop.route_progress_m

        # Final leg to destination
        final_leg = (Decimal("1200") * Decimal("1609.34") - prev_position) / Decimal("1609.34")
        self.assertLessEqual(final_leg, 500)
        self.assertEqual([stop.station.id for stop in result.fuel_stops], ["300", "600", "900"])
        self.assertEqual(
            [stop.gallons_purchased for stop in result.fuel_stops],
            [Decimal("30.000"), Decimal("10.000"), Decimal("30.000")],
        )
        self.assertEqual(
            [stop.cost_usd for stop in result.fuel_stops],
            [Decimal("105.00"), Decimal("36.00"), Decimal("102.00")],
        )
        self.assertEqual(result.total_cost_usd, Decimal("243.00"))

    def test_large_increasing_price_route_reads_station_list_linearly(self):
        class CountingList(list):
            def __init__(self, values):
                super().__init__(values)
                self.items_read = 0

            def __iter__(self):
                for item in super().__iter__():
                    self.items_read += 1
                    yield item

        class InstrumentedService(FuelOptimizationService):
            normalized = None

            def _normalize_stations(self, stations, destination):
                self.normalized = CountingList(
                    super()._normalize_stations(stations, destination)
                )
                return self.normalized

        station_count = 30
        route_geometry = RouteGeometry(
            distance_miles=Decimal(str(station_count * 490 + 100)),
            duration_minutes=Decimal("1"),
            coordinates=[(Decimal("32.7767"), Decimal("-96.7970"))],
        )
        stations = [
            self._create_nearby_station(
                index * 490 * 1609.344,
                Decimal(index),
                station_id=str(index),
            )
            for index in range(1, station_count + 1)
        ]
        service = InstrumentedService()

        result = service.optimize_fuel_stops(route_geometry, stations)

        self.assertEqual(len(result.fuel_stops), station_count)
        self.assertLessEqual(service.normalized.items_read, station_count * 4)

    def test_prefer_cheaper_downstream_station(self):
        """Test that algorithm prefers cheaper stations when possible."""
        # 600-mile route
        route_geometry = RouteGeometry(
            distance_miles=Decimal("600"),
            duration_minutes=Decimal("600"),
            coordinates=[(Decimal("32.7767"), Decimal("-96.7970"))],
        )

        # Station A at 400 miles ($4.00), Station B at 450 miles ($3.00)
        stations = [
            self._create_nearby_station(400 * 1609.34, Decimal("4.00")),
            self._create_nearby_station(450 * 1609.34, Decimal("3.00")),
        ]

        result = self.service.optimize_fuel_stops(route_geometry, stations)

        # Should stop at cheaper station B
        self.assertEqual(len(result.fuel_stops), 1)
        stop = result.fuel_stops[0]
        self.assertAlmostEqual(stop.route_progress_miles, Decimal("450"), places=0)

    def test_buys_extra_before_a_more_expensive_required_stop(self):
        route_geometry = RouteGeometry(
            distance_miles=Decimal("900"),
            duration_minutes=Decimal("900"),
            coordinates=[(Decimal("32.7767"), Decimal("-96.7970"))],
        )
        stations = [
            self._create_nearby_station(100 * 1609.344, Decimal("1.00"), station_id="cheap"),
            self._create_nearby_station(490 * 1609.344, Decimal("5.00"), station_id="expensive"),
        ]

        result = self.service.optimize_fuel_stops(route_geometry, stations)

        self.assertEqual([stop.station.id for stop in result.fuel_stops], ["cheap", "expensive"])
        self.assertEqual(result.fuel_stops[0].gallons_purchased, Decimal("10.000"))
        self.assertEqual(result.fuel_stops[1].gallons_purchased, Decimal("30.000"))
        self.assertEqual(result.total_cost_usd, Decimal("160.00"))

    def test_gap_too_large_raises_error(self):
        """Test that unreachable gaps raise RouteGapTooLargeError."""
        # 1000-mile route
        route_geometry = RouteGeometry(
            distance_miles=Decimal("1000"),
            duration_minutes=Decimal("1000"),
            coordinates=[(Decimal("32.7767"), Decimal("-96.7970"))],
        )

        # Only one station at 200 miles - 800-mile gap to destination
        stations = [
            self._create_nearby_station(200 * 1609.34, Decimal("3.50")),
        ]

        with self.assertRaises(RouteGapTooLargeError) as cm:
            self.service.optimize_fuel_stops(route_geometry, stations)

        self.assertIn("exceeds vehicle range", str(cm.exception))

    def test_no_stations_short_trip_ok(self):
        """Test that no stations is OK for short trips."""
        route_geometry = RouteGeometry(
            distance_miles=Decimal("300"),
            duration_minutes=Decimal("300"),
            coordinates=[(Decimal("32.7767"), Decimal("-96.7970"))],
        )

        result = self.service.optimize_fuel_stops(route_geometry, [])

        self.assertEqual(len(result.fuel_stops), 0)

    def test_no_stations_long_trip_fails(self):
        """Test that no stations fails for long trips."""
        route_geometry = RouteGeometry(
            distance_miles=Decimal("600"),
            duration_minutes=Decimal("600"),
            coordinates=[(Decimal("32.7767"), Decimal("-96.7970"))],
        )

        with self.assertRaises(RouteGapTooLargeError):
            self.service.optimize_fuel_stops(route_geometry, [])

    def test_duplicate_prices_deterministic(self):
        """Test that duplicate prices result in deterministic behavior."""
        route_geometry = RouteGeometry(
            distance_miles=Decimal("600"),
            duration_minutes=Decimal("600"),
            coordinates=[(Decimal("32.7767"), Decimal("-96.7970"))],
        )

        # Two stations at same price
        stations = [
            self._create_nearby_station(400 * 1609.34, Decimal("3.50")),
            self._create_nearby_station(450 * 1609.34, Decimal("3.50")),
        ]

        # Equal prices deterministically favor the furthest reachable station.
        result1 = self.service.optimize_fuel_stops(route_geometry, stations)
        result2 = self.service.optimize_fuel_stops(route_geometry, stations)

        self.assertEqual(len(result1.fuel_stops), 1)
        self.assertEqual(len(result2.fuel_stops), 1)
        self.assertEqual(
            result1.fuel_stops[0].route_progress_m,
            result2.fuel_stops[0].route_progress_m,
        )
        self.assertAlmostEqual(result1.fuel_stops[0].route_progress_miles, Decimal("450"), places=0)

    def test_duplicate_positions_choose_cheapest_station_once(self):
        route_geometry = RouteGeometry(
            distance_miles=Decimal("700"),
            duration_minutes=Decimal("700"),
            coordinates=[(Decimal("32.7767"), Decimal("-96.7970"))],
        )
        stations = [
            self._create_nearby_station(400 * 1609.344, Decimal("4.00"), station_id="expensive"),
            self._create_nearby_station(400 * 1609.344, Decimal("3.00"), station_id="cheap"),
        ]

        result = self.service.optimize_fuel_stops(route_geometry, stations)

        self.assertEqual([stop.station.id for stop in result.fuel_stops], ["cheap"])

    def test_destination_is_terminal_and_never_a_purchase_stop(self):
        route_geometry = RouteGeometry(
            distance_miles=Decimal("700"),
            duration_minutes=Decimal("700"),
            coordinates=[(Decimal("32.7767"), Decimal("-96.7970"))],
        )
        stations = [
            self._create_nearby_station(400 * 1609.344, Decimal("3.50"), station_id="400"),
            self._create_nearby_station(700 * 1609.344, Decimal("1.00"), station_id="destination"),
        ]

        result = self.service.optimize_fuel_stops(route_geometry, stations)

        self.assertEqual([stop.station.id for stop in result.fuel_stops], ["400"])
        self.assertTrue(
            all(stop.route_progress_miles < route_geometry.distance_miles for stop in result.fuel_stops)
        )

    def test_cost_calculation_decimal_precision(self):
        """Test that costs are calculated with Decimal precision."""
        route_geometry = RouteGeometry(
            distance_miles=Decimal("700"),
            duration_minutes=Decimal("700"),
            coordinates=[(Decimal("32.7767"), Decimal("-96.7970"))],
        )

        stations = [
            self._create_nearby_station(400 * 1609.34, Decimal("3.339")),  # Odd price
        ]

        result = self.service.optimize_fuel_stops(route_geometry, stations)

        self.assertEqual(len(result.fuel_stops), 1)
        stop = result.fuel_stops[0]

        # 20 gallons * $3.339 = $66.78
        expected_cost = Decimal("66.78")
        self.assertEqual(stop.cost_usd, expected_cost)

        # Verify Decimal type
        self.assertIsInstance(stop.cost_usd, Decimal)
        self.assertIsInstance(stop.gallons_purchased, Decimal)

    def test_gallons_and_each_stop_cost_use_stable_half_up_rounding(self):
        route_geometry = RouteGeometry(
            distance_miles=Decimal("700.005"),
            duration_minutes=Decimal("700"),
            coordinates=[(Decimal("32.7767"), Decimal("-96.7970"))],
        )
        stations = [
            self._create_nearby_station(400 * 1609.344, Decimal("3.333")),
        ]

        result = self.service.optimize_fuel_stops(route_geometry, stations)

        stop = result.fuel_stops[0]
        self.assertEqual(stop.gallons_purchased, Decimal("20.001"))
        self.assertEqual(stop.cost_usd, Decimal("66.66"))
        self.assertEqual(stop.gallons_purchased.as_tuple().exponent, -3)
        self.assertEqual(stop.cost_usd.as_tuple().exponent, -2)
        self.assertIsInstance(stop.route_progress_m, Decimal)
        self.assertEqual(result.total_cost_usd, sum(item.cost_usd for item in result.fuel_stops))

    def test_calculate_leg_distance(self):
        """Test leg distance calculation."""
        station = self._create_nearby_station(400 * 1609.34, Decimal("3.50"))
        fuel_stop = FuelStop(
            station=station.station,
            route_progress_m=station.route_progress_m,
            gallons_purchased=Decimal("30"),
            cost_usd=Decimal("105"),
        )

        # Distance from start
        distance = self.service.calculate_leg_distance(None, fuel_stop)
        self.assertAlmostEqual(distance / 1609.34, 400, places=0)

    def test_route_gap_between_start_and_first_station(self):
        """Test gap validation from route start to first station."""
        route_geometry = RouteGeometry(
            distance_miles=Decimal("600"),
            duration_minutes=Decimal("600"),
            coordinates=[(Decimal("32.7767"), Decimal("-96.7970"))],
        )

        # First station at 600 miles (gap from start)
        stations = [
            self._create_nearby_station(600 * 1609.34, Decimal("3.50")),
            self._create_nearby_station(900 * 1609.34, Decimal("3.50")),
        ]

        with self.assertRaises(RouteGapTooLargeError):
            self.service.optimize_fuel_stops(route_geometry, stations)

    def test_route_gap_between_stations(self):
        """Test gap validation between consecutive stations."""
        route_geometry = RouteGeometry(
            distance_miles=Decimal("1200"),
            duration_minutes=Decimal("1200"),
            coordinates=[(Decimal("32.7767"), Decimal("-96.7970"))],
        )

        # Stations 600 miles apart
        stations = [
            self._create_nearby_station(300 * 1609.34, Decimal("3.50")),
            self._create_nearby_station(900 * 1609.34, Decimal("3.50")),
        ]

        with self.assertRaises(RouteGapTooLargeError):
            self.service.optimize_fuel_stops(route_geometry, stations)

    def test_vehicle_assumptions_in_plan(self):
        """Test that vehicle assumptions are included in fuel plan."""
        route_geometry = RouteGeometry(
            distance_miles=Decimal("300"),
            duration_minutes=Decimal("300"),
            coordinates=[(Decimal("32.7767"), Decimal("-96.7970"))],
        )

        result = self.service.optimize_fuel_stops(route_geometry, [])

        self.assertIn("range_miles", result.vehicle_assumptions)
        self.assertIn("mpg", result.vehicle_assumptions)
        self.assertIn("tank_gallons", result.vehicle_assumptions)
        self.assertEqual(result.vehicle_assumptions["range_miles"], 500)
        self.assertEqual(result.vehicle_assumptions["mpg"], 10)
        self.assertEqual(result.vehicle_assumptions["tank_gallons"], 50)

    def test_exact_range_boundary(self):
        """Test behavior at exact 500-mile range boundary."""
        # Exactly 500 miles - should work without stops
        route_geometry = RouteGeometry(
            distance_miles=Decimal("500"),
            duration_minutes=Decimal("500"),
            coordinates=[(Decimal("32.7767"), Decimal("-96.7970"))],
        )

        result = self.service.optimize_fuel_stops(route_geometry, [])
        self.assertEqual(len(result.fuel_stops), 0)

    def test_furthest_reachable_station_fallback(self):
        """Test fallback to furthest reachable station when needed."""
        # 600-mile route with stations at 350, 400, 450 miles
        route_geometry = RouteGeometry(
            distance_miles=Decimal("600"),
            duration_minutes=Decimal("600"),
            coordinates=[(Decimal("32.7767"), Decimal("-96.7970"))],
        )

        # All same price - should pick furthest that allows reaching destination
        stations = [
            self._create_nearby_station(350 * 1609.34, Decimal("3.50")),
            self._create_nearby_station(400 * 1609.34, Decimal("3.50")),
            self._create_nearby_station(450 * 1609.34, Decimal("3.50")),
        ]

        result = self.service.optimize_fuel_stops(route_geometry, stations)

        # Should pick furthest station that allows completing trip
        self.assertEqual(len(result.fuel_stops), 1)
        self.assertAlmostEqual(result.fuel_stops[0].route_progress_miles, Decimal("450"), places=0)

    def _create_nearby_station(
        self,
        route_progress_m: float,
        price: Decimal,
        station_id: str | None = None,
    ) -> NearbyStation:
        """Helper to create a NearbyStation for testing."""
        station = FuelStation(
            id=station_id or f"station-{route_progress_m}",
            name=f"Station at {route_progress_m / 1609.34:.0f} miles",
            address="Test Address",
            city="Test City",
            state="TX",
            price_per_gallon=price,
            latitude=Decimal("32.7767"),
            longitude=Decimal("-96.7970"),
        )
        return NearbyStation(
            station=station,
            route_distance_m=0.0,
            route_progress_m=route_progress_m,
            is_on_route=True,
        )
