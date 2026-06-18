"""
Client for OSRM (Open Source Routing Machine) routing API.

TODO: Implement OSRM API integration for route planning.
"""

import requests


class OSRMClient:
    """Client for OSRM routing API."""

    BASE_URL = "https://router.project-osrm.org"

    @staticmethod
    def get_route(start_coords, end_coords):
        """
        Get route geometry and distance between two coordinates.

        Args:
            start_coords: tuple (lon, lat)
            end_coords: tuple (lon, lat)

        Returns: dict with 'distance', 'duration', 'geometry' or None
        """
        pass

    @staticmethod
    def parse_geometry(encoded_geometry):
        """
        Parse encoded polyline geometry.

        Returns: list of (lat, lon) coordinates
        """
        pass
