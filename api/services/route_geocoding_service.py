"""
Service for geocoding route start/finish locations.

Validates that locations resolve within the USA and provides caching
to avoid duplicate geocoding calls within a single route plan.
"""

from dataclasses import dataclass
from decimal import Decimal
from functools import lru_cache

from api.clients.nominatim_client import (
    NominatimClient,
    NominatimTransientError,
    NominatimPermanentError,
)


class LocationNotInUSAError(Exception):
    """Raised when a geocoded location falls outside USA bounds."""
    pass


class GeocodingTransientError(Exception):
    """Raised when geocoding fails due to network/upstream error (retryable)."""
    pass


@dataclass(frozen=True)
class GeocodedLocation:
    """A geocoded location with coordinates and display name."""
    lat: Decimal
    lon: Decimal
    display_name: str

    @property
    def coords(self):
        """Return coordinates as (lat, lon) tuple."""
        return (self.lat, self.lon)

    @property
    def osrm_coords(self):
        """Return coordinates in OSRM format (lon, lat)."""
        return (self.lon, self.lat)


@dataclass(frozen=True)
class RouteGeocodingResult:
    """Raw endpoint-geocoding result without station match metadata."""

    latitude: Decimal
    longitude: Decimal
    display_name: str


class RouteGeocodingService:
    """
    Service for geocoding route start and finish locations.

    Provides USA bounds validation and caching to avoid duplicate calls
    when the same location is used as both start and finish.
    """

    # Continental USA bounds (excluding Alaska/Hawaii for route planning)
    MIN_LAT = Decimal("24.5")
    MAX_LAT = Decimal("49.4")
    MIN_LON = Decimal("-124.8")
    MAX_LON = Decimal("-66.9")

    def __init__(self, *, client=None):
        """
        Initialize the service.

        Args:
            client: Optional NominatimClient instance for testing
        """
        self.client = client or NominatimClient()

    def geocode_location(self, location_string):
        """
        Geocode a single location string and validate it's within USA.

        Args:
            location_string: Free-form location string (e.g., "Dallas, TX")

        Returns:
            GeocodedLocation with coordinates and display name

        Raises:
            LocationNotInUSAError: Geocoded location is outside USA
            GeocodingTransientError: Network or upstream error (retryable)
        """
        try:
            result = self._geocode_with_nominatim(location_string)
        except NominatimTransientError as exc:
            raise GeocodingTransientError(str(exc)) from exc
        except NominatimPermanentError as exc:
            # Treat permanent errors as location not found
            raise LocationNotInUSAError(
                f"Location '{location_string}' could not be resolved"
            ) from exc

        if result is None:
            raise LocationNotInUSAError(
                f"Location '{location_string}' could not be resolved"
            )

        # Validate the coordinates are within USA bounds
        self._validate_usa_bounds(result.latitude, result.longitude, location_string)

        return GeocodedLocation(
            lat=result.latitude,
            lon=result.longitude,
            display_name=result.display_name,
        )

    def _geocode_with_nominatim(self, location_string):
        """
        Call Nominatim API to geocode a location string.

        Uses a simple query format for general location search.
        """
        try:
            # For route planning, use a free-form search
            # This handles city names, addresses, landmarks, etc.
            import requests
            from django.conf import settings

            response = self.client.session.get(
                f"{settings.NOMINATIM_BASE_URL}/search",
                params={
                    "q": location_string,
                    "countrycodes": "us",
                    "format": "jsonv2",
                    "limit": 1,
                },
                headers={"User-Agent": settings.NOMINATIM_USER_AGENT},
                timeout=settings.NOMINATIM_TIMEOUT_SECONDS,
            )
        except requests.RequestException as exc:
            raise NominatimTransientError("Nominatim request failed") from exc

        if response.status_code >= 500:
            raise NominatimTransientError("Nominatim is temporarily unavailable")
        if response.status_code != 200:
            raise NominatimPermanentError("Nominatim rejected the request")

        try:
            payload = response.json()
        except (TypeError, ValueError) as exc:
            raise NominatimPermanentError("Nominatim returned malformed JSON") from exc

        if not isinstance(payload, list) or not payload:
            return None

        result = payload[0]

        # Validate country code if present
        address = result.get("address", {})
        country_code = address.get("country_code", "")
        if country_code and country_code.lower() != "us":
            return None

        try:
            from decimal import Decimal, InvalidOperation
            latitude = Decimal(str(result["lat"]))
            longitude = Decimal(str(result["lon"]))
        except (KeyError, InvalidOperation, TypeError, ValueError) as exc:
            raise NominatimPermanentError("Nominatim returned invalid coordinates") from exc

        if not Decimal("-90") <= latitude <= Decimal("90"):
            raise NominatimPermanentError("Nominatim returned invalid coordinates")
        if not Decimal("-180") <= longitude <= Decimal("180"):
            raise NominatimPermanentError("Nominatim returned invalid coordinates")

        return RouteGeocodingResult(
            latitude=latitude.quantize(Decimal("0.0000001")),
            longitude=longitude.quantize(Decimal("0.0000001")),
            display_name=str(result.get("display_name", "")),
        )

    @classmethod
    def validate_usa_bounds(cls, lat, lon, location_context=""):
        """
        Validate that coordinates are within continental USA bounds.

        Args:
            lat: Latitude as Decimal
            lon: Longitude as Decimal
            location_context: Optional location string for error messages

        Raises:
            LocationNotInUSAError: Coordinates are outside USA bounds
        """
        cls._validate_lat_range(lat, location_context)
        cls._validate_lon_range(lon, location_context)

    @classmethod
    def _validate_usa_bounds(cls, lat, lon, location_context):
        """Internal validation method used by geocode_location."""
        cls._validate_lat_range(lat, location_context)
        cls._validate_lon_range(lon, location_context)

    @classmethod
    def _validate_lat_range(cls, lat, location_context):
        """Validate latitude is within USA bounds."""
        if not cls.MIN_LAT <= lat <= cls.MAX_LAT:
            context = f" for '{location_context}'" if location_context else ""
            raise LocationNotInUSAError(
                f"Latitude {lat} is outside USA bounds{context}. "
                f"Must be between {cls.MIN_LAT} and {cls.MAX_LAT}"
            )

    @classmethod
    def _validate_lon_range(cls, lon, location_context):
        """Validate longitude is within USA bounds."""
        if not cls.MIN_LON <= lon <= cls.MAX_LON:
            context = f" for '{location_context}'" if location_context else ""
            raise LocationNotInUSAError(
                f"Longitude {lon} is outside USA bounds{context}. "
                f"Must be between {cls.MIN_LON} and {cls.MAX_LON}"
            )


def geocode_and_validate_route(start: str, finish: str):
    """
    Geocode both start and finish locations for a route.

    This function provides caching for repeated calls with the same
    location strings and validates both locations are within the USA.

    Args:
        start: Start location string
        finish: Finish location string

    Returns:
        tuple of (GeocodedLocation, GeocodedLocation) for start and finish

    Raises:
        LocationNotInUSAError: Either location is outside USA
        GeocodingTransientError: Network or upstream error
    """
    service = RouteGeocodingService()

    # Using lru_cache on a helper function for simple string-based caching
    @lru_cache(maxsize=4)
    def _cached_geocode(loc_str):
        return service.geocode_location(loc_str)

    start_geocoded = _cached_geocode(start)
    finish_geocoded = _cached_geocode(finish)

    return start_geocoded, finish_geocoded
