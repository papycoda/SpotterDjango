from django.db import models
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from api.models import FuelStation


class Command(BaseCommand):
    help = "Generate a geocoding status report for fuel stations."

    def add_arguments(self, parser):
        parser.add_argument(
            "--strict",
            action="store_true",
            help="Exit with non-zero code if issues are found.",
        )

    def handle(self, *args, **options):
        strict = options["strict"]
        issues_found = []

        total_stations = FuelStation.objects.count()

        # Status counts
        success_count = FuelStation.objects.filter(geocoding_status="success").count()
        pending_count = FuelStation.objects.filter(geocoding_status="pending").count()
        claimed_count = FuelStation.objects.filter(geocoding_status="claimed").count()
        processing_count = FuelStation.objects.filter(geocoding_status="processing").count()
        failed_count = FuelStation.objects.filter(geocoding_status="failed").count()

        # Confidence breakdowns for success
        success_stations = FuelStation.objects.filter(geocoding_status="success")
        high_confidence_count = success_stations.filter(geocoding_confidence="high").count()
        medium_confidence_count = success_stations.filter(geocoding_confidence="medium").count()
        low_confidence_count = success_stations.filter(geocoding_confidence="low").count()
        null_confidence_count = success_stations.filter(geocoding_confidence__isnull=True).count()

        # Coordinate source breakdowns
        exact_count = FuelStation.objects.filter(coordinate_source="exact_station").count()
        city_centroid_count = FuelStation.objects.filter(coordinate_source="city_centroid").count()
        manual_count = FuelStation.objects.filter(coordinate_source="manual").count()
        unknown_source_count = FuelStation.objects.filter(coordinate_source="unknown").count()

        # Coordinate quality breakdowns
        high_quality_count = FuelStation.objects.filter(coordinate_quality="high").count()
        medium_quality_count = FuelStation.objects.filter(coordinate_quality="medium").count()
        approximate_count = FuelStation.objects.filter(coordinate_quality="approximate").count()
        unknown_quality_count = FuelStation.objects.filter(coordinate_quality="unknown").count()

        # Other success issues
        success_missing_coords = success_stations.filter(
            models.Q(latitude__isnull=True) | models.Q(longitude__isnull=True)
        ).count()

        success_low_confidence = success_stations.filter(geocoding_confidence="low").count()
        success_stage3 = success_stations.filter(geocoding_stage=3).count()

        # Invalid coordinates: latitude must be [-90, 90], longitude [-180, 180]
        invalid_coords_success = success_stations.filter(
            models.Q(latitude__lt=-90) | models.Q(latitude__gt=90) |
            models.Q(longitude__lt=-180) | models.Q(longitude__gt=180)
        ).count()

        # Route eligible
        route_eligible = FuelStation.objects.filter(
            geocoding_status="success",
            latitude__isnull=False,
            longitude__isnull=False,
            coordinate_source__in=["exact_station", "manual", "unknown"]
        ).count()

        # Get unmatched pairs (stations that don't have exact or manual or city centroid)
        unresolved_stations = FuelStation.objects.filter(
            models.Q(coordinate_source="unknown") | models.Q(latitude__isnull=True)
        )
        unresolved_count = unresolved_stations.count()
        unmatched_pairs = set()
        for station in unresolved_stations:
            pair = (station.city, station.state)
            unmatched_pairs.add(pair)

        # Print report
        self.stdout.write("\n" + "="*70)
        self.stdout.write("                          GEOCODING REPORT")
        self.stdout.write("="*70)
        self.stdout.write(f"\nTotal stations: {total_stations}")
        
        self.stdout.write("\nStatus breakdown:")
        self.stdout.write(f"  Success count: {success_count}")
        self.stdout.write(f"  Pending count: {pending_count}")
        self.stdout.write(f"  Claimed count: {claimed_count}")
        self.stdout.write(f"  Processing count: {processing_count}")
        self.stdout.write(f"  Failed count: {failed_count}")
        
        self.stdout.write("\nCoordinate source breakdown:")
        self.stdout.write(f"  Exact station coordinates: {exact_count}")
        self.stdout.write(f"  City centroid (approximate): {city_centroid_count}")
        self.stdout.write(f"  Manual coordinates: {manual_count}")
        self.stdout.write(f"  Unknown source: {unknown_source_count}")
        
        self.stdout.write("\nConfidence breakdown (success stations only):")
        self.stdout.write(f"  High confidence: {high_confidence_count}")
        self.stdout.write(f"  Medium confidence: {medium_confidence_count}")
        self.stdout.write(f"  Low confidence: {low_confidence_count}")
        self.stdout.write(f"  Null confidence: {null_confidence_count}")
        
        self.stdout.write("\nCoordinate quality breakdown:")
        self.stdout.write(f"  High quality: {high_quality_count}")
        self.stdout.write(f"  Medium quality: {medium_quality_count}")
        self.stdout.write(f"  Approximate (city centroid): {approximate_count}")
        self.stdout.write(f"  Unknown quality: {unknown_quality_count}")
        
        self.stdout.write("\nSuccess quality checks:")
        self.stdout.write(f"  Success rows missing latitude/longitude: {success_missing_coords}")
        self.stdout.write(f"  Low-confidence success rows: {success_low_confidence}")
        self.stdout.write(f"  Stage-3 success rows: {success_stage3}")
        self.stdout.write(f"  Success rows with invalid coordinates: {invalid_coords_success}")
        
        self.stdout.write("\nRoute eligibility:")
        self.stdout.write(f"  Exact/manual route-eligible stations: {route_eligible}")
        self.stdout.write(f"  Approximate city centroid stations: {city_centroid_count}")
        self.stdout.write(f"  Unresolved stations: {unresolved_count}")
        self.stdout.write(f"  Approx coordinates allowed for routing: {settings.ALLOW_APPROXIMATE_CITY_COORDINATES_FOR_ROUTE_MATCHING}")
        
        if unmatched_pairs:
            self.stdout.write(f"\nUnmatched city/state pairs: {len(unmatched_pairs)} total")
            self.stdout.write("  (Run 'python manage.py fetch_city_coordinates' to get these!)")

        # Check issues for strict mode
        if strict:
            if success_missing_coords > 0:
                issues_found.append(f"Found {success_missing_coords} success row(s) missing latitude/longitude")
            if invalid_coords_success > 0:
                issues_found.append(f"Found {invalid_coords_success} success row(s) with invalid coordinate ranges")
            if success_low_confidence > 0:
                issues_found.append(f"Found {success_low_confidence} low-confidence success row(s)")
            if success_stage3 > 0:
                issues_found.append(f"Found {success_stage3} stage-3 success row(s)")
            if route_eligible == 0:
                issues_found.append("Route-eligible station count is zero")

            if issues_found:
                self.stdout.write("\n--- Strict mode issues found:")
                for issue in issues_found:
                    self.stdout.write(self.style.ERROR(f"  - {issue}"))
                raise CommandError("Strict mode check failed")
            else:
                self.stdout.write("\n--- Strict mode: No issues found")
