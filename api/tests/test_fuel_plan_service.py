from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock

from django.test import SimpleTestCase, override_settings

from api import services
from api.services.fuel_plan_service import FuelPlanService


class FuelPlanServiceContractTests(SimpleTestCase):
    def test_service_is_exported_from_service_layer(self):
        self.assertTrue(hasattr(services, "FuelPlanService"))

    @override_settings(FUEL_ROUTE_CORRIDOR_MILES=5)
    def test_orchestrates_route_filtering_and_optimization(self):
        route_plan = SimpleNamespace(
            route_geometry=[
                (Decimal("32.7767"), Decimal("-96.7970")),
                (Decimal("39.7392"), Decimal("-104.9903")),
            ],
            total_distance_miles=Decimal("700.125"),
            total_duration_minutes=Decimal("650.0"),
        )
        nearby_stations = [object()]
        fuel_plan = object()
        route_service = Mock()
        route_service.plan_route.return_value = route_plan
        filtering_service = Mock()
        filtering_service.find_nearby_stations.return_value = nearby_stations
        optimization_service = Mock()
        optimization_service.optimize_fuel_stops.return_value = fuel_plan
        try:
            service = FuelPlanService(
                route_service=route_service,
                filtering_service=filtering_service,
                optimization_service=optimization_service,
            )
        except TypeError as exc:
            self.fail(f"FuelPlanService does not accept workflow dependencies: {exc}")

        result = service.create_plan("Dallas, TX", "Denver, CO")

        route_service.plan_route.assert_called_once_with("Dallas, TX", "Denver, CO")
        filtering_service.find_nearby_stations.assert_called_once_with(
            route_geometry=route_plan.route_geometry,
            max_distance_m=8046.72,
        )
        optimization_route, optimization_stations = (
            optimization_service.optimize_fuel_stops.call_args.args
        )
        self.assertEqual(optimization_route.distance_miles, Decimal("700.125"))
        self.assertEqual(optimization_route.duration_minutes, Decimal("650.0"))
        self.assertEqual(optimization_route.coordinates, route_plan.route_geometry)
        self.assertIs(optimization_stations, nearby_stations)
        self.assertIs(result.route_plan, route_plan)
        self.assertIs(result.fuel_plan, fuel_plan)
