from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from api.services.import_service import ImportService


class Command(BaseCommand):
    help = "Import and canonicalize fuel stations from the CSV."

    def add_arguments(self, parser):
        parser.add_argument(
            "csv_path",
            nargs="?",
            type=Path,
            default=settings.BASE_DIR / "fuel-prices-for-be-assessment.csv",
            help="CSV path (defaults to fuel-prices-for-be-assessment.csv).",
        )

    def handle(self, *args, **options):
        source_path = options["csv_path"].expanduser().resolve()
        if not source_path.is_file():
            raise CommandError(f"CSV file does not exist: {source_path}")

        try:
            with source_path.open(encoding="utf-8-sig", newline="") as source:
                parsed = ImportService.parse_csv(source)
            result = ImportService.bulk_import(parsed)
        except (OSError, UnicodeError, ValueError) as exc:
            raise CommandError(str(exc)) from exc

        station_word = "station" if len(parsed.stations) == 1 else "stations"
        duplicate_word = "row" if result.collapsed_rows == 1 else "rows"
        self.stdout.write(
            self.style.SUCCESS(
                f"Processed {result.total_rows} rows into {len(parsed.stations)} "
                f"{station_word}: {result.created} created, {result.updated} updated; "
                f"{result.collapsed_rows} duplicate {duplicate_word} collapsed."
            )
        )
