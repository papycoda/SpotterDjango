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

        self.stdout.write(self.style.SUCCESS(f"Geocoding worker started with ID: {worker_id}"))
        if watch:
            self.stdout.write(f"  - Watch mode enabled (poll every {settings.GEOCODING_POLL_SECONDS}s)")
        if auto_queue:
            self.stdout.write(f"  - Auto-queue enabled (batch size: {auto_queue})")
        self.stdout.write("")

        while True:
            cutoff = timezone.now() - timedelta(
                seconds=settings.GEOCODING_STALE_AFTER_SECONDS
            )
            recovered = GeocodingService.recover_stale_jobs(cutoff=cutoff)
            if recovered:
                self.stdout.write(f"Recovered {len(recovered)} stale job(s)")
            
            job = GeocodeJob.objects.filter(status="pending").order_by(
                "created_at", "pk"
            ).first()

            if (
                job is None
                and auto_queue is not None
                and GeocodingService.has_eligible_stations(retry_failed=False)
            ):
                self.stdout.write(self.style.NOTICE("Auto-queueing new geocoding job..."))
                job = GeocodingService.create_job(
                    limit=auto_queue,
                    retry_failed=False,
                )
                self.stdout.write(f"Created job {job.id} with {job.total_stations} stations to process\n")

            if job is None:
                if not watch:
                    self.stdout.write("No jobs found. Exiting.")
                    return
                # Show we're waiting without spamming
                time_str = timezone.now().strftime("%H:%M:%S")
                self.stdout.write(f"\rWaiting for pending jobs... (last check: {time_str})", ending="")
                time.sleep(settings.GEOCODING_POLL_SECONDS)
                continue

            # Clear the waiting line and start processing
            self.stdout.write("\n")
            self._process_job_with_output(job.id, worker_id)
            
            if not watch:
                return

    def _process_job_with_output(self, job_id, worker_id):
        """Process a single job with real-time output and retries"""
        self.stdout.write(f"Processing job {job_id}")
        
        # Create our progress callback once
        def progress_callback(event_type, station, job_ref, *args):
            if event_type == "start":
                self.stdout.write(f"  [{job_ref.processed_count+1}/{job_ref.total_stations}] Geocoding: {station.name} - {station.city}, {station.state}")
            elif event_type == "success":
                confidence, stage = args
                self.stdout.write(self.style.SUCCESS(f"    SUCCESS: {confidence} confidence (stage {stage})"))
            elif event_type == "failed":
                reason = args[0]
                self.stdout.write(self.style.ERROR(f"    FAILED: {reason}"))
            elif event_type == "transient_error":
                reason = args[0]
                self.stdout.write(self.style.WARNING(f"    Transient error: {reason}"))
        
        for retry_number in range(settings.GEOCODING_MAX_RETRIES + 1):
            try:
                GeocodingService.process_job(
                    job_id, 
                    worker_id=worker_id, 
                    progress_callback=progress_callback
                )
                
                job = GeocodeJob.objects.get(pk=job_id)
                self.stdout.write("")
                self.stdout.write(self.style.SUCCESS(f"Job {job_id} complete!"))
                self.stdout.write(f"  - Success: {job.success_count}")
                self.stdout.write(f"  - Failed: {job.failed_count}")
                return
            except GeocodingJobAlreadyRunning:
                self.stdout.write(f"Job {job_id} already running, skipping.")
                return
            except NominatimTransientError:
                if retry_number >= settings.GEOCODING_MAX_RETRIES:
                    self.stdout.write(self.style.ERROR(f"Job {job_id} failed permanently due to transient errors"))
                    GeocodingService.fail_job(
                        job_id,
                        "Geocoding service unavailable",
                    )
                    return
                wait_time = min(2 ** retry_number, 300)
                self.stdout.write(self.style.WARNING(f"  Transient error, retrying in {wait_time}s (retry {retry_number+1}/{settings.GEOCODING_MAX_RETRIES+1})"))
                time.sleep(wait_time)
            except Exception as exc:
                self.stdout.write(self.style.ERROR(f"Job {job_id} failed: {str(exc)}"))
                GeocodingService.fail_job(job_id, "Geocoding job failed")
                raise CommandError("Geocoding job failed") from exc
