"""Cost-aware fuel-stop optimization for the assignment's fixed vehicle."""

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import List, Optional

from api.models import FuelStation
from api.services.routing_service import RouteGeometry
from api.services.station_filtering_service import NearbyStation


VEHICLE_RANGE_MILES = 500
VEHICLE_MPG = 10
VEHICLE_TANK_GALLONS = 50
METERS_PER_MILE = Decimal("1609.344")
MILES_PER_METER = Decimal("0.000621371192237334")
GALLON_QUANTUM = Decimal("0.001")
MONEY_QUANTUM = Decimal("0.01")
VEHICLE_RANGE_METERS = float(Decimal(VEHICLE_RANGE_MILES) * METERS_PER_MILE)


class RouteGapTooLargeError(Exception):
    """Raised when no sequence of stations can bridge the route."""

    def __init__(self, gap_miles: float | Decimal, location: str):
        self.gap_miles = float(gap_miles)
        self.location = location
        super().__init__(
            f"Route gap too large: {self.gap_miles:.1f} miles exceeds "
            f"vehicle range of {VEHICLE_RANGE_MILES} miles at {location}"
        )


@dataclass
class FuelStop:
    station: FuelStation
    route_progress_m: Decimal
    gallons_purchased: Decimal
    cost_usd: Decimal

    @property
    def route_progress_miles(self) -> Decimal:
        return (
            Decimal(str(self.route_progress_m)) * MILES_PER_METER
        ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


@dataclass
class FuelPlan:
    route_geometry: RouteGeometry
    fuel_stops: List[FuelStop]
    total_fuel_purchased: Decimal
    total_cost_usd: Decimal
    vehicle_assumptions: dict


class FuelOptimizationService:
    """Choose reachable fuel stops and calculate purchases using Decimal."""

    def __init__(self):
        self.range_miles = VEHICLE_RANGE_MILES
        self.mpg = VEHICLE_MPG
        self.tank_gallons = VEHICLE_TANK_GALLONS
        self.range_meters = VEHICLE_RANGE_METERS
        self._tank = Decimal(VEHICLE_TANK_GALLONS)
        self._mpg = Decimal(VEHICLE_MPG)
        self._range = Decimal(VEHICLE_RANGE_MILES)

    def optimize_fuel_stops(
        self,
        route_geometry: RouteGeometry,
        nearby_stations: List[NearbyStation],
    ) -> FuelPlan:
        destination = Decimal(route_geometry.distance_miles)
        if destination <= self._range:
            return self._plan(route_geometry, [])

        stations = self._normalize_stations(nearby_stations, destination)
        self._validate_station_gaps(stations, destination)

        stops: list[FuelStop] = []
        position = Decimal("0")
        fuel = self._tank

        # The initial tank is already full. Pick the least expensive station
        # reachable with that fuel; equal prices favor more forward progress.
        reachable = self._reachable(stations, position, fuel * self._mpg)
        if not reachable:
            raise RouteGapTooLargeError(destination, "entire route (no stations available)")
        current = min(
            reachable,
            key=lambda item: (item[1].station.price_per_gallon, -item[0]),
        )

        while True:
            station_miles, nearby = current
            fuel = self._arrival_fuel(fuel, station_miles - position)
            position = station_miles

            target = self._next_target(stations, current, destination)
            target_miles = destination if target is None else target[0]
            gallons_needed = (target_miles - position) / self._mpg
            current_price = Decimal(nearby.station.price_per_gallon)
            should_fill_tank = (
                target is not None
                and Decimal(target[1].station.price_per_gallon) >= current_price
            )
            if should_fill_tank:
                purchase = self._tank - fuel
            else:
                purchase = max(Decimal("0"), gallons_needed - fuel)
            purchase = min(purchase, self._tank - fuel).quantize(
                GALLON_QUANTUM,
                rounding=ROUND_HALF_UP,
            )

            if purchase > 0:
                price = Decimal(nearby.station.price_per_gallon)
                cost = (purchase * price).quantize(
                    MONEY_QUANTUM,
                    rounding=ROUND_HALF_UP,
                )
                stops.append(
                    FuelStop(
                        station=nearby.station,
                        route_progress_m=Decimal(str(nearby.route_progress_m)),
                        gallons_purchased=purchase,
                        cost_usd=cost,
                    )
                )
                fuel += purchase

            if target is None:
                break
            current = target

        return self._plan(route_geometry, stops)

    def _normalize_stations(
        self,
        stations: List[NearbyStation],
        destination: Decimal,
    ) -> list[tuple[Decimal, NearbyStation]]:
        """Drop non-route nodes and collapse duplicate progress deterministically."""
        by_progress: dict[Decimal, NearbyStation] = {}
        for station in stations:
            progress = Decimal(str(station.route_progress_m)) * MILES_PER_METER
            if progress <= 0 or progress >= destination:
                continue
            existing = by_progress.get(progress)
            candidate_key = (station.station.price_per_gallon, str(station.station.pk))
            if existing is None or candidate_key < (
                existing.station.price_per_gallon,
                str(existing.station.pk),
            ):
                by_progress[progress] = station
        return sorted(by_progress.items(), key=lambda item: item[0])

    def _validate_station_gaps(
        self,
        stations: list[tuple[Decimal, NearbyStation]],
        destination: Decimal,
    ) -> None:
        previous = Decimal("0")
        for progress, station in stations:
            gap = progress - previous
            if gap > self._range:
                raise RouteGapTooLargeError(
                    gap,
                    f"{station.station.city}, {station.station.state}",
                )
            previous = progress
        final_gap = destination - previous
        if final_gap > self._range:
            raise RouteGapTooLargeError(final_gap, "destination")

    @staticmethod
    def _reachable(
        stations: list[tuple[Decimal, NearbyStation]],
        position: Decimal,
        range_miles: Decimal,
    ) -> list[tuple[Decimal, NearbyStation]]:
        return [
            item
            for item in stations
            if position < item[0] <= position + range_miles
        ]

    def _next_target(
        self,
        stations: list[tuple[Decimal, NearbyStation]],
        current: tuple[Decimal, NearbyStation],
        destination: Decimal,
    ) -> Optional[tuple[Decimal, NearbyStation]]:
        """Return the first cheaper reachable station, else the furthest node."""
        position, nearby = current
        reachable = self._reachable(stations, position, self._range)
        current_price = Decimal(nearby.station.price_per_gallon)
        for candidate in reachable:
            if Decimal(candidate[1].station.price_per_gallon) < current_price:
                return candidate
        if destination <= position + self._range:
            return None
        if not reachable:
            raise RouteGapTooLargeError(destination - position, "destination")
        return reachable[-1]

    def _arrival_fuel(self, fuel: Decimal, distance_miles: Decimal) -> Decimal:
        remaining = fuel - (distance_miles / self._mpg)
        # Quantized purchases can leave a sub-thousandth numerical deficit at
        # the target. It is within the declared fuel precision.
        if remaining < 0 and remaining >= -GALLON_QUANTUM:
            return Decimal("0")
        if remaining < 0:
            raise RouteGapTooLargeError(distance_miles, "current fuel")
        return remaining

    def _plan(self, route_geometry: RouteGeometry, stops: list[FuelStop]) -> FuelPlan:
        total_fuel = sum(
            (stop.gallons_purchased for stop in stops),
            start=Decimal("0.000"),
        )
        total_cost = sum(
            (stop.cost_usd for stop in stops),
            start=Decimal("0.00"),
        )
        return FuelPlan(
            route_geometry=route_geometry,
            fuel_stops=stops,
            total_fuel_purchased=total_fuel,
            total_cost_usd=total_cost,
            vehicle_assumptions=self._get_vehicle_assumptions(),
        )

    def _get_vehicle_assumptions(self) -> dict:
        return {
            "range_miles": self.range_miles,
            "mpg": self.mpg,
            "tank_gallons": self.tank_gallons,
        }

    def calculate_leg_distance(
        self,
        prev_stop: Optional[FuelStop],
        next_stop: Optional[FuelStop],
    ) -> float:
        if next_stop is None:
            raise ValueError("next_stop cannot be None for distance calculation")
        previous = Decimal("0") if prev_stop is None else Decimal(str(prev_stop.route_progress_m))
        return float(Decimal(str(next_stop.route_progress_m)) - previous)
