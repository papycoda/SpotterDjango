"""
Tests for route geocoding and OSRM integration service.
"""

from decimal import Decimal
from unittest.mock import Mock, MagicMock

from django.test import SimpleTestCase, override_settings

from api.services.route_service import (
    RouteService,
    RoutePlan,
    RouteNotFoundError,
    RoutingTransientError,
    plan_route,
)
from api.services.route_geocoding_service import (
    RouteGeocodingService,
    GeocodedLocation,
    LocationNotInUSAError,
)


@override_settings(
    NOMINATIM_BASE_URL="https://nominatim.example.test",
    NOMINATIM_USER_AGENT="FuelSpotterTests/1.0",
    NOMINATIM_TIMEOUT_SECONDS=10,
    OSRM_BASE_URL="https://osrm.example.test",
    EXTERNAL_HTTP_TIMEOUT_SECONDS=10,
)
class RouteGeocodingServiceTests(SimpleTestCase):
    """Tests for route geocoding service."""

    def setUp(self):
        self.session = Mock()
        self.client = Mock()
        self.client.session = self.session
        self.service = RouteGeocodingService(client=self.client)

    def mock_response(self, status_code=200, payload=None):
        """Create a mock response object."""
        response = Mock(status_code=status_code)
        response.json.return_value = payload or {}
        return response

    def test_geocode_usa_city_success(self):
        """Test successful geocoding of a US city."""
        from django.conf import settings

        self.session.get.return_value = self.mock_response(
            payload=[{
                "lat": "32.7767",
                "lon": "-96.7970",
                "display_name": "Dallas, TX, USA",
                "address": {
                    "country_code": "us",
                }
            }]
        )

        result = self.service.geocode_location("Dallas, TX")

        self.assertIsInstance(result, GeocodedLocation)
        self.assertEqual(result.lat, Decimal("32.7767000"))
        self.assertEqual(result.lon, Decimal("-96.7970000"))
        self.assertEqual(result.display_name, "Dallas, TX, USA")

        # Verify Nominatim was called correctly
        self.session.get.assert_called_once_with(
            f"{settings.NOMINATIM_BASE_URL}/search",
            params={
                "q": "Dallas, TX",
                "countrycodes": "us",
                "format": "jsonv2",
                "limit": 1,
            },
            headers={"User-Agent": settings.NOMINATIM_USER_AGENT},
            timeout=settings.NOMINATIM_TIMEOUT_SECONDS,
        )

    def test_endpoint_geocoding_result_does_not_require_station_match_metadata(self):
        self.session.get.return_value = self.mock_response(
            payload=[{
                "lat": "32.7767",
                "lon": "-96.7970",
                "display_name": "Dallas, TX, USA",
                "address": {"country_code": "us"},
            }]
        )

        try:
            result = self.service._geocode_with_nominatim("Dallas, TX")
        except TypeError as exc:
            self.fail(f"Endpoint geocoding is coupled to station metadata: {exc}")

        self.assertEqual(result.latitude, Decimal("32.7767000"))
        self.assertEqual(result.longitude, Decimal("-96.7970000"))
        self.assertFalse(hasattr(result, "stage"))
        self.assertFalse(hasattr(result, "confidence"))

    def test_geocode_non_usa_rejection(self):
        """Test that non-US locations are rejected."""
        # Toronto, Canada
        self.session.get.return_value = self.mock_response(
            payload=[{
                "lat": "43.6532",
                "lon": "-79.3832",
                "display_name": "Toronto, ON, Canada",
                "address": {
                    "country_code": "ca",
                }
            }]
        )

        with self.assertRaises(LocationNotInUSAError) as cm:
            self.service.geocode_location("Toronto, Canada")

        self.assertIn("could not be resolved", str(cm.exception))

    def test_geocode_no_results(self):
        """Test handling of no geocoding results."""
        self.session.get.return_value = self.mock_response(payload=[])

        with self.assertRaises(LocationNotInUSAError) as cm:
            self.service.geocode_location("Invalid Location")

        self.assertIn("could not be resolved", str(cm.exception))

    def test_validate_usa_bounds_continental(self):
        """Test USA bounds validation for continental US."""
        # Valid locations
        valid_locations = [
            (Decimal("32.7767"), Decimal("-96.7970")),  # Dallas, TX
            (Decimal("40.7128"), Decimal("-74.0060")),  # New York, NY
            (Decimal("34.0522"), Decimal("-118.2437")),  # Los Angeles, CA
        ]

        for lat, lon in valid_locations:
            with self.subTest(location=f"{lat}, {lon}"):
                RouteGeocodingService.validate_usa_bounds(lat, lon)

    def test_validate_usa_bounds_outside_lat(self):
        """Test that locations outside USA latitude bounds are rejected."""
        invalid_locations = [
            (Decimal("20.0"), Decimal("-100.0")),  # Too far south
            (Decimal("50.0"), Decimal("-100.0")),  # Too far north
        ]

        for lat, lon in invalid_locations:
            with self.subTest(location=f"{lat}, {lon}"):
                with self.assertRaises(LocationNotInUSAError):
                    RouteGeocodingService.validate_usa_bounds(lat, lon)

    def test_validate_usa_bounds_outside_lon(self):
        """Test that locations outside USA longitude bounds are rejected."""
        invalid_locations = [
            (Decimal("40.0"), Decimal("-130.0")),  # Too far west
            (Decimal("40.0"), Decimal("-60.0")),   # Too far east
        ]

        for lat, lon in invalid_locations:
            with self.subTest(location=f"{lat}, {lon}"):
                with self.assertRaises(LocationNotInUSAError):
                    RouteGeocodingService.validate_usa_bounds(lat, lon)

    def test_geocode_with_display_name(self):
        """Test that display name is preserved from geocoding result."""
        self.session.get.return_value = self.mock_response(
            payload=[{
                "lat": "41.8781",
                "lon": "-87.6298",
                "display_name": "Chicago, Cook County, Illinois, USA",
                "address": {"country_code": "us"}
            }]
        )

        result = self.service.geocode_location("Chicago")

        self.assertEqual(result.display_name, "Chicago, Cook County, Illinois, USA")

    def test_geocoded_location_coords_property(self):
        """Test GeocodedLocation coords property returns (lat, lon)."""
        location = GeocodedLocation(
            lat=Decimal("32.7767"),
            lon=Decimal("-96.7970"),
            display_name="Dallas, TX"
        )

        self.assertEqual(location.coords, (Decimal("32.7767"), Decimal("-96.7970")))

    def test_geocoded_location_osrm_coords_property(self):
        """Test GeocodedLocation osrm_coords property returns (lon, lat)."""
        location = GeocodedLocation(
            lat=Decimal("32.7767"),
            lon=Decimal("-96.7970"),
            display_name="Dallas, TX"
        )

        self.assertEqual(location.osrm_coords, (Decimal("-96.7970"), Decimal("32.7767")))

    def test_validate_bounds_with_context(self):
        """Test that validation error includes location context."""
        with self.assertRaises(LocationNotInUSAError) as cm:
            RouteGeocodingService.validate_usa_bounds(
                Decimal("60.0"),
                Decimal("-100.0"),
                "North Pole"
            )

        self.assertIn("North Pole", str(cm.exception))


@override_settings(
    NOMINATIM_BASE_URL="https://nominatim.example.test",
    OSRM_BASE_URL="https://osrm.example.test",
    EXTERNAL_HTTP_TIMEOUT_SECONDS=10,
)
class RouteServiceTests(SimpleTestCase):
    """Tests for complete route service."""

    def setUp(self):
        self.geocoding_service = Mock()
        self.osrm_client = Mock()
        self.service = RouteService(
            geocoding_service=self.geocoding_service,
            osrm_client=self.osrm_client,
        )

    def test_plan_route_complete_flow(self):
        """Test complete route planning flow from locations to route plan."""
        # Mock geocoding results
        start = GeocodedLocation(
            lat=Decimal("32.7767"),
            lon=Decimal("-96.7970"),
            display_name="Dallas, TX, USA"
        )
        end = GeocodedLocation(
            lat=Decimal("29.7604"),
            lon=Decimal("-95.3698"),
            display_name="Houston, TX, USA"
        )
        self.geocoding_service.geocode_location.side_effect = [start, end]

        # Mock OSRM result
        from api.clients.osrm_client import RouteResult
        geometry = [
            (Decimal("32.7767"), Decimal("-96.7970")),
            (Decimal("31.5000"), Decimal("-95.9000")),
            (Decimal("29.7604"), Decimal("-95.3698")),
        ]
        self.osrm_client.get_route.return_value = RouteResult(
            distance_meters=Decimal("386000"),  # ~240 miles
            duration_seconds=Decimal("14400"),  # 4 hours
            geometry=geometry,
        )

        # Plan the route
        route_plan = self.service.plan_route("Dallas, TX", "Houston, TX")

        # Verify complete RoutePlan structure
        self.assertIsInstance(route_plan, RoutePlan)
        self.assertEqual(route_plan.start_geocoded, start)
        self.assertEqual(route_plan.end_geocoded, end)
        self.assertEqual(route_plan.route_geometry, geometry)
        self.assertEqual(route_plan.total_distance_m, Decimal("386000"))
        self.assertEqual(route_plan.total_duration_s, Decimal("14400"))

        # Verify geocoding was called for both locations
        self.assertEqual(self.geocoding_service.geocode_location.call_count, 2)
        self.geocoding_service.geocode_location.assert_any_call("Dallas, TX")
        self.geocoding_service.geocode_location.assert_any_call("Houston, TX")

        # Verify OSRM was called with OSRM format (lon, lat)
        self.osrm_client.get_route.assert_called_once_with(
            (Decimal("-96.7970"), Decimal("32.7767")),  # start (lon, lat)
            (Decimal("-95.3698"), Decimal("29.7604")),  # end (lon, lat)
        )

    def test_calculate_bounding_box(self):
        """Test bounding box calculation from geometry."""
        from api.clients.osrm_client import RouteResult

        geometry = [
            (Decimal("30.0"), Decimal("-95.0")),
            (Decimal("35.0"), Decimal("-100.0")),
            (Decimal("40.0"), Decimal("-90.0")),
        ]
        self.osrm_client.get_route.return_value = RouteResult(
            distance_meters=Decimal("1000"),
            duration_seconds=Decimal("60"),
            geometry=geometry,
        )

        self.geocoding_service.geocode_location.side_effect = [
            GeocodedLocation(
                lat=Decimal("30.0"),
                lon=Decimal("-95.0"),
                display_name="Start"
            ),
            GeocodedLocation(
                lat=Decimal("40.0"),
                lon=Decimal("-90.0"),
                display_name="End"
            ),
        ]

        route_plan = self.service.plan_route("Start", "End")

        # Bounding box: (min_lat, min_lon, max_lat, max_lon)
        expected_bbox = (
            Decimal("30.0000000"),
            Decimal("-100.0000000"),
            Decimal("40.0000000"),
            Decimal("-90.0000000"),
        )
        self.assertEqual(route_plan.bounding_box, expected_bbox)

    def test_plan_route_properties(self):
        """Test RoutePlan computed properties."""
        from api.clients.osrm_client import RouteResult

        self.osrm_client.get_route.return_value = RouteResult(
            distance_meters=Decimal("160934"),  # 100 miles
            duration_seconds=Decimal("3600"),   # 1 hour
            geometry=[
                (Decimal("32.7767"), Decimal("-96.7970")),
                (Decimal("29.7604"), Decimal("-95.3698")),
            ],
        )

        self.geocoding_service.geocode_location.side_effect = [
            GeocodedLocation(Decimal("32.7767"), Decimal("-96.7970"), "Start"),
            GeocodedLocation(Decimal("29.7604"), Decimal("-95.3698"), "End"),
        ]

        route_plan = self.service.plan_route("Start", "End")

        # Test distance conversion (meters to miles)
        # 160934 meters ≈ 100.00 miles
        expected_miles = (Decimal("160934") * Decimal("0.000621371")).quantize(Decimal("0.01"))
        self.assertEqual(route_plan.total_distance_miles, expected_miles)

        # Test duration conversion (seconds to minutes)
        self.assertEqual(route_plan.total_duration_minutes, Decimal("60.0"))

    def test_geocoding_transient_error_mapping(self):
        """Test that geocoding transient errors are mapped."""
        from api.services.route_geocoding_service import GeocodingTransientError

        self.geocoding_service.geocode_location.side_effect = GeocodingTransientError("Network error")

        with self.assertRaises(RoutingTransientError) as cm:
            self.service.plan_route("Start", "End")

        self.assertIn("Failed to geocode", str(cm.exception))
        self.assertIn("Network error", str(cm.exception))

    def test_osrm_transient_error_mapping(self):
        """Test that OSRM transient errors are mapped."""
        from api.clients.osrm_client import OSRMTransientError

        self.geocoding_service.geocode_location.side_effect = [
            GeocodedLocation(Decimal("32.7767"), Decimal("-96.7970"), "Start"),
            GeocodedLocation(Decimal("29.7604"), Decimal("-95.3698"), "End"),
        ]
        self.osrm_client.get_route.side_effect = OSRMTransientError("OSRM down")

        with self.assertRaises(RoutingTransientError) as cm:
            self.service.plan_route("Start", "End")

        self.assertIn("temporarily unavailable", str(cm.exception))

    def test_osrm_no_route_error_mapping(self):
        """Test that OSRM no route errors are mapped to RouteNotFoundError."""
        from api.clients.osrm_client import OSRMPermanentError

        self.geocoding_service.geocode_location.side_effect = [
            GeocodedLocation(Decimal("32.7767"), Decimal("-96.7970"), "Start"),
            GeocodedLocation(Decimal("29.7604"), Decimal("-95.3698"), "End"),
        ]
        self.osrm_client.get_route.side_effect = OSRMPermanentError("No route found")

        with self.assertRaises(RouteNotFoundError) as cm:
            self.service.plan_route("Start", "End")

        self.assertIn("No route found", str(cm.exception))

    def test_location_not_in_usa_propagates(self):
        """Test that LocationNotInUSAError is propagated from geocoding."""
        self.geocoding_service.geocode_location.side_effect = LocationNotInUSAError("Outside USA")

        with self.assertRaises(LocationNotInUSAError):
            self.service.plan_route("Start", "End")

    def test_calculate_bounding_box_empty_geometry(self):
        """Test that empty geometry raises ValueError."""
        from api.clients.osrm_client import RouteResult

        self.osrm_client.get_route.return_value = RouteResult(
            distance_meters=Decimal("0"),
            duration_seconds=Decimal("0"),
            geometry=[],
        )

        self.geocoding_service.geocode_location.side_effect = [
            GeocodedLocation(Decimal("0"), Decimal("0"), "Start"),
            GeocodedLocation(Decimal("0"), Decimal("0"), "End"),
        ]

        with self.assertRaises(ValueError) as cm:
            self.service.plan_route("Start", "End")

        self.assertIn("empty geometry", str(cm.exception))

    def test_route_plan_coords_properties(self):
        """Test RoutePlan coordinate access properties."""
        start = GeocodedLocation(
            lat=Decimal("32.7767"),
            lon=Decimal("-96.7970"),
            display_name="Dallas"
        )
        end = GeocodedLocation(
            lat=Decimal("29.7604"),
            lon=Decimal("-95.3698"),
            display_name="Houston"
        )

        route_plan = RoutePlan(
            start_geocoded=start,
            end_geocoded=end,
            route_geometry=[(start.coords), (end.coords)],
            total_distance_m=Decimal("1000"),
            total_duration_s=Decimal("60"),
            bounding_box=(Decimal("29.7604"), Decimal("-96.7970"), Decimal("32.7767"), Decimal("-95.3698")),
        )

        # Test coords property (lat, lon)
        self.assertEqual(route_plan.start_coords, start.coords)
        self.assertEqual(route_plan.end_coords, end.coords)

        # Test osrm_coords property (lon, lat)
        self.assertEqual(route_plan.start_osrm_coords, start.osrm_coords)
        self.assertEqual(route_plan.end_osrm_coords, end.osrm_coords)


@override_settings(
    NOMINATIM_BASE_URL="https://nominatim.example.test",
    OSRM_BASE_URL="https://osrm.example.test",
    EXTERNAL_HTTP_TIMEOUT_SECONDS=10,
)
class PlanRouteConvenienceFunctionTests(SimpleTestCase):
    """Tests for the plan_route convenience function."""

    def test_plan_route_convenience_function(self):
        """Test the plan_route convenience function creates service."""
        # This test verifies the function exists and creates a service
        # The actual geocoding and routing would be mocked in integration tests
        from unittest.mock import patch

        with patch('api.services.route_service.RouteService') as MockService:
            mock_instance = MockService.return_value
            mock_plan = Mock(spec=RoutePlan)
            mock_instance.plan_route.return_value = mock_plan

            result = plan_route("Dallas, TX", "Houston, TX")

            MockService.assert_called_once()
            mock_instance.plan_route.assert_called_once_with("Dallas, TX", "Houston, TX")
            self.assertEqual(result, mock_plan)
