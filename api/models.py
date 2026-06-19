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
    geocoding_status = models.CharField(
        max_length=20,
        choices=GEOCODING_STATUS_CHOICES,
        default='pending'
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
        ]

    def __str__(self):
        return f"{self.name} - {self.city}, {self.state}"


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
