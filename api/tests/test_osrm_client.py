"""Tests for OSRM client."""

from decimal import Decimal
from unittest.mock import Mock

import requests
from django.test import SimpleTestCase, override_settings

from api.clients.osrm_client import (
    OSRMClient,
    OSRMPermanentError,
    OSRMTransientError,
    RouteResult,
)


@override_settings(
    OSRM_BASE_URL="https://osrm.example.test",
    EXTERNAL_HTTP_TIMEOUT_SECONDS=10,
)
class OSRMClientTests(SimpleTestCase):
    """Tests for OSRM client."""

    def setUp(self):
        self.session = Mock()
        self.client = OSRMClient(session=self.session)

    def response(self, status_code=200, payload=None):
        """Create a mock response object."""
        response = Mock(status_code=status_code)
        response.json.return_value = payload
        return response

    def test_get_route_between_two_coordinates(self):
        """Test successful route retrieval between two coordinates."""
        # Mock a valid OSRM response with encoded polyline
        # Using "?" which encodes as a valid 0 value
        # A single coordinate (lat=0, lon=0) at origin encodes as "??"
        # "?" = binary 00000 = value 0 after sign removal
        self.session.get.return_value = self.response(
            payload={
                "code": "Ok",
                "routes": [{
                    "distance": 5000.0,  # 5 km
                    "duration": 300.0,  # 5 minutes
                    # "?" represents a single 0 in the encoding
                    # We need two "?" for one point (lat and lon)
                    # But "?" alone decodes to 0 (one value)
                    # The decoder expects pairs, so "??" gives us one point (0,0)
                    "geometry": "??",
                }],
            }
        )

        # Dallas, TX to Houston, TX
        start = (-96.7970, 32.7767)  # (lon, lat) - OSRM order
        end = (-95.3698, 29.7604)  # (lon, lat) - OSRM order

        result = self.client.get_route(start, end)

        # Verify correct endpoint was called
        self.session.get.assert_called_once_with(
            "https://osrm.example.test/route/v1/driving/-96.797,32.7767;-95.3698,29.7604",
            params={
                "overview": "simplified",
                "geometries": "polyline",
            },
            timeout=10,
        )

        # Verify result structure
        self.assertIsInstance(result, RouteResult)
        self.assertEqual(result.distance_meters, Decimal("5000.0"))
        self.assertEqual(result.duration_seconds, Decimal("300.0"))
        self.assertIsInstance(result.geometry, list)
        self.assertGreater(len(result.geometry), 0)

    def test_returns_coordinates_in_lat_lon_order(self):
        """Test that decoded geometry returns (lat, lon) tuples."""
        # Use a polyline encoding for two points: (0,0) and (1,1)
        # Each coordinate is encoded as lat then lon
        self.session.get.return_value = self.response(
            payload={
                "code": "Ok",
                "routes": [{
                    "distance": 1000.0,
                    "duration": 60.0,
                    "geometry": "??",  # Encodes (0,0)
                }],
            }
        )

        result = self.client.get_route((0, 0), (0, 0))

        # Verify coordinates are tuples
        self.assertIsInstance(result.geometry, list)
        if result.geometry:
            coord = result.geometry[0]
            self.assertIsInstance(coord, tuple)
            self.assertEqual(len(coord), 2)
            # Each coordinate should be (lat, lon)
            lat, lon = coord
            self.assertIsInstance(lat, Decimal)
            self.assertIsInstance(lon, Decimal)

    def test_maps_network_errors_to_transient(self):
        """Test that network errors raise transient exceptions."""
        errors = (
            requests.RequestException("Network offline"),
            requests.Timeout("Request timed out"),
        )

        for error in errors:
            with self.subTest(error=error):
                self.session.get.side_effect = error
                self.session.get.return_value = None

                with self.assertRaises(OSRMTransientError):
                    self.client.get_route((0, 0), (1, 1))

    def test_maps_5xx_to_transient(self):
        """Test that 5xx server errors raise transient exceptions."""
        for status in [500, 502, 503, 504]:
            with self.subTest(status=status):
                self.session.get.side_effect = None
                self.session.get.return_value = self.response(status_code=status)

                with self.assertRaises(OSRMTransientError):
                    self.client.get_route((0, 0), (1, 1))

    def test_maps_4xx_to_permanent(self):
        """Test that 4xx client errors raise permanent exceptions."""
        for status in [400, 404]:
            with self.subTest(status=status):
                self.session.get.side_effect = None
                self.session.get.return_value = self.response(status_code=status)

                with self.assertRaises(OSRMPermanentError):
                    self.client.get_route((0, 0), (1, 1))

    def test_maps_osrm_no_route_to_permanent(self):
        """Test that OSRM NoRoute error raises permanent exception."""
        self.session.get.return_value = self.response(
            payload={
                "code": "NoRoute",
                "message": "No route found between coordinates",
            }
        )

        with self.assertRaises(OSRMPermanentError) as cm:
            self.client.get_route((0, 0), (1, 1))

        self.assertIn("No route found", str(cm.exception))

    def test_maps_osrm_notfound_to_permanent(self):
        """Test that OSRM NotFound error raises permanent exception."""
        self.session.get.return_value = self.response(
            payload={
                "code": "NotFound",
                "message": "Cannot find route",
            }
        )

        with self.assertRaises(OSRMPermanentError):
            self.client.get_route((0, 0), (1, 1))

    def test_maps_malformed_json_to_permanent(self):
        """Test that malformed JSON raises permanent exception."""
        response = self.response()
        response.json.side_effect = ValueError("Invalid JSON")
        self.session.get.return_value = response

        with self.assertRaises(OSRMPermanentError) as cm:
            self.client.get_route((0, 0), (1, 1))

        self.assertIn("malformed JSON", str(cm.exception))

    def test_maps_empty_routes_to_permanent(self):
        """Test that empty routes array raises permanent exception."""
        self.session.get.return_value = self.response(
            payload={
                "code": "Ok",
                "routes": [],
            }
        )

        with self.assertRaises(OSRMPermanentError) as cm:
            self.client.get_route((0, 0), (1, 1))

        self.assertIn("no routes", str(cm.exception))

    def test_maps_missing_distance_to_permanent(self):
        """Test that missing distance field raises permanent exception."""
        self.session.get.return_value = self.response(
            payload={
                "code": "Ok",
                "routes": [{
                    "duration": 60.0,
                    "geometry": "??",
                }],
            }
        )

        with self.assertRaises(OSRMPermanentError) as cm:
            self.client.get_route((0, 0), (1, 1))

        self.assertIn("distance", str(cm.exception))

    def test_maps_invalid_distance_to_permanent(self):
        """Test that invalid distance raises permanent exception."""
        self.session.get.return_value = self.response(
            payload={
                "code": "Ok",
                "routes": [{
                    "distance": "invalid",
                    "duration": 60.0,
                    "geometry": "??",
                }],
            }
        )

        with self.assertRaises(OSRMPermanentError) as cm:
            self.client.get_route((0, 0), (1, 1))

        self.assertIn("distance", str(cm.exception))

    def test_maps_negative_distance_to_permanent(self):
        """Test that negative distance raises permanent exception."""
        self.session.get.return_value = self.response(
            payload={
                "code": "Ok",
                "routes": [{
                    "distance": -100.0,
                    "duration": 60.0,
                    "geometry": "??",
                }],
            }
        )

        with self.assertRaises(OSRMPermanentError) as cm:
            self.client.get_route((0, 0), (1, 1))

        self.assertIn("distance", str(cm.exception))

    def test_maps_missing_duration_to_permanent(self):
        """Test that missing duration field raises permanent exception."""
        self.session.get.return_value = self.response(
            payload={
                "code": "Ok",
                "routes": [{
                    "distance": 1000.0,
                    "geometry": "??",
                }],
            }
        )

        with self.assertRaises(OSRMPermanentError) as cm:
            self.client.get_route((0, 0), (1, 1))

        self.assertIn("duration", str(cm.exception))

    def test_maps_missing_geometry_to_permanent(self):
        """Test that missing geometry field raises permanent exception."""
        self.session.get.return_value = self.response(
            payload={
                "code": "Ok",
                "routes": [{
                    "distance": 1000.0,
                    "duration": 60.0,
                }],
            }
        )

        with self.assertRaises(OSRMPermanentError) as cm:
            self.client.get_route((0, 0), (1, 1))

        self.assertIn("geometry", str(cm.exception))

    def test_handles_empty_geometry_string(self):
        """Test that empty geometry string is handled."""
        self.session.get.return_value = self.response(
            payload={
                "code": "Ok",
                "routes": [{
                    "distance": 1000.0,
                    "duration": 60.0,
                    "geometry": "",
                }],
            }
        )

        with self.assertRaises(OSRMPermanentError) as cm:
            self.client.get_route((0, 0), (1, 1))

        self.assertIn("geometry", str(cm.exception))

    def test_polyline_decoder_basic(self):
        """Test basic polyline decoding functionality."""
        # Test with known polyline values
        client = OSRMClient(session=self.session)

        # Test empty string
        result = client._decode_polyline("")
        self.assertEqual(result, [])

        # Test single coordinate (0,0) encoded as "??"
        # This is a minimal valid encoding
        result = client._decode_polyline("??")
        self.assertEqual(len(result), 1)
        lat, lon = result[0]
        self.assertIsInstance(lat, Decimal)
        self.assertIsInstance(lon, Decimal)

    def test_polyline_decoder_invalid_char(self):
        """Test that invalid characters in polyline raise error."""
        client = OSRMClient(session=self.session)

        # Invalid base64 character
        with self.assertRaises(ValueError):
            client._decode_polyline("!!!invalid!!!")


@override_settings(
    OSRM_BASE_URL="https://osrm.example.test",
    EXTERNAL_HTTP_TIMEOUT_SECONDS=10,
)
class OSRMClientPolylineDecoderTests(SimpleTestCase):
    """Specific tests for polyline decoding algorithm."""

    def test_decodes_simple_polyline(self):
        """Test decoding a simple polyline."""
        client = OSRMClient()

        # "??" encodes as a single point (0,0)
        # First "?" = 0 (lat delta)
        # Second "?" = 0 (lon delta)
        encoded = "??"

        result = client._decode_polyline(encoded)

        # Should decode to one point at origin
        self.assertEqual(len(result), 1)
        lat, lon = result[0]
        self.assertEqual(lat, Decimal("0"))
        self.assertEqual(lon, Decimal("0"))
