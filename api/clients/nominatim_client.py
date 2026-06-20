"""Bounded Nominatim adapter for conservative US fuel-station geocoding."""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import re

import requests
from django.conf import settings

COORDINATE_PRECISION = Decimal("0.0000001")
CURRENT_STRATEGY_VERSION = 1
LOCALITY_FIELDS = ("city", "town", "village", "hamlet", "municipality")


class NominatimError(Exception):
    """Base sanitized Nominatim failure."""

    def __init__(self, reason):
        self.reason = reason
        super().__init__(reason)


class NominatimTransientError(NominatimError):
    """A network or upstream condition that can be retried."""


class NominatimPermanentError(NominatimError):
    """A request or response that should not be retried."""


class NominatimNoMatchError(NominatimPermanentError):
    """No candidate safely identifies the requested station."""


@dataclass(frozen=True)
class GeocodingResult:
    latitude: Decimal
    longitude: Decimal
    display_name: str
    stage: int
    confidence: str


@dataclass(frozen=True)
class GeocodingFailure:
    """Legacy result contract retained for backwards-compatible service tests."""

    reason: str
    transient: bool = False


class NominatimClient:
    """Search Nominatim without accepting locality-only station guesses."""

    REQUEST_PARAMS = {
        "countrycodes": "us",
        "format": "jsonv2",
        "addressdetails": 1,
        "namedetails": 1,
        "extratags": 1,
        "limit": 10,
    }

    def __init__(self, *, session=None, rate_limiter=None):
        self.session = session or requests.Session()
        if rate_limiter is None:
            # Import lazily so the client does not depend on eager service-package
            # exports while this module itself is still initializing.
            from api.services.geocoding_rate_limiter import DatabaseRateLimiter

            rate_limiter = DatabaseRateLimiter()
        self.rate_limiter = rate_limiter

    def geocode(self, *, name, address, city, state):
        station_name = self._normalize_station_name(name)
        queries = (
            f"{station_name}, {address}, {city}, {state.upper()}, USA",
            f"{station_name}, {city}, {state.upper()}, USA",
        )
        best_reason = None

        for stage, query in enumerate(queries, start=1):
            payload = self._search(query)
            stage_reason = None
            for candidate in payload:
                reason = self._candidate_failure_reason(
                    candidate,
                    expected_name=station_name,
                    expected_city=city,
                    expected_state=state,
                )
                if reason is None:
                    confidence = (
                        "high"
                        if self._has_address_evidence(candidate, address)
                        else "medium"
                    )
                    return self._build_result(candidate, stage, confidence)
                stage_reason = self._prefer_reason(stage_reason, reason)
            best_reason = self._prefer_reason(best_reason, stage_reason)

        raise NominatimNoMatchError(best_reason or "no_match_osm")

    def _search(self, query):
        self.rate_limiter.acquire()
        try:
            response = self.session.get(
                f"{settings.NOMINATIM_BASE_URL}/search",
                params={"q": query, **self.REQUEST_PARAMS},
                headers={"User-Agent": settings.NOMINATIM_USER_AGENT},
                timeout=settings.NOMINATIM_TIMEOUT_SECONDS,
            )
        except requests.RequestException as exc:
            raise NominatimTransientError("network_error") from exc

        if response.status_code == 429:
            raise NominatimTransientError("rate_limited")
        if response.status_code >= 500:
            raise NominatimTransientError("upstream_error")
        if response.status_code != 200:
            raise NominatimPermanentError("upstream_error")
        try:
            payload = response.json()
        except (TypeError, ValueError) as exc:
            raise NominatimPermanentError("invalid_response") from exc
        if not isinstance(payload, list):
            raise NominatimPermanentError("invalid_response")
        return payload

    @classmethod
    def _candidate_failure_reason(
        cls, candidate, *, expected_name, expected_city, expected_state
    ):
        if not isinstance(candidate, dict):
            return "no_match_osm"
        address = candidate.get("address")
        if not isinstance(address, dict):
            return "no_match_osm"

        iso_state = str(address.get("ISO3166-2-lvl4", "")).upper()
        if iso_state and not iso_state.startswith("US-"):
            return "outside_usa"
        if iso_state != f"US-{expected_state.upper()}":
            return "state_mismatch"

        localities = {
            cls._normalize_text(address.get(field, ""))
            for field in LOCALITY_FIELDS
            if address.get(field)
        }
        if cls._normalize_text(expected_city) not in localities:
            return "city_mismatch"
        if not cls._has_fuel_evidence(candidate):
            return "not_fuel_station"
        if not cls._has_identity_evidence(candidate, expected_name):
            return "no_match_osm"
        return None

    @staticmethod
    def _has_fuel_evidence(candidate):
        category = candidate.get("category")
        feature_type = candidate.get("type")
        extras = candidate.get("extratags")
        extras = extras if isinstance(extras, dict) else {}
        if (category, feature_type) == ("amenity", "fuel"):
            return True
        if (category, feature_type) == ("highway", "services"):
            return True
        if (category, feature_type) == ("shop", "convenience"):
            return extras.get("amenity") == "fuel" or extras.get("fuel") == "yes"
        return False

    @classmethod
    def _has_identity_evidence(cls, candidate, expected_name):
        namedetails = candidate.get("namedetails")
        namedetails = namedetails if isinstance(namedetails, dict) else {}
        extras = candidate.get("extratags")
        extras = extras if isinstance(extras, dict) else {}
        identities = [
            namedetails.get("name"),
            namedetails.get("brand"),
            namedetails.get("operator"),
            extras.get("brand"),
            extras.get("operator"),
        ]
        normalized_expected = cls._normalize_identity(expected_name)
        if not normalized_expected:
            return False
        for identity in identities:
            normalized_identity = cls._normalize_identity(identity)
            if normalized_identity and (
                normalized_identity in normalized_expected
                or normalized_expected in normalized_identity
            ):
                return True
        return False

    @classmethod
    def _has_address_evidence(cls, candidate, expected_address):
        address = candidate.get("address")
        if not isinstance(address, dict) or not expected_address:
            return False
        candidate_address = " ".join(
            str(address.get(field, ""))
            for field in ("house_number", "road", "highway", "exit")
        )
        expected = cls._normalize_address(expected_address)
        actual = cls._normalize_address(candidate_address)
        if not expected or not actual:
            return False
        expected_tokens = set(expected.split())
        actual_tokens = set(actual.split())
        return expected == actual or (
            len(expected_tokens) >= 2 and expected_tokens.issubset(actual_tokens)
        )

    @staticmethod
    def _prefer_reason(current, candidate):
        if candidate is None:
            return current
        priority = {
            "outside_usa": 5,
            "state_mismatch": 4,
            "city_mismatch": 3,
            "not_fuel_station": 2,
            "no_match_osm": 1,
        }
        if current is None or priority.get(candidate, 0) > priority.get(current, 0):
            return candidate
        return current

    @staticmethod
    def _build_result(candidate, stage, confidence):
        try:
            latitude = Decimal(str(candidate["lat"]))
            longitude = Decimal(str(candidate["lon"]))
        except (KeyError, InvalidOperation, TypeError, ValueError) as exc:
            raise NominatimPermanentError("invalid_coordinates") from exc
        if not Decimal("-90") <= latitude <= Decimal("90"):
            raise NominatimPermanentError("invalid_coordinates")
        if not Decimal("-180") <= longitude <= Decimal("180"):
            raise NominatimPermanentError("invalid_coordinates")
        return GeocodingResult(
            latitude=latitude.quantize(COORDINATE_PRECISION),
            longitude=longitude.quantize(COORDINATE_PRECISION),
            display_name=str(candidate.get("display_name", "")),
            stage=stage,
            confidence=confidence,
        )

    @classmethod
    def _normalize_identity(cls, value):
        value = cls._normalize_text(value).replace("loves", "love")
        words = [
            word
            for word in value.split()
            if word not in {"travel", "center", "centers", "stop", "station"}
        ]
        return " ".join(words)

    @classmethod
    def _normalize_address(cls, value):
        value = cls._normalize_text(value)
        replacements = {
            "street": "st",
            "road": "rd",
            "avenue": "ave",
            "highway": "hwy",
            "interstate": "i",
        }
        return " ".join(replacements.get(word, word) for word in value.split())

    @staticmethod
    def _normalize_text(value):
        return " ".join(re.findall(r"[a-z0-9]+", str(value or "").casefold()))

    @staticmethod
    def _normalize_station_name(name):
        normalized = " ".join((name or "").strip().split())
        normalized = re.sub(
            r"\bPILOT TRAVEL CENTERS\b",
            "Pilot Travel Center",
            normalized,
            flags=re.IGNORECASE,
        )
        normalized = re.sub(r"\bLOVES\b", "Love's", normalized, flags=re.IGNORECASE)
        normalized = re.sub(r"\s*#\s*\d+\b", "", normalized)
        return " ".join(normalized.split()).strip(" ,- ")
