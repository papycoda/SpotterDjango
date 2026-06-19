"""Persisted, resumable fuel-station geocoding workflow."""

from datetime import timedelta
from uuid import uuid4

from django.conf import settings
from django.db import connection, transaction
from django.db.models import Q
from django.utils import timezone

from api.clients.nominatim_client import (
    NominatimClient,
    NominatimPermanentError,
    NominatimTransientError,
)
from api.models import FuelStation, GeocodeJob


class GeocodingJobAlreadyRunning(Exception):
    """Raised when another live worker owns a geocoding job."""


class GeocodingService:
    @staticmethod
    def has_eligible_stations(*, retry_failed=False):
        eligibility = Q(geocoding_status="pending", geocode_job__isnull=True)
        if retry_failed:
            eligibility |= Q(geocoding_status="failed")
        return FuelStation.objects.filter(
            Q(latitude__isnull=True) | Q(longitude__isnull=True),
            eligibility,
        ).exists()

    @staticmethod
    @transaction.atomic
    def create_job(*, limit, retry_failed):
        job = GeocodeJob.objects.create(
            id=uuid4().hex,
            retry_failed=retry_failed,
        )
        eligible_statuses = ["pending"]
        eligibility = Q(geocoding_status="pending", geocode_job__isnull=True)
        if retry_failed:
            eligible_statuses.append("failed")
            eligibility |= Q(geocoding_status="failed")

        stations = FuelStation.objects.filter(
            Q(latitude__isnull=True) | Q(longitude__isnull=True),
            eligibility,
        ).order_by("pk")
        if connection.features.has_select_for_update:
            lock_options = {}
            if connection.features.has_select_for_update_skip_locked:
                lock_options["skip_locked"] = True
            stations = stations.select_for_update(**lock_options)
        station_ids = list(stations.values_list("pk", flat=True)[:limit])

        if not station_ids:
            now = timezone.now()
            job.status = "completed"
            job.completed_at = now
            job.save(update_fields=["status", "completed_at"])
            return job

        FuelStation.objects.filter(
            pk__in=station_ids,
            geocoding_status__in=eligible_statuses,
        ).update(geocoding_status="claimed", geocode_job=job)
        job.total_stations = FuelStation.objects.filter(geocode_job=job).count()
        job.save(update_fields=["total_stations"])
        return job

    @staticmethod
    @transaction.atomic
    def _start_job(job_id, worker_id):
        job = GeocodeJob.objects.select_for_update().get(pk=job_id)
        if job.status in {"completed", "failed"}:
            return False

        now = timezone.now()
        fresh_cutoff = now - timedelta(
            seconds=settings.GEOCODING_STALE_AFTER_SECONDS
        )
        owned_by_other_worker = (
            job.status == "processing"
            and job.worker_id
            and job.worker_id != worker_id
            and job.heartbeat_at is not None
            and job.heartbeat_at >= fresh_cutoff
        )
        if owned_by_other_worker:
            raise GeocodingJobAlreadyRunning("Geocoding job has a live worker")

        if job.status == "processing" and job.heartbeat_at is not None:
            FuelStation.objects.filter(
                geocode_job=job,
                geocoding_status="processing",
            ).update(geocoding_status="claimed")
        job.status = "processing"
        job.worker_id = worker_id
        job.heartbeat_at = now
        job.error_message = None
        job.save(
            update_fields=["status", "worker_id", "heartbeat_at", "error_message"]
        )
        return True

    @staticmethod
    def process_job(job_id, *, client=None, worker_id="direct-worker"):
        if not GeocodingService._start_job(job_id, worker_id):
            return
        client = client or NominatimClient()

        while True:
            with transaction.atomic():
                station = (
                    FuelStation.objects.select_for_update()
                    .filter(
                        geocode_job_id=job_id,
                        geocoding_status="claimed",
                    )
                    .order_by("pk")
                    .first()
                )
                if station is None:
                    break
                station.geocoding_status = "processing"
                station.save(update_fields=["geocoding_status", "updated_at"])
                GeocodeJob.objects.filter(pk=job_id).update(
                    heartbeat_at=timezone.now()
                )

            try:
                result = client.geocode(
                    name=station.name,
                    address=station.address,
                    city=station.city,
                    state=station.state,
                )
            except NominatimTransientError:
                FuelStation.objects.filter(
                    pk=station.pk,
                    geocode_job_id=job_id,
                    geocoding_status="processing",
                ).update(geocoding_status="claimed")
                GeocodingService._refresh_counts(job_id)
                raise
            except NominatimPermanentError:
                result = None

            if result is None:
                FuelStation.objects.filter(pk=station.pk).update(
                    geocoding_status="failed",
                    latitude=None,
                    longitude=None,
                )
            else:
                FuelStation.objects.filter(pk=station.pk).update(
                    geocoding_status="success",
                    latitude=result.latitude,
                    longitude=result.longitude,
                )
            GeocodingService._refresh_counts(job_id)

        GeocodingService._complete_job(job_id)

    @staticmethod
    def _refresh_counts(job_id):
        stations = FuelStation.objects.filter(geocode_job_id=job_id)
        success_count = stations.filter(geocoding_status="success").count()
        failed_count = stations.filter(geocoding_status="failed").count()
        GeocodeJob.objects.filter(pk=job_id).update(
            processed_count=success_count + failed_count,
            success_count=success_count,
            failed_count=failed_count,
            heartbeat_at=timezone.now(),
        )

    @staticmethod
    @transaction.atomic
    def _complete_job(job_id):
        GeocodingService._refresh_counts(job_id)
        GeocodeJob.objects.filter(pk=job_id).update(
            status="completed",
            worker_id="",
            completed_at=timezone.now(),
            heartbeat_at=timezone.now(),
        )

    @staticmethod
    @transaction.atomic
    def fail_job(job_id, message="Geocoding job failed"):
        job = GeocodeJob.objects.select_for_update().get(pk=job_id)
        FuelStation.objects.filter(
            geocode_job=job,
            geocoding_status__in=["claimed", "processing"],
        ).update(geocoding_status="failed")
        GeocodingService._refresh_counts(job_id)
        job.refresh_from_db()
        job.status = "failed"
        job.worker_id = ""
        job.error_message = message[:255]
        job.completed_at = timezone.now()
        job.save(
            update_fields=["status", "worker_id", "error_message", "completed_at"]
        )

    @staticmethod
    def recover_stale_jobs(*, cutoff):
        recovered_ids = []
        stale_ids = list(
            GeocodeJob.objects.filter(
                status="processing",
                heartbeat_at__lt=cutoff,
            )
            .order_by("created_at")
            .values_list("pk", flat=True)
        )
        for job_id in stale_ids:
            with transaction.atomic():
                job = GeocodeJob.objects.select_for_update().get(pk=job_id)
                if job.status != "processing" or job.heartbeat_at >= cutoff:
                    continue
                FuelStation.objects.filter(
                    geocode_job=job,
                    geocoding_status="processing",
                ).update(geocoding_status="claimed")
                job.status = "pending"
                job.worker_id = ""
                job.heartbeat_at = None
                job.save(update_fields=["status", "worker_id", "heartbeat_at"])
                recovered_ids.append(job_id)
        return recovered_ids
