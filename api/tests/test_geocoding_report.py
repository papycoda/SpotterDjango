"""Tests for the geocoding_report management command."""

from io import StringIO
from unittest.mock import patch

from django.core.management import call_command, CommandError
from django.test import TestCase

from api.models import FuelStation


class GeocodingReportCommandTests(TestCase):
    """Test the geocoding_report command."""

    def _create_test_stations(self):
        """Create a standard set of test stations."""
        # Success stations with high confidence and valid coordinates
        FuelStation.objects.create(
            id="success-1",
            name="Success High Valid",
            address="123 Main St",
            city="Nashville",
            state="TN",
            latitude=36.1627,
            longitude=-86.7816,
            geocoding_status="success",
            geocoding_confidence="high",
        )

        # Success with medium confidence
        FuelStation.objects.create(
            id="success-2",
            name="Success Medium",
            address="456 Oak Ave",
            city="Memphis",
            state="TN",
            latitude=35.1495,
            longitude=-90.0490,
            geocoding_status="success",
            geocoding_confidence="medium",
        )

        # Success with low confidence (anomaly)
        FuelStation.objects.create(
            id="success-3",
            name="Success Low",
            address="789 Pine Rd",
            city="Knoxville",
            state="TN",
            latitude=35.9606,
            longitude=-83.9207,
            geocoding_status="success",
            geocoding_confidence="low",
        )

        # Success with null coordinates (anomaly)
        FuelStation.objects.create(
            id="success-4",
            name="Success Null",
            address="321 Elm St",
            city="Chattanooga",
            state="TN",
            geocoding_status="success",
            geocoding_confidence="high",
            latitude=None,
            longitude=None,
        )

        # Failed station
        FuelStation.objects.create(
            id="failed-1",
            name="Failed Station",
            address="999 No Way",
            city="Nowhere",
            state="XX",
            geocoding_status="failed",
            geocoding_confidence=None,
        )

        # Pending station
        FuelStation.objects.create(
            id="pending-1",
            name="Pending Station",
            address="111 Wait St",
            city="Somewhere",
            state="YY",
            geocoding_status="pending",
            geocoding_confidence=None,
        )

        # Claimed station
        FuelStation.objects.create(
            id="claimed-1",
            name="Claimed Station",
            address="222 Taken Ave",
            city="Elsewhere",
            state="ZZ",
            geocoding_status="claimed",
            geocoding_confidence=None,
        )

        # Processing station
        FuelStation.objects.create(
            id="processing-1",
            name="Processing Station",
            address="333 Work Ln",
            city="Active",
            state="AA",
            geocoding_status="processing",
            geocoding_confidence=None,
        )

    def test_command_runs_successfully(self):
        """Test that the command runs without errors."""
        self._create_test_stations()
        out = StringIO()
        call_command("geocoding_report", stdout=out)
        output = out.getvalue()

        # Check report structure
        self.assertIn("GEOCODING STATUS REPORT", output)
        self.assertIn("Total Stations:", output)
        self.assertIn("Geocoding Status:", output)
        self.assertIn("Confidence Distribution", output)
        self.assertIn("Anomalies:", output)
        self.assertIn("Route Eligibility:", output)

    def test_report_counts_correctly(self):
        """Test that the report counts match the actual data."""
        self._create_test_stations()
        out = StringIO()
        call_command("geocoding_report", stdout=out)
        output = out.getvalue()

        # Check total count
        self.assertIn("Total Stations: 8", output)

        # Check state counts
        self.assertIn("Success:         4", output)  # 4 success stations
        self.assertIn("Failed:          1", output)
        self.assertIn("Pending:         1", output)
        self.assertIn("Claimed:         1", output)
        self.assertIn("Processing:      1", output)

        # Check confidence counts (all stations)
        self.assertIn("High:        2", output)  # 2 high confidence (success-1, success-4)
        self.assertIn("Medium:      1", output)  # 1 medium confidence (success-2)
        self.assertIn("Low:         1", output)  # 1 low confidence (success-3)
        self.assertIn("Unset:       4", output)  # 4 unset confidence (failed, pending, claimed, processing)

        # Check anomalies
        self.assertIn("Null coords in success:      1", output)
        self.assertIn("Low confidence in success:      1", output)

        # Check eligibility
        self.assertIn("Eligible (success + valid coords):      3", output)

    def test_strict_mode_passes_with_clean_data(self):
        """Test strict mode passes when all data is clean."""
        # Create a clean dataset
        FuelStation.objects.all().delete()

        FuelStation.objects.create(
            id="clean-1",
            name="Clean Station 1",
            address="123 Clean St",
            city="Clean",
            state="CL",
            latitude=36.0,
            longitude=-86.0,
            geocoding_status="success",
            geocoding_confidence="high",
        )

        out = StringIO()
        # Should not raise CommandError
        call_command("geocoding_report", "--strict", stdout=out)
        output = out.getvalue()
        self.assertIn("Strict mode: ALL CHECKS PASSED", output)

    def test_strict_mode_fails_with_failed_stations(self):
        """Test strict mode fails when there are failed stations."""
        FuelStation.objects.all().delete()

        FuelStation.objects.create(
            id="fail-1",
            name="Failed Station",
            address="123 Fail St",
            city="Fail",
            state="FL",
            geocoding_status="failed",
        )

        out = StringIO()
        with self.assertRaises(CommandError) as cm:
            call_command("geocoding_report", "--strict", stdout=out)

        self.assertIn("Strict mode check failed", str(cm.exception))
        output = out.getvalue()
        self.assertIn("Failed stations: 1 > 0", output)
        self.assertIn("Strict mode: FAILURE CLASSES DETECTED", output)

    def test_strict_mode_fails_with_pending_stations(self):
        """Test strict mode fails when there are pending stations."""
        FuelStation.objects.all().delete()

        FuelStation.objects.create(
            id="pending-1",
            name="Pending Station",
            address="123 Pending St",
            city="Pending",
            state="PN",
            geocoding_status="pending",
        )

        out = StringIO()
        with self.assertRaises(CommandError) as cm:
            call_command("geocoding_report", "--strict", stdout=out)

        self.assertIn("Strict mode check failed", str(cm.exception))
        output = out.getvalue()
        self.assertIn("Pending stations: 1 > 0", output)

    def test_strict_mode_fails_with_null_coords_in_success(self):
        """Test strict mode fails when success stations have null coordinates."""
        FuelStation.objects.all().delete()

        FuelStation.objects.create(
            id="null-1",
            name="Null Coords",
            address="123 Null St",
            city="Null",
            state="NL",
            geocoding_status="success",
            geocoding_confidence="high",
            latitude=None,
            longitude=None,
        )

        out = StringIO()
        with self.assertRaises(CommandError) as cm:
            call_command("geocoding_report", "--strict", stdout=out)

        self.assertIn("Strict mode check failed", str(cm.exception))
        output = out.getvalue()
        self.assertIn("Null coordinates in success: 1 > 0", output)

    def test_strict_mode_fails_with_low_confidence_in_success(self):
        """Test strict mode fails when success stations have low confidence."""
        FuelStation.objects.all().delete()

        FuelStation.objects.create(
            id="low-1",
            name="Low Confidence",
            address="123 Low St",
            city="Low",
            state="LW",
            latitude=36.0,
            longitude=-86.0,
            geocoding_status="success",
            geocoding_confidence="low",
        )

        out = StringIO()
        with self.assertRaises(CommandError) as cm:
            call_command("geocoding_report", "--strict", stdout=out)

        self.assertIn("Strict mode check failed", str(cm.exception))
        output = out.getvalue()
        self.assertIn("Low confidence in success: 1 > 0", output)

    def test_strict_mode_fails_with_multiple_issues(self):
        """Test strict mode reports all issues found."""
        self._create_test_stations()
        out = StringIO()
        with self.assertRaises(CommandError) as cm:
            call_command("geocoding_report", "--strict", stdout=out)

        self.assertIn("Strict mode check failed", str(cm.exception))
        output = out.getvalue()

        # Should report all four failure classes
        self.assertIn("Failed stations: 1 > 0", output)
        self.assertIn("Pending stations: 1 > 0", output)
        self.assertIn("Null coordinates in success: 1 > 0", output)
        self.assertIn("Low confidence in success: 1 > 0", output)

    def test_eligibility_count_excludes_null_coordinates(self):
        """Test that eligibility count excludes stations with null coordinates."""
        self._create_test_stations()
        out = StringIO()
        call_command("geocoding_report", stdout=out)
        output = out.getvalue()

        # 4 success stations, but 1 has null coords, so eligible = 3
        self.assertIn("Eligible (success + valid coords):      3", output)

    def test_report_with_empty_database(self):
        """Test report behavior when database is empty."""
        FuelStation.objects.all().delete()

        out = StringIO()
        call_command("geocoding_report", stdout=out)
        output = out.getvalue()

        self.assertIn("Total Stations: 0", output)
        self.assertIn("Success:         0", output)
        self.assertIn("Eligible (success + valid coords):      0", output)

        # Strict mode should pass with empty database
        out = StringIO()
        call_command("geocoding_report", "--strict", stdout=out)
        output = out.getvalue()
        self.assertIn("Strict mode: ALL CHECKS PASSED", output)

    def test_confidence_counts_all_stations(self):
        """Test that confidence distribution counts all stations, not just success."""
        # Create stations with various confidence levels and different statuses
        FuelStation.objects.all().delete()

        FuelStation.objects.create(
            id="conf-1",
            name="High Confidence Failed",
            address="1 St",
            city="A",
            state="AA",
            geocoding_status="failed",
            geocoding_confidence="high",
        )
        FuelStation.objects.create(
            id="conf-2",
            name="Medium Confidence Pending",
            address="2 St",
            city="B",
            state="BB",
            geocoding_status="pending",
            geocoding_confidence="medium",
        )
        FuelStation.objects.create(
            id="conf-3",
            name="Low Confidence Success",
            address="3 St",
            city="C",
            state="CC",
            latitude=1.0,
            longitude=1.0,
            geocoding_status="success",
            geocoding_confidence="low",
        )

        out = StringIO()
        call_command("geocoding_report", stdout=out)
        output = out.getvalue()

        # Should count all stations regardless of status
        self.assertIn("High:        1", output)
        self.assertIn("Medium:      1", output)
        self.assertIn("Low:         1", output)
        self.assertIn("Unset:       0", output)
