from decimal import Decimal

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class UnsafeGeocodeInvalidationMigrationTests(TransactionTestCase):
    migrate_from = ("api", "0004_geocoding_failure_classification")
    migrate_to = ("api", "0005_invalidate_low_confidence_geocodes")

    def setUp(self):
        super().setUp()
        executor = MigrationExecutor(connection)
        executor.migrate([self.migrate_from])
        old_apps = executor.loader.project_state([self.migrate_from]).apps
        FuelStation = old_apps.get_model("api", "FuelStation")
        GeocodeJob = old_apps.get_model("api", "GeocodeJob")
        job = GeocodeJob.objects.create(id="unsafe-job")

        common = {
            "address": "100 Main Street",
            "city": "Dallas",
            "state": "TX",
            "latitude": Decimal("32.7767000"),
            "longitude": Decimal("-96.7970000"),
            "geocoding_status": "success",
        }
        FuelStation.objects.create(
            id="high", name="High", geocoding_confidence="high", geocoding_stage=1, **common
        )
        FuelStation.objects.create(
            id="medium", name="Medium", geocoding_confidence="medium", geocoding_stage=2, **common
        )
        FuelStation.objects.create(
            id="low", name="Low", geocoding_confidence="low", geocoding_stage=3,
            geocode_job=job, **common
        )
        FuelStation.objects.create(
            id="stage3", name="Stage 3", geocoding_confidence="medium", geocoding_stage=3,
            geocode_job=job, **common
        )
        FuelStation.objects.create(
            id="legacy", name="Legacy", geocoding_confidence=None, geocoding_stage=None, **common
        )

        executor = MigrationExecutor(connection)
        executor.migrate([self.migrate_to])
        self.apps = executor.loader.project_state([self.migrate_to]).apps

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()

    def test_only_low_confidence_or_stage_3_successes_are_invalidated(self):
        FuelStation = self.apps.get_model("api", "FuelStation")

        for station_id in ("low", "stage3"):
            station = FuelStation.objects.get(pk=station_id)
            self.assertEqual(station.geocoding_status, "pending")
            self.assertIsNone(station.latitude)
            self.assertIsNone(station.longitude)
            self.assertIsNone(station.geocoding_confidence)
            self.assertIsNone(station.geocoding_stage)
            self.assertIsNone(station.geocode_job_id)
            self.assertEqual(station.geocoding_strategy_version, 0)

        for station_id in ("high", "medium", "legacy"):
            station = FuelStation.objects.get(pk=station_id)
            self.assertEqual(station.geocoding_status, "success")
            self.assertIsNotNone(station.latitude)
            self.assertIsNotNone(station.longitude)
