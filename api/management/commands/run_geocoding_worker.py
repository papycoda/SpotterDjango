"""Run the database-backed fuel-station geocoding worker."""

import time
from datetime import timedelta
from uuid import uuid4

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from api.clients.nominatim_client import NominatimTransientError
from api.models import GeocodeJob
from api.services.geocoding_service import (
    GeocodingJobAlreadyRunning,
    GeocodingService,
)


class Command(BaseCommand):
    help = "Process persisted fuel-station geocoding jobs without Celery."

    def add_arguments(self, parser):
        mode = parser.add_mutually_exclusive_group()
        mode.add_argument("--once", action="store_true", help="Process at most one job")
        mode.add_argument("--watch", action="store_true", help="Continue polling for jobs")
        parser.add_argument(
            "--auto-queue",
            type=int,
            metavar="BATCH_SIZE",
            help="Automatically create bounded jobs for pending stations",
        )

    def handle(self, *args, **options):
        worker_id = f"django-{uuid4().hex}"
        watch = options["watch"]
        auto_queue = options["auto_queue"]
        if auto_queue is not None and auto_queue <= 0:
            raise CommandError("--auto-queue must be positive")

        while True:
            cutoff = timezone.now() - timedelta(
                seconds=settings.GEOCODING_STALE_AFTER_SECONDS
            )
            GeocodingService.recover_stale_jobs(cutoff=cutoff)
            job = GeocodeJob.objects.filter(status="pending").order_by(
                "created_at", "pk"
            ).first()

            if (
                job is None
                and auto_queue is not None
                and GeocodingService.has_eligible_stations(retry_failed=False)
            ):
                job = GeocodingService.create_job(
                    limit=auto_queue,
                    retry_failed=False,
                )

            if job is None:
                if not watch:
                    return
                time.sleep(settings.GEOCODING_POLL_SECONDS)
                continue

            self._process_with_retries(job.id, worker_id)
            if not watch:
                return

    def _process_with_retries(self, job_id, worker_id):
        for retry_number in range(settings.GEOCODING_MAX_RETRIES + 1):
            try:
                GeocodingService.process_job(job_id, worker_id=worker_id)
                return
            except GeocodingJobAlreadyRunning:
                return
            except NominatimTransientError:
                if retry_number >= settings.GEOCODING_MAX_RETRIES:
                    GeocodingService.fail_job(
                        job_id,
                        "Geocoding service unavailable",
                    )
                    return
                time.sleep(min(2 ** retry_number, 300))
            except Exception as exc:
                GeocodingService.fail_job(job_id, "Geocoding job failed")
                raise CommandError("Geocoding job failed") from exc
