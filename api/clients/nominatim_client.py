"""Bounded Nominatim adapter for US fuel-station geocoding."""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

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
    def __init__(self, *, session=None, rate_limiter=None):
        self.session = session or requests.Session()
        self.rate_limiter = rate_limiter or DatabaseRateLimiter()

    def geocode(self, *, address, city, state):
        self.rate_limiter.acquire()
        try:
            response = self.session.get(
                f"{settings.NOMINATIM_BASE_URL}/search",
                params={
                    "street": address,
                    "city": city,
                    "state": state,
                    "countrycodes": "us",
                    "format": "jsonv2",
                    "limit": 1,
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
        if payload == []:
            return None
        if not isinstance(payload, list) or not isinstance(payload[0], dict):
            raise NominatimPermanentError("Nominatim returned malformed data")

        result = payload[0]
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
