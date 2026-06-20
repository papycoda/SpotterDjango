from dataclasses import dataclass

from api.services.fuel_optimization_service import FuelOptimizationService, FuelPlan
from api.services.route_service import RoutePlan, RouteService
from api.services.routing_service import RouteGeometry
from api.services.station_filtering_service import StationFilteringService


@dataclass(frozen=True)
class FuelPlanResult:
    route_plan: RoutePlan
    fuel_plan: FuelPlan


class FuelPlanService:
    """Orchestrate route acquisition, local matching, and fuel optimization."""

    def __init__(
        self,
        *,
        route_service=None,
        filtering_service=None,
        optimization_service=None,
    ):
        self.route_service = route_service or RouteService()
        self.filtering_service = filtering_service or StationFilteringService()
        self.optimization_service = optimization_service or FuelOptimizationService()

    def create_plan(self, start: str, finish: str) -> FuelPlanResult:
        route_plan = self.route_service.plan_route(start, finish)
        nearby_stations = self.filtering_service.find_nearby_stations(
            route_geometry=route_plan.route_geometry,
            max_distance_m=1000,
        )
        route_geometry = RouteGeometry(
            distance_miles=route_plan.total_distance_miles,
            duration_minutes=route_plan.total_duration_minutes,
            coordinates=route_plan.route_geometry,
        )
        fuel_plan = self.optimization_service.optimize_fuel_stops(
            route_geometry,
            nearby_stations,
        )
        return FuelPlanResult(route_plan=route_plan, fuel_plan=fuel_plan)
