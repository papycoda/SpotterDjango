from decimal import Decimal

from rest_framework import serializers
from .models import FuelStation, GeocodeJob


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


class ErrorResponseSerializer(serializers.Serializer):
    error = serializers.CharField()
    details = serializers.DictField(required=False)


class HealthResponseSerializer(serializers.Serializer):
    status = serializers.CharField()


class FuelStationListResponseSerializer(serializers.Serializer):
    count = serializers.IntegerField(min_value=0)
    results = FuelStationSerializer(many=True)


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


class GeoJSONLineStringSerializer(serializers.Serializer):
    type = serializers.ChoiceField(choices=("LineString",))
    coordinates = serializers.ListField(
        child=serializers.ListField(
            child=serializers.FloatField(),
            min_length=2,
            max_length=2,
        ),
        min_length=2,
    )


class FuelStopResponseSerializer(serializers.Serializer):
    station_id = serializers.CharField()
    name = serializers.CharField()
    address = serializers.CharField()
    city = serializers.CharField()
    state = serializers.CharField()
    price_per_gallon = serializers.RegexField(r"^\d+\.\d{2}$")
    route_progress_miles = serializers.RegexField(r"^\d+\.\d{3}$")
    gallons_purchased = serializers.RegexField(r"^\d+\.\d{3}$")
    cost_usd = serializers.RegexField(r"^\d+\.\d{2}$")


class VehicleAssumptionsSerializer(serializers.Serializer):
    range_miles = serializers.IntegerField(min_value=1)
    mpg = serializers.IntegerField(min_value=1)
    tank_gallons = serializers.IntegerField(min_value=1)


class FuelPlanResponseSerializer(serializers.Serializer):
    start = serializers.CharField()
    finish = serializers.CharField()
    distance_miles = serializers.FloatField()
    duration_minutes = serializers.IntegerField(min_value=0)
    route_geometry = GeoJSONLineStringSerializer()
    fuel_stops = FuelStopResponseSerializer(many=True)
    total_fuel_purchased = serializers.RegexField(r"^\d+\.\d{3}$")
    total_fuel_cost = serializers.RegexField(r"^\d+\.\d{2}$")
    vehicle_assumptions = VehicleAssumptionsSerializer()
