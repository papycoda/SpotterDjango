from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.urls import reverse
from rest_framework.test import APITestCase

from api.clients.osrm_client import OSRMTransientError, RouteResult
from api.models import FuelStation
from api.services.fuel_optimization_service import (
    FuelPlan,
    FuelStop,
    RouteGapTooLargeError,
)
from api.services.fuel_plan_service import FuelPlanResult
from api.services.route_geocoding_service import (
    GeocodingTransientError,
    GeocodedLocation,
    LocationNotInUSAError,
)
from api.services.route_service import RouteNotFoundError, RoutingTransientError
from api.services.routing_service import RouteGeometry


class RouteFuelPlanEndpointTests(APITestCase):
    def setUp(self):
        self.url = reverse("route-fuel-plan")
        self.payload = {"start": "Dallas, TX", "finish": "Denver, CO"}

    @staticmethod
    def route_plan():
        return SimpleNamespace(
            start_geocoded=GeocodedLocation(
                Decimal("32.7767"), Decimal("-96.7970"), "Dallas, TX, USA"
            ),
            end_geocoded=GeocodedLocation(
                Decimal("39.7392"), Decimal("-104.9903"), "Denver, CO, USA"
            ),
            route_geometry=[
                (Decimal("32.7767"), Decimal("-96.7970")),
                (Decimal("39.7392"), Decimal("-104.9903")),
            ],
            total_distance_miles=Decimal("700.125"),
            total_duration_minutes=Decimal("650.0"),
        )

    def test_returns_geojson_and_fixed_precision_decimal_strings(self):
        station = FuelStation(
            id="station-1",
            name="Example Fuel",
            address="100 Main St",
            city="Amarillo",
            state="TX",
            price_per_gallon=Decimal("3.49"),
        )
        route_geometry = RouteGeometry(
            distance_miles=Decimal("700.125"),
            duration_minutes=Decimal("650.0"),
            coordinates=self.route_plan().route_geometry,
        )
        fuel_plan = FuelPlan(
            route_geometry=route_geometry,
            fuel_stops=[
                FuelStop(
                    station=station,
                    route_progress_m=643737.6,
                    gallons_purchased=Decimal("20.125"),
                    cost_usd=Decimal("70.24"),
                )
            ],
            total_fuel_purchased=Decimal("20.125"),
            total_cost_usd=Decimal("70.24"),
            vehicle_assumptions={
                "range_miles": 500,
                "mpg": 10,
                "tank_gallons": 50,
            },
        )

        with patch(
            "api.views.FuelPlanService.create_plan",
            return_value=FuelPlanResult(
                route_plan=self.route_plan(),
                fuel_plan=fuel_plan,
            ),
        ):
            response = self.client.post(self.url, self.payload, format="json")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["route_geometry"]["type"], "LineString")
        self.assertEqual(
            response.data["route_geometry"]["coordinates"][0],
            [-96.797, 32.7767],
        )
        self.assertEqual(response.data["total_fuel_cost"], "70.24")
        self.assertEqual(response.data["total_fuel_purchased"], "20.125")
        stop = response.data["fuel_stops"][0]
        self.assertEqual(stop["price_per_gallon"], "3.49")
        self.assertEqual(stop["route_progress_miles"], "400.000")
        self.assertEqual(stop["gallons_purchased"], "20.125")
        self.assertEqual(stop["cost_usd"], "70.24")

    def test_invalid_input_has_stable_error_envelope(self):
        response = self.client.post(
            self.url, {"start": "", "finish": "Denver, CO"}, format="json"
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["error"], "Invalid request.")
        self.assertIn("start", response.data["details"])

    def test_view_delegates_domain_workflow_to_one_fuel_plan_service_call(self):
        fuel_plan = SimpleNamespace(
            fuel_stops=[],
            total_fuel_purchased=Decimal("0.000"),
            total_cost_usd=Decimal("0.00"),
            vehicle_assumptions={
                "range_miles": 500,
                "mpg": 10,
                "tank_gallons": 50,
            },
        )
        result = FuelPlanResult(route_plan=self.route_plan(), fuel_plan=fuel_plan)

        with patch(
            "api.views.FuelPlanService.create_plan",
            return_value=result,
        ) as create_plan:
            response = self.client.post(self.url, self.payload, format="json")

        self.assertEqual(response.status_code, 200)
        create_plan.assert_called_once_with("Dallas, TX", "Denver, CO")

    def test_maps_domain_and_upstream_errors(self):
        cases = (
            (LocationNotInUSAError("not found"), 404),
            (RouteNotFoundError("no route"), 422),
            (RoutingTransientError("unavailable"), 502),
        )
        for error, expected_status in cases:
            with self.subTest(error=type(error).__name__):
                with patch("api.views.FuelPlanService.create_plan", side_effect=error):
                    response = self.client.post(self.url, self.payload, format="json")
                self.assertEqual(response.status_code, expected_status)
                self.assertEqual(set(response.data), {"error"})

    def test_maps_infeasible_fuel_corridor_to_422(self):
        with patch(
            "api.views.FuelPlanService.create_plan",
            side_effect=RouteGapTooLargeError(550, "destination"),
        ):
            response = self.client.post(self.url, self.payload, format="json")

        self.assertEqual(response.status_code, 422)
        self.assertEqual(set(response.data), {"error"})

    def test_maps_endpoint_geocoder_transient_failure_to_502(self):
        with patch(
            "api.services.route_geocoding_service.RouteGeocodingService.geocode_location",
            side_effect=GeocodingTransientError("temporarily unavailable"),
        ):
            response = self.client.post(self.url, self.payload, format="json")

        self.assertEqual(response.status_code, 502)
        self.assertEqual(set(response.data), {"error"})

    def test_maps_osrm_transient_failure_to_502(self):
        locations = [
            GeocodedLocation(
                Decimal("32.7767"), Decimal("-96.7970"), "Dallas, TX, USA"
            ),
            GeocodedLocation(
                Decimal("39.7392"), Decimal("-104.9903"), "Denver, CO, USA"
            ),
        ]
        with (
            patch(
                "api.services.route_geocoding_service.RouteGeocodingService.geocode_location",
                side_effect=locations,
            ),
            patch(
                "api.clients.osrm_client.OSRMClient.get_route",
                side_effect=OSRMTransientError("temporarily unavailable"),
            ),
        ):
            response = self.client.post(self.url, self.payload, format="json")

        self.assertEqual(response.status_code, 502)
        self.assertEqual(set(response.data), {"error"})

    @patch("api.clients.nominatim_client.NominatimClient.geocode")
    @patch("api.clients.osrm_client.OSRMClient.get_route")
    @patch("api.services.route_geocoding_service.RouteGeocodingService._geocode_with_nominatim")
    def test_uses_two_endpoint_geocodes_one_route_call_and_no_station_geocoding(
        self, endpoint_geocode, get_route, station_geocode
    ):
        endpoint_geocode.side_effect = [
            SimpleNamespace(
                latitude=Decimal("32.7767"),
                longitude=Decimal("-96.7970"),
                display_name="Dallas, TX, USA",
            ),
            SimpleNamespace(
                latitude=Decimal("39.7392"),
                longitude=Decimal("-104.9903"),
                display_name="Denver, CO, USA",
            ),
        ]
        get_route.return_value = RouteResult(
            distance_meters=Decimal("160934.4"),
            duration_seconds=Decimal("7200"),
            geometry=[
                (Decimal("32.7767"), Decimal("-96.7970")),
                (Decimal("39.7392"), Decimal("-104.9903")),
            ],
        )

        response = self.client.post(self.url, self.payload, format="json")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(endpoint_geocode.call_count, 2)
        get_route.assert_called_once()
        station_geocode.assert_not_called()
