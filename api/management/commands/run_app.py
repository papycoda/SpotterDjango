"""Run the local Django server and geocoding worker under one supervisor."""

import subprocess
import sys
import time

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Run the local web server and automatic geocoding worker"

    def add_arguments(self, parser):
        parser.add_argument(
            "--addrport",
            default=settings.RUN_APP_ADDRPORT,
            help="Development server address and port",
        )
        parser.add_argument(
            "--auto-queue",
            type=int,
            default=settings.GEOCODING_AUTO_QUEUE_BATCH_SIZE,
            metavar="BATCH_SIZE",
            help="Number of pending stations claimed per automatic job",
        )

    def handle(self, *args, **options):
        batch_size = options["auto_queue"]
        if batch_size <= 0:
            raise CommandError("--auto-queue must be positive")

        manage_py = str(settings.BASE_DIR / "manage.py")
        commands = (
            (
                "web",
                [
                    sys.executable,
                    manage_py,
                    "runserver",
                    options["addrport"],
                    "--noreload",
                ],
            ),
            (
                "worker",
                [
                    sys.executable,
                    manage_py,
                    "run_geocoding_worker",
                    "--watch",
                    "--auto-queue",
                    str(batch_size),
                ],
            ),
        )
        processes = []
        try:
            for name, command in commands:
                process = subprocess.Popen(command, cwd=settings.BASE_DIR)
                processes.append((name, process))
                self.stdout.write(f"Started {name} process (PID {process.pid})")

            while True:
                for name, process in processes:
                    return_code = process.poll()
                    if return_code is not None:
                        raise CommandError(
                            f"{name} process exited with status {return_code}"
                        )
                time.sleep(0.2)
        except KeyboardInterrupt:
            self.stdout.write("Stopping application processes")
        finally:
            self._stop_processes(processes)

    @staticmethod
    def _stop_processes(processes):
        for _, process in processes:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
