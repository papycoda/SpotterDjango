from django.core.management.base import BaseCommand, CommandError
from django.core.management import call_command
from django.conf import settings
from django.db import transaction


class Command(BaseCommand):
    help = "One-step setup: load city data, fetch missing cities, enrich all stations, and optionally run geocoding worker"

    def add_arguments(self, parser):
        parser.add_argument(
            "--geocode",
            action="store_true",
            help="After setup, run the geocoding worker to get precise station coordinates"
        )
        parser.add_argument(
            "--allow-approximate",
            action="store_true",
            help="Allow approximate city coordinates for route planning after setup"
        )

    def handle(self, *args, **options):
        geocode = options.get("geocode", False)
        allow_approximate = options.get("allow_approximate", False)
        
        self.stdout.write("\n" + "="*70)
        self.stdout.write("                        FUEL STATION SETUP")
        self.stdout.write("="*70)
        self.stdout.write("\n")
        
        # Step 1: Load city coordinates from CSV if available
        try:
            self.stdout.write(self.style.NOTICE("Step 1: Loading city coordinates from CSV..."))
            call_command("load_city_coordinates")
            self.stdout.write(self.style.SUCCESS("✅ CSV city data loaded!"))
        except Exception as e:
            self.stdout.write(self.style.WARNING(f"⚠️ Could not load CSV city data: {e}"))
        
        self.stdout.write("\n")
        
        # Step 2: Fetch missing cities from Nominatim
        self.stdout.write(self.style.NOTICE("Step 2: Fetching missing city coordinates from Nominatim..."))
        try:
            call_command("fetch_city_coordinates", limit=100000)
            self.stdout.write(self.style.SUCCESS("✅ City coordinates fetched!"))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"⚠️ Error fetching city coordinates: {e}"))
        
        self.stdout.write("\n")
        
        # Step 3: Report status
        self.stdout.write(self.style.NOTICE("\nStep 3: Final status report..."))
        call_command("geocoding_report")
        
        # Step 4: Update settings if requested
        if allow_approximate:
            self.stdout.write("\n" + "="*70)
            self.stdout.write(self.style.WARNING("\n⚠️ You requested approximate coordinates for route planning!"))
            self.stdout.write("Please add this to your .env file:")
            self.stdout.write("    ALLOW_APPROXIMATE_CITY_COORDINATES_FOR_ROUTE_MATCHING=True")
        
        # Step 5: Run geocoding worker if requested
        if geocode:
            self.stdout.write("\n" + "="*70)
            self.stdout.write(self.style.NOTICE("\nStep 5: Starting geocoding worker for precise coordinates..."))
            self.stdout.write("Press Ctrl+C to stop the worker.")
            try:
                call_command("run_geocoding_worker", watch=True, auto_queue=500)
            except KeyboardInterrupt:
                self.stdout.write(self.style.SUCCESS("\n✅ Worker stopped by user."))
        
        self.stdout.write("\n" + "="*70)
        self.stdout.write(self.style.SUCCESS("\n✅ Setup complete!"))
