from django.test import SimpleTestCase

from api import models
from api.models import FuelStation, GeocodeJob


class FuelStationModelTests(SimpleTestCase):
    def test_stores_rack_id_from_source_csv(self):
        field_names = {field.name for field in FuelStation._meta.fields}

        self.assertIn("rack_id", field_names)

    def test_supports_claimed_job_lifecycle(self):
        statuses = dict(FuelStation.GEOCODING_STATUS_CHOICES)
        field_names = {field.name for field in FuelStation._meta.fields}

        self.assertIn("claimed", statuses)
        self.assertIn("geocode_job", field_names)


class GeocodeJobModelTests(SimpleTestCase):
    def test_tracks_worker_heartbeat_and_ownership(self):
        field_names = {field.name for field in GeocodeJob._meta.fields}

        self.assertIn("heartbeat_at", field_names)
        self.assertIn("worker_id", field_names)

    def test_rate_limit_state_tracks_next_request_slot(self):
        self.assertTrue(hasattr(models, "GeocodingRateLimit"))
        field_names = {
            field.name for field in models.GeocodingRateLimit._meta.fields
        }

        self.assertIn("next_allowed_at", field_names)
