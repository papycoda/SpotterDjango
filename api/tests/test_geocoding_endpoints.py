from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from api.models import FuelStation, GeocodeJob


class GeocodingEndpointTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff_user = get_user_model().objects.create_user(
            username="geocoding-admin",
            password="test-password",
            is_staff=True,
        )

    def setUp(self):
        self.client.force_authenticate(self.staff_user)

    def create_station(self, station_id, *, geocoding_status="pending"):
        return FuelStation.objects.create(
            id=station_id,
            opis_truckstop_id=station_id,
            rack_id=f"rack-{station_id}",
            name=f"Station {station_id}",
            address="100 Main Street",
            city="Dallas",
            state="TX",
            price_per_gallon=Decimal("3.45"),
            geocoding_status=geocoding_status,
        )

    @patch("api.tasks.geocode_stations_task.delay")
    def test_post_claims_a_job_without_dispatching_celery(self, delay):
        self.create_station("1")
        self.create_station("2")

        response = self.client.post(
            reverse("admin-geocode-stations"),
            {"limit": 1},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(response.data["status"], "pending")
        self.assertEqual(response.data["total_stations"], 1)
        self.assertEqual(GeocodeJob.objects.count(), 1)
        delay.assert_not_called()

    def test_post_returns_completed_job_when_there_is_no_work(self):
        response = self.client.post(
            reverse("admin-geocode-stations"),
            {},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], "completed")
        self.assertEqual(response.data["total_stations"], 0)

    def test_post_validates_limit(self):
        response = self.client.post(
            reverse("admin-geocode-stations"),
            {"limit": 2001},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("limit", response.data)

    def test_post_can_explicitly_retry_failed_stations(self):
        failed = self.create_station("1", geocoding_status="failed")

        response = self.client.post(
            reverse("admin-geocode-stations"),
            {"retry_failed": True},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        failed.refresh_from_db()
        self.assertEqual(failed.geocoding_status, "claimed")

    def test_status_returns_aggregate_counts_and_latest_job(self):
        for index, station_status in enumerate(
            ("pending", "claimed", "processing", "success", "failed"),
            start=1,
        ):
            station = self.create_station(
                str(index),
                geocoding_status=station_status,
            )
            if station_status == "success":
                station.latitude = Decimal("32.7767000")
                station.longitude = Decimal("-96.7970000")
                station.save(update_fields=["latitude", "longitude", "updated_at"])
        GeocodeJob.objects.create(id="older", status="completed")
        latest = GeocodeJob.objects.create(id="latest", status="processing")

        response = self.client.get(reverse("admin-geocode-status"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["counts"]["total"], 5)
        for station_status in ("pending", "claimed", "processing", "success", "failed"):
            self.assertEqual(response.data["counts"][station_status], 1)
        self.assertEqual(response.data["latest_job"]["id"], latest.id)
