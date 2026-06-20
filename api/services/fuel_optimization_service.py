"""Cost-aware fuel-stop optimization for the assignment's fixed vehicle."""

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import List, Optional, Tuple

from api.models import FuelStation
from api.services.routing_service import RouteGeometry
from api.services.station_filtering_service import NearbyStation


# Vehicle assumptions
VEHICLE_RANGE_MILES = 500
VEHICLE_MPG = 10
VEHICLE_TANK_GALLONS = 50

# Unit conversions
METERS_PER_MILE = Decimal("1609.344")
MILES_PER_METER = Decimal("0.000621371192237334")

# Precision constants
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


@dataclass(frozen=True)
class StationProgress:
    miles: Decimal
    nearby_station: NearbyStation


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
        
        # If destination is already reachable, no stops needed
        if destination <= self._range:
            return self._plan(route_geometry, [])

        # Normalize stations (drop start/end, collapse duplicates, sort)
        normalized_stations = self._normalize_stations(nearby_stations, destination)
        
        # Validate all gaps are crossable
        self._validate_station_gaps(normalized_stations, destination)

        stops: List[FuelStop] = []
        current_position = Decimal("0")
        current_fuel = self._tank
        search_start_index = 0

        # Pick initial station: cheapest in reach, tie breaks favor further progress
        initial_reachable, search_start_index = self._find_reachable(
            normalized_stations,
            current_position,
            current_fuel * self._mpg,
            search_start_index
        )
        
        if not initial_reachable:
            raise RouteGapTooLargeError(destination, "entire route (no stations available)")
        
        current_station = min(
            initial_reachable,
            key=lambda station: (station.nearby_station.station.price_per_gallon, -station.miles),
        )

        while True:
            # Move to current station
            distance_to_station = current_station.miles - current_position
            current_fuel = self._calculate_arrival_fuel(current_fuel, distance_to_station)
            current_position = current_station.miles

            # Determine next stop
            target_station, search_start_index = self._find_next_target(
                normalized_stations,
                current_station,
                destination,
                search_start_index
            )

            # Calculate purchase amount
            target_miles = destination if target_station is None else target_station.miles
            gallons_needed = (target_miles - current_position) / self._mpg
            current_price = Decimal(current_station.nearby_station.station.price_per_gallon)
            should_fill_tank = (
                target_station is not None
                and Decimal(target_station.nearby_station.station.price_per_gallon) >= current_price
            )
            
            if should_fill_tank:
                purchase_amount = self._tank - current_fuel
            else:
                purchase_amount = max(Decimal("0"), gallons_needed - current_fuel)
                
            purchase_amount = min(purchase_amount, self._tank - current_fuel).quantize(
                GALLON_QUANTUM,
                rounding=ROUND_HALF_UP,
            )

            # Record purchase if we bought fuel
            if purchase_amount > 0:
                cost = (purchase_amount * current_price).quantize(
                    MONEY_QUANTUM,
                    rounding=ROUND_HALF_UP,
                )
                stops.append(
                    FuelStop(
                        station=current_station.nearby_station.station,
                        route_progress_m=Decimal(str(current_station.nearby_station.route_progress_m)),
                        gallons_purchased=purchase_amount,
                        cost_usd=cost,
                    )
                )
                current_fuel += purchase_amount

            # If no next target, we're done
            if target_station is None:
                break
                
            # Move to target station
            current_station = target_station

        return self._plan(route_geometry, stops)

    def _normalize_stations(
        self,
        stations: List[NearbyStation],
        destination: Decimal,
    ) -> List[StationProgress]:
        """Drop non-route nodes and collapse duplicate progress deterministically."""
        by_progress: dict[Decimal, NearbyStation] = {}
        
        for station in stations:
            progress = Decimal(str(station.route_progress_m)) * MILES_PER_METER
            
            # Skip stations at or before start, at or beyond destination
            if progress <= 0 or progress >= destination:
                continue
                
            existing = by_progress.get(progress)
            candidate_key = (station.station.price_per_gallon, str(station.station.pk))
            
            if existing is None or candidate_key < (
                existing.station.price_per_gallon,
                str(existing.station.pk),
            ):
                by_progress[progress] = station
        
        sorted_stations = sorted(by_progress.items(), key=lambda item: item[0])
        return [StationProgress(miles, station) for miles, station in sorted_stations]

    def _validate_station_gaps(
        self,
        stations: List[StationProgress],
        destination: Decimal,
    ) -> None:
        previous = Decimal("0")
        
        for station in stations:
            gap = station.miles - previous
            if gap > self._range:
                raise RouteGapTooLargeError(
                    gap,
                    f"{station.nearby_station.station.city}, {station.nearby_station.station.state}",
                )
            previous = station.miles
            
        # Validate final gap to destination
        final_gap = destination - previous
        if final_gap > self._range:
            raise RouteGapTooLargeError(final_gap, "destination")

    def _find_reachable(
        self,
        stations: List[StationProgress],
        current_position: Decimal,
        range_miles: Decimal,
        start_index: int = 0,
    ) -> Tuple[List[StationProgress], int]:
        """Find all stations within reach from current position."""
        max_reachable = current_position + range_miles
        reachable: List[StationProgress] = []
        end_index = start_index
        
        # Since stations are sorted by progress, we can stop once we exceed max
        for i in range(start_index, len(stations)):
            station = stations[i]
            if station.miles <= current_position:
                continue
            if station.miles > max_reachable:
                break
            reachable.append(station)
            end_index = i
            
        return reachable, end_index

    def _find_next_target(
        self,
        stations: List[StationProgress],
        current_station: StationProgress,
        destination: Decimal,
        start_index: int = 0,
    ) -> Tuple[Optional[StationProgress], int]:
        """Return the first cheaper reachable station, else the furthest node."""
        reachable_stations, search_end_index = self._find_reachable(
            stations,
            current_station.miles,
            self._range,
            start_index
        )
        current_price = Decimal(current_station.nearby_station.station.price_per_gallon)

        # First, look for first cheaper station in reach
        for candidate in reachable_stations:
            if Decimal(candidate.nearby_station.station.price_per_gallon) < current_price:
                return candidate, search_end_index

        # If no cheaper station, check if destination is reachable
        if destination <= current_station.miles + self._range:
            return None, search_end_index

        # Otherwise, go to furthest possible station
        if not reachable_stations:
            raise RouteGapTooLargeError(
                destination - current_station.miles, "destination"
            )
        return reachable_stations[-1], search_end_index

    def _calculate_arrival_fuel(self, fuel: Decimal, distance_miles: Decimal) -> Decimal:
        remaining = fuel - (distance_miles / self._mpg)
        # Quantized purchases can leave a sub-thousandth numerical deficit
        if remaining < 0 and remaining >= -GALLON_QUANTUM:
            return Decimal("0")
        if remaining < 0:
            raise RouteGapTooLargeError(distance_miles, "current fuel")
        return remaining

    def _plan(self, route_geometry: RouteGeometry, stops: List[FuelStop]) -> FuelPlan:
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
