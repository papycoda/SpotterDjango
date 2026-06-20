from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import Mock

import requests
from django.test import SimpleTestCase, TestCase, override_settings

from api.clients.nominatim_client import (
    NominatimClient,
    NominatimNoMatchError,
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
            interval_seconds=1, clock=lambda: now, sleeper=sleeps.append
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
        self.client = NominatimClient(session=self.session, rate_limiter=self.limiter)

    @staticmethod
    def response(status_code=200, payload=None):
        response = Mock(status_code=status_code)
        response.json.return_value = payload
        return response

    @staticmethod
    def candidate(**overrides):
        value = {
            "lat": "32.7767",
            "lon": "-96.7970",
            "display_name": "Love's Travel Stop, 100 Main Street, Dallas, Texas",
            "category": "amenity",
            "type": "fuel",
            "address": {
                "house_number": "100",
                "road": "Main Street",
                "city": "Dallas",
                "ISO3166-2-lvl4": "US-TX",
            },
            "namedetails": {"name": "Love's Travel Stop", "brand": "Love's"},
            "extratags": {"brand": "Love's", "amenity": "fuel"},
        }
        value.update(overrides)
        return value

    def test_high_confidence_requires_identity_and_address_evidence(self):
        self.session.get.return_value = self.response(payload=[self.candidate()])

        result = self.client.geocode(
            name="Love's Travel Stop #429",
            address="100 Main St",
            city="Dallas",
            state="TX",
        )

        self.assertEqual(result.confidence, "high")
        self.assertEqual(result.stage, 1)
        self.assertEqual(result.latitude, Decimal("32.7767000"))
        params = self.session.get.call_args.kwargs["params"]
        self.assertEqual(params["namedetails"], 1)
        self.assertEqual(params["extratags"], 1)
        self.assertEqual(params["limit"], 10)

    def test_medium_confidence_accepts_identity_without_address_evidence(self):
        candidate = self.candidate(
            display_name="Pilot Travel Center, Memphis, Tennessee",
            address={"city": "Memphis", "ISO3166-2-lvl4": "US-TN"},
            namedetails={"name": "Pilot Travel Center", "brand": "Pilot"},
            extratags={"brand": "Pilot", "amenity": "fuel"},
        )
        self.session.get.return_value = self.response(payload=[candidate])

        result = self.client.geocode(
            name="Pilot Travel Center #10",
            address="I-40 Exit 1",
            city="Memphis",
            state="TN",
        )

        self.assertEqual(result.confidence, "medium")
        self.assertEqual(result.stage, 1)

    def test_never_accepts_an_unrelated_same_city_station(self):
        unrelated = self.candidate(
            display_name="Quick Fuel, Florence, Kansas",
            address={"city": "Florence", "ISO3166-2-lvl4": "US-KS"},
            namedetails={"name": "Quick Fuel", "brand": "Quick Fuel"},
            extratags={"amenity": "fuel"},
        )
        self.session.get.side_effect = [
            self.response(payload=[unrelated]),
            self.response(payload=[]),
        ]

        with self.assertRaises(NominatimNoMatchError) as raised:
            self.client.geocode(
                name="Independent Truck Stop",
                address="US-50 Exit 2",
                city="Florence",
                state="KS",
            )

        self.assertEqual(raised.exception.reason, "no_match_osm")
        self.assertEqual(self.session.get.call_count, 2)

    def test_generic_station_name_is_not_identity_evidence(self):
        candidate = self.candidate(
            namedetails={"name": "Quick Fuel"},
            extratags={"brand": "Quick Fuel", "amenity": "fuel"},
        )
        self.session.get.side_effect = [
            self.response(payload=[candidate]), self.response(payload=[])
        ]

        with self.assertRaises(NominatimNoMatchError):
            self.client.geocode(
                name="Station", address="100 Main St", city="Dallas", state="TX"
            )

    def test_rejects_state_locality_and_non_fuel_candidates_with_specific_reason(self):
        cases = (
            ({"address": {"city": "Dallas", "ISO3166-2-lvl4": "US-OK"}}, "state_mismatch"),
            ({"address": {"city": "Austin", "ISO3166-2-lvl4": "US-TX"}}, "city_mismatch"),
            ({"category": "highway", "type": "primary"}, "not_fuel_station"),
        )
        for overrides, reason in cases:
            with self.subTest(reason=reason):
                self.session.get.reset_mock()
                self.session.get.side_effect = [
                    self.response(payload=[self.candidate(**overrides)]),
                    self.response(payload=[]),
                ]
                with self.assertRaises(NominatimNoMatchError) as raised:
                    self.client.geocode(
                        name="Love's Travel Stop",
                        address="100 Main St",
                        city="Dallas",
                        state="TX",
                    )
                self.assertEqual(raised.exception.reason, reason)

    def test_convenience_shop_requires_explicit_fuel_evidence(self):
        candidate = self.candidate(
            category="shop", type="convenience", extratags={"brand": "Love's"}
        )
        self.session.get.side_effect = [
            self.response(payload=[candidate]), self.response(payload=[])
        ]

        with self.assertRaises(NominatimNoMatchError) as raised:
            self.client.geocode(
                name="Love's Travel Stop", address="100 Main", city="Dallas", state="TX"
            )

        self.assertEqual(raised.exception.reason, "not_fuel_station")

    def test_network_and_upstream_failures_remain_typed_transient_errors(self):
        cases = (
            (requests.RequestException("offline"), "network_error"),
            (self.response(status_code=429), "rate_limited"),
            (self.response(status_code=503), "upstream_error"),
        )
        for outcome, reason in cases:
            with self.subTest(reason=reason):
                self.session.get.side_effect = outcome if isinstance(outcome, Exception) else None
                self.session.get.return_value = None if isinstance(outcome, Exception) else outcome
                with self.assertRaises(NominatimTransientError) as raised:
                    self.client.geocode(
                        name="Station", address="100 Main", city="Dallas", state="TX"
                    )
                self.assertEqual(raised.exception.reason, reason)

    def test_bad_response_and_invalid_coordinates_are_permanent(self):
        cases = (
            (self.response(status_code=400), "upstream_error"),
            (self.response(payload={"lat": "32"}), "invalid_response"),
            (self.response(payload=[self.candidate(lat="invalid")]), "invalid_coordinates"),
            (self.response(payload=[self.candidate(lat="91")]), "invalid_coordinates"),
        )
        for response, reason in cases:
            with self.subTest(reason=reason):
                self.session.get.side_effect = None
                self.session.get.return_value = response
                with self.assertRaises(NominatimPermanentError) as raised:
                    self.client.geocode(
                        name="Love's", address="100 Main", city="Dallas", state="TX"
                    )
                self.assertEqual(raised.exception.reason, reason)

    def test_invalid_json_is_permanent(self):
        response = self.response()
        response.json.side_effect = ValueError("bad json")
        self.session.get.return_value = response

        with self.assertRaises(NominatimPermanentError) as raised:
            self.client.geocode(
                name="Station", address="100 Main", city="Dallas", state="TX"
            )

        self.assertEqual(raised.exception.reason, "invalid_response")
