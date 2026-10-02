"""
Endpointy Street View (Google) użyte przez tę aplikację.

Wszystkie endpointy zweryfikowane empirycznie (stan: październik 2026):

``maps.googleapis.com/maps/api/js/GeoPhotoService.SingleImageSearch``
    wyszukiwanie panoramy w zadanym promieniu wokół współrzędnych (JSONP, bez klucza).
``www.google.com/maps/photometa/v1``
    pełne metadane panoramy o danym ``panoid`` **łącznie z mapą głębi** (bez klucza).
``www.google.com/maps/photometa/ac/v1``
    kafel pokrycia (zoom 17) - lista panoram w kaflu wraz ze współrzędnymi.
``streetviewpixels-pa.googleapis.com/v1/tile``
    kafel obrazu 512x512 px panoramy; siatka ``2^zoom`` x ``2^(zoom-1)``.
``streetviewpixels-pa.googleapis.com/v1/thumbnail``
    podgląd panoramy w jednym pliku (maks. ok. 1024x512 px).
``maps.googleapis.com/maps/api/streetview``
    oficjalne Street View Static API - wymaga klucza (tryb ``--api-key``).
"""

import json

from . import protobuf_url as pb
from .geo import distance_m, lonlat_to_tile, tile_ring, tile_to_lonlat
from .protobuf_url import ProtobufEnum

REFERER = "https://www.google.com/"
MAPS_REFERER = "https://www.google.com/maps/"
TILE_HOST = "streetviewpixels-pa.googleapis.com"
#: Rozmiar "pustego" kafla zwracanego przez endpoint kafli (szara zaślepka).
BLANK_TILE_SIZE = 1184


def split_locale(locale):
    """``pl-PL`` -> ``('pl', 'PL')``."""
    parts = (locale or "en").split("-")
    return parts[0], (parts[1] if len(parts) > 1 else parts[0].upper())


def is_third_party_panoid(panoid):
    """Czy identyfikator pochodzi od zewnętrznego dostawcy (Photo Sphere)?"""
    return panoid.startswith("CIHM0og") or len(panoid) > 22


def search_url(lat, lon, radius=50, download_depth=True, locale="pl-PL", search_third_party=False):
    """SingleImageSearch - najbliższa panorama w promieniu ``radius`` metrów."""
    lang, country = split_locale(locale)
    image_type = 10 if search_third_party else 2
    toggles = [ProtobufEnum(1), ProtobufEnum(2), ProtobufEnum(3), ProtobufEnum(4), ProtobufEnum(6),
               ProtobufEnum(8), ProtobufEnum(12)]
    message = {
        1: {1: "apiv3", 5: "US", 11: {1: {1: False}}},
        2: {1: {3: float(lat), 4: float(lon)}, 2: float(radius)},
        3: {
            2: {1: lang, 2: country},
            9: {1: ProtobufEnum(2)},
            11: {1: {1: ProtobufEnum(image_type), 2: True, 3: ProtobufEnum(2)}},
        },
        4: {1: toggles,
            5: {1: ProtobufEnum(0)} if download_depth else {},
            6: {1: ProtobufEnum(2)} if download_depth else {}},
    }
    return ("https://maps.googleapis.com/maps/api/js/GeoPhotoService.SingleImageSearch?pb="
            + pb.to_protobuf_url(message) + "&callback=_xdc_._v2mub5")



def photometa_url(panoid, download_depth=True, locale="pl-PL"):
    """``photometa/v1`` - metadane i (opcjonalnie) mapa głębi panoramy."""
    lang, country = split_locale(locale)
    toggles = [ProtobufEnum(1), ProtobufEnum(2), ProtobufEnum(3), ProtobufEnum(4), ProtobufEnum(5),
               ProtobufEnum(6), ProtobufEnum(8), ProtobufEnum(12)]
    if download_depth:
        depth1 = [{1: ProtobufEnum(1)}, {1: ProtobufEnum(2)}]
        depth2 = [{1: ProtobufEnum(1)}, {1: ProtobufEnum(2)}]
    else:
        depth1, depth2 = [{}], [{}]
    message = {
        1: {1: "maps_sv.tactile", 11: {2: {1: True}}},
        2: {1: lang, 2: country},
        3: {1: {1: ProtobufEnum(10 if is_third_party_panoid(panoid) else 2), 2: panoid}},
        4: {
            1: toggles,
            2: {1: ProtobufEnum(1)},
            4: {1: 48},
            5: depth1,
            6: depth2,
            9: {1: [{1: ProtobufEnum(2), 2: True, 3: ProtobufEnum(2)},
                    {1: ProtobufEnum(2), 2: False, 3: ProtobufEnum(3)},
                    {1: ProtobufEnum(3), 2: True, 3: ProtobufEnum(2)},
                    {1: ProtobufEnum(3), 2: False, 3: ProtobufEnum(3)},
                    {1: ProtobufEnum(8), 2: False, 3: ProtobufEnum(3)},
                    {1: ProtobufEnum(1), 2: False, 3: ProtobufEnum(3)},
                    {1: ProtobufEnum(4), 2: False, 3: ProtobufEnum(3)},
                    {1: ProtobufEnum(10), 2: True, 3: ProtobufEnum(2)},
                    {1: ProtobufEnum(10), 2: False, 3: ProtobufEnum(3)}]},
            11: {3: {4: True}},
        },
    }
    return ("https://www.google.com/maps/photometa/v1?authuser=0&hl=%s&gl=%s&pb=" % (lang, country)
            + pb.to_protobuf_url(message))


def coverage_tile_url(tile_x, tile_y, zoom=17):
    """Kafel pokrycia - lista panoram w kaflu XYZ (zoom 17 = ok. 300 m na kafel)."""
    return ("https://www.google.com/maps/photometa/ac/v1?pb=!1m1!1smaps_sv.tactile"
            "!6m3!1i%d!2i%d!3i%d!8b1" % (tile_x, tile_y, zoom))


def tile_url(panoid, x, y, zoom):
    """Pojedynczy kafel obrazu panoramy (512x512 px)."""
    return ("https://%s/v1/tile?cb_client=maps_sv.tactile&panoid=%s&x=%d&y=%d&zoom=%d"
            % (TILE_HOST, panoid, x, y, zoom))


def thumbnail_url(panoid, width=1024, height=512, yaw=0, pitch=0, fov=100):
    """Podgląd panoramy (jeden plik JPEG, maks. ok. 1024x512 px)."""
    return ("https://%s/v1/thumbnail?panoid=%s&cb_client=maps_sv.tactile&w=%d&h=%d"
            "&yaw=%s&pitch=%s&thumbfov=%s" % (TILE_HOST, panoid, width, height, yaw, pitch, fov))


def static_image_url(api_key, panoid=None, location=None, size="640x640", heading=None,
                     pitch=None, fov=None):
    """Oficjalne Street View Static API (tryb z kluczem)."""
    params = ["size=%s" % size]
    if panoid:
        params.append("pano=%s" % panoid)
    elif location:
        params.append("location=%s" % location.replace(" ", "+"))
    for name, value in (("heading", heading), ("pitch", pitch), ("fov", fov)):
        if value is not None:
            params.append("%s=%s" % (name, value))
    params.append("return_error_code=true")
    params.append("key=%s" % api_key)
    return "https://maps.googleapis.com/maps/api/streetview?" + "&".join(params)


def static_metadata_url(api_key, panoid=None, location=None):
    """Oficjalne metadane Street View Static API (bezpłatne)."""
    params = []
    if panoid:
        params.append("pano=%s" % panoid)
    elif location:
        params.append("location=%s" % location.replace(" ", "+"))
    params.append("key=%s" % api_key)
    return "https://maps.googleapis.com/maps/api/streetview/metadata?" + "&".join(params)


def tile_grid(zoom):
    """Siatka kafli panoramy ``(kolumny, wiersze)``.

    Zweryfikowane empirycznie na żywych panoramach: zoom 0 -> 1x1,
    zoom 1 -> 2x1, zoom 3 -> 8x4, zoom 5 -> 32x16 (kafle 512 px).
    """
    if zoom <= 0:
        return 1, 1
    return 2 ** zoom, 2 ** (zoom - 1)



# ---------------------------------------------------------------------------
# Klient API
# ---------------------------------------------------------------------------
def _repair_jsonp(text):
    """Zamienia odpowiedź JSONP (``/**/cb && cb( [[...]] )``) na JSON."""
    if "(" not in text or ")" not in text:
        return json.loads(text)
    first = text.index("(")
    last = text.rindex(")")
    return json.loads("[" + text[first + 1:last] + "]")


def search_error(response):
    """Zwraca opis błędu z odpowiedzi SingleImageSearch albo ``None``."""
    try:
        code = response[0][0][0]
    except (IndexError, TypeError):
        return "nieznany format odpowiedzi"
    if code == 0:
        return None
    message = ""
    try:
        if isinstance(response[0][0][1], str):
            message = response[0][0][2]
    except (IndexError, TypeError):
        pass
    if code == 5:
        return message or "brak zdjęć w tym miejscu"
    return "kod %s: %s" % (code, message)


class StreetViewApi:
    """Bezkluczowy klient nieoficjalnych endpointów Street View oraz tryb z kluczem."""

    def __init__(self, client, locale="pl-PL", api_key=None):
        self.client = client
        self.locale = locale
        self.api_key = api_key

    # -- wyszukiwanie ------------------------------------------------------
    def find_panorama(self, lat, lon, radius=50, download_depth=False, search_third_party=False):
        """Wyszukuje panoramę w promieniu. Zwraca ``(response, error)``."""
        url = search_url(lat, lon, radius=radius, download_depth=download_depth,
                         locale=self.locale, search_third_party=search_third_party)
        try:
            text = self.client.get_text(url, referer=MAPS_REFERER, use_cache=False)
        except Exception as error:  # noqa: BLE001 - błąd przekazujemy do CLI
            return None, "blad sieci: %s" % (error,)
        try:
            response = _repair_jsonp(text)
        except ValueError:
            return None, "nieczytelna odpowiedz: %s" % text[:120]
        return response, search_error(response)

    def panorama_by_id(self, panoid, download_depth=True):
        """Pełne metadane panoramy (z głębią) - surowa odpowiedź ``photometa``."""
        url = photometa_url(panoid, download_depth=download_depth, locale=self.locale)
        text = self.client.get_text(url, prefix_strip=4, referer=REFERER,
                                    headers={"Accept": "*/*", "Host": "www.google.com",
                                             "Alt-Used": "www.google.com"})
        return json.loads(text)

    def coverage_tile(self, tile_x, tile_y, zoom=17):
        """Lista panoram w kaflu pokrycia (surowa odpowiedź)."""
        url = coverage_tile_url(tile_x, tile_y, zoom)
        text = self.client.get_text(url, prefix_strip=4, referer=REFERER)
        return json.loads(text)

    def panos_near(self, lat, lon, radius_m=500, zoom=17, rings=1, max_results=60,
                   use_search=True):
        """Panoramy wokół punktu, posortowane po odległości.

        Najpierw kafle pokrycia (pewne i tanie), a gdy nic nie ma - zapytanie
        SingleImageSearch z promieniem ``radius_m``.
        """
        from .parse import coverage_tile_panos, search_response_pano

        tile_x, tile_y = lonlat_to_tile(lon, lat, zoom)
        found = {}
        for tx, ty in tile_ring(tile_x, tile_y, rings=rings, zoom=zoom):
            try:
                raw = self.coverage_tile(tx, ty, zoom)
            except Exception:  # noqa: BLE001 - pojedynczy kafel może nie odpowiedzieć
                continue
            for pano in coverage_tile_panos(raw):
                pano["tile"] = [tx, ty, zoom]
                found.setdefault(pano["panoid"], pano)

        if not found and use_search:
            response, error = self.find_panorama(lat, lon, radius=radius_m)
            if not error and response:
                pano = search_response_pano(response)
                if pano:
                    found[pano["panoid"]] = pano

        panos = list(found.values())
        for pano in panos:
            pano["distance_m"] = distance_m(lat, lon, pano["lat"], pano["lon"])
        panos.sort(key=lambda item: item["distance_m"])
        return panos[:max_results]

    # -- pliki -------------------------------------------------------------
    def download_tile(self, panoid, x, y, zoom, use_cache=True):
        """Pobiera kafel obrazu. Zwraca ``(bajty, czy_pusty)``."""
        payload = self.client.get_bytes(
            tile_url(panoid, x, y, zoom), referer=REFERER, use_cache=use_cache,
            allow_all_status=True,
            headers={"Host": TILE_HOST, "Origin": "https://www.google.com"})
        return payload, len(payload) <= BLANK_TILE_SIZE + 8

    def download_thumbnail(self, panoid, width=1024, height=512, yaw=0, pitch=0, fov=100,
                           use_cache=True):
        """Podglądowe zdjęcie panoramy (JPEG)."""
        return self.client.get_bytes(
            thumbnail_url(panoid, width, height, yaw, pitch, fov), referer=REFERER,
            use_cache=use_cache, allow_all_status=True,
            headers={"Host": TILE_HOST, "Origin": "https://www.google.com"})

    # -- tryb z kluczem (oficjalne API) ------------------------------------
    def static_metadata(self, panoid=None, location=None):
        if not self.api_key:
            raise RuntimeError("Tryb oficjalny wymaga klucza API (--api-key).")
        return json.loads(self.client.get_text(
            static_metadata_url(self.api_key, panoid=panoid, location=location), use_cache=False))

    def static_image(self, panoid=None, location=None, size="640x640", heading=None,
                     pitch=None, fov=None):
        if not self.api_key:
            raise RuntimeError("Tryb oficjalny wymaga klucza API (--api-key).")
        return self.client.get_bytes(
            static_image_url(self.api_key, panoid=panoid, location=location, size=size,
                             heading=heading, pitch=pitch, fov=fov),
            use_cache=True, allow_all_status=True)

