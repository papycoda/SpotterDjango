import time

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from api.models import FuelStation, CityCoordinate


# Coordinate sources that should be skipped because they are already precise.
SKIP_SOURCES = {"exact_station", "manual"}


class Command(BaseCommand):
    help = "Enriches fuel stations with approximate city centroid coordinates"

    def add_arguments(self, parser):
        parser.add_argument(
            "--only-missing",
            action="store_true",
            help="Only enrich stations that do not have valid coordinates yet",
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
            help="Number of stations to process per bulk update batch",
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

        self.stdout.write("Loading city coordinates...")
        city_cache = self._build_city_cache()
        self.stdout.write(f"Loaded {len(city_cache)} cities into cache")

        self.stdout.write("Collecting stations to process...")

        query = FuelStation.objects.all()

        if only_missing:
            query = query.filter(
                Q(latitude__isnull=True) | Q(longitude__isnull=True)
            )

        total = query.count()
        self.stdout.write(f"Total stations to process: {total}")

        if total == 0:
            self.stdout.write(self.style.WARNING("No stations to process."))
            return

        matched = 0
        skipped = 0
        unmatched_pairs = set()
        stations_to_update = []
        now = timezone.now()

        for station in query.iterator(chunk_size=1000):
            if self._should_skip_station(station):
                skipped += 1
                continue

            city = self._normalize_city(station.city)
            state = self._normalize_state(station.state)

            if not city or not state:
                unmatched_pairs.add(
                    (station.city or "<missing>", station.state or "<missing>")
                )
                continue

            key = (city.casefold(), state)
            city_coord = city_cache.get(key)

            if city_coord is None:
                unmatched_pairs.add((station.city, station.state))
                continue

            lat, lon = city_coord

            station.latitude = lat
            station.longitude = lon
            station.coordinate_source = "city_centroid"
            station.coordinate_quality = "approximate"

            # Important: route eligibility currently depends on this.
            station.geocoding_status = "success"

            # Clear failure reason if the model has this field.
            if hasattr(station, "geocoding_failure_reason"):
                station.geocoding_failure_reason = ""

            station.updated_at = now

            stations_to_update.append(station)
            matched += 1

        if not stations_to_update:
            self.stdout.write(self.style.WARNING("No stations matched for enrichment."))
        else:
            self._bulk_update_stations(
                stations_to_update=stations_to_update,
                batch_size=batch_size,
                dry_run=dry_run,
                skip_confirmation=skip_confirmation,
            )

        elapsed = time.time() - start_time

        self.stdout.write("\n" + "=" * 60)
        self.stdout.write(self.style.SUCCESS("\nEnrichment complete!"))
        self.stdout.write(f"  Total stations processed: {total}")
        self.stdout.write(f"  Matched and enriched: {matched}")
        self.stdout.write(f"  Skipped existing usable/exact coordinates: {skipped}")
        self.stdout.write(f"  Unmatched city/state pairs: {len(unmatched_pairs)}")
        self.stdout.write(f"  Elapsed time: {elapsed:.2f}s")

        if unmatched_pairs:
            self.stdout.write("\nUnmatched pairs (first 50):")
            for city, state in sorted(unmatched_pairs)[:50]:
                self.stdout.write(f"  - {city}, {state}")
            if len(unmatched_pairs) > 50:
                self.stdout.write(f"  ... and {len(unmatched_pairs) - 50} more")

    def _build_city_cache(self):
        """
        Return:
            {
                ("nashville", "TN"): (lat, lon),
                ...
            }
        """
        cache = {}

        query = CityCoordinate.objects.exclude(city="").exclude(state="")

        for city_coord in query.iterator(chunk_size=1000):
            city = self._normalize_city(city_coord.city)
            state = self._normalize_state(city_coord.state)

            if not city or not state:
                continue

            key = (city.casefold(), state)
            cache[key] = (city_coord.latitude, city_coord.longitude)

        return cache

    def _should_skip_station(self, station):
        """
        Do not overwrite stations that already have usable coordinates.

        This protects exact station geocoding results, even if older rows did not
        set coordinate_source properly.
        """
        has_coords = station.latitude is not None and station.longitude is not None
        status = (station.geocoding_status or "").strip().lower()
        source = (station.coordinate_source or "").strip().lower()

        if source in SKIP_SOURCES and has_coords:
            return True

        # Important safety rule:
        # If a station is already success + has coordinates, do not replace it
        # with an approximate city centroid.
        if status == "success" and has_coords and source != "city_centroid":
            return True

        return False

    def _bulk_update_stations(
        self,
        *,
        stations_to_update,
        batch_size,
        dry_run,
        skip_confirmation,
    ):
        count = len(stations_to_update)

        update_fields = [
            "latitude",
            "longitude",
            "coordinate_source",
            "coordinate_quality",
            "geocoding_status",
            "updated_at",
        ]

        # Only include this field if it exists on the model.
        model_field_names = {field.name for field in FuelStation._meta.fields}
        if "geocoding_failure_reason" in model_field_names:
            update_fields.append("geocoding_failure_reason")

        total_batches = (count + batch_size - 1) // batch_size

        if dry_run:
            self.stdout.write(
                self.style.WARNING(
                    f"\n[DRY RUN] Would update {count} stations in "
                    f"{total_batches} batch(es)."
                )
            )
            return

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

        updated_total = 0

        for index in range(0, count, batch_size):
            batch = stations_to_update[index : index + batch_size]
            batch_number = index // batch_size + 1

            try:
                with transaction.atomic():
                    updated = FuelStation.objects.bulk_update(
                        batch,
                        update_fields,
                        batch_size=batch_size,
                    )
            except Exception as exc:
                raise CommandError(
                    f"Bulk update failed on batch {batch_number}/{total_batches}: {exc}"
                ) from exc

            updated_total += updated

            self.stdout.write(
                f"  Updated batch {batch_number}/{total_batches} "
                f"({len(batch)} stations)"
            )

        self.stdout.write(
            self.style.SUCCESS(f"Successfully updated {updated_total} stations!")
        )

    @staticmethod
    def _normalize_city(value):
        return " ".join(str(value or "").strip().split())

    @staticmethod
    def _normalize_state(value):
        return str(value or "").strip().upper()