"""CSV parsing and canonical fuel-station import."""

import csv
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from django.db import transaction

from api.models import FuelStation


REQUIRED_HEADERS = (
    "OPIS Truckstop ID",
    "Truckstop Name",
    "Address",
    "City",
    "State",
    "Rack ID",
    "Retail Price",
)
CENT = Decimal("0.01")


@dataclass(frozen=True)
class CanonicalStation:
    opis_truckstop_id: str
    rack_id: str
    name: str
    address: str
    city: str
    state: str
    price_per_gallon: Decimal


@dataclass(frozen=True)
class ParsedFuelPrices:
    stations: tuple[CanonicalStation, ...]
    total_rows: int
    collapsed_rows: int


@dataclass(frozen=True)
class ImportResult:
    total_rows: int
    collapsed_rows: int
    created: int
    updated: int


class ImportService:
    """Parse the assessment CSV and upsert one station per OPIS ID."""

    @staticmethod
    def parse_csv(file):
        reader = csv.DictReader(file)
        headers = reader.fieldnames or []
        missing_headers = [header for header in REQUIRED_HEADERS if header not in headers]
        if missing_headers:
            raise ValueError(
                f"Missing required CSV headers: {', '.join(missing_headers)}"
            )

        groups = {}
        total_rows = 0

        for row_number, row in enumerate(reader, start=2):
            total_rows += 1
            normalized = {
                header: ImportService._normalize(row.get(header))
                for header in REQUIRED_HEADERS
            }
            blank_fields = [
                header for header, value in normalized.items() if not value
            ]
            if blank_fields:
                raise ValueError(
                    f"Row {row_number} has blank fields: {', '.join(blank_fields)}"
                )

            opis_id = normalized["OPIS Truckstop ID"]
            state = normalized["State"].upper()
            if len(state) != 2 or not state.isalpha():
                raise ValueError(f"Row {row_number} has invalid State: {state}")

            try:
                price = Decimal(normalized["Retail Price"])
            except InvalidOperation as exc:
                raise ValueError(
                    f"Row {row_number} has invalid Retail Price: "
                    f"{normalized['Retail Price']}"
                ) from exc
            if price <= 0:
                raise ValueError(
                    f"Row {row_number} has invalid Retail Price: {price}"
                )

            stable_values = {
                "Rack ID": normalized["Rack ID"],
                "Address": normalized["Address"],
                "City": normalized["City"],
                "State": state,
            }
            group = groups.get(opis_id)
            if group is None:
                groups[opis_id] = {
                    **stable_values,
                    "names": {normalized["Truckstop Name"]},
                    "prices": [price],
                }
                continue

            for field_name, value in stable_values.items():
                if group[field_name] != value:
                    raise ValueError(
                        f"OPIS {opis_id} has conflicting {field_name} values"
                    )
            group["names"].add(normalized["Truckstop Name"])
            group["prices"].append(price)

        stations = []
        for opis_id, group in groups.items():
            name = max(
                group["names"],
                key=lambda candidate: (len(candidate), candidate.casefold()),
            )
            median_price = ImportService._median(group["prices"]).quantize(
                CENT,
                rounding=ROUND_HALF_UP,
            )
            stations.append(
                CanonicalStation(
                    opis_truckstop_id=opis_id,
                    rack_id=group["Rack ID"],
                    name=name,
                    address=group["Address"],
                    city=group["City"],
                    state=group["State"],
                    price_per_gallon=median_price,
                )
            )

        stations.sort(key=lambda station: station.opis_truckstop_id)
        return ParsedFuelPrices(
            stations=tuple(stations),
            total_rows=total_rows,
            collapsed_rows=total_rows - len(stations),
        )

    @staticmethod
    @transaction.atomic
    def bulk_import(data):
        created_count = 0
        updated_count = 0

        for station in data.stations:
            _, created = FuelStation.objects.update_or_create(
                id=station.opis_truckstop_id,
                defaults={
                    "opis_truckstop_id": station.opis_truckstop_id,
                    "rack_id": station.rack_id,
                    "name": station.name,
                    "address": station.address,
                    "city": station.city,
                    "state": station.state,
                    "price_per_gallon": station.price_per_gallon,
                },
            )
            if created:
                created_count += 1
            else:
                updated_count += 1

        return ImportResult(
            total_rows=data.total_rows,
            collapsed_rows=data.collapsed_rows,
            created=created_count,
            updated=updated_count,
        )

    @staticmethod
    def _normalize(value):
        return " ".join((value or "").strip().split())

    @staticmethod
    def _median(values):
        ordered = sorted(values)
        midpoint = len(ordered) // 2
        if len(ordered) % 2:
            return ordered[midpoint]
        return (ordered[midpoint - 1] + ordered[midpoint]) / 2
