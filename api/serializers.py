from decimal import Decimal

from rest_framework import serializers
from .models import FuelStation, ImportJob, GeocodeJob


class FuelStationSerializer(serializers.ModelSerializer):
    """Serializer for FuelStation model."""
    class Meta:
        model = FuelStation
        fields = (
            'id',
            'opis_truckstop_id',
            'rack_id',
            'name',
            'address',
            'city',
            'state',
            'price_per_gallon',
            'latitude',
            'longitude',
        )


class FuelStationQuerySerializer(serializers.Serializer):
    state = serializers.RegexField(
        r'^[A-Za-z]{2}$',
        required=False,
        trim_whitespace=True,
    )
    min_price = serializers.DecimalField(
        max_digits=5,
        decimal_places=2,
        min_value=Decimal('0'),
        required=False,
    )
    max_price = serializers.DecimalField(
        max_digits=5,
        decimal_places=2,
        min_value=Decimal('0'),
        required=False,
    )
    page = serializers.IntegerField(min_value=1, default=1)
    page_size = serializers.IntegerField(min_value=1, max_value=200, default=50)

    def validate_state(self, value):
        return value.upper()

    def validate(self, attrs):
        min_price = attrs.get('min_price')
        max_price = attrs.get('max_price')
        if min_price is not None and max_price is not None and min_price > max_price:
            raise serializers.ValidationError({
                'max_price': 'Must be greater than or equal to min_price.'
            })
        return attrs


class ImportJobSerializer(serializers.ModelSerializer):
    """Serializer for ImportJob model."""
    class Meta:
        model = ImportJob
        fields = '__all__'


class GeocodeJobSerializer(serializers.ModelSerializer):
    """Serializer for GeocodeJob model."""
    class Meta:
        model = GeocodeJob
        fields = (
            'id',
            'status',
            'total_stations',
            'processed_count',
            'success_count',
            'failed_count',
            'retry_failed',
            'created_at',
            'completed_at',
        )


class GeocodeRequestSerializer(serializers.Serializer):
    limit = serializers.IntegerField(min_value=1, max_value=2000, default=500)
    retry_failed = serializers.BooleanField(default=False)


class GeocodeCountsSerializer(serializers.Serializer):
    total = serializers.IntegerField(min_value=0)
    pending = serializers.IntegerField(min_value=0)
    claimed = serializers.IntegerField(min_value=0)
    processing = serializers.IntegerField(min_value=0)
    success = serializers.IntegerField(min_value=0)
    failed = serializers.IntegerField(min_value=0)


class GeocodeStatusResponseSerializer(serializers.Serializer):
    counts = GeocodeCountsSerializer()
    latest_job = GeocodeJobSerializer(allow_null=True)


# Request/Response serializers for API endpoints

class RoutePreviewRequestSerializer(serializers.Serializer):
    start = serializers.CharField()
    finish = serializers.CharField()


class RoutePreviewResponseSerializer(serializers.Serializer):
    distance_miles = serializers.FloatField()
    duration_minutes = serializers.IntegerField()
    geometry = serializers.DictField()


class FuelPlanRequestSerializer(serializers.Serializer):
    start = serializers.CharField()
    finish = serializers.CharField()


class FuelPlanResponseSerializer(serializers.Serializer):
    start = serializers.CharField()
    finish = serializers.CharField()
    distance_miles = serializers.FloatField()
    duration_minutes = serializers.IntegerField(min_value=0)
    route_geometry = serializers.DictField()
    fuel_stops = serializers.ListField(child=serializers.DictField())
    total_fuel_cost = serializers.DecimalField(max_digits=12, decimal_places=2)


class FuelStationsNearRouteRequestSerializer(serializers.Serializer):
    start = serializers.CharField()
    finish = serializers.CharField()
    corridor_miles = serializers.FloatField()


class LocationValidateRequestSerializer(serializers.Serializer):
    location = serializers.CharField()


class LocationValidateResponseSerializer(serializers.Serializer):
    valid = serializers.BooleanField()
    formatted_address = serializers.CharField()
    latitude = serializers.FloatField()
    longitude = serializers.FloatField()
    country = serializers.CharField()


class FuelEstimateRequestSerializer(serializers.Serializer):
    distance_miles = serializers.FloatField()
    mpg = serializers.FloatField()
    average_price_per_gallon = serializers.FloatField()


class FuelEstimateResponseSerializer(serializers.Serializer):
    gallons_needed = serializers.FloatField()
    estimated_cost = serializers.FloatField()
