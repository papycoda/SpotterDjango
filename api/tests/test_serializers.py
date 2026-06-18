from decimal import Decimal

from django.test import SimpleTestCase

from api.serializers import FuelPlanRequestSerializer, FuelPlanResponseSerializer


class FuelPlanRequestSerializerTests(SimpleTestCase):
    def test_accepts_start_and_finish_as_the_complete_request(self):
        serializer = FuelPlanRequestSerializer(
            data={"start": "Chicago, IL", "finish": "Dallas, TX"}
        )

        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertEqual(
            serializer.validated_data,
            {"start": "Chicago, IL", "finish": "Dallas, TX"},
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
                        "distance_from_start_miles": 450.0,
                        "price_per_gallon": "3.49",
                        "gallons_purchased": 42.5,
                        "cost": "148.33",
                    }
                ],
                "total_fuel_cost": "148.33",
            }
        )

        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertIn("route_geometry", serializer.validated_data)
        self.assertIn("fuel_stops", serializer.validated_data)
        self.assertEqual(
            serializer.validated_data["total_fuel_cost"], Decimal("148.33")
        )
