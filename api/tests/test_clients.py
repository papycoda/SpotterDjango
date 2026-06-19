from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import Mock

import requests
from django.test import SimpleTestCase, TestCase, override_settings

from api.clients.nominatim_client import (
    NominatimClient,
    NominatimPermanentError,
    NominatimTransientError,
)
from api.models import GeocodingRateLimit
from api.services.geocoding_rate_limiter import DatabaseRateLimiter


class DatabaseRateLimiterTests(TestCase):
    def test_reserves_database_slots_and_waits_for_the_second_slot(self):
        now = datetime(2026, 6, 19, 12, 0, tzinfo=timezone.utc)
        sleeps = []
        limiter = DatabaseRateLimiter(
            interval_seconds=1,
            clock=lambda: now,
            sleeper=sleeps.append,
        )

        limiter.acquire()
        limiter.acquire()

        state = GeocodingRateLimit.objects.get(pk=1)
        self.assertEqual(state.next_allowed_at, now + timedelta(seconds=2))
        self.assertEqual(sleeps, [1.0])


@override_settings(
    NOMINATIM_BASE_URL="https://geo.example.test",
    NOMINATIM_USER_AGENT="FuelSpotterTests/1.0",
    NOMINATIM_TIMEOUT_SECONDS=4.5,
)
class NominatimClientTests(SimpleTestCase):
    def setUp(self):
        self.session = Mock()
        self.limiter = Mock()
        self.client = NominatimClient(
            session=self.session,
            rate_limiter=self.limiter,
        )

    def response(self, status_code=200, payload=None):
        response = Mock(status_code=status_code)
        response.json.return_value = payload
        return response

    def test_geocodes_a_named_station_in_the_expected_state(self):
        self.session.get.return_value = self.response(
            payload=[{
                "lat": "32.7767",
                "lon": "-96.7970",
                "display_name": "Love's Travel Stop, Dallas, Texas, USA",
                "category": "highway",
                "type": "services",
                "address": {
                    "ISO3166-2-lvl4": "US-TX",
                    "city": "Dallas",
                },
            }]
        )

        result = self.client.geocode(
            name="Loves Travel Stop #429",
            address="100 Main St",
            city="Dallas",
            state="TX",
        )

        self.limiter.acquire.assert_called_once_with()
        self.session.get.assert_called_once_with(
            "https://geo.example.test/search",
            params={
                "q": "Love's Travel Stop, Dallas, TX, USA",
                "countrycodes": "us",
                "format": "jsonv2",
                "addressdetails": 1,
                "limit": 5,
            },
            headers={"User-Agent": "FuelSpotterTests/1.0"},
            timeout=4.5,
        )
        self.assertEqual(result.latitude, Decimal("32.7767000"))
        self.assertEqual(result.longitude, Decimal("-96.7970000"))

    def test_returns_none_when_no_address_matches(self):
        self.session.get.return_value = self.response(payload=[])

        result = self.client.geocode(
            name="Missing Stop",
            address="Missing",
            city="Nowhere",
            state="TX",
        )

        self.assertIsNone(result)

    def test_rejects_city_or_road_fallbacks_as_station_coordinates(self):
        self.session.get.return_value = self.response(payload=[{
            "lat": "38.4620932",
            "lon": "-99.3053414",
            "display_name": "US 50, Kansas, USA",
            "category": "highway",
            "type": "primary",
            "address": {
                "ISO3166-2-lvl4": "US-KS",
                "city": "Florence",
            },
        }])

        result = self.client.geocode(
            name="Flint Hills Fuel Mart",
            address="US-50 & US-77",
            city="Florence",
            state="KS",
        )

        self.assertIsNone(result)

    def test_selects_station_feature_matching_the_requested_state(self):
        self.session.get.return_value = self.response(payload=[
            {
                "lat": "33.0",
                "lon": "-84.0",
                "display_name": "Pilot Travel Center, Georgia, USA",
                "category": "highway",
                "type": "services",
                "address": {
                    "ISO3166-2-lvl4": "US-GA",
                    "city": "Augusta",
                },
            },
            {
                "lat": "37.0976952",
                "lon": "-88.6914019",
                "display_name": "Pilot Travel Center, Paducah, Kentucky, USA",
                "category": "highway",
                "type": "services",
                "address": {
                    "ISO3166-2-lvl4": "US-KY",
                    "city": "Paducah",
                },
            },
        ])

        result = self.client.geocode(
            name="PILOT TRAVEL CENTERS #358",
            address="I-24 & US-305, EXIT 3",
            city="Paducah",
            state="KY",
        )

        self.assertEqual(result.latitude, Decimal("37.0976952"))

    def test_rejects_same_brand_in_the_wrong_city(self):
        self.session.get.return_value = self.response(payload=[{
            "lat": "36.2051981",
            "lon": "-86.7718598",
            "display_name": "Love's Travel Stop, Nashville, Tennessee, USA",
            "category": "highway",
            "type": "services",
            "address": {
                "ISO3166-2-lvl4": "US-TN",
                "city": "Nashville",
            },
        }])

        result = self.client.geocode(
            name="Loves Travel Stop #429",
            address="I-65",
            city="Memphis",
            state="TN",
        )

        self.assertIsNone(result)

    def test_maps_network_rate_limit_and_server_errors_to_transient(self):
        cases = (
            requests.RequestException("offline"),
            self.response(status_code=429),
            self.response(status_code=503),
        )
        for outcome in cases:
            with self.subTest(outcome=outcome):
                if isinstance(outcome, Exception):
                    self.session.get.side_effect = outcome
                    self.session.get.return_value = None
                else:
                    self.session.get.side_effect = None
                    self.session.get.return_value = outcome
                with self.assertRaises(NominatimTransientError):
                    self.client.geocode(
                        name="Station",
                        address="100 Main",
                        city="Dallas",
                        state="TX",
                    )

    def test_maps_bad_status_and_malformed_payload_to_permanent(self):
        responses = (
            self.response(status_code=400),
            self.response(payload={"lat": "32"}),
            self.response(payload=[{
                "lat": "invalid", "lon": "-96",
                "category": "amenity", "type": "fuel",
                "address": {
                    "ISO3166-2-lvl4": "US-TX",
                    "city": "Dallas",
                },
            }]),
            self.response(payload=[{
                "lat": "91", "lon": "-96",
                "category": "amenity", "type": "fuel",
                "address": {
                    "ISO3166-2-lvl4": "US-TX",
                    "city": "Dallas",
                },
            }]),
        )
        for response in responses:
            with self.subTest(response=response):
                self.session.get.side_effect = None
                self.session.get.return_value = response
                with self.assertRaises(NominatimPermanentError):
                    self.client.geocode(
                        name="Station",
                        address="100 Main",
                        city="Dallas",
                        state="TX",
                    )

    def test_maps_invalid_json_to_permanent(self):
        response = self.response()
        response.json.side_effect = ValueError("bad json")
        self.session.get.return_value = response

        with self.assertRaises(NominatimPermanentError):
            self.client.geocode(
                name="Station",
                address="100 Main",
                city="Dallas",
                state="TX",
            )
