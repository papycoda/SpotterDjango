from django.core.management.base import BaseCommand, CommandError
from django.conf import settings
from django.db import transaction
import time

from api.models import FuelStation, CityCoordinate

# Try to import optional async dependencies
try:
    import asyncio
    import aiohttp
    HAS_ASYNC = True
except ImportError:
    HAS_ASYNC = False
    self = None  # Just a placeholder for type hints


class Command(BaseCommand):
    help = "Fetch city centroid coordinates from Nominatim for all unique cities in FuelStation"

    def add_arguments(self, parser):
        parser.add_argument(
            "--limit",
            type=int,
            default=1000,
            help="Maximum number of cities to process in one run (default: 1000)"
        )
        parser.add_argument(
            "--no-enrich",
            action="store_true",
            help="Don't automatically enrich fuel stations after fetching cities"
        )
        parser.add_argument(
            "--concurrency",
            type=int,
            default=3,
            help="Number of concurrent requests (default: 3, safe for public Nominatim, only with aiohttp)"
        )
        parser.add_argument(
            "--sync",
            action="store_true",
            help="Force synchronous mode even if aiohttp is available"
        )

    def handle(self, *args, **options):
        limit = options.get("limit", 1000)
        no_enrich = options.get("no_enrich", False)
        concurrency = options.get("concurrency", 3)
        force_sync = options.get("sync", False)
        
        # Get unique (city, state) pairs from FuelStation that aren't already in CityCoordinate
        self.stdout.write("Collecting cities to process...")
        # Get existing cities from CityCoordinate (case-insensitive keys)
        existing_city_keys = set()
        for cc in CityCoordinate.objects.all():
            key = (cc.city.strip().lower(), cc.state.strip().upper())
            existing_city_keys.add(key)
        
        self.stdout.write(f"Loaded {len(existing_city_keys)} existing cities from CityCoordinate")
        
        station_city_states = FuelStation.objects.values_list("city", "state").distinct()
        self.stdout.write(f"Found {len(station_city_states)} unique city-state pairs in FuelStation")
        
        cities_to_process = []
        for city, state in station_city_states:
            normalized_city = city.strip().lower()
            normalized_state = state.strip().upper()
            key = (normalized_city, normalized_state)
            if key not in existing_city_keys:
                cities_to_process.append((city.strip(), state.strip()))
        
        self.stdout.write(f"Cities already in CityCoordinate: {len(existing_city_keys)}")
        self.stdout.write(f"Cities to process: {len(cities_to_process)}")
        
        if not cities_to_process:
            self.stdout.write(self.style.SUCCESS("\nNo new cities to process!"))
            if not no_enrich:
                self._enrich_stations()
            return
        
        cities_to_process = cities_to_process[:limit]
        
        # Choose mode
        use_async = HAS_ASYNC and not force_sync
        if use_async:
            self.stdout.write(f"\nProcessing first {len(cities_to_process)} cities with async mode and concurrency {concurrency}...")
            successful_cities = asyncio.run(
                self.fetch_cities_async(cities_to_process, concurrency)
            )
        else:
            self.stdout.write(f"\nProcessing first {len(cities_to_process)} cities with sync mode...")
            successful_cities = self.fetch_cities_sync(cities_to_process)
        
        # Bulk insert all successful cities at once (much faster!)
        if successful_cities:
            self.stdout.write(f"\nBulk inserting {len(successful_cities)} cities...")
            with transaction.atomic():
                cities_to_create = []
                for city_data in successful_cities:
                    exists = CityCoordinate.objects.filter(
                        city__iexact=city_data["city"],
                        state__iexact=city_data["state"]
                    ).exists()
                    if not exists:
                        cities_to_create.append(CityCoordinate(
                            city=city_data["city"].strip().lower().title(),
                            state=city_data["state"].strip().upper(),
                            latitude=city_data["latitude"],
                            longitude=city_data["longitude"],
                            source="Nominatim"
                        ))
                
                CityCoordinate.objects.bulk_create(cities_to_create)
            self.stdout.write(self.style.SUCCESS(f"Successfully inserted {len(cities_to_create)} cities!"))
        
        # Summary
        self.stdout.write("\n" + "="*60)
        self.stdout.write(self.style.SUCCESS("\nProcessing complete!"))
        self.stdout.write(f"  Total cities processed: {len(cities_to_process)}")
        self.stdout.write(f"  Successful: {len(successful_cities)}")
        self.stdout.write(f"  Failed/skipped: {len(cities_to_process) - len(successful_cities)}")
        
        if not no_enrich:
            self._enrich_stations()
    
    def fetch_cities_sync(self, cities):
        """Synchronous fallback using requests"""
        import requests
        nominatim_url = settings.NOMINATIM_BASE_URL.rstrip('/')
        successful_cities = []
        
        for i, (city, state) in enumerate(cities):
            if i % 50 == 0:
                self.stdout.write(f"  Processed {i}/{len(cities)} cities...")
            try:
                params = {
                    "q": f"{city}, {state}",
                    "format": "json",
                    "limit": 1,
                    "addressdetails": 0,
                    "extratags": 0,
                    "namedetails": 0
                }
                
                headers = {
                    "User-Agent": settings.NOMINATIM_USER_AGENT
                }
                
                response = requests.get(
                    f"{nominatim_url}/search",
                    params=params,
                    headers=headers,
                    timeout=settings.NOMINATIM_TIMEOUT_SECONDS
                )
                if response.status_code == 429:
                    time.sleep(2)
                    response = requests.get(
                        f"{nominatim_url}/search",
                        params=params,
                        headers=headers,
                        timeout=settings.NOMINATIM_TIMEOUT_SECONDS
                    )
                response.raise_for_status()
                data = response.json()
                
                if data and len(data) > 0 and "lat" in data[0] and "lon" in data[0]:
                    lat = float(data[0]["lat"])
                    lon = float(data[0]["lon"])
                    successful_cities.append({
                        "city": city,
                        "state": state,
                        "latitude": lat,
                        "longitude": lon
                    })
            except Exception as e:
                pass
            time.sleep(0.5)
        return successful_cities
    
    async def fetch_cities_async(self, cities, concurrency):
        """Fetch cities using async aiohttp with concurrency limit"""
        nominatim_url = settings.NOMINATIM_BASE_URL.rstrip('/')
        user_agent = settings.NOMINATIM_USER_AGENT
        timeout = aiohttp.ClientTimeout(total=settings.NOMINATIM_TIMEOUT_SECONDS)
        
        # Create a semaphore to limit concurrency
        semaphore = asyncio.Semaphore(concurrency)
        processed = 0
        
        async def fetch_city(session, city, state):
            nonlocal processed
            async with semaphore:
                try:
                    params = {
                        "q": f"{city}, {state}",
                        "format": "json",
                        "limit": 1,
                        "addressdetails": 0,
                        "extratags": 0,
                        "namedetails": 0
                    }
                    
                    async with session.get(
                        f"{nominatim_url}/search",
                        params=params,
                        headers={"User-Agent": user_agent},
                        timeout=timeout
                    ) as response:
                        if response.status == 429:
                            # Rate limited, wait and retry once
                            await asyncio.sleep(2)
                            async with session.get(
                                f"{nominatim_url}/search",
                                params=params,
                                headers={"User-Agent": user_agent},
                                timeout=timeout
                            ) as retry_response:
                                data = await retry_response.json()
                        else:
                            data = await response.json()
                    
                    processed += 1
                    if processed % 50 == 0:
                        self.stdout.write(f"  Processed {processed}/{len(cities)} cities...")
                    
                    if data and len(data) > 0 and "lat" in data[0] and "lon" in data[0]:
                        return {
                            "city": city,
                            "state": state,
                            "latitude": float(data[0]["lat"]),
                            "longitude": float(data[0]["lon"])
                        }
                    return None
                except Exception as e:
                    processed += 1
                    if processed % 50 == 0:
                        self.stdout.write(f"  Processed {processed}/{len(cities)} cities...")
                    return None
        
        async with aiohttp.ClientSession() as session:
            tasks = []
            for city, state in cities:
                task = fetch_city(session, city, state)
                tasks.append(task)
                # Add a small delay between starting tasks to avoid hitting rate limits
                await asyncio.sleep(0.15)
            
            results = await asyncio.gather(*tasks, return_exceptions=False)
        
        successful = [res for res in results if res is not None]
        return successful
    
    def _enrich_stations(self):
        self.stdout.write("\n" + "="*60)
        self.stdout.write("Enriching fuel stations with city coordinates...")
        from django.core.management import call_command
        call_command("enrich_fuel_stations_with_city_coordinates")
