"""
Geokodowanie adresu na współrzędne.

Domyślnie korzystamy z Nominatim (OpenStreetMap) - bezpłatnie i bez klucza API.
Jeżeli podano klucz Google (``--api-key``), używamy Geocoding API, a Nominatim
zostaje jako zapas.
"""

import json
import urllib.parse

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
GOOGLE_GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"
#: Nominatim wymaga czytelnego User-Agenta identyfikującego aplikację.
NOMINATIM_USER_AGENT = "gsvgrab/1.0 (pobieranie panoram Street View; Python stdlib)"


class GeocodeError(Exception):
    """Nie udało się ustalić współrzędnych."""


def geocode_nominatim(query, client, limit=1):
    """Geokodowanie przez Nominatim (OSM)."""
    params = {"q": query, "format": "jsonv2", "limit": str(limit),
              "addressdetails": "1", "accept-language": "pl,en"}
    url = NOMINATIM_URL + "?" + urllib.parse.urlencode(params)
    payload = client.get_text(url, use_cache=True, cache_only=False,
                              headers={"User-Agent": NOMINATIM_USER_AGENT,
                                       "Accept": "application/json"})
    results = json.loads(payload)
    if not results:
        raise GeocodeError("Nominatim nie znalazl adresu: %s" % query)
    best = results[0]
    return {
        "source": "nominatim",
        "query": query,
        "lat": float(best["lat"]),
        "lon": float(best["lon"]),
        "display_name": best.get("display_name"),
        "type": best.get("type"),
        "category": best.get("category"),
        "osm_id": best.get("osm_id"),
        "raw": best,
    }


def geocode_google(query, api_key, client):
    """Geokodowanie przez Google Geocoding API (wymaga klucza)."""
    params = {"address": query, "key": api_key, "language": "pl"}
    url = GOOGLE_GEOCODE_URL + "?" + urllib.parse.urlencode(params)
    payload = client.get_text(url, use_cache=False)
    data = json.loads(payload)
    if data.get("status") != "OK" or not data.get("results"):
        raise GeocodeError("Google Geocoding: %s dla %s"
                           % (data.get("status"), data.get("error_message") or query))
    best = data["results"][0]
    location = best["geometry"]["location"]
    return {
        "source": "google",
        "query": query,
        "lat": location["lat"],
        "lon": location["lng"],
        "display_name": best.get("formatted_address"),
        "location_type": best["geometry"].get("location_type"),
        "place_id": best.get("place_id"),
        "raw": best,
    }


def geocode(query, client, api_key=None):
    """Geokoduje adres; gdy podano klucz, preferuje Google."""
    if api_key:
        try:
            return geocode_google(query, api_key, client)
        except GeocodeError:
            pass
    return geocode_nominatim(query, client)
