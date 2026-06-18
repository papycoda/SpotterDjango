from rest_framework import serializers
from .models import FuelStation, ImportJob, GeocodeJob


class FuelStationSerializer(serializers.ModelSerializer):
    """Serializer for FuelStation model."""
    class Meta:
        model = FuelStation
        fields = '__all__'


class ImportJobSerializer(serializers.ModelSerializer):
    """Serializer for ImportJob model."""
    class Meta:
        model = ImportJob
        fields = '__all__'


class GeocodeJobSerializer(serializers.ModelSerializer):
    """Serializer for GeocodeJob model."""
    class Meta:
        model = GeocodeJob
        fields = '__all__'


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
