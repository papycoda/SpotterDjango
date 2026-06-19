from decimal import Decimal

from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from api.models import FuelStation


class FuelStationsListTests(APITestCase):
    def create_station(self, station_id, *, state="TX", price="3.45"):
        return FuelStation.objects.create(
            id=station_id,
            opis_truckstop_id=station_id,
            rack_id=f"rack-{station_id}",
            name=f"Station {station_id}",
            address="100 Main Street",
            city="Dallas",
            state=state,
            price_per_gallon=Decimal(price),
        )

    def test_returns_stations_from_the_database(self):
        self.create_station("1")
        self.create_station("2", state="OK", price="3.25")

        response = self.client.get(reverse("fuel-stations-list"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 2)
        self.assertEqual(
            [station["id"] for station in response.data["results"]],
            ["1", "2"],
        )
        public_fields = set(response.data["results"][0])
        self.assertNotIn("geocoding_status", public_fields)
        self.assertNotIn("created_at", public_fields)
        self.assertNotIn("updated_at", public_fields)

    def test_filters_by_state_and_price_range(self):
        self.create_station("1", state="TX", price="3.10")
        self.create_station("2", state="TX", price="3.50")
        self.create_station("3", state="OK", price="3.30")

        response = self.client.get(
            reverse("fuel-stations-list"),
            {"state": "tx", "min_price": "3.25", "max_price": "3.75"},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["id"], "2")

    def test_rejects_page_size_above_limit(self):
        response = self.client.get(
            reverse("fuel-stations-list"),
            {"page_size": "201"},
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("page_size", response.data)

    def test_rejects_minimum_price_above_maximum_price(self):
        response = self.client.get(
            reverse("fuel-stations-list"),
            {"min_price": "4.00", "max_price": "3.00"},
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("max_price", response.data)

    def test_accepts_zero_as_a_price_filter(self):
        self.create_station("1", price="3.45")

        response = self.client.get(
            reverse("fuel-stations-list"),
            {"max_price": "0"},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 0)

    def test_ignores_authorization_header_on_public_station_list(self):
        self.create_station("1")

        response = self.client.get(
            reverse("fuel-stations-list"),
            HTTP_AUTHORIZATION="Basic not-valid-base64",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 1)
