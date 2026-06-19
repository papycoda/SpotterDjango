from datetime import timedelta
from decimal import Decimal
from unittest.mock import Mock

from django.test import TestCase
from django.utils import timezone

from api.clients.nominatim_client import (
    GeocodingResult,
    NominatimPermanentError,
    NominatimTransientError,
)
from api.models import FuelStation, GeocodeJob
from api.services.geocoding_service import GeocodingService


class GeocodingServiceTests(TestCase):
    def create_station(self, station_id, *, status="pending", coordinates=False):
        return FuelStation.objects.create(
            id=station_id,
            opis_truckstop_id=station_id,
            rack_id=f"rack-{station_id}",
            name=f"Station {station_id}",
            address="100 Main Street",
            city="Dallas",
            state="TX",
            price_per_gallon=Decimal("3.45"),
            geocoding_status=status,
            latitude=Decimal("32.7767000") if coordinates else None,
            longitude=Decimal("-96.7970000") if coordinates else None,
        )

    def test_claims_a_deterministic_bounded_pending_batch(self):
        self.create_station("3")
        self.create_station("1")
        self.create_station("2")
        self.create_station("4", status="failed")
        self.create_station("5", status="success", coordinates=True)

        job = GeocodingService.create_job(limit=2, retry_failed=False)

        self.assertEqual(job.status, "pending")
        self.assertEqual(job.total_stations, 2)
        self.assertEqual(
            list(job.stations.order_by("pk").values_list("pk", flat=True)),
            ["1", "2"],
        )
        self.assertEqual(
            set(job.stations.values_list("geocoding_status", flat=True)),
            {"claimed"},
        )

    def test_explicit_retry_can_claim_failed_stations(self):
        failed = self.create_station("1", status="failed")

        job = GeocodingService.create_job(limit=1, retry_failed=True)

        failed.refresh_from_db()
        self.assertEqual(failed.geocode_job, job)
        self.assertEqual(failed.geocoding_status, "claimed")
        self.assertTrue(job.retry_failed)

    def test_explicit_retry_reassigns_failure_from_an_older_job(self):
        failed = self.create_station("1", status="failed")
        older_job = GeocodeJob.objects.create(id="older", status="completed")
        failed.geocode_job = older_job
        failed.save(update_fields=["geocode_job", "updated_at"])

        retry_job = GeocodingService.create_job(limit=1, retry_failed=True)

        failed.refresh_from_db()
        self.assertEqual(failed.geocode_job, retry_job)
        self.assertEqual(failed.geocoding_status, "claimed")

    def test_creates_completed_job_when_no_station_is_eligible(self):
        self.create_station("1", status="success", coordinates=True)

        job = GeocodingService.create_job(limit=10, retry_failed=False)

        self.assertEqual(job.status, "completed")
        self.assertEqual(job.total_stations, 0)
        self.assertIsNotNone(job.completed_at)

    def test_processes_one_claimed_station_and_persists_coordinates(self):
        station = self.create_station("1")
        job = GeocodingService.create_job(limit=1, retry_failed=False)
        observed_statuses = []
        client = Mock()

        def geocode(**kwargs):
            station.refresh_from_db()
            observed_statuses.append(station.geocoding_status)
            return GeocodingResult(
                latitude=Decimal("32.7767000"),
                longitude=Decimal("-96.7970000"),
                display_name="Dallas, Texas, USA",
            )

        client.geocode.side_effect = geocode

        GeocodingService.process_job(job.id, client=client, worker_id="worker-1")

        station.refresh_from_db()
        job.refresh_from_db()
        self.assertEqual(observed_statuses, ["processing"])
        self.assertEqual(station.geocoding_status, "success")
        self.assertEqual(station.latitude, Decimal("32.7767000"))
        self.assertEqual(job.status, "completed")
        self.assertEqual(job.processed_count, 1)
        self.assertEqual(job.success_count, 1)
        self.assertEqual(job.failed_count, 0)

    def test_no_match_and_permanent_error_fail_only_the_station(self):
        self.create_station("1")
        self.create_station("2")
        job = GeocodingService.create_job(limit=2, retry_failed=False)
        client = Mock()
        client.geocode.side_effect = [
            None,
            NominatimPermanentError("sanitized"),
        ]

        GeocodingService.process_job(job.id, client=client, worker_id="worker-1")

        job.refresh_from_db()
        self.assertEqual(job.status, "completed")
        self.assertEqual(job.processed_count, 2)
        self.assertEqual(job.failed_count, 2)
        self.assertEqual(
            set(job.stations.values_list("geocoding_status", flat=True)),
            {"failed"},
        )

    def test_transient_error_returns_station_to_claimed_for_retry(self):
        station = self.create_station("1")
        job = GeocodingService.create_job(limit=1, retry_failed=False)
        client = Mock()
        client.geocode.side_effect = NominatimTransientError("offline")

        with self.assertRaises(NominatimTransientError):
            GeocodingService.process_job(job.id, client=client, worker_id="worker-1")

        station.refresh_from_db()
        job.refresh_from_db()
        self.assertEqual(station.geocoding_status, "claimed")
        self.assertEqual(job.status, "processing")
        self.assertEqual(job.processed_count, 0)

    def test_recovers_stale_processing_job_without_releasing_claims(self):
        station = self.create_station("1")
        job = GeocodingService.create_job(limit=1, retry_failed=False)
        stale_time = timezone.now() - timedelta(minutes=10)
        GeocodeJob.objects.filter(pk=job.pk).update(
            status="processing",
            worker_id="dead-worker",
            heartbeat_at=stale_time,
        )
        FuelStation.objects.filter(pk=station.pk).update(
            geocoding_status="processing"
        )

        recovered_ids = GeocodingService.recover_stale_jobs(
            cutoff=timezone.now() - timedelta(minutes=2)
        )

        station.refresh_from_db()
        job.refresh_from_db()
        self.assertEqual(recovered_ids, [job.id])
        self.assertEqual(job.status, "pending")
        self.assertEqual(job.worker_id, "")
        self.assertEqual(station.geocoding_status, "claimed")
        self.assertEqual(station.geocode_job, job)
