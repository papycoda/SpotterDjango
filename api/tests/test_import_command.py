from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

from django.core.management import call_command, get_commands
from django.core.management.base import CommandError
from django.test import TestCase

from api.models import FuelStation


class ImportFuelPricesCommandTests(TestCase):
    def test_command_is_registered(self):
        self.assertIn("import_fuel_prices", get_commands())

    def test_imports_csv_and_reports_summary(self):
        with TemporaryDirectory() as directory:
            source_path = Path(directory) / "prices.csv"
            source_path.write_text(
                "OPIS Truckstop ID,Truckstop Name,Address,City,State,Rack ID,Retail Price\n"
                "7,SHORT NAME,US-69,Big Cabin,OK,307,3.345\n"
                "7,WOODED TRUCK STOP,US-69,Big Cabin,OK,307,3.355\n",
                encoding="utf-8",
            )
            output = StringIO()

            call_command("import_fuel_prices", source_path, stdout=output)

        self.assertEqual(FuelStation.objects.count(), 1)
        station = FuelStation.objects.get(pk="7")
        self.assertEqual(station.rack_id, "307")
        self.assertIn("Processed 2 rows into 1 station", output.getvalue())
        self.assertIn("1 duplicate row collapsed", output.getvalue())

    def test_rejects_missing_source_file(self):
        with self.assertRaisesRegex(CommandError, "CSV file does not exist"):
            call_command("import_fuel_prices", "missing-prices.csv")
