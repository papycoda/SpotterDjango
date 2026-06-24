from django.test import SimpleTestCase

from api import serializers
from api.serializers import FuelPlanRequestSerializer, FuelPlanResponseSerializer


class GeocodeRequestSerializerTests(SimpleTestCase):
    def test_defaults_to_a_safe_batch_without_failed_retries(self):
        self.assertTrue(hasattr(serializers, "GeocodeRequestSerializer"))
        serializer = serializers.GeocodeRequestSerializer(data={})

        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertEqual(
            serializer.validated_data,
            {"limit": 500, "retry_failed": False},
        )

    def test_rejects_limits_outside_the_bounded_range(self):
        self.assertTrue(hasattr(serializers, "GeocodeRequestSerializer"))

        for invalid_limit in (0, 2001):
            with self.subTest(limit=invalid_limit):
                serializer = serializers.GeocodeRequestSerializer(
                    data={"limit": invalid_limit}
                )
                self.assertFalse(serializer.is_valid())
                self.assertIn("limit", serializer.errors)


class FuelPlanRequestSerializerTests(SimpleTestCase):
    def test_accepts_start_and_finish_as_the_complete_request(self):
        serializer = FuelPlanRequestSerializer(
            data={"start": "Chicago, IL", "finish": "Dallas, TX"}
        )

        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertEqual(
            serializer.validated_data,
            {"start": "Chicago, IL", "finish": "Dallas, TX", "include_geometry": False},
        )

    def test_rejects_blank_start(self):
        serializer = FuelPlanRequestSerializer(
            data={"start": "  ", "finish": "Dallas, TX"}
        )

        self.assertFalse(serializer.is_valid())
        self.assertIn("start", serializer.errors)

    def test_rejects_blank_finish(self):
        serializer = FuelPlanRequestSerializer(
            data={"start": "Chicago, IL", "finish": "  "}
        )

        self.assertFalse(serializer.is_valid())
        self.assertIn("finish", serializer.errors)


class FuelPlanResponseSerializerTests(SimpleTestCase):
    def test_defines_the_assignment_response_contract(self):
        serializer = FuelPlanResponseSerializer(
            data={
                "start": "Chicago, IL",
                "finish": "Dallas, TX",
                "distance_miles": 925.4,
                "duration_minutes": 840,
                "route_geometry": {
                    "type": "LineString",
                    "coordinates": [[-87.63, 41.88], [-96.8, 32.78]],
                },
                "fuel_stops": [
                    {
                        "station_id": "123",
                        "name": "Example Truck Stop",
                        "address": "100 Main St",
                        "city": "Amarillo",
                        "state": "TX",
                        "price_per_gallon": "3.49",
                        "route_progress_miles": "450.000",
                        "gallons_purchased": "42.500",
                        "cost_usd": "148.33",
                    }
                ],
                "total_fuel_purchased": "42.500",
                "total_fuel_cost": "148.33",
                "vehicle_assumptions": {
                    "range_miles": 500,
                    "mpg": 10,
                    "tank_gallons": 50,
                },
            }
        )

        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertIn("route_geometry", serializer.validated_data)
        self.assertIn("fuel_stops", serializer.validated_data)
        self.assertEqual(
            serializer.validated_data["total_fuel_cost"], "148.33"
        )

    def test_rejects_decimal_values_without_fixed_precision(self):
        serializer = FuelPlanResponseSerializer(
            data={
                "start": "Chicago, IL",
                "finish": "Dallas, TX",
                "distance_miles": 925.4,
                "duration_minutes": 840,
                "route_geometry": {
                    "type": "LineString",
                    "coordinates": [[-87.63, 41.88], [-96.8, 32.78]],
                },
                "fuel_stops": [],
                "total_fuel_purchased": "42.5",
                "total_fuel_cost": "148.3",
                "vehicle_assumptions": {
                    "range_miles": 500,
                    "mpg": 10,
                    "tank_gallons": 50,
                },
            }
        )

        self.assertFalse(serializer.is_valid())
        self.assertIn("total_fuel_purchased", serializer.errors)
        self.assertIn("total_fuel_cost", serializer.errors)
