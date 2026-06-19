from unittest.mock import patch

from django.test import TestCase

from api.models import GeocodeJob
from api.tasks import geocode_stations_task


class OptionalGeocodingTaskTests(TestCase):
    @patch("api.tasks.GeocodingService.process_job")
    def test_task_is_a_thin_persisted_job_adapter(self, process_job):
        job = GeocodeJob.objects.create(id="job-1")

        geocode_stations_task.run(job.id)

        process_job.assert_called_once_with(job.id, worker_id="celery-job-1")

    @patch("api.tasks.GeocodingService.process_job")
    def test_missing_job_is_a_safe_noop(self, process_job):
        result = geocode_stations_task.run("missing")

        self.assertIsNone(result)
        process_job.assert_not_called()
