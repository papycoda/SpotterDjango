from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

import requests
from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db import transaction

from api.models import CityCoordinate, FuelStation


@dataclass
class FetchResult:
    city: str
    state: str
    latitude: float
    longitude: float
    display_name: str = ""


@dataclass
class FetchFailure:
    city: str
    state: str
    reason: str


class Command(BaseCommand):
    help = (
        "Fetch city centroid coordinates from Nominatim for unique "
        "FuelStation city/state pairs. Saves progress incrementally."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--limit",
            type=int,
            default=1000,
            help="Maximum number of missing cities to process in one run.",
        )
        parser.add_argument(
            "--no-enrich",
            action="store_true",
            help="Do not automatically enrich fuel stations after fetching cities.",
        )
        parser.add_argument(
            "--sleep",
            type=float,
            default=None,
            help=(
                "Seconds to wait between Nominatim requests. Defaults to "
                "settings.NOMINATIM_MIN_INTERVAL_SECONDS or 1.0."
            ),
        )

    def handle(self, *args, **options):
        limit = options["limit"]
        no_enrich = options["no_enrich"]
        sleep_seconds = options["sleep"]

        if limit <= 0:
            self.stderr.write(self.style.ERROR("--limit must be greater than 0"))
            return

        if sleep_seconds is None:
            sleep_seconds = float(
                getattr(settings, "NOMINATIM_MIN_INTERVAL_SECONDS", 1.0)
            )

        if sleep_seconds < 0:
            self.stderr.write(self.style.ERROR("--sleep cannot be negative"))
            return

        self.stdout.write("Collecting city/state pairs...")

        existing_keys = self._existing_city_keys()
        fuel_station_pairs = self._fuel_station_city_state_pairs()

        missing_pairs = [
            pair
            for pair in fuel_station_pairs
            if self._key(pair[0], pair[1]) not in existing_keys
        ]

        to_process = missing_pairs[:limit]

        self.stdout.write(f"Total unique city/state pairs: {len(fuel_station_pairs)}")
        self.stdout.write(f"Already cached: {len(existing_keys)}")
        self.stdout.write(f"Missing: {len(missing_pairs)}")
        self.stdout.write(f"Processing this run: {len(to_process)}")

        if not to_process:
            self.stdout.write(self.style.SUCCESS("\nNo new cities to fetch."))
            if not no_enrich:
                self._enrich_stations()
            return

        saved_count = 0
        failed: list[FetchFailure] = []

        session = requests.Session()
        interrupted = False

        try:
            for index, (city, state) in enumerate(to_process, start=1):
                label = f"[{index}/{len(to_process)}] {city}, {state}"

                # Another process/run may have inserted it after we built the list.
                if self._city_exists(city, state):
                    self.stdout.write(f"{label} -> already cached, skipped")
                    continue

                result, failure_reason = self._fetch_city(
                    session=session,
                    city=city,
                    state=state,
                )

                if result is None:
                    failed.append(FetchFailure(city=city, state=state, reason=failure_reason))
                    self.stdout.write(
                        self.style.WARNING(f"{label} -> failed: {failure_reason}")
                    )
                else:
                    self._save_city(result)
                    saved_count += 1
                    self.stdout.write(
                        self.style.SUCCESS(
                            f"{label} -> saved ({result.latitude}, {result.longitude})"
                        )
                    )

                if index < len(to_process) and sleep_seconds:
                    time.sleep(sleep_seconds)

        except KeyboardInterrupt:
            interrupted = True
            self.stdout.write("")
            self.stdout.write(
                self.style.WARNING(
                    "Interrupted by user. Already-saved coordinates were kept."
                )
            )

        remaining_missing = self._remaining_missing_count()

        self.stdout.write("\n" + "=" * 60)
        self.stdout.write("City coordinate fetch summary")
        self.stdout.write("=" * 60)
        self.stdout.write(f"Attempted this run: {saved_count + len(failed)}")
        self.stdout.write(f"Saved this run: {saved_count}")
        self.stdout.write(f"Failed this run: {len(failed)}")
        self.stdout.write(f"Remaining missing: {remaining_missing}")

        if failed:
            self.stdout.write("\nFailed city/state pairs:")
            for item in failed[:100]:
                self.stdout.write(f"  - {item.city}, {item.state}: {item.reason}")
            if len(failed) > 100:
                self.stdout.write(f"  ... and {len(failed) - 100} more")

        if interrupted:
            return

        if not no_enrich:
            self._enrich_stations()

    def _existing_city_keys(self) -> set[tuple[str, str]]:
        return {
            self._key(city, state)
            for city, state in CityCoordinate.objects.values_list("city", "state")
        }

    def _fuel_station_city_state_pairs(self) -> list[tuple[str, str]]:
        pairs = set()

        queryset = (
            FuelStation.objects.exclude(city__isnull=True)
            .exclude(city="")
            .exclude(state__isnull=True)
            .exclude(state="")
            .values_list("city", "state")
            .distinct()
        )

        for city, state in queryset.iterator():
            normalized_city = self._clean_city(city)
            normalized_state = self._clean_state(state)
            if normalized_city and normalized_state:
                pairs.add((normalized_city, normalized_state))

        return sorted(pairs, key=lambda item: (item[1], item[0]))

    def _city_exists(self, city: str, state: str) -> bool:
        return CityCoordinate.objects.filter(
            city__iexact=self._clean_city(city),
            state__iexact=self._clean_state(state),
        ).exists()

    def _remaining_missing_count(self) -> int:
        existing = self._existing_city_keys()
        return sum(
            1
            for city, state in self._fuel_station_city_state_pairs()
            if self._key(city, state) not in existing
        )

    def _fetch_city(
        self,
        *,
        session: requests.Session,
        city: str,
        state: str,
    ) -> tuple[Optional[FetchResult], str]:
        nominatim_url = settings.NOMINATIM_BASE_URL.rstrip("/")

        params = {
            "q": f"{city}, {state}, USA",
            "format": "jsonv2",
            "limit": 1,
            "countrycodes": "us",
            "addressdetails": 1,
        }

        headers = {
            "User-Agent": settings.NOMINATIM_USER_AGENT,
        }

        try:
            response = session.get(
                f"{nominatim_url}/search",
                params=params,
                headers=headers,
                timeout=settings.NOMINATIM_TIMEOUT_SECONDS,
            )
        except requests.Timeout:
            return None, "timeout"
        except requests.RequestException as exc:
            return None, f"network_error: {exc.__class__.__name__}"

        if response.status_code == 429:
            return None, "rate_limited"
        if response.status_code >= 500:
            return None, f"upstream_error_{response.status_code}"
        if response.status_code != 200:
            return None, f"http_{response.status_code}"

        try:
            payload = response.json()
        except ValueError:
            return None, "invalid_json"

        if not isinstance(payload, list) or not payload:
            return None, "no_match"

        first = payload[0]
        try:
            latitude = float(first["lat"])
            longitude = float(first["lon"])
        except (KeyError, TypeError, ValueError):
            return None, "invalid_coordinates"

        if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
            return None, "coordinates_out_of_range"

        return (
            FetchResult(
                city=self._clean_city(city),
                state=self._clean_state(state),
                latitude=latitude,
                longitude=longitude,
                display_name=str(first.get("display_name", "")),
            ),
            "",
        )

    @transaction.atomic
    def _save_city(self, result: FetchResult) -> None:
        CityCoordinate.objects.update_or_create(
            city=result.city,
            state=result.state,
            defaults={
                "latitude": result.latitude,
                "longitude": result.longitude,
                "source": "Nominatim",
            },
        )

    def _enrich_stations(self) -> None:
        self.stdout.write("\n" + "=" * 60)
        self.stdout.write("Enriching fuel stations with city coordinates...")
        call_command("enrich_fuel_stations_with_city_coordinates")

    @staticmethod
    def _clean_city(value: str) -> str:
        return " ".join(str(value or "").strip().split()).title()

    @staticmethod
    def _clean_state(value: str) -> str:
        return str(value or "").strip().upper()

    @classmethod
    def _key(cls, city: str, state: str) -> tuple[str, str]:
        return (cls._clean_city(city).casefold(), cls._clean_state(state))