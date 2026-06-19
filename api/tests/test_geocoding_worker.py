from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from api.clients.nominatim_client import NominatimTransientError
from api.models import GeocodeJob


class GeocodingWorkerCommandTests(TestCase):
    @patch("api.management.commands.run_geocoding_worker.GeocodingService.process_job")
    def test_once_processes_the_oldest_pending_job(self, process_job):
        oldest = GeocodeJob.objects.create(id="oldest")
        GeocodeJob.objects.create(id="newest")

        call_command("run_geocoding_worker", "--once", stdout=StringIO())

        self.assertEqual(process_job.call_count, 1)
        self.assertEqual(process_job.call_args.args[0], oldest.id)
        self.assertIn("worker_id", process_job.call_args.kwargs)

    @patch("api.management.commands.run_geocoding_worker.GeocodingService.process_job")
    @patch("api.management.commands.run_geocoding_worker.GeocodingService.recover_stale_jobs")
    def test_recovers_stale_jobs_before_polling(self, recover_stale_jobs, process_job):
        recover_stale_jobs.return_value = []

        call_command("run_geocoding_worker", "--once", stdout=StringIO())

        recover_stale_jobs.assert_called_once()
        cutoff = recover_stale_jobs.call_args.kwargs["cutoff"]
        self.assertLess(cutoff, timezone.now())
        process_job.assert_not_called()

    @override_settings(GEOCODING_MAX_RETRIES=2)
    @patch("api.management.commands.run_geocoding_worker.time.sleep")
    @patch("api.management.commands.run_geocoding_worker.GeocodingService.process_job")
    def test_retries_transient_failures_with_bounded_backoff(
        self, process_job, sleep
    ):
        GeocodeJob.objects.create(id="job-1")
        process_job.side_effect = [NominatimTransientError("offline"), None]

        call_command("run_geocoding_worker", "--once", stdout=StringIO())

        self.assertEqual(process_job.call_count, 2)
        sleep.assert_called_once_with(1)

    @override_settings(GEOCODING_MAX_RETRIES=1)
    @patch("api.management.commands.run_geocoding_worker.GeocodingService.fail_job")
    @patch("api.management.commands.run_geocoding_worker.time.sleep")
    @patch("api.management.commands.run_geocoding_worker.GeocodingService.process_job")
    def test_exhausted_transient_failure_marks_job_failed(
        self, process_job, sleep, fail_job
    ):
        GeocodeJob.objects.create(id="job-1")
        process_job.side_effect = NominatimTransientError("secret upstream detail")

        call_command("run_geocoding_worker", "--once", stdout=StringIO())

        self.assertEqual(process_job.call_count, 2)
        fail_job.assert_called_once_with("job-1", "Geocoding service unavailable")

    @patch("api.management.commands.run_geocoding_worker.GeocodingService.process_job")
    def test_fresh_processing_job_is_not_selected(self, process_job):
        GeocodeJob.objects.create(
            id="active",
            status="processing",
            heartbeat_at=timezone.now(),
            worker_id="another-worker",
        )

        call_command("run_geocoding_worker", "--once", stdout=StringIO())

        process_job.assert_not_called()
