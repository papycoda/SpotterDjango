"""
Client for Nominatim (OpenStreetMap) geocoding API.

TODO: Implement Nominatim API integration for address geocoding.
"""

import requests


class NominatimClient:
    """Client for Nominatim geocoding API."""

    BASE_URL = "https://nominatim.openstreetmap.org/search"

    @staticmethod
    def geocode(address):
        """
        Geocode an address to latitude/longitude.

        Returns: dict with 'lat', 'lon', 'display_name' or None
        """
        pass

    @staticmethod
    def reverse_geocode(lat, lon):
        """
        Reverse geocode coordinates to address.

        Returns: dict with address details or None
        """
        pass
