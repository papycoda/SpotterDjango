from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.conf import settings

from api.models import FuelStation, CityCoordinate


class Command(BaseCommand):
    help = "Enriches fuel stations with approximate city centroid coordinates"

    def add_arguments(self, parser):
        parser.add_argument(
            "--only-missing",
            action="store_true",
            help="Only enrich stations that don't have any coordinates yet"
        )

    def handle(self, *args, **options):
        only_missing = options.get("only_missing", False)
        total = 0
        matched = 0
        skipped = 0
        unmatched_pairs = set()
        
        # Load all city coordinates into a dictionary for fast lookups (much faster!)
        self.stdout.write("Loading city coordinates...")
        city_cache = {}
        for city_coord in CityCoordinate.objects.all():
            key = (city_coord.city.lower(), city_coord.state.lower())
            city_cache[key] = (city_coord.latitude, city_coord.longitude)
        
        self.stdout.write(f"Loaded {len(city_cache)} cities into cache")
        
        # Get stations to process
        self.stdout.write("Collecting stations to process...")
        query = FuelStation.objects.all()
        if only_missing:
            query = query.filter(latitude__isnull=True)
        
        stations_to_process = list(query.select_related())  # Load all stations at once
        total = len(stations_to_process)
        
        self.stdout.write(f"Total stations to process: {total}")
        
        # Prepare batch updates for performance
        stations_to_update = []
        
        for station in stations_to_process:
            # Check if we already have exact/manual coordinates
            if station.coordinate_source in ["exact_station", "manual"]:
                skipped += 1
                continue
            
            # Normalize city/state for lookup
            city = station.city.strip().lower()
            state = station.state.strip().lower()
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
                original_key = (station.city, station.state)
                unmatched_pairs.add(original_key)
        
        # Bulk update all matched stations (WAY faster!)
        if stations_to_update:
            self.stdout.write(f"\nBulk updating {len(stations_to_update)} stations...")
            with transaction.atomic():
                FuelStation.objects.bulk_update(
                    stations_to_update,
                    ["latitude", "longitude", "coordinate_source", "coordinate_quality", "updated_at"]
                )
            self.stdout.write(self.style.SUCCESS(f"Successfully updated {len(stations_to_update)} stations!"))
        
        # Print the results
        self.stdout.write("\n" + "="*60)
        self.stdout.write(self.style.SUCCESS("\nEnrichment complete!"))
        self.stdout.write(f"  Total stations processed: {total}")
        self.stdout.write(f"  Matched and enriched: {matched}")
        self.stdout.write(f"  Skipped (already had exact/manual coordinates): {skipped}")
        self.stdout.write(f"  Unmatched city/state pairs: {len(unmatched_pairs)}")
        
        if unmatched_pairs:
            self.stdout.write("\nUnmatched pairs (first 50):")
            for city, state in sorted(unmatched_pairs)[:50]:
                self.stdout.write(f"  - {city}, {state}")
            if len(unmatched_pairs) > 50:
                self.stdout.write(f"  ... and {len(unmatched_pairs)-50} more")
