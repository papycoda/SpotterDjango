from datetime import timedelta
from decimal import Decimal
from unittest.mock import Mock

from django.test import TestCase
from django.utils import timezone

from api.clients.nominatim_client import (
    CURRENT_STRATEGY_VERSION,
    GeocodingResult,
    GeocodingFailure,
    NominatimNoMatchError,
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

    def test_retry_failed_skips_rows_already_attempted_by_current_strategy(self):
        stale = self.create_station("1", status="failed")
        current = self.create_station("2", status="failed")
        stale.geocoding_strategy_version = CURRENT_STRATEGY_VERSION - 1
        stale.save(update_fields=["geocoding_strategy_version", "updated_at"])
        current.geocoding_strategy_version = CURRENT_STRATEGY_VERSION
        current.save(update_fields=["geocoding_strategy_version", "updated_at"])

        job = GeocodingService.create_job(limit=10, retry_failed=True)

        self.assertEqual(list(job.stations.values_list("pk", flat=True)), ["1"])

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
                stage=1,
                confidence="high",
            )

        client.geocode.side_effect = geocode

        GeocodingService.process_job(job.id, client=client, worker_id="worker-1")

        station.refresh_from_db()
        job.refresh_from_db()
        self.assertEqual(observed_statuses, ["processing"])
        self.assertEqual(station.geocoding_status, "success")
        self.assertEqual(station.latitude, Decimal("32.7767000"))
        self.assertEqual(station.geocoding_confidence, "high")
        self.assertEqual(station.geocoding_stage, 1)
        self.assertEqual(job.status, "completed")
        self.assertEqual(job.processed_count, 1)
        self.assertEqual(job.success_count, 1)
        self.assertEqual(job.failed_count, 0)
        client.geocode.assert_called_once_with(
            name=station.name,
            address=station.address,
            city=station.city,
            state=station.state,
        )

    def test_no_match_and_permanent_error_fail_only_the_station(self):
        self.create_station("1")
        self.create_station("2")
        job = GeocodingService.create_job(limit=2, retry_failed=False)
        client = Mock()
        client.geocode.side_effect = [
            GeocodingFailure(reason="no_match_osm", transient=False),
            GeocodingFailure(reason="not_fuel_station", transient=False),
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
        # Check that failure reasons are recorded
        station1 = job.stations.first()
        station1.refresh_from_db()
        self.assertIn(station1.geocoding_failure_reason, ["no_match_osm", "not_fuel_station"])

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

    def test_records_failure_reason_on_osm_no_match(self):
        station = self.create_station("1")
        job = GeocodingService.create_job(limit=1, retry_failed=False)
        client = Mock()
        client.geocode.return_value = GeocodingFailure(
            reason="no_match_osm",
            transient=False
        )

        GeocodingService.process_job(job.id, client=client, worker_id="worker-1")

        station.refresh_from_db()
        job.refresh_from_db()
        self.assertEqual(station.geocoding_status, "failed")
        self.assertEqual(station.geocoding_failure_reason, "no_match_osm")
        self.assertIsNone(station.latitude)
        self.assertIsNone(station.longitude)
        self.assertIsNone(station.geocoding_confidence)
        self.assertIsNone(station.geocoding_stage)

    def test_records_medium_confidence_from_stage_2(self):
        station = self.create_station("1")
        job = GeocodingService.create_job(limit=1, retry_failed=False)
        client = Mock()

        def geocode(**kwargs):
            return GeocodingResult(
                latitude=Decimal("32.7767000"),
                longitude=Decimal("-96.7970000"),
                display_name="Shell, Dallas, Texas, USA",
                stage=2,
                confidence="medium",
            )

        client.geocode.side_effect = geocode

        GeocodingService.process_job(job.id, client=client, worker_id="worker-1")

        station.refresh_from_db()
        job.refresh_from_db()
        self.assertEqual(station.geocoding_status, "success")
        self.assertEqual(station.geocoding_confidence, "medium")
        self.assertEqual(station.geocoding_stage, 2)

    def test_rejects_low_confidence_stage_3_result(self):
        station = self.create_station("1")
        job = GeocodingService.create_job(limit=1, retry_failed=False)
        client = Mock()

        def geocode(**kwargs):
            return GeocodingResult(
                latitude=Decimal("32.7767000"),
                longitude=Decimal("-96.7970000"),
                display_name="Fuel Station, Dallas, Texas, USA",
                stage=3,
                confidence="low",
            )

        client.geocode.side_effect = geocode

        GeocodingService.process_job(job.id, client=client, worker_id="worker-1")

        station.refresh_from_db()
        job.refresh_from_db()
        self.assertEqual(station.geocoding_status, "failed")
        self.assertEqual(station.geocoding_failure_reason, "no_match_osm")
        self.assertIsNone(station.latitude)
        self.assertIsNone(station.longitude)
        self.assertIsNone(station.geocoding_confidence)
        self.assertIsNone(station.geocoding_stage)

    def test_permanent_no_match_persists_specific_reason(self):
        station = self.create_station("1")
        job = GeocodingService.create_job(limit=1, retry_failed=False)
        client = Mock()
        client.geocode.side_effect = NominatimNoMatchError("city_mismatch")

        GeocodingService.process_job(job.id, client=client, worker_id="worker-1")

        station.refresh_from_db()
        job.refresh_from_db()
        self.assertEqual(station.geocoding_status, "failed")
        self.assertEqual(station.geocoding_failure_reason, "city_mismatch")
        self.assertEqual(
            station.geocoding_strategy_version, CURRENT_STRATEGY_VERSION
        )
        self.assertEqual(job.failed_count, 1)

    def test_invalid_response_reason_is_not_downgraded_to_unknown(self):
        station = self.create_station("1")
        job = GeocodingService.create_job(limit=1, retry_failed=False)
        client = Mock()
        client.geocode.side_effect = NominatimPermanentError("invalid_response")

        GeocodingService.process_job(job.id, client=client, worker_id="worker-1")

        station.refresh_from_db()
        self.assertEqual(station.geocoding_failure_reason, "invalid_response")
        self.assertEqual(
            station.geocoding_strategy_version, CURRENT_STRATEGY_VERSION
        )

    def test_transient_error_persists_reason_while_restoring_claim(self):
        station = self.create_station("1")
        job = GeocodingService.create_job(limit=1, retry_failed=False)
        client = Mock()
        client.geocode.side_effect = NominatimTransientError("network_error")

        with self.assertRaises(NominatimTransientError):
            GeocodingService.process_job(job.id, client=client, worker_id="worker-1")

        station.refresh_from_db()
        self.assertEqual(station.geocoding_status, "claimed")
        self.assertEqual(station.geocoding_failure_reason, "network_error")
        self.assertEqual(station.geocoding_strategy_version, 0)

    def test_transient_failure_returns_station_to_claimed_for_retry(self):
        station = self.create_station("1")
        job = GeocodingService.create_job(limit=1, retry_failed=False)
        client = Mock()
        client.geocode.return_value = GeocodingFailure(
            reason="rate_limited",
            transient=True
        )

        with self.assertRaises(NominatimTransientError):
            GeocodingService.process_job(job.id, client=client, worker_id="worker-1")

        station.refresh_from_db()
        job.refresh_from_db()
        self.assertEqual(station.geocoding_status, "claimed")
        self.assertEqual(job.status, "processing")
        self.assertEqual(job.processed_count, 0)

    def test_records_city_mismatch_failure_reason(self):
        station = self.create_station("1")
        job = GeocodingService.create_job(limit=1, retry_failed=False)
        client = Mock()
        client.geocode.return_value = GeocodingFailure(
            reason="city_mismatch",
            transient=False
        )

        GeocodingService.process_job(job.id, client=client, worker_id="worker-1")

        station.refresh_from_db()
        self.assertEqual(station.geocoding_failure_reason, "city_mismatch")
        self.assertEqual(station.geocoding_status, "failed")

    def test_records_not_fuel_station_failure_reason(self):
        station = self.create_station("1")
        job = GeocodingService.create_job(limit=1, retry_failed=False)
        client = Mock()
        client.geocode.return_value = GeocodingFailure(
            reason="not_fuel_station",
            transient=False
        )

        GeocodingService.process_job(job.id, client=client, worker_id="worker-1")

        station.refresh_from_db()
        self.assertEqual(station.geocoding_failure_reason, "not_fuel_station")
        self.assertEqual(station.geocoding_status, "failed")
