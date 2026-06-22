"""Generate a geocoding status report for fuel stations."""

from django.db import models
from django.core.management.base import BaseCommand, CommandError

from api.models import FuelStation


class Command(BaseCommand):
    help = "Generate a geocoding status report for fuel stations."

    def add_arguments(self, parser):
        parser.add_argument(
            "--strict",
            action="store_true",
            help="Exit with non-zero code if failure classes are detected.",
        )

    def handle(self, *args, **options):
        strict = options["strict"]

        # Get all counts
        report = self._generate_report()

        # Print report
        self._print_report(report)

        # Handle strict mode
        if strict:
            failure_reasons = self._check_strict_conditions(report)
            if failure_reasons:
                self.stdout.write("")
                self.stdout.write(self.style.ERROR("--- Strict mode: FAILURE CLASSES DETECTED ---"))
                for reason in failure_reasons:
                    self.stdout.write(self.style.ERROR(f"  - {reason}"))
                raise CommandError("Strict mode check failed")
            else:
                self.stdout.write("")
                self.stdout.write(self.style.SUCCESS("--- Strict mode: ALL CHECKS PASSED ---"))

    def _generate_report(self):
        """Generate all report data."""
        total = FuelStation.objects.count()

        # State counts
        states = {
            "success": FuelStation.objects.filter(geocoding_status="success").count(),
            "failed": FuelStation.objects.filter(geocoding_status="failed").count(),
            "pending": FuelStation.objects.filter(geocoding_status="pending").count(),
            "claimed": FuelStation.objects.filter(geocoding_status="claimed").count(),
            "processing": FuelStation.objects.filter(geocoding_status="processing").count(),
        }

        # Confidence counts (all stations)
        confidence = {
            "high": FuelStation.objects.filter(geocoding_confidence="high").count(),
            "medium": FuelStation.objects.filter(geocoding_confidence="medium").count(),
            "low": FuelStation.objects.filter(geocoding_confidence="low").count(),
            "unset": FuelStation.objects.filter(geocoding_confidence__isnull=True).count(),
        }

        # Anomaly counts
        success_stations = FuelStation.objects.filter(geocoding_status="success")
        anomalies = {
            "null_coordinates_in_success": success_stations.filter(
                models.Q(latitude__isnull=True) | models.Q(longitude__isnull=True)
            ).count(),
            "low_confidence_in_success": success_stations.filter(
                geocoding_confidence="low"
            ).count(),
        }

        # Eligibility count (successful geocoding AND valid coordinates)
        eligible = success_stations.filter(
            latitude__isnull=False,
            longitude__isnull=False,
        ).count()

        return {
            "total": total,
            "states": states,
            "confidence": confidence,
            "anomalies": anomalies,
            "eligible": eligible,
        }

    def _print_report(self, report):
        """Print the formatted report."""
        self.stdout.write("")
        self.stdout.write("=" * 60)
        self.stdout.write("               GEOCODING STATUS REPORT")
        self.stdout.write("=" * 60)
        self.stdout.write("")

        # Total
        self.stdout.write(f"Total Stations: {report['total']}")
        self.stdout.write("")

        # State distribution
        self.stdout.write("Geocoding Status:")
        self.stdout.write(f"  Success:    {report['states']['success']:>6}")
        self.stdout.write(f"  Failed:     {report['states']['failed']:>6}")
        self.stdout.write(f"  Pending:    {report['states']['pending']:>6}")
        self.stdout.write(f"  Claimed:    {report['states']['claimed']:>6}")
        self.stdout.write(f"  Processing: {report['states']['processing']:>6}")
        self.stdout.write("")

        # Confidence distribution
        self.stdout.write("Confidence Distribution (all stations):")
        self.stdout.write(f"  High:   {report['confidence']['high']:>6}")
        self.stdout.write(f"  Medium: {report['confidence']['medium']:>6}")
        self.stdout.write(f"  Low:    {report['confidence']['low']:>6}")
        self.stdout.write(f"  Unset:  {report['confidence']['unset']:>6}")
        self.stdout.write("")

        # Anomaly counts
        self.stdout.write("Anomalies:")
        null_coords = report['anomalies']['null_coordinates_in_success']
        low_conf = report['anomalies']['low_confidence_in_success']
        if null_coords > 0:
            self.stdout.write(
                self.style.WARNING(
                    f"  Null coords in success: {null_coords:>6}"
                )
            )
        else:
            self.stdout.write(f"  Null coords in success: {null_coords:>6}")

        if low_conf > 0:
            self.stdout.write(
                self.style.WARNING(
                    f"  Low confidence in success: {low_conf:>6}"
                )
            )
        else:
            self.stdout.write(f"  Low confidence in success: {low_conf:>6}")
        self.stdout.write("")

        # Eligibility
        self.stdout.write("Route Eligibility:")
        eligible = report['eligible']
        if eligible == report['states']['success']:
            self.stdout.write(
                self.style.SUCCESS(
                    f"  Eligible (success + valid coords): {eligible:>6}"
                )
            )
        else:
            self.stdout.write(
                self.style.WARNING(
                    f"  Eligible (success + valid coords): {eligible:>6}"
                )
            )
        self.stdout.write("")

    def _check_strict_conditions(self, report):
        """Check strict mode conditions and return list of failure reasons."""
        failures = []

        if report['states']['failed'] > 0:
            failures.append(
                f"Failed stations: {report['states']['failed']} > 0"
            )

        if report['states']['pending'] > 0:
            failures.append(
                f"Pending stations: {report['states']['pending']} > 0"
            )

        if report['anomalies']['null_coordinates_in_success'] > 0:
            failures.append(
                f"Null coordinates in success: "
                f"{report['anomalies']['null_coordinates_in_success']} > 0"
            )

        if report['anomalies']['low_confidence_in_success'] > 0:
            failures.append(
                f"Low confidence in success: "
                f"{report['anomalies']['low_confidence_in_success']} > 0"
            )

        return failures
