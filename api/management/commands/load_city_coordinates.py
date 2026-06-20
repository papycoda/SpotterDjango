from pathlib import Path
import csv

from django.core.management.base import BaseCommand, CommandError
from django.conf import settings
from django.db import transaction

from api.models import CityCoordinate


class Command(BaseCommand):
    help = "Loads city centroid coordinates from api/data/us_cities.csv"

    def add_arguments(self, parser):
        parser.add_argument(
            "csv_path",
            nargs="?",
            type=Path,
            default=settings.BASE_DIR / "api" / "data" / "us_cities.csv",
            help="Path to us_cities.csv file",
        )

    def handle(self, *args, **options):
        csv_path = options.get("csv_path", settings.BASE_DIR / "api" / "data" / "us_cities.csv")
        if not csv_path or not csv_path.is_file():
            raise CommandError(f"CSV file does not exist: {csv_path}")

        self.stdout.write(f"Loading cities from {csv_path}...")
        
        inserted = 0
        updated = 0
        total = 0
        cities_to_process = []
        
        with open(csv_path, encoding="utf-8") as csv_file:
            reader = csv.DictReader(csv_file)
            for row in reader:
                total += 1
                # Normalize city and state
                city = row.get("city", row.get("name", "")).strip().lower().title()
                state = row.get("state", row.get("state_abbreviation", "")).strip().upper()
                latitude = row.get("lat", row.get("latitude"))
                longitude = row.get("lon", row.get("longitude"))
                source = row.get("source", "CSV")
                
                if city and state and latitude and longitude:
                    cities_to_process.append({
                        "city": city,
                        "state": state,
                        "latitude": float(latitude),
                        "longitude": float(longitude),
                        "source": source
                    })
        
        # Now process all cities
        existing_cities = set(
            (cc.city.lower(), cc.state.lower())
            for cc in CityCoordinate.objects.all()
        )
        
        cities_to_create = []
        cities_to_update = []
        
        for city_data in cities_to_process:
            key = (city_data["city"].lower(), city_data["state"].lower())
            if key in existing_cities:
                # Update existing city
                try:
                    city = CityCoordinate.objects.get(city=city_data["city"], state=city_data["state"])
                    city.latitude = city_data["latitude"]
                    city.longitude = city_data["longitude"]
                    city.source = city_data["source"]
                    cities_to_update.append(city)
                except:
                    pass
            else:
                # Create new city
                cities_to_create.append(CityCoordinate(**city_data))
        
        # Bulk create and update
        with transaction.atomic():
            if cities_to_create:
                CityCoordinate.objects.bulk_create(cities_to_create)
                inserted = len(cities_to_create)
            if cities_to_update:
                CityCoordinate.objects.bulk_update(
                    cities_to_update,
                    ["latitude", "longitude", "source", "updated_at"]
                )
                updated = len(cities_to_update)
        
        self.stdout.write(self.style.SUCCESS("City coordinates loaded successfully!"))
        self.stdout.write(f"Total rows read from CSV: {total}")
        self.stdout.write(f"Inserted new cities: {inserted}")
        self.stdout.write(f"Updated existing cities: {updated}")
