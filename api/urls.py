from django.urls import path
from drf_yasg.views import get_schema_view
from drf_yasg import openapi
from rest_framework import permissions
from . import views

schema_view = get_schema_view(
    openapi.Info(
        title='FuelSpotter API',
        default_version='v1',
        description='Route fuel planning and fuel station management API',
        terms_of_service='https://www.google.com/policies/terms/',
        contact=openapi.Contact(email='contact@example.com'),
        license=openapi.License(name='MIT License'),
    ),
    public=True,
    permission_classes=(permissions.AllowAny,),
)

urlpatterns = [
    # API Documentation
    path('swagger/', schema_view.with_ui('swagger', cache_timeout=0), name='schema-swagger-ui'),
    path('redoc/', schema_view.with_ui('redoc', cache_timeout=0), name='schema-redoc'),
    path('schema/', schema_view.without_ui(cache_timeout=0), name='schema-json'),

    # Health check
    path('health/', views.health_check, name='health-check'),

    # Route planning
    path('routes/preview/', views.route_preview, name='route-preview'),
    path('routes/fuel-plan/', views.route_fuel_plan, name='route-fuel-plan'),

    # Fuel stations
    path('fuel-stations/', views.fuel_stations_list, name='fuel-stations-list'),
    path('fuel-stations/near-route/', views.fuel_stations_near_route, name='fuel-stations-near-route'),
    path('fuel-stations/<str:station_id>/', views.fuel_station_detail, name='fuel-station-detail'),

    # Admin/operations
    path('admin/fuel-prices/import/', views.admin_import_fuel_prices, name='admin-import-fuel-prices'),
    path('admin/fuel-prices/imports/<str:import_id>/', views.admin_import_status, name='admin-import-status'),
    path('admin/fuel-stations/geocode/', views.admin_geocode_stations, name='admin-geocode-stations'),
    path('admin/fuel-stations/geocode/status/', views.admin_geocode_status, name='admin-geocode-status'),

    # Utilities
    path('locations/validate/', views.location_validate, name='location-validate'),
    path('fuel/estimate/', views.fuel_estimate, name='fuel-estimate'),
]
