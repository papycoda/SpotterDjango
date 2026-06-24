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
                self.iteration_reads = 0
                self.indexed_reads = 0

            def __iter__(self):
                for item in super().__iter__():
                    self.iteration_reads += 1
                    yield item

            def __getitem__(self, key):
                self.indexed_reads += 1
                return super().__getitem__(key)

            @property
            def items_read(self):
                return self.iteration_reads + self.indexed_reads

        class InstrumentedService(FuelOptimizationService):
            normalized = None

            def _normalize_stations(self, stations, destination):
                result = super()._normalize_stations(stations, destination)
                self.normalized = CountingList(result)
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
        # The algorithm uses monotonic indexes: each station is read a constant
        # number of times during the greedy scan. This gives O(n) total.
        # iteration_reads: gap validation (n) = n
        # indexed_reads: initial call (2) + n-1 iterations (3 each) = 3n - 1
        # Total: 4n - 1 which is O(n) linear behavior.
        self.assertEqual(service.normalized.iteration_reads, station_count)  # gap validation
        self.assertLessEqual(service.normalized.indexed_reads, station_count * 3)
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

    def test_skip_station_with_less_than_1_gallon_purchase(self):
        """Test that stations requiring < 1 gallon purchase are skipped when possible."""
        # 900-mile route: stations at 100, 300, 500, 700 miles
        # All stations have the same price, so algorithm minimizes stops
        route_geometry = RouteGeometry(
            distance_miles=Decimal("900"),
            duration_minutes=Decimal("900"),
            coordinates=[(Decimal("32.7767"), Decimal("-96.7970"))],
        )

        stations = [
            self._create_nearby_station(100 * 1609.344, Decimal("3.50"), station_id="100"),
            self._create_nearby_station(300 * 1609.344, Decimal("3.50"), station_id="300"),
            self._create_nearby_station(500 * 1609.344, Decimal("3.50"), station_id="500"),
            self._create_nearby_station(700 * 1609.344, Decimal("3.50"), station_id="700"),
        ]

        result = self.service.optimize_fuel_stops(route_geometry, stations)

        # Algorithm picks optimal stops: 300 and 700 miles
        # At 300: arrive with 20 gallons, buy 30 to reach 700
        # At 700: arrive with 10 gallons, buy 10 to reach destination
        self.assertEqual(len(result.fuel_stops), 2)
        self.assertEqual([stop.station.id for stop in result.fuel_stops], ["300", "700"])
        self.assertGreaterEqual(result.fuel_stops[-1].gallons_purchased, Decimal("1.0"))

    def test_minimum_purchase_when_close_to_destination(self):
        """Test minimum purchase is enforced even when close to destination."""
        # 550-mile route with station at 400 miles
        # At 400 miles, we've used 40 gallons, have 10 left
        # Need 5 gallons for remaining 150 miles - well above 1 gallon minimum
        route_geometry = RouteGeometry(
            distance_miles=Decimal("550"),
            duration_minutes=Decimal("550"),
            coordinates=[(Decimal("32.7767"), Decimal("-96.7970"))],
        )

        stations = [
            self._create_nearby_station(400 * 1609.344, Decimal("3.50"), station_id="400"),
        ]

        result = self.service.optimize_fuel_stops(route_geometry, stations)

        self.assertEqual(len(result.fuel_stops), 1)
        # At 400 miles: used 40 gallons, need 15 more for remaining 150 miles
        # Purchase = 15 - 10 (remaining) = 5 gallons
        self.assertGreaterEqual(result.fuel_stops[0].gallons_purchased, Decimal("1.0"))

    def test_skip_station_requiring_less_than_1_gallon(self):
        """Test that stations which would require < 1 gallon are skipped."""
        # 1000-mile route with stations at 400, 490, 600 miles
        # With strategic pricing, we should skip stations that don't meet minimum
        route_geometry = RouteGeometry(
            distance_miles=Decimal("1000"),
            duration_minutes=Decimal("1000"),
            coordinates=[(Decimal("32.7767"), Decimal("-96.7970"))],
        )

        # Station at 490 is very close to 500-mile range boundary
        # If we arrive with ~1 gallon, stopping there would require buying < 1 gallon
        stations = [
            self._create_nearby_station(400 * 1609.344, Decimal("5.00"), station_id="expensive"),
            self._create_nearby_station(490 * 1609.344, Decimal("3.00"), station_id="close_cheap"),
            self._create_nearby_station(600 * 1609.344, Decimal("3.00"), station_id="further_cheap"),
        ]

        result = self.service.optimize_fuel_stops(route_geometry, stations)

        # All stops should require at least 1 gallon
        for stop in result.fuel_stops:
            self.assertGreaterEqual(
                stop.gallons_purchased,
                Decimal("1.000"),
                f"Station {stop.station.id} would purchase {stop.gallons_purchased} gallons"
            )

    def test_optimizer_reproduces_tiny_top_up_when_cheaper_station_is_just_out_of_range(self):
        """
        Regression test for tiny top-up behavior from TODO.md.

        Florence → Savannah route (1199.29 miles):
        - Emporia (43.500 miles, $2.84)
        - Harrisburg (496.430 miles, $3.69)
        - Metropolis (544.030 miles, $3.60)

        Emporia → Metropolis = 500.53 miles (exceeds 500-mile range by 0.53 miles).
        Original behavior: Harrisburg top-up of 0.053 gallons.
        Fixed behavior: Harrisburg rounds up to 1.000 gallon minimum.

        The fix eliminates the tiny purchase without overbuying fuel.
        """
        route_geometry = RouteGeometry(
            distance_miles=Decimal("1199.29"),
            duration_minutes=Decimal("1328"),
            coordinates=[(Decimal("32.7767"), Decimal("-96.7970"))],
        )

        stations = [
            self._create_nearby_station(43.500 * 1609.344, Decimal("2.84"), station_id="emporia"),
            self._create_nearby_station(496.430 * 1609.344, Decimal("3.69"), station_id="harrisburg"),
            self._create_nearby_station(544.030 * 1609.344, Decimal("3.60"), station_id="metropolis"),
            self._create_nearby_station(996.150 * 1609.344, Decimal("3.17"), station_id="byron"),
            self._create_nearby_station(1042.460 * 1609.344, Decimal("3.00"), station_id="dublin"),
        ]

        result = self.service.optimize_fuel_stops(route_geometry, stations)

        stops_by_id = {stop.station.id: stop for stop in result.fuel_stops}

        # Verify Emporia and Harrisburg stops exist
        self.assertIn("emporia", stops_by_id)
        self.assertIn("harrisburg", stops_by_id)

        # Verify the gap that necessitated Harrisburg
        emporia_to_metropolis = Decimal("544.030") - Decimal("43.500")
        self.assertEqual(emporia_to_metropolis, Decimal("500.530"))

        shortfall_miles = emporia_to_metropolis - Decimal("500.000")
        self.assertEqual(shortfall_miles, Decimal("0.530"))

        tiny_top_up_gallons = shortfall_miles / Decimal("10")
        self.assertEqual(tiny_top_up_gallons, Decimal("0.053"))

        # Emporia buys fuel to reach destination
        self.assertAlmostEqual(stops_by_id["emporia"].gallons_purchased, Decimal("4.350"), places=3)

        # Harrisburg: instead of 0.053 gallons, rounds up to 1.000 gallon minimum
        self.assertGreaterEqual(stops_by_id["harrisburg"].gallons_purchased, Decimal("1.000"))
        self.assertLessEqual(stops_by_id["harrisburg"].gallons_purchased, Decimal("2.000"))

        # Total fuel purchased should be close to theoretical minimum (not wasteful)
        # Theoretical: 1199.29 / 10 = 119.929 gallons total consumption
        # Starting tank: 50 gallons
        # Minimum en-route purchase: 119.929 - 50 = 69.929 gallons
        # Allow small tolerance for rounding and minimum purchase logic
        self.assertLessEqual(result.total_fuel_purchased, Decimal("75.000"))

        # All stops must meet the minimum purchase requirement
        for stop in result.fuel_stops:
            self.assertGreaterEqual(
                stop.gallons_purchased,
                Decimal("1.000"),
                f"Station {stop.station.id} purchased {stop.gallons_purchased} gallons (< 1.000 minimum)"
            )

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
