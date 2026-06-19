from django.core.paginator import Paginator
from django.db.models import Count
from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.decorators import authentication_classes
from rest_framework.decorators import permission_classes
from rest_framework.response import Response
from .models import FuelStation, ImportJob, GeocodeJob
from .permissions import IsOperationsAdmin
from .serializers import (
    FuelStationSerializer, FuelStationQuerySerializer,
    ImportJobSerializer, GeocodeJobSerializer, GeocodeRequestSerializer,
    GeocodeStatusResponseSerializer,
    RoutePreviewRequestSerializer, RoutePreviewResponseSerializer,
    FuelPlanRequestSerializer, FuelPlanResponseSerializer,
    FuelStationsNearRouteRequestSerializer,
    LocationValidateRequestSerializer, LocationValidateResponseSerializer,
    FuelEstimateRequestSerializer, FuelEstimateResponseSerializer
)
from .services.geocoding_service import GeocodingService


# Health check
@api_view(['GET'])
def health_check(request):
    """Health check endpoint."""
    return Response({'status': 'healthy'})


# Route planning endpoints
@api_view(['POST'])
def route_preview(request):
    """
    Preview route geometry/distance without fuel optimization.

    TODO: Implement routing logic using RoutingService.
    """
    serializer = RoutePreviewRequestSerializer(data=request.data)
    if serializer.is_valid():
        return Response({
            'distance_miles': 0.0,
            'duration_minutes': 0,
            'geometry': {}
        })
    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@api_view(['POST'])
def route_fuel_plan(request):
    """
    Generate optimized fuel plan for a route.

    TODO: Implement fuel optimization logic using FuelOptimizer.
    """
    serializer = FuelPlanRequestSerializer(data=request.data)
    if serializer.is_valid():
        return Response({
            'start': '',
            'finish': '',
            'distance_miles': 0.0,
            'estimated_fuel_cost': 0.0,
            'stops': []
        })
    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


# Fuel station endpoints
@api_view(['GET'])
@authentication_classes([])
def fuel_stations_list(request):
    """
    List public fuel-station data with optional filters and bounded pagination.

    Query params: state, min_price, max_price, page, page_size

    """
    query_serializer = FuelStationQuerySerializer(data=request.query_params)
    if not query_serializer.is_valid():
        return Response(query_serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    filters = query_serializer.validated_data
    state = filters.get('state')
    min_price = filters.get('min_price')
    max_price = filters.get('max_price')
    page = filters['page']
    page_size = filters['page_size']

    stations = FuelStation.objects.all()
    if state:
        stations = stations.filter(state__iexact=state)
    if min_price is not None:
        stations = stations.filter(price_per_gallon__gte=min_price)
    if max_price is not None:
        stations = stations.filter(price_per_gallon__lte=max_price)
    stations = stations.order_by('id')
    page_obj = Paginator(stations, page_size).get_page(page)
    station_serializer = FuelStationSerializer(page_obj.object_list, many=True)

    return Response({
        'count': page_obj.paginator.count,
        'results': station_serializer.data
    })


@api_view(['GET'])
def fuel_station_detail(request, station_id):
    """
    Get details for a specific fuel station.

    TODO: Implement station lookup logic.
    """
    try:
        station = FuelStation.objects.get(id=station_id)
        serializer = FuelStationSerializer(station)
        return Response(serializer.data)
    except FuelStation.DoesNotExist:
        return Response({'error': 'Station not found'},
                        status=status.HTTP_404_NOT_FOUND)


@api_view(['POST'])
def fuel_stations_near_route(request):
    """
    Find fuel stations near a route corridor.

    TODO: Implement corridor matching logic using StationMatchingService.
    """
    serializer = FuelStationsNearRouteRequestSerializer(data=request.data)
    if serializer.is_valid():
        return Response({
            'route_distance_miles': 0.0,
            'stations': []
        })
    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


# Admin/operations endpoints
@api_view(['POST'])
@permission_classes([IsOperationsAdmin])
def admin_import_fuel_prices(request):
    """
    Upload/import fuel price CSV.

    TODO: Implement CSV import logic triggering async import task.
    """
    return Response({
        'imported': 0,
        'deduplicated': 0,
        'invalid_rows': 0,
        'message': 'TODO: Implement CSV import'
    })


@api_view(['GET'])
@permission_classes([IsOperationsAdmin])
def admin_import_status(request, import_id):
    """
    Check status of an import job.

    TODO: Implement import status lookup.
    """
    return Response({
        'import_id': import_id,
        'status': 'pending',
        'total_rows': 0,
        'processed_rows': 0,
        'failed_rows': 0
    })


@swagger_auto_schema(
    method='post',
    request_body=GeocodeRequestSerializer,
    responses={
        200: GeocodeJobSerializer,
        202: GeocodeJobSerializer,
        400: openapi.Response('Invalid geocoding request'),
    },
)
@api_view(['POST'])
@permission_classes([IsOperationsAdmin])
def admin_geocode_stations(request):
    """Claim a bounded geocoding job for the database worker."""
    request_serializer = GeocodeRequestSerializer(data=request.data)
    if not request_serializer.is_valid():
        return Response(
            request_serializer.errors,
            status=status.HTTP_400_BAD_REQUEST,
        )
    job = GeocodingService.create_job(**request_serializer.validated_data)
    response_status = (
        status.HTTP_202_ACCEPTED
        if job.total_stations
        else status.HTTP_200_OK
    )
    return Response(GeocodeJobSerializer(job).data, status=response_status)


@swagger_auto_schema(
    method='get',
    responses={200: GeocodeStatusResponseSerializer},
)
@api_view(['GET'])
@permission_classes([IsOperationsAdmin])
def admin_geocode_status(request):
    """Return aggregate station states and the latest persisted job."""
    counts = {
        'total': FuelStation.objects.count(),
        'pending': 0,
        'claimed': 0,
        'processing': 0,
        'success': 0,
        'failed': 0,
    }
    grouped_counts = FuelStation.objects.values('geocoding_status').annotate(
        count=Count('pk')
    )
    for group in grouped_counts:
        counts[group['geocoding_status']] = group['count']

    latest_job = GeocodeJob.objects.order_by('-created_at', '-pk').first()
    return Response({
        'counts': counts,
        'latest_job': (
            GeocodeJobSerializer(latest_job).data if latest_job else None
        ),
    })


# Utility endpoints
@api_view(['POST'])
def location_validate(request):
    """
    Validate an address/location.

    TODO: Implement location validation using GeocodingService.
    """
    serializer = LocationValidateRequestSerializer(data=request.data)
    if serializer.is_valid():
        return Response({
            'valid': False,
            'formatted_address': '',
            'latitude': 0.0,
            'longitude': 0.0,
            'country': ''
        })
    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@api_view(['POST'])
def fuel_estimate(request):
    """
    Simple fuel cost calculation without routing.

    TODO: Implement fuel calculation logic.
    """
    serializer = FuelEstimateRequestSerializer(data=request.data)
    if serializer.is_valid():
        return Response({
            'gallons_needed': 0.0,
            'estimated_cost': 0.0
        })
    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
