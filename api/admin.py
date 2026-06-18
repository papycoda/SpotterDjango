from django.contrib import admin
from .models import FuelStation, ImportJob, GeocodeJob


@admin.register(FuelStation)
class FuelStationAdmin(admin.ModelAdmin):
    list_display = ['name', 'city', 'state', 'price_per_gallon', 'geocoding_status']
    list_filter = ['state', 'geocoding_status']
    search_fields = ['name', 'address', 'city']
    readonly_fields = ['created_at', 'updated_at']


@admin.register(ImportJob)
class ImportJobAdmin(admin.ModelAdmin):
    list_display = ['id', 'status', 'total_rows', 'processed_rows', 'failed_rows', 'created_at']
    list_filter = ['status']
    readonly_fields = ['id', 'created_at', 'completed_at']


@admin.register(GeocodeJob)
class GeocodeJobAdmin(admin.ModelAdmin):
    list_display = ['id', 'status', 'total_stations', 'processed_count', 'success_count', 'failed_count', 'created_at']
    list_filter = ['status']
    readonly_fields = ['id', 'created_at', 'completed_at']
