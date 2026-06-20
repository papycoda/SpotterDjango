from django.core.paginator import Paginator
from django.db.models import Count
from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.decorators import authentication_classes
from rest_framework.decorators import parser_classes
from rest_framework.decorators import permission_classes
from rest_framework.parsers import MultiPartParser
from rest_framework.response import Response
from .models import FuelStation, ImportJob, GeocodeJob
from .permissions import IsOperationsAdmin
from .serializers import (
    ErrorResponseSerializer, HealthResponseSerializer,
    FuelStationSerializer, FuelStationQuerySerializer,
    FuelStationListResponseSerializer, ImportJobSerializer,
    ImportFuelPricesResponseSerializer,
    GeocodeJobSerializer, GeocodeRequestSerializer,
    GeocodeStatusResponseSerializer,
    RoutePreviewRequestSerializer, RoutePreviewResponseSerializer,
    FuelPlanRequestSerializer, FuelPlanResponseSerializer,
    FuelStationsNearRouteRequestSerializer, FuelStationsNearRouteResponseSerializer,
    LocationValidateRequestSerializer, LocationValidateResponseSerializer,
    FuelEstimateRequestSerializer, FuelEstimateResponseSerializer
)
from .services.geocoding_service import GeocodingService
from .services.fuel_optimization_service import RouteGapTooLargeError
from .services.fuel_plan_service import FuelPlanService


# Health check
@swagger_auto_schema(
    method='get',
    operation_summary='Check API health',
    operation_description='Returns a lightweight liveness response.',
    tags=['System'],
    responses={200: HealthResponseSerializer},
)
@api_view(['GET'])
def health_check(request):
    """Health check endpoint."""
    return Response({'status': 'healthy'})


# Route planning endpoints
@swagger_auto_schema(
    method='post',
    operation_summary='Preview a route',
    operation_description=(
        'Scaffolded endpoint. The final operation will geocode two US locations '
        'and return one OSRM route without fuel-stop optimization.'
    ),
    tags=['Routes'],
    request_body=RoutePreviewRequestSerializer,
    responses={
        200: RoutePreviewResponseSerializer,
        400: openapi.Response('Invalid request', ErrorResponseSerializer),
        502: openapi.Response('Upstream routing service failure', ErrorResponseSerializer),
    },
)
@api_view(['POST'])
def route_preview(request):
    """
    Preview route geometry/distance without fuel optimization.

    Uses geocoding and OSRM routing to return route preview.
    """
    from api.services.route_service import (
        plan_route,
        LocationNotInUSAError,
        RouteNotFoundError,
        RoutingTransientError,
    )

    serializer = RoutePreviewRequestSerializer(data=request.data)
    if not serializer.is_valid():
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    start = serializer.validated_data['start']
    finish = serializer.validated_data['finish']

    try:
        route_plan = plan_route(start, finish)

        # Convert route geometry to GeoJSON format
        geojson_geometry = {
            "type": "LineString",
            "coordinates": [
                [float(lon), float(lat)]
                for lat, lon in route_plan.route_geometry
            ]
        }

        return Response({
            'distance_miles': float(route_plan.total_distance_miles),
            'duration_minutes': int(float(route_plan.total_duration_minutes)),
            'geometry': geojson_geometry
        })

    except LocationNotInUSAError as exc:
        return Response(
            {'error': f'Location not in USA: {exc}'},
            status=status.HTTP_404_NOT_FOUND
        )
    except RouteNotFoundError as exc:
        return Response(
            {'error': f'No route found: {exc}'},
            status=status.HTTP_422_UNPROCESSABLE_ENTITY
        )
    except RoutingTransientError as exc:
        return Response(
            {'error': f'Routing service unavailable: {exc}'},
            status=status.HTTP_502_BAD_GATEWAY
        )
    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@swagger_auto_schema(
    method='post',
    operation_summary='Create an optimized fuel plan',
    operation_description=(
        'Primary assignment endpoint. Start and finish must be within the USA. '
        'The vehicle assumptions are fixed server-side at 500 miles maximum '
        'range, 10 MPG, and an initial full 50-gallon tank. The completed '
        'operation will return GeoJSON route geometry, selected fuel stops, '
        'total fuel purchased, and total en-route fuel cost.'
    ),
    tags=['Routes'],
    request_body=FuelPlanRequestSerializer,
    responses={
        200: FuelPlanResponseSerializer,
        400: openapi.Response('Invalid request', ErrorResponseSerializer),
        404: openapi.Response('Start or finish could not be resolved', ErrorResponseSerializer),
        422: openapi.Response('No feasible fuel plan exists', ErrorResponseSerializer),
        502: openapi.Response('Geocoding or routing service failure', ErrorResponseSerializer),
    },
)
@api_view(['POST'])
def route_fuel_plan(request):
    """
    Generate optimized fuel plan for a route.

    Uses geocoding, OSRM routing, station filtering, and fuel optimization
    to return the most cost-effective fuel stops along the route.
    """
    from api.services.route_service import (
        LocationNotInUSAError,
        RouteNotFoundError,
        RoutingTransientError,
    )

    serializer = FuelPlanRequestSerializer(data=request.data)
    if not serializer.is_valid():
        return Response(
            {'error': 'Invalid request.', 'details': serializer.errors},
            status=status.HTTP_400_BAD_REQUEST,
        )

    start = serializer.validated_data['start']
    finish = serializer.validated_data['finish']

    try:
        result = FuelPlanService().create_plan(start, finish)
        route_plan = result.route_plan
        fuel_plan = result.fuel_plan

        # Step 5: Convert route geometry to GeoJSON format
        # GeoJSON LineString: {"type": "LineString", "coordinates": [[lon, lat], ...]}
        geojson_geometry = {
            "type": "LineString",
            "coordinates": [
                [float(lon), float(lat)]
                for lat, lon in route_plan.route_geometry
            ]
        }

        # Step 6: Format fuel stops for response
        fuel_stops = []
        for stop in fuel_plan.fuel_stops:
            fuel_stops.append({
                'station_id': stop.station.id,
                'name': stop.station.name,
                'address': stop.station.address,
                'city': stop.station.city,
                'state': stop.station.state,
                'price_per_gallon': format(stop.station.price_per_gallon, '.2f'),
                'route_progress_miles': format(stop.route_progress_miles, '.3f'),
                'gallons_purchased': format(stop.gallons_purchased, '.3f'),
                'cost_usd': format(stop.cost_usd, '.2f'),
            })

        # Step 7: Build response
        response_data = {
            'start': route_plan.start_geocoded.display_name,
            'finish': route_plan.end_geocoded.display_name,
            'distance_miles': float(route_plan.total_distance_miles),
            'duration_minutes': int(float(route_plan.total_duration_minutes)),
            'route_geometry': geojson_geometry,
            'fuel_stops': fuel_stops,
            'total_fuel_purchased': format(fuel_plan.total_fuel_purchased, '.3f'),
            'total_fuel_cost': format(fuel_plan.total_cost_usd, '.2f'),
            'vehicle_assumptions': fuel_plan.vehicle_assumptions,
        }

        return Response(FuelPlanResponseSerializer(response_data).data)

    except LocationNotInUSAError as exc:
        return Response(
            {'error': f'Location not in USA: {exc}'},
            status=status.HTTP_404_NOT_FOUND
        )
    except RouteNotFoundError as exc:
        return Response(
            {'error': f'No route found: {exc}'},
            status=status.HTTP_422_UNPROCESSABLE_ENTITY
        )
    except RouteGapTooLargeError as exc:
        return Response(
            {'error': str(exc)},
            status=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )
    except RoutingTransientError as exc:
        return Response(
            {'error': f'Routing service unavailable: {exc}'},
            status=status.HTTP_502_BAD_GATEWAY
        )


# Fuel station endpoints
@swagger_auto_schema(
    method='get',
    operation_summary='List fuel stations',
    operation_description='Returns bounded, paginated public station data.',
    tags=['Fuel stations'],
    query_serializer=FuelStationQuerySerializer,
    responses={
        200: FuelStationListResponseSerializer,
        400: openapi.Response('Invalid filter or pagination value', ErrorResponseSerializer),
    },
)
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


@swagger_auto_schema(
    method='get',
    operation_summary='Get a fuel station',
    tags=['Fuel stations'],
    responses={
        200: FuelStationSerializer,
        404: openapi.Response('Station not found', ErrorResponseSerializer),
    },
)
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


@swagger_auto_schema(
    method='post',
    operation_summary='Find stations near a route',
    operation_description=(
        'Scaffolded corridor-matching endpoint. Only already-geocoded stations '
        'will be eligible; route planning never geocodes stations.'
    ),
    tags=['Fuel stations'],
    request_body=FuelStationsNearRouteRequestSerializer,
    responses={
        200: FuelStationsNearRouteResponseSerializer,
        400: openapi.Response('Invalid request', ErrorResponseSerializer),
        502: openapi.Response('Upstream routing service failure', ErrorResponseSerializer),
    },
)
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
@swagger_auto_schema(
    method='post',
    operation_summary='Import fuel prices',
    operation_description=(
        'Staff-only scaffolded endpoint for importing the assignment CSV. '
        'The management command is the currently supported import path.'
    ),
    tags=['Operations'],
    manual_parameters=[openapi.Parameter(
        'file', openapi.IN_FORM, type=openapi.TYPE_FILE, required=True,
        description='Fuel-price CSV file.',
    )],
    consumes=['multipart/form-data'],
    responses={
        200: ImportFuelPricesResponseSerializer,
        401: openapi.Response('Authentication required', ErrorResponseSerializer),
        403: openapi.Response('Staff access required', ErrorResponseSerializer),
    },
)
@api_view(['POST'])
@parser_classes([MultiPartParser])
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


@swagger_auto_schema(
    method='get',
    operation_summary='Get fuel-price import status',
    operation_description='Staff-only scaffolded import-job status endpoint.',
    tags=['Operations'],
    responses={
        200: ImportJobSerializer,
        401: openapi.Response('Authentication required', ErrorResponseSerializer),
        403: openapi.Response('Staff access required', ErrorResponseSerializer),
        404: openapi.Response('Import job not found', ErrorResponseSerializer),
    },
)
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
    operation_summary='Queue stations for geocoding',
    operation_description=(
        'Staff-only. Claims a bounded persisted batch for the database-backed '
        'run_geocoding_worker process; no geocoding call occurs in this request.'
    ),
    tags=['Operations'],
    request_body=GeocodeRequestSerializer,
    responses={
        200: GeocodeJobSerializer,
        202: GeocodeJobSerializer,
        400: openapi.Response('Invalid geocoding request'),
        401: openapi.Response('Authentication required', ErrorResponseSerializer),
        403: openapi.Response('Staff access required', ErrorResponseSerializer),
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
    operation_summary='Get geocoding status',
    operation_description='Staff-only aggregate station states and latest persisted job.',
    tags=['Operations'],
    responses={
        200: GeocodeStatusResponseSerializer,
        401: openapi.Response('Authentication required', ErrorResponseSerializer),
        403: openapi.Response('Staff access required', ErrorResponseSerializer),
    },
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
@swagger_auto_schema(
    method='post',
    operation_summary='Validate a location',
    operation_description='Scaffolded endpoint for validating a US location.',
    tags=['Utilities'],
    request_body=LocationValidateRequestSerializer,
    responses={
        200: LocationValidateResponseSerializer,
        400: openapi.Response('Invalid request', ErrorResponseSerializer),
        502: openapi.Response('Upstream geocoding service failure', ErrorResponseSerializer),
    },
)
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


@swagger_auto_schema(
    method='post',
    operation_summary='Estimate fuel cost',
    operation_description='Scaffolded standalone fuel calculation without routing.',
    tags=['Utilities'],
    request_body=FuelEstimateRequestSerializer,
    responses={
        200: FuelEstimateResponseSerializer,
        400: openapi.Response('Invalid request', ErrorResponseSerializer),
    },
)
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
