"""
Client for OSRM (Open Source Routing Machine) routing API.

Provides route planning and geometry calculations between coordinates.
"""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

import requests
from django.conf import settings


class OSRMError(Exception):
    """Base sanitized OSRM failure."""


class OSRMTransientError(OSRMError):
    """A network or upstream condition that can be retried."""


class OSRMPermanentError(OSRMError):
    """A request or response that should not be retried."""


@dataclass(frozen=True)
class RouteResult:
    """Result from OSRM route calculation."""
    distance_meters: Decimal
    duration_seconds: Decimal
    geometry: list[tuple[Decimal, Decimal]]  # List of (lat, lon) points


class OSRMClient:
    """Client for OSRM routing API."""

    def __init__(self, *, session=None):
        self.session = session or requests.Session()
        self.base_url = settings.OSRM_BASE_URL

    def get_route(self, start_coords, end_coords):
        """
        Get route geometry and distance between two coordinates.

        Args:
            start_coords: tuple (lon, lat) - longitude first for OSRM
            end_coords: tuple (lon, lat) - longitude first for OSRM

        Returns:
            RouteResult with distance, duration, and geometry

        Raises:
            OSRMTransientError: Network or server error that can be retried
            OSRMPermanentError: Invalid request or response
        """
        lon1, lat1 = start_coords
        lon2, lat2 = end_coords

        # Build coordinate string for OSRM: lon,lat;lon,lat
        coord_string = f"{lon1},{lat1};{lon2},{lat2}"

        try:
            response = self.session.get(
                f"{self.base_url}/route/v1/driving/{coord_string}",
                params={
                    "overview": "full",  # Return full geometry
                    "geometries": "polyline",  # Use encoded polyline
                },
                timeout=settings.EXTERNAL_HTTP_TIMEOUT_SECONDS,
            )
        except requests.RequestException as exc:
            raise OSRMTransientError("OSRM request failed") from exc

        if response.status_code >= 500:
            raise OSRMTransientError("OSRM is temporarily unavailable")
        if response.status_code != 200:
            raise OSRMPermanentError(f"OSRM rejected the request: {response.status_code}")

        try:
            payload = response.json()
        except (TypeError, ValueError) as exc:
            raise OSRMPermanentError("OSRM returned malformed JSON") from exc

        # Check for OSRM error structure
        if "code" in payload and payload["code"] != "Ok":
            error_msg = payload.get("message", "Unknown OSRM error")
            if payload["code"] in ("NoRoute", "NotFound"):
                # No valid route found - this is a permanent error for these coordinates
                raise OSRMPermanentError(f"No route found: {error_msg}")
            raise OSRMPermanentError(f"OSRM error: {error_msg}")

        # Validate response structure
        if "routes" not in payload or not payload["routes"]:
            raise OSRMPermanentError("OSRM returned no routes")

        route = payload["routes"][0]

        # Extract and validate distance
        try:
            distance = Decimal(str(route["distance"]))
        except (KeyError, InvalidOperation, TypeError, ValueError) as exc:
            raise OSRMPermanentError("OSRM returned invalid distance") from exc
        if distance < 0:
            raise OSRMPermanentError("OSRM returned negative distance")

        # Extract and validate duration
        try:
            duration = Decimal(str(route["duration"]))
        except (KeyError, InvalidOperation, TypeError, ValueError) as exc:
            raise OSRMPermanentError("OSRM returned invalid duration") from exc
        if duration < 0:
            raise OSRMPermanentError("OSRM returned negative duration")

        # Parse geometry
        try:
            geometry_encoded = route["geometry"]
            geometry = self._decode_polyline(geometry_encoded)
        except (KeyError, ValueError) as exc:
            raise OSRMPermanentError("OSRM returned invalid geometry") from exc

        if not geometry:
            raise OSRMPermanentError("OSRM returned empty geometry")

        return RouteResult(
            distance_meters=distance,
            duration_seconds=duration,
            geometry=geometry,
        )

    def _decode_polyline(self, encoded):
        """
        Decode an encoded polyline string into a list of (lat, lon) tuples.

        Uses the Google polyline encoding algorithm that OSRM follows.
        Each coordinate is stored as latitude then longitude.

        Args:
            encoded: String containing encoded polyline

        Returns:
            List of (lat, lon) tuples as Decimals

        Raises:
            ValueError: If encoded string is invalid
        """
        if not encoded:
            return []

        coordinates = []
        index = 0
        len_encoded = len(encoded)

        # Current position after applying deltas
        lat = 0
        lon = 0

        while index < len_encoded:
            # Decode latitude
            lat_change, index = self._decode_single_value(encoded, index)
            lat += lat_change

            # Decode longitude
            lon_change, index = self._decode_single_value(encoded, index)
            lon += lon_change

            # Convert to degrees and store as (lat, lon) with Decimal precision
            try:
                lat_decimal = (Decimal(lat) / Decimal("10000000")).quantize(
                    Decimal("0.0000001")
                )
                lon_decimal = (Decimal(lon) / Decimal("10000000")).quantize(
                    Decimal("0.0000001")
                )
                coordinates.append((lat_decimal, lon_decimal))
            except (InvalidOperation, TypeError) as exc:
                raise ValueError("Invalid coordinate value in polyline") from exc

        return coordinates

    def _decode_single_value(self, encoded, index):
        """
        Decode a single encoded value (delta for lat or lon).

        Returns:
            Tuple of (decoded_value, new_index)
        """
        result = 0
        shift = 0
        byte = None

        while byte != 0:
            if index >= len(encoded):
                raise ValueError("Unexpected end of encoded string")

            byte = ord(encoded[index]) - 63
            index += 1

            # Check if continuation bit is set
            continuation = (byte & 0x20) != 0
            byte &= 0x1F  # Remove continuation bit

            result |= (byte << shift)
            shift += 5

        # Handle sign bit and two's complement
        if result & 1:
            result = ~(result >> 1)
        else:
            result = result >> 1

        return result, index
