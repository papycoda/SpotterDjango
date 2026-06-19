import json
import os
import subprocess
import sys
from pathlib import Path

from django.test import SimpleTestCase


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


class EnvironmentSettingsTests(SimpleTestCase):
    def run_settings_probe(self, environment, expression):
        process_environment = os.environ.copy()
        for key in (
            "SECRET_KEY",
            "DEBUG",
            "ALLOWED_HOSTS",
            "CORS_ALLOW_ALL_ORIGINS",
            "CORS_ALLOWED_ORIGINS",
            "SUPPORTED_COUNTRY_CODES",
            "NOMINATIM_TIMEOUT_SECONDS",
            "GEOCODING_STALE_AFTER_SECONDS",
            "GEOCODING_POLL_SECONDS",
            "SQLITE_PATH",
        ):
            process_environment.pop(key, None)
        process_environment.update(environment)
        completed = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import json; "
                    "import fuelSpotter.settings as settings; "
                    f"print(json.dumps({expression}))"
                ),
            ],
            cwd=REPOSITORY_ROOT,
            env=process_environment,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return json.loads(completed.stdout.strip())

    def test_security_settings_are_loaded_from_environment(self):
        values = self.run_settings_probe(
            {
                "SECRET_KEY": "test-production-key",
                "DEBUG": "false",
                "ALLOWED_HOSTS": "api.example.com, localhost",
                "CORS_ALLOW_ALL_ORIGINS": "no",
                "CORS_ALLOWED_ORIGINS": "https://app.example.com,https://admin.example.com",
            },
            "{"
            "'secret_key': settings.SECRET_KEY, "
            "'debug': settings.DEBUG, "
            "'allowed_hosts': settings.ALLOWED_HOSTS, "
            "'cors_allow_all': settings.CORS_ALLOW_ALL_ORIGINS, "
            "'cors_origins': settings.CORS_ALLOWED_ORIGINS"
            "}",
        )

        self.assertEqual(values["secret_key"], "test-production-key")
        self.assertIs(values["debug"], False)
        self.assertEqual(values["allowed_hosts"], ["api.example.com", "localhost"])
        self.assertIs(values["cors_allow_all"], False)
        self.assertEqual(
            values["cors_origins"],
            ["https://app.example.com", "https://admin.example.com"],
        )

    def test_external_service_settings_are_loaded_from_environment(self):
        values = self.run_settings_probe(
            {
                "SECRET_KEY": "test-key",
                "OSRM_BASE_URL": "https://osrm.example.test",
                "NOMINATIM_BASE_URL": "https://geo.example.test",
                "EXTERNAL_HTTP_TIMEOUT_SECONDS": "4.5",
                "NOMINATIM_USER_AGENT": "FuelSpotterTests/1.0",
                "NOMINATIM_MIN_INTERVAL_SECONDS": "1.25",
                "NOMINATIM_TIMEOUT_SECONDS": "6.5",
                "GEOCODING_STALE_AFTER_SECONDS": "90",
                "GEOCODING_POLL_SECONDS": "2.5",
                "SUPPORTED_COUNTRY_CODES": "US",
            },
            "{"
            "'osrm': settings.OSRM_BASE_URL, "
            "'nominatim': settings.NOMINATIM_BASE_URL, "
            "'timeout': settings.EXTERNAL_HTTP_TIMEOUT_SECONDS, "
            "'user_agent': settings.NOMINATIM_USER_AGENT, "
            "'interval': settings.NOMINATIM_MIN_INTERVAL_SECONDS, "
            "'nominatim_timeout': settings.NOMINATIM_TIMEOUT_SECONDS, "
            "'stale_after': settings.GEOCODING_STALE_AFTER_SECONDS, "
            "'poll': settings.GEOCODING_POLL_SECONDS, "
            "'countries': settings.SUPPORTED_COUNTRY_CODES"
            "}",
        )

        self.assertEqual(values["osrm"], "https://osrm.example.test")
        self.assertEqual(values["nominatim"], "https://geo.example.test")
        self.assertEqual(values["timeout"], 4.5)
        self.assertEqual(values["user_agent"], "FuelSpotterTests/1.0")
        self.assertEqual(values["interval"], 1.25)
        self.assertEqual(values["nominatim_timeout"], 6.5)
        self.assertEqual(values["stale_after"], 90.0)
        self.assertEqual(values["poll"], 2.5)
        self.assertEqual(values["countries"], ["us"])

    def test_assignment_vehicle_assumptions_are_fixed(self):
        values = self.run_settings_probe(
            {"SECRET_KEY": "test-key"},
            "{"
            "'max_range_miles': settings.VEHICLE_MAX_RANGE_MILES, "
            "'mpg': settings.VEHICLE_MPG"
            "}",
        )

        self.assertEqual(values["max_range_miles"], 500)
        self.assertEqual(values["mpg"], 10)

    def test_sqlite_path_is_loaded_from_environment(self):
        values = self.run_settings_probe(
            {
                "SECRET_KEY": "test-key",
                "SQLITE_PATH": "/data/fuelspotter.sqlite3",
            },
            "{'database_name': str(settings.DATABASES['default']['NAME'])}",
        )

        self.assertEqual(values["database_name"], "/data/fuelspotter.sqlite3")

    def test_production_requires_an_explicit_secret_key(self):
        process_environment = os.environ.copy()
        # An explicit empty value prevents a developer's local .env from
        # supplying a secret and making this test machine-dependent.
        process_environment["SECRET_KEY"] = ""
        process_environment["DEBUG"] = "false"
        completed = subprocess.run(
            [sys.executable, "-c", "import fuelSpotter.settings"],
            cwd=REPOSITORY_ROOT,
            env=process_environment,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("SECRET_KEY must be set when DEBUG is false", completed.stderr)
