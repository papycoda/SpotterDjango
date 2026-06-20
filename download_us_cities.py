#!/usr/bin/env python3
"""Download and prepare a comprehensive US cities dataset from SimpleMaps."""
import csv
import requests
from pathlib import Path
from django.conf import settings

# URL for SimpleMaps free basic US cities dataset
# Source: https://simplemaps.com/data/us-cities
DATA_URL = "https://simplemaps.com/static/data/us-cities/1.83/basic/simplemaps_uscities_basicv1.83.zip"

def main():
    data_dir = Path(settings.BASE_DIR) / "api" / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    zip_path = data_dir / "simplemaps_uscities.zip"
    csv_path = data_dir / "us_cities.csv"
    
    print("Downloading US cities dataset...")
    response = requests.get(DATA_URL, stream=True)
    response.raise_for_status()
    
    with open(zip_path, "wb") as f:
        for chunk in response.iter_content(chunk_size=8192):
            f.write(chunk)
    print("Download complete.")
    
    print("\nExtracting and processing data...")
    import zipfile
    with zipfile.ZipFile(zip_path, "r") as zf:
        # Find the CSV file in the zip
        csv_files = [f for f in zf.namelist() if f.endswith('.csv') and 'uscities' in f.lower()]
        if not csv_files:
            raise RuntimeError("Could not find CSV file in downloaded zip")
        
        with zf.open(csv_files[0], 'r') as infile, open(csv_path, 'w', newline='', encoding='utf-8') as outfile:
            reader = csv.DictReader(infile.read().decode('utf-8').splitlines())
            writer = csv.DictWriter(outfile, fieldnames=["city", "state", "latitude", "longitude", "source"])
            writer.writeheader()
            
            count = 0
            for row in reader:
                # Normalize the state code to 2 letters
                state = row.get("state_id", row.get("state_code", ""))[:2].upper()
                city = row.get("city", row.get("name", ""))
                lat = row.get("lat", row.get("latitude"))
                lng = row.get("lng", row.get("longitude"))
                
                if city and state and lat and lng:
                    writer.writerow({
                        "city": city,
                        "state": state,
                        "latitude": lat,
                        "longitude": lng,
                        "source": "SimpleMaps"
                    })
                    count +=1
            print(f"Processed {count} cities.")
    
    # Clean up zip file
    zip_path.unlink(missing_ok=True)
    print(f"\nComplete! Dataset saved to {csv_path}")

if __name__ == "__main__":
    import django
    import os
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "fuelspotter.settings")
    django.setup()
    main()
