from dataclasses import dataclass

from django.conf import settings

from api.services.fuel_optimization_service import FuelOptimizationService, FuelPlan
from api.services.route_service import RoutePlan, RouteService
from api.services.routing_service import RouteGeometry
from api.services.station_filtering_service import StationFilteringService
from api.utils.geometry_utils import simplify_for_station_filtering


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

        # Simplify geometry for station filtering (reduces 9000+ points to ~300)
        # Full geometry is preserved for the response
        simplified_geometry = simplify_for_station_filtering(
            route_plan.route_geometry,
            target_point_count=300
        )

        nearby_stations = self.filtering_service.find_nearby_stations(
            route_geometry=simplified_geometry,
            max_distance_m=settings.FUEL_ROUTE_CORRIDOR_MILES * 1609.344,
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
