from django.test import SimpleTestCase
from django.urls import Resolver404, resolve

from api import views


class ApiUrlTests(SimpleTestCase):
    def test_unsupported_paths_do_not_resolve(self):
        unsupported_paths = (
            "/api/v1/fuel-stations/near-route/",
            "/api/v1/admin/fuel-prices/import/",
            "/api/v1/admin/fuel-prices/imports/import-1/",
            "/api/v1/locations/validate/",
            "/api/v1/fuel/estimate/",
        )

        for path in unsupported_paths:
            with self.subTest(path=path):
                with self.assertRaises(Resolver404):
                    resolve(path)

    def test_station_id_path_resolves_to_detail_view(self):
        match = resolve("/api/v1/fuel-stations/station-1/")

        self.assertIs(match.func, views.fuel_station_detail)

    def test_supported_operation_paths_still_resolve(self):
        expected_views = {
            "/api/v1/health/": views.health_check,
            "/api/v1/routes/preview/": views.route_preview,
            "/api/v1/routes/fuel-plan/": views.route_fuel_plan,
            "/api/v1/fuel-stations/": views.fuel_stations_list,
            "/api/v1/admin/fuel-stations/geocode/": views.admin_geocode_stations,
            "/api/v1/admin/fuel-stations/geocode/status/": views.admin_geocode_status,
        }

        for path, expected_view in expected_views.items():
            with self.subTest(path=path):
                self.assertIs(resolve(path).func, expected_view)

    def test_api_documentation_paths_still_resolve(self):
        for path in (
            "/api/v1/swagger/",
            "/api/v1/redoc/",
            "/api/v1/schema/",
        ):
            with self.subTest(path=path):
                self.assertIsNotNone(resolve(path).func)
