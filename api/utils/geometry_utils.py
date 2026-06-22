
"""Geometry utilities for route simplification."""

import math
from decimal import Decimal
from typing import List, Tuple


def perpendicular_distance_meters_fast(
    point: Tuple[float, float],
    line_start: Tuple[float, float],
    line_end: Tuple[float, float],
) -> float:
    """
    Calculate perpendicular distance from point to line segment using equirectangular projection.
    For small distances (like 50m tolerance), this is accurate enough for simplification!
    """
    lat1, lon1 = line_start
    lat2, lon2 = line_end
    lat_p, lon_p = point

    # If line segment is essentially a point
    if abs(lat1 - lat2) < 1e-9 and abs(lon1 - lon2) < 1e-9:
        return haversine_distance_meters(point, line_start)

    # Approximate using equirectangular projection (super fast)
    # Use mid-point of line_start to calculate cos term only once
    mid_lat = (lat1 + lat2) / 2
    cos_mid = math.cos(math.radians(mid_lat))

    # Convert to meters
    def to_meters(lat, lon):
        # Approximate coordinates in meters
        return (
            lon * 111320 * cos_mid, lat * 110574)

    # Project all points to local Cartesian (in meters)
    sx, sy = to_meters(lat1, lon1)
    ex, ey = to_meters(lat2, lon2)
    px, py = to_meters(lat_p, lon_p)

    dx = ex - sx
    dy = ey - sy
    length_squared = dx * dx + dy * dy

    if length_squared < 1e-6:
        return haversine_distance_meters(point, line_start)

    t = ((px - sx) * dx + (py - sy) * dy)
    t /= length_squared
    t = max(0.0, min(1.0, t))
    nearest_x = sx + t * dx
    nearest_y = sy + t * dy

    dx_p = px - nearest_x
    dy_p = py - nearest_y
    return math.hypot(dx_p, dy_p)


def haversine_distance_meters(
    point1: Tuple[float, float],
    point2: Tuple[float, float],
) -> float:
    """Calculate great-circle distance between two points in meters."""
    lat1, lon1 = point1
    lat2, lon2 = point2

    # Earth radius in meters
    R = 6_371_008.8

    # Convert to radians
    lat1_rad = math.radians(lat1)
    lat2_rad = math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)

    # Haversine formula
    a = (math.sin(dlat / 2) ** 2 +
         math.cos(lat1_rad) * math.cos(lat2_rad) *
         math.sin(dlon / 2) ** 2)

    return 2 * R * math.atan2(math.sqrt(a), math.sqrt(max(0.0, 1 - a)))


def perpendicular_distance_meters(
    point: Tuple[float, float],
    line_start: Tuple[float, float],
    line_end: Tuple[float, float],
) -> float:
    """
    Calculate perpendicular distance from point to line segment in meters.

    Uses haversine-based approximation for reasonable accuracy.
    """
    lat1, lon1 = line_start
    lat2, lon2 = line_end
    lat_p, lon_p = point

    # If line segment is essentially a point
    if abs(lat1 - lat2) < 1e-9 and abs(lon1 - lon2) < 1e-9:
        return haversine_distance_meters(point, line_start)

    # Project point onto line using dot product
    # Vector from start to end
    dx = lon2 - lon1
    dy = lat2 - lat1
    length_squared = dx * dx + dy * dy

    if length_squared < 1e-18:
        return haversine_distance_meters(point, line_start)

    # Vector from start to point
    px = lon_p - lon1
    py = lat_p - lat1

    # Projection parameter t (0 to 1 for points on segment)
    t = (px * dx + py * dy) / length_squared
    t = max(0.0, min(1.0, t))

    # Nearest point on segment
    nearest_lat = lat1 + t * dy
    nearest_lon = lon1 + t * dx

    # Distance to nearest point using haversine
    return haversine_distance_meters(point, (nearest_lat, nearest_lon))


# Alias for backward compatibility!
perpendicular_distance = perpendicular_distance_meters


def douglas_peucker_simplify(
    coordinates: List[Tuple[Decimal, Decimal]],
    tolerance_meters: float = 50.0,
) -> List[Tuple[Decimal, Decimal]]:
    """
    Simplify a polyline using Douglas-Peucker algorithm.

    Preserves route shape while reducing point count for faster processing.
    50-meter tolerance maintains ~100m accuracy which is sufficient for
    station filtering (we use 5-mile corridor anyway).

    Args:
        coordinates: List of (lat, lon) tuples
        tolerance_meters: Maximum deviation from original (default 50m)

    Returns:
        Simplified list of (lat, lon) tuples
    """
    if len(coordinates) <= 2:
        return list(coordinates)

    # Convert to float for performance
    coords = [(float(lat), float(lon)) for lat, lon in coordinates]
    n = len(coords)

    # Use stack-based iterative approach to avoid recursion limit issues
    # keep[i] = True means the point at index i should be kept
    keep = [True] * n
    # Stack contains (start_idx, end_idx) pairs to process
    stack = [(0, n - 1)]

    while stack:
        start_idx, end_idx = stack.pop()
        if end_idx - start_idx <= 1:
            continue

        start_pt = coords[start_idx]
        end_pt = coords[end_idx]

        # Find point with maximum distance from the line segment
        max_dist = 0.0
        max_idx = start_idx

        for i in range(start_idx + 1, end_idx):
            dist = perpendicular_distance_meters_fast(coords[i], start_pt, end_pt)
            if dist > max_dist:
                max_dist = dist
                max_idx = i

        # If max distance exceeds tolerance, we need to keep the max_idx point
        # and recurse on both sides
        if max_dist > tolerance_meters:
            stack.append((start_idx, max_idx))
            stack.append((max_idx, end_idx))
        else:
            # All intermediate points are within tolerance, mark for removal
            for i in range(start_idx + 1, end_idx):
                keep[i] = False

    return [coordinates[i] for i in range(n) if keep[i]]


def simplify_for_station_filtering(
    coordinates: List[Tuple[Decimal, Decimal]],
    target_point_count: int = 300,
) -> List[Tuple[Decimal, Decimal]]:
    """
    Simplify route geometry to a target point count for station filtering.

    Uses binary search for accurate target hit, with fast distance calculation!

    Args:
        coordinates: Original route geometry
        target_point_count: Target number of points (default 300)

    Returns:
        Simplified coordinates suitable for station distance calculations
    """
    if len(coordinates) <= target_point_count:
        return list(coordinates)

    # Binary search for appropriate tolerance
    min_tolerance = 50.0   # Start with 50 meters
    max_tolerance = 2000.0 # Max tolerance of 2km
    best_result = douglas_peucker_simplify(coordinates, max_tolerance)

    for _ in range(8):
        tolerance = (min_tolerance + max_tolerance) / 2
        result = douglas_peucker_simplify(coordinates, tolerance)

        if len(result) > target_point_count:
            # Too many points, increase tolerance
            min_tolerance = tolerance
        else:
            # Fewer or equal points, this could work
            max_tolerance = tolerance
            best_result = result

    return best_result
