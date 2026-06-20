from django.db import models


class FuelStation(models.Model):
    """Model for fuel station data."""
    GEOCODING_STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('claimed', 'Claimed'),
        ('processing', 'Processing'),
        ('success', 'Success'),
        ('failed', 'Failed'),
    ]

    GEOCODING_FAILURE_REASON_CHOICES = [
        ('no_match_osm', 'No OSM Match'),
        ('outside_usa', 'Outside USA'),
        ('not_fuel_station', 'Not Fuel Station'),
        ('city_mismatch', 'City Mismatch'),
        ('state_mismatch', 'State Mismatch'),
        ('rate_limited', 'Rate Limited'),
        ('network_error', 'Network Error'),
        ('upstream_error', 'Upstream Error'),
        ('invalid_response', 'Invalid Response'),
        ('invalid_coordinates', 'Invalid Coordinates'),
        ('unknown', 'Unknown Error'),
    ]

    GEOCODING_CONFIDENCE_CHOICES = [
        ('high', 'High'),
        ('medium', 'Medium'),
        ('low', 'Low'),
    ]

    COORDINATE_SOURCE_CHOICES = [
        ('exact_station', 'Exact Station Location'),
        ('city_centroid', 'City Centroid (Approximate)'),
        ('manual', 'Manual Coordinates'),
        ('unknown', 'Unknown Source'),
    ]

    COORDINATE_QUALITY_CHOICES = [
        ('high', 'High Quality'),
        ('medium', 'Medium Quality'),
        ('approximate', 'Approximate (City Centroid)'),
        ('unknown', 'Unknown Quality'),
    ]

    id = models.CharField(primary_key=True, max_length=50)
    opis_truckstop_id = models.CharField(max_length=50, unique=True, null=True, blank=True)
    rack_id = models.CharField(max_length=20, blank=True)
    name = models.CharField(max_length=255)
    address = models.CharField(max_length=255)
    city = models.CharField(max_length=100)
    state = models.CharField(max_length=2)
    price_per_gallon = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    latitude = models.DecimalField(max_digits=10, decimal_places=7, null=True, blank=True)
    longitude = models.DecimalField(max_digits=10, decimal_places=7, null=True, blank=True)
    coordinate_source = models.CharField(
        max_length=20,
        choices=COORDINATE_SOURCE_CHOICES,
        default='unknown',
        help_text="Source of station coordinates"
    )
    coordinate_quality = models.CharField(
        max_length=20,
        choices=COORDINATE_QUALITY_CHOICES,
        default='unknown',
        help_text="Quality/accuracy of coordinates"
    )
    geocoding_status = models.CharField(
        max_length=20,
        choices=GEOCODING_STATUS_CHOICES,
        default='pending'
    )
    geocoding_failure_reason = models.CharField(
        max_length=30,
        choices=GEOCODING_FAILURE_REASON_CHOICES,
        blank=True,
        null=True,
        help_text="Reason why geocoding failed"
    )
    geocoding_confidence = models.CharField(
        max_length=10,
        choices=GEOCODING_CONFIDENCE_CHOICES,
        blank=True,
        null=True,
        help_text="Confidence level of geocoding result"
    )
    geocoding_stage = models.SmallIntegerField(
        null=True,
        blank=True,
        help_text="Which stage of geocoding succeeded (1-4)"
    )
    geocoding_strategy_version = models.SmallIntegerField(
        default=0,
        help_text="Deterministic geocoding strategy version last completed",
    )
    geocode_job = models.ForeignKey(
        'GeocodeJob',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='stations',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=['state', 'price_per_gallon']),
            models.Index(fields=['geocoding_status']),
            models.Index(fields=['latitude', 'longitude']),
            models.Index(fields=['coordinate_source']),
            models.Index(fields=['coordinate_quality']),
        ]

    def __str__(self):
        return f"{self.name} - {self.city}, {self.state}"


class CityCoordinate(models.Model):
    """Model for city centroid coordinates."""
    city = models.CharField(max_length=100)
    state = models.CharField(max_length=2)
    latitude = models.DecimalField(max_digits=10, decimal_places=7)
    longitude = models.DecimalField(max_digits=10, decimal_places=7)
    source = models.CharField(max_length=255, default="us_cities_dataset")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['city', 'state'], name='unique_city_state_coordinate')
        ]
        indexes = [
            models.Index(fields=['city', 'state']),
        ]

    def __str__(self):
        return f"{self.city}, {self.state}"


class ImportJob(models.Model):
    """Model for tracking CSV import jobs."""
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('processing', 'Processing'),
        ('completed', 'Completed'),
        ('failed', 'Failed'),
    ]

    id = models.CharField(primary_key=True, max_length=50)
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='pending'
    )
    total_rows = models.IntegerField(default=0)
    processed_rows = models.IntegerField(default=0)
    failed_rows = models.IntegerField(default=0)
    error_message = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"ImportJob {self.id} - {self.status}"


class GeocodeJob(models.Model):
    """Model for tracking geocoding batch jobs."""
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('processing', 'Processing'),
        ('completed', 'Completed'),
        ('failed', 'Failed'),
    ]

    id = models.CharField(primary_key=True, max_length=50)
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='pending'
    )
    total_stations = models.IntegerField(default=0)
    processed_count = models.IntegerField(default=0)
    success_count = models.IntegerField(default=0)
    failed_count = models.IntegerField(default=0)
    error_message = models.TextField(blank=True, null=True)
    retry_failed = models.BooleanField(default=False)
    heartbeat_at = models.DateTimeField(null=True, blank=True)
    worker_id = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"GeocodeJob {self.id} - {self.status}"


class GeocodingRateLimit(models.Model):
    """Singleton database state used to reserve Nominatim request slots."""

    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    next_allowed_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return "Nominatim rate limit"
