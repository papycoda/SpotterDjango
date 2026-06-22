import time
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.conf import settings

from api.models import FuelStation, CityCoordinate


# Fields updated during bulk operation — keep in sync with model logic
BULK_UPDATE_FIELDS = [
    "latitude",
    "longitude",
    "coordinate_source",
    "coordinate_quality",
    "updated_at",
]

# Coordinate sources that should be skipped (already precise)
SKIP_SOURCES = {"exact_station", "manual"}


class Command(BaseCommand):
    help = "Enriches fuel stations with approximate city centroid coordinates"

    def add_arguments(self, parser):
        parser.add_argument(
            "--only-missing",
            action="store_true",
            help="Only enrich stations that don't have any coordinates yet",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Preview changes without writing to the database",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=200,
            help="Number of stations to process per bulk update batch (default: 200)",
        )
        parser.add_argument(
            "--skip-confirmation",
            action="store_true",
            help="Skip the confirmation prompt before bulk update",
        )

    def handle(self, *args, **options):
        only_missing = options["only_missing"]
        dry_run = options["dry_run"]
        batch_size = options["batch_size"]
        skip_confirmation = options["skip_confirmation"]

        if batch_size <= 0:
            raise CommandError("--batch-size must be a positive integer")

        start_time = time.time()

        # ------------------------------------------------------------------
        # 1. Load city coordinate cache
        # ------------------------------------------------------------------
        self.stdout.write("Loading city coordinates...")
        city_cache = self._build_city_cache()
        self.stdout.write(f"Loaded {len(city_cache)} cities into cache")

        # ------------------------------------------------------------------
        # 2. Collect stations to process
        # ------------------------------------------------------------------
        self.stdout.write("Collecting stations to process...")
        query = FuelStation.objects.all()
        if only_missing:
            query = query.filter(latitude__isnull=True)

        # Use iterator() to avoid loading the entire table into memory
        stations_to_process = list(query.iterator())
        total = len(stations_to_process)
        self.stdout.write(f"Total stations to process: {total}")

        if total == 0:
            self.stdout.write(self.style.WARNING("No stations to process."))
            return

        # ------------------------------------------------------------------
        # 3. Match stations to city centroids
        # ------------------------------------------------------------------
        matched = 0
        skipped = 0
        unmatched_pairs = set()
        stations_to_update = []

        for station in stations_to_process:
            # Skip stations that already have precise coordinates
            if station.coordinate_source in SKIP_SOURCES:
                skipped += 1
                continue

            # Guard against null city/state values
            city = (station.city or "").strip().lower()
            state = (station.state or "").strip().lower()

            if not city or not state:
                unmatched_pairs.add(
                    (station.city or "<missing>", station.state or "<missing>")
                )
                continue

            key = (city, state)
            if key in city_cache:
                lat, lon = city_cache[key]
                station.latitude = lat
                station.longitude = lon
                station.coordinate_source = "city_centroid"
                station.coordinate_quality = "approximate"
                stations_to_update.append(station)
                matched += 1
            else:
                unmatched_pairs.add((station.city, station.state))

        # ------------------------------------------------------------------
        # 4. Bulk update matched stations
        # ------------------------------------------------------------------
        if not stations_to_update:
            self.stdout.write(self.style.WARNING("No stations matched for enrichment."))
        else:
            self._report_bulk_update(
                stations_to_update,
                batch_size,
                dry_run,
                skip_confirmation,
            )

        # ------------------------------------------------------------------
        # 5. Summary
        # ------------------------------------------------------------------
        elapsed = time.time() - start_time
        self.stdout.write("\n" + "=" * 60)
        self.stdout.write(self.style.SUCCESS("\nEnrichment complete!"))
        self.stdout.write(f"  Total stations processed: {total}")
        self.stdout.write(f"  Matched and enriched: {matched}")
        self.stdout.write(f"  Skipped (already had exact/manual coordinates): {skipped}")
        self.stdout.write(f"  Unmatched city/state pairs: {len(unmatched_pairs)}")
        self.stdout.write(f"  Elapsed time: {elapsed:.2f}s")

        if unmatched_pairs:
            self.stdout.write("\nUnmatched pairs (first 50):")
            for city, state in sorted(unmatched_pairs)[:50]:
                self.stdout.write(f"  - {city}, {state}")
            if len(unmatched_pairs) > 50:
                self.stdout.write(f"  ... and {len(unmatched_pairs) - 50} more")

    # ------------------------------------------------------------------
    # Helper methods
    # ------------------------------------------------------------------

    def _build_city_cache(self):
        """Return a dict mapping (city_lower, state_lower) -> (lat, lon)."""
        cache = {}
        for city_coord in CityCoordinate.objects.all():
            key = (city_coord.city.lower(), city_coord.state.lower())
            cache[key] = (city_coord.latitude, city_coord.longitude)
        return cache

    def _report_bulk_update(
        self,
        stations_to_update,
        batch_size,
        dry_run,
        skip_confirmation,
    ):
        """Execute (or preview) the bulk update in batches."""
        count = len(stations_to_update)

        if dry_run:
            self.stdout.write(
                self.style.WARNING(
                    f"\n[DRY RUN] Would update {count} stations in "
                    f"{(count + batch_size - 1) // batch_size} batch(es)."
                )
            )
            return

        # Confirmation prompt for safety
        if not skip_confirmation:
            self.stdout.write(
                self.style.WARNING(
                    f"\nAbout to update {count} stations. "
                    "This operation cannot be undone."
                )
            )
            confirm = input("Type 'yes' to continue: ")
            if confirm.strip().lower() != "yes":
                self.stdout.write(self.style.WARNING("Aborted by user."))
                return

        self.stdout.write(f"\nBulk updating {count} stations...")

        try:
            with transaction.atomic():
                # Process in batches to keep transaction size manageable
                for i in range(0, count, batch_size):
                    batch = stations_to_update[i : i + batch_size]
                    FuelStation.objects.bulk_update(batch, BULK_UPDATE_FIELDS)
                    self.stdout.write(
                        f"  Updated batch {i // batch_size + 1}/"
                        f"{(count + batch_size - 1) // batch_size} "
                        f"({len(batch)} stations)"
                    )
        except Exception as exc:
            raise CommandError(f"Bulk update failed: {exc}") from exc

        self.stdout.write(
            self.style.SUCCESS(f"Successfully updated {count} stations!")
        )
