from django.urls import path, re_path
from drf_yasg.views import get_schema_view
from drf_yasg import openapi
from rest_framework import permissions
from . import views

schema_view = get_schema_view(
    openapi.Info(
        title='FuelSpotter API',
        default_version='v1',
        description=(
            'USA route fuel-planning API. The primary fuel-plan operation uses '
            'a fixed 500-mile vehicle range, 10 MPG fuel economy, and an initial '
            'full 50-gallon tank. Station geocoding is an offline operational '
            'workflow and is never performed during route planning.'
        ),
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
    re_path(
        r'^fuel-stations/(?P<station_id>(?!near-route/$)[^/]+)/$',
        views.fuel_station_detail,
        name='fuel-station-detail',
    ),

    # Admin/operations
    path('admin/fuel-stations/geocode/', views.admin_geocode_stations, name='admin-geocode-stations'),
    path('admin/fuel-stations/geocode/status/', views.admin_geocode_status, name='admin-geocode-status'),
]
