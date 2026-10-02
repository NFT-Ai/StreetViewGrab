"""Obliczenia geograficzne (odległość, azymut, kafle XYZ)."""

import math

EARTH_RADIUS_M = 6371008.8


def distance_m(lat1, lon1, lat2, lon2):
    """Odległość na sferze (haversine) w metrach."""
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = phi2 - phi1
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2.0) ** 2
    return 2.0 * EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(a)))


def bearing_deg(lat1, lon1, lat2, lon2):
    """Azymut (0 = północ, 90 = wschód) ze punktu 1 do punktu 2."""
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dlambda = math.radians(lon2 - lon1)
    y = math.sin(dlambda) * math.cos(phi2)
    x = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(dlambda)
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


def lonlat_to_tile(lon, lat, zoom):
    """Konwersja współrzędnych na indeks kafla XYZ (siatka Web Mercator)."""
    scale = 2 ** zoom
    lat = max(min(lat, 85.05112878), -85.05112878)
    x = int((lon + 180.0) / 360.0 * scale)
    y = int((1.0 - math.log(math.tan(math.radians(lat)) + 1.0 / math.cos(math.radians(lat)))
             / math.pi) / 2.0 * scale)
    return max(0, min(scale - 1, x)), max(0, min(scale - 1, y))


def tile_to_lonlat(tile_x, tile_y, zoom):
    """Środek kafla XYZ jako (lon, lat)."""
    scale = 2 ** zoom
    lon = (tile_x + 0.5) / scale * 360.0 - 180.0
    n = math.pi - 2.0 * math.pi * (tile_y + 0.5) / scale
    lat = math.degrees(math.atan(math.sinh(n)))
    return lon, lat


def tile_ring(center_x, center_y, rings=1, zoom=17):
    """Zwraca listę kafli wokół środkowego kafla (kwadrat o boku 2*rings+1)."""
    scale = 2 ** zoom
    tiles = []
    for dx in range(-rings, rings + 1):
        for dy in range(-rings, rings + 1):
            x = center_x + dx
            y = center_y + dy
            if 0 <= x < scale and 0 <= y < scale:
                tiles.append((x, y))
    return tiles
