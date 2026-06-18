from django.test import SimpleTestCase
from django.urls import resolve

from api import views


class FuelStationUrlTests(SimpleTestCase):
    def test_near_route_static_path_is_not_captured_as_station_id(self):
        match = resolve("/api/v1/fuel-stations/near-route/")

        self.assertIs(match.func, views.fuel_stations_near_route)

    def test_station_id_path_resolves_to_detail_view(self):
        match = resolve("/api/v1/fuel-stations/station-1/")

        self.assertIs(match.func, views.fuel_station_detail)
