"""Bounded Nominatim adapter for US fuel-station geocoding."""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import re

import requests
from django.conf import settings

from api.services.geocoding_rate_limiter import DatabaseRateLimiter


COORDINATE_PRECISION = Decimal("0.0000001")


class NominatimError(Exception):
    """Base sanitized Nominatim failure."""


class NominatimTransientError(NominatimError):
    """A network or upstream condition that can be retried."""


class NominatimPermanentError(NominatimError):
    """A request or response that should not be retried."""


@dataclass(frozen=True)
class GeocodingResult:
    latitude: Decimal
    longitude: Decimal
    display_name: str


class NominatimClient:
    STATION_FEATURES = {
        ("amenity", "fuel"),
        ("highway", "services"),
        ("shop", "convenience"),
    }

    def __init__(self, *, session=None, rate_limiter=None):
        self.session = session or requests.Session()
        self.rate_limiter = rate_limiter or DatabaseRateLimiter()

    def geocode(self, *, name, address, city, state):
        search_name = self._normalize_station_name(name)
        self.rate_limiter.acquire()
        try:
            response = self.session.get(
                f"{settings.NOMINATIM_BASE_URL}/search",
                params={
                    "q": f"{search_name}, {city}, {state.upper()}, USA",
                    "countrycodes": "us",
                    "format": "jsonv2",
                    "addressdetails": 1,
                    "limit": 5,
                },
                headers={"User-Agent": settings.NOMINATIM_USER_AGENT},
                timeout=settings.NOMINATIM_TIMEOUT_SECONDS,
            )
        except requests.RequestException as exc:
            raise NominatimTransientError("Nominatim request failed") from exc

        if response.status_code == 429 or response.status_code >= 500:
            raise NominatimTransientError("Nominatim is temporarily unavailable")
        if response.status_code != 200:
            raise NominatimPermanentError("Nominatim rejected the request")

        try:
            payload = response.json()
        except (TypeError, ValueError) as exc:
            raise NominatimPermanentError("Nominatim returned malformed JSON") from exc
        if not isinstance(payload, list):
            raise NominatimPermanentError("Nominatim returned malformed data")
        if not payload:
            return None

        expected_iso_state = f"US-{state.upper()}"
        matching_results = [
            candidate
            for candidate in payload
            if self._is_station_candidate(candidate, expected_iso_state, city)
        ]
        if not matching_results:
            return None

        result = matching_results[0]
        try:
            latitude = Decimal(str(result["lat"]))
            longitude = Decimal(str(result["lon"]))
        except (KeyError, InvalidOperation, TypeError, ValueError) as exc:
            raise NominatimPermanentError("Nominatim returned invalid coordinates") from exc
        if not Decimal("-90") <= latitude <= Decimal("90"):
            raise NominatimPermanentError("Nominatim returned invalid coordinates")
        if not Decimal("-180") <= longitude <= Decimal("180"):
            raise NominatimPermanentError("Nominatim returned invalid coordinates")

        return GeocodingResult(
            latitude=latitude.quantize(COORDINATE_PRECISION),
            longitude=longitude.quantize(COORDINATE_PRECISION),
            display_name=str(result.get("display_name", "")),
        )

    @classmethod
    def _is_station_candidate(cls, candidate, expected_iso_state, expected_city):
        if not isinstance(candidate, dict):
            return False
        feature = (candidate.get("category"), candidate.get("type"))
        address = candidate.get("address")
        localities = {
            str(address.get(field, "")).strip().casefold()
            for field in ("city", "town", "village", "hamlet", "municipality")
            if address.get(field)
        } if isinstance(address, dict) else set()
        return (
            feature in cls.STATION_FEATURES
            and isinstance(address, dict)
            and address.get("ISO3166-2-lvl4") == expected_iso_state
            and expected_city.strip().casefold() in localities
        )

    @staticmethod
    def _normalize_station_name(name):
        normalized = " ".join((name or "").strip().split())
        normalized = re.sub(
            r"\bPILOT TRAVEL CENTERS\b",
            "Pilot Travel Center",
            normalized,
            flags=re.IGNORECASE,
        )
        normalized = re.sub(
            r"\bLOVES\b",
            "Love's",
            normalized,
            flags=re.IGNORECASE,
        )
        normalized = re.sub(r"\s*#\s*\d+\b", "", normalized)
        return " ".join(normalized.split()).strip(" ,- ")
