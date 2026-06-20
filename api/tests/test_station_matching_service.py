from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase

from api.models import FuelStation
from api.services.station_matching_service import StationMatchingService


class StationMatchingServiceTests(TestCase):
    def create_station(self, station_id, *, status, latitude=None, longitude=None):
        return FuelStation.objects.create(
            id=station_id,
            opis_truckstop_id=station_id,
            rack_id=f"rack-{station_id}",
            name=f"Station {station_id}",
            address="100 Main Street",
            city="Dallas",
            state="TX",
            price_per_gallon=Decimal("3.45"),
            geocoding_status=status,
            latitude=latitude,
            longitude=longitude,
        )

    @patch("api.clients.nominatim_client.NominatimClient.geocode")
    def test_eligible_stations_are_successful_local_coordinates_only(self, geocode):
        eligible = self.create_station(
            "1",
            status="success",
            latitude=Decimal("32.7767000"),
            longitude=Decimal("-96.7970000"),
        )
        self.create_station("2", status="pending")
        self.create_station("3", status="failed")
        self.create_station("5", status="claimed")
        self.create_station("6", status="processing")
        self.create_station(
            "4",
            status="success",
            latitude=Decimal("32.7767000"),
            longitude=None,
        )
        self.create_station(
            "7",
            status="success",
            latitude=None,
            longitude=Decimal("-96.7970000"),
        )

        station_ids = list(
            StationMatchingService.eligible_stations().values_list("pk", flat=True)
        )

        self.assertEqual(station_ids, [eligible.pk])
        geocode.assert_not_called()
