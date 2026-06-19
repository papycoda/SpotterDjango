from decimal import Decimal
from io import StringIO

from django.test import TestCase

from api.models import FuelStation
from api.services.import_service import ImportService


HEADERS = (
    "OPIS Truckstop ID,Truckstop Name,Address,City,State,Rack ID,Retail Price\n"
)


class ImportServiceParsingTests(TestCase):
    def test_canonicalizes_duplicate_station_rows(self):
        source = StringIO(
            HEADERS
            + '105,TA #1,"I-75, EXIT 144-B",Bridgeport,mi,260,3.269\n'
            + '105,TA SAGINAW I 75 TRAVEL CENTER,"I-75, EXIT 144-B",Bridgeport,MI,260,3.399\n'
            + '105,TA SAGINAW,"I-75, EXIT 144-B",Bridgeport,MI,260,3.339\n'
        )

        parsed = ImportService.parse_csv(source)

        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.total_rows, 3)
        self.assertEqual(parsed.collapsed_rows, 2)
        self.assertEqual(len(parsed.stations), 1)
        station = parsed.stations[0]
        self.assertEqual(station.opis_truckstop_id, "105")
        self.assertEqual(station.rack_id, "260")
        self.assertEqual(station.name, "TA SAGINAW I 75 TRAVEL CENTER")
        self.assertEqual(station.state, "MI")
        self.assertEqual(station.price_per_gallon, Decimal("3.34"))

    def test_rounds_half_up_to_cents(self):
        source = StringIO(
            HEADERS
            + "7,WOODED STOP,US-69,Big Cabin,OK,307,3.345\n"
        )

        parsed = ImportService.parse_csv(source)

        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.stations[0].price_per_gallon, Decimal("3.35"))

    def test_rejects_missing_required_header(self):
        source = StringIO(
            "OPIS Truckstop ID,Truckstop Name,Address,City,State,Retail Price\n"
            "7,WOODED STOP,US-69,Big Cabin,OK,3.45\n"
        )

        with self.assertRaisesRegex(ValueError, "Missing required CSV headers: Rack ID"):
            ImportService.parse_csv(source)

    def test_rejects_conflicting_location_for_same_opis_id(self):
        source = StringIO(
            HEADERS
            + "7,WOODED STOP,US-69,Big Cabin,OK,307,3.45\n"
            + "7,WOODED STOP,US-69,Tulsa,OK,307,3.40\n"
        )

        with self.assertRaisesRegex(ValueError, "OPIS 7 has conflicting City"):
            ImportService.parse_csv(source)


class ImportServiceDatabaseTests(TestCase):
    def test_bulk_import_is_idempotent_and_preserves_coordinates(self):
        source = StringIO(
            HEADERS
            + "7,WOODED STOP,US-69,Big Cabin,OK,307,3.45\n"
        )
        parsed = ImportService.parse_csv(source)

        first = ImportService.bulk_import(parsed)
        station = FuelStation.objects.get(pk="7")
        station.latitude = Decimal("36.5000000")
        station.longitude = Decimal("-95.0000000")
        station.geocoding_status = "success"
        station.save()

        updated_source = StringIO(
            HEADERS
            + "7,WOODED STOP,US-69,Big Cabin,OK,307,3.55\n"
        )
        second = ImportService.bulk_import(ImportService.parse_csv(updated_source))
        station.refresh_from_db()

        self.assertIsNotNone(first)
        self.assertEqual(first.created, 1)
        self.assertEqual(first.updated, 0)
        self.assertEqual(second.created, 0)
        self.assertEqual(second.updated, 1)
        self.assertEqual(FuelStation.objects.count(), 1)
        self.assertEqual(station.price_per_gallon, Decimal("3.55"))
        self.assertEqual(station.latitude, Decimal("36.5000000"))
        self.assertEqual(station.longitude, Decimal("-95.0000000"))
        self.assertEqual(station.geocoding_status, "success")
