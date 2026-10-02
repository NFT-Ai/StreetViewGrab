"""
Parser metadanych Street View.

Mapowanie pól z odpowiedzi ``photometa/v1`` (zweryfikowane na żywych danych;
``msg`` to ``response[1][0]`` dla zapytania po ``panoid`` oraz ``response[0][1]``
dla zapytania po promieniu):

======  ===============================================
 Ścieżka  Znaczenie
======  ===============================================
 1[1]    panoid
 2[3][0] lista rozmiarów obrazu (kolejne poziomy zoom)
 2[3][1] rozmiar kafla (zwykle 512x512)
 3[2]    adres: lista par [tekst, język]
 4[0][0][0][0]  copyright
 4[1][0][0][0]  autor zdjęcia
 5[0][1][0][2]  szerokość geograficzna
 5[0][1][0][3]  długość geograficzna
 5[0][1][1][0]  wysokość n.p.m.
 5[0][1][2]     [heading, 90-pitch, roll] w stopniach
 5[0][1][4]     kod kraju
 5[0][3][0]     sąsiednie / historyczne panoramy
 5[0][5][1][2]  mapa głębi (base64)
 5[0][6]        połączenia do sąsiadów
 5[0][8]        daty historycznych zdjęć w tym punkcie
 5[0][12]       etykiety ulic
 6[7]    data wykonania zdjęcia [rok, miesiąc]
 6[8]    data publikacji
 7[0]    adres URL formularza zgłoszeń do zdjęcia
======  ===============================================
"""

from .api import is_third_party_panoid
from .depth import parse_depth
from .geo import bearing_deg, distance_m


def try_get(function, default=None):
    """Wywołuje ``function`` i zwraca ``default`` przy dowolnym błędzie."""
    try:
        value = function()
    except (IndexError, KeyError, TypeError, ValueError):
        return default
    return default if value is None else value


def localized_strings(entries):
    """Lista par ``[tekst, język]`` -> lista słowników."""
    result = []
    for entry in entries or []:
        if isinstance(entry, list) and len(entry) >= 2 and isinstance(entry[0], str):
            result.append({"text": entry[0], "lang": entry[1]})
    return result


def _degrees(value):
    return None if value is None else round(float(value), 6)


def extract_depth_b64(msg):
    """Ciąg base64 z mapą głębi (``msg[5][0][5][1][2]``) albo ``None``."""
    value = try_get(lambda: msg[5][0][5][1][2])
    return value if isinstance(value, str) and len(value) > 100 else None


def parse_image_sizes(msg):
    """Rozmiary obrazu panoramy dla kolejnych poziomów zoom."""
    raw = try_get(lambda: msg[2][3][0], []) or []
    sizes = []
    for entry in raw:
        pair = entry[0] if entry and isinstance(entry[0], list) else entry
        if not pair or len(pair) < 2:
            continue
        # W odpowiedzi kolejność to (wysokość, szerokość) - normalizujemy do x,y.
        sizes.append({"x": int(pair[1]), "y": int(pair[0])})
    return sizes


def _parse_connected(other):
    """Pojedynczy wpis sąsiada / zdjęcia historycznego."""
    panoid = try_get(lambda: other[0][1])
    if not panoid:
        return None
    return {
        "panoid": panoid,
        "lat": _degrees(try_get(lambda: float(other[2][0][2]))),
        "lon": _degrees(try_get(lambda: float(other[2][0][3]))),
        "elevation_m": try_get(lambda: other[2][1][0]),
        "heading_deg": _degrees(try_get(lambda: float(other[2][2][0]))),
        "pitch_deg": _degrees(try_get(lambda: 90.0 - float(other[2][2][1]))),
        "roll_deg": _degrees(try_get(lambda: float(other[2][2][2]))),
    }


def _parse_street_labels(raw):
    """Etykiety ulic wraz z zakresami kątowymi."""
    labels = []
    for entry in raw or []:
        name = try_get(lambda: entry[0][0][2])
        angles = try_get(lambda: entry[1], []) or []
        if isinstance(name, list) and len(name) >= 2:
            labels.append({"name": name[0], "lang": name[1],
                           "angles_deg": [round(float(a), 2) for a in angles
                                          if isinstance(a, (int, float))]})
    return labels


def _collect_place_names(raw):
    """Nazwy miejsc/obiektów widocznych na panoramie (wyciągane zachowawczo)."""
    names = []

    def visit(node, depth=0):
        if depth > 8 or len(names) > 200:
            return
        if isinstance(node, list):
            if (len(node) == 2 and isinstance(node[0], str) and isinstance(node[1], str)
                    and len(node[1]) in (2, 5)):
                names.append(node[0])
                return
            for child in node:
                visit(child, depth + 1)

    visit(raw)
    seen = set()
    unique = []
    for name in names:
        if name not in seen and len(name) > 1:
            seen.add(name)
            unique.append(name)
    return unique



def parse_panorama_message(msg, include_depth=True):
    """Zamienia wiadomość panoramy na słownik metadanych (gotowy do JSON)."""
    panoid = try_get(lambda: msg[1][1])
    latitude = try_get(lambda: float(msg[5][0][1][0][2]))
    longitude = try_get(lambda: float(msg[5][0][1][0][3]))

    others_raw = try_get(lambda: msg[5][0][3][0], []) or []
    links_map = {entry[0]: try_get(lambda: entry[1])
                 for entry in (try_get(lambda: msg[5][0][6], []) or []) if entry}
    date_map = {entry[0]: try_get(lambda: entry[1])
                for entry in (try_get(lambda: msg[5][0][8], []) or []) if entry}

    links, neighbors, historical = [], [], []
    for index, other in enumerate(others_raw):
        connected = _parse_connected(other)
        if not connected or connected["panoid"] == panoid:
            continue
        if connected["lat"] is not None and latitude is not None:
            connected["distance_m"] = round(
                distance_m(latitude, longitude, connected["lat"], connected["lon"]), 2)
            connected["bearing_deg"] = round(
                bearing_deg(latitude, longitude, connected["lat"], connected["lon"]), 2)
        capture = date_map.get(index)
        if isinstance(capture, list) and capture:
            connected["capture_date"] = {"year": capture[0],
                                         "month": capture[1] if len(capture) > 1 else None}
            historical.append(connected)
            continue
        neighbors.append(connected)
        link_entry = links_map.get(index)
        if link_entry:
            angle = try_get(lambda: link_entry[3])
            links.append({"panoid": connected["panoid"],
                          "angle_deg": angle if angle is not None
                          else connected.get("bearing_deg")})

    historical.sort(key=lambda item: (item.get("capture_date") or {}).get("year") or 0,
                    reverse=True)

    capture_date = try_get(lambda: msg[6][7])
    upload_date = try_get(lambda: msg[6][8])
    tile_size = try_get(lambda: msg[2][3][1])

    result = {
        "panoid": panoid,
        "is_third_party": bool(panoid) and is_third_party_panoid(panoid),
        "lat": latitude,
        "lon": longitude,
        "elevation_m": try_get(lambda: msg[5][0][1][1][0]),
        "heading_deg": _degrees(try_get(lambda: float(msg[5][0][1][2][0]))),
        "pitch_deg": _degrees(try_get(lambda: 90.0 - float(msg[5][0][1][2][1]))),
        "roll_deg": _degrees(try_get(lambda: float(msg[5][0][1][2][2]))),
        "country_code": try_get(lambda: msg[5][0][1][4]),
        "address": localized_strings(try_get(lambda: msg[3][2])),
        "copyright": try_get(lambda: msg[4][0][0][0][0]),
        "uploader": try_get(lambda: msg[4][1][0][0][0]),
        "uploader_icon_url": try_get(lambda: msg[4][1][0][2]),
        "source": try_get(lambda: str(msg[6][5][2]).lower()),
        "capture_date": ({"year": capture_date[0],
                          "month": capture_date[1] if len(capture_date) > 1 else None,
                          "day": capture_date[2] if len(capture_date) > 2 else None}
                         if isinstance(capture_date, list) and capture_date else None),
        "upload_date": ({"year": upload_date[0],
                         "month": upload_date[1] if len(upload_date) > 1 else None}
                        if isinstance(upload_date, list) and upload_date else None),
        "image_sizes": parse_image_sizes(msg),
        "tile_size": ({"x": tile_size[0], "y": tile_size[1]}
                      if isinstance(tile_size, list) and len(tile_size) >= 2 else None),
        "street_labels": _parse_street_labels(try_get(lambda: msg[5][0][12])),
        "neighbors": neighbors,
        "links": links,
        "historical_captures": historical,
        "imagery_report_url": try_get(lambda: msg[7][0]),
        "place_names": _collect_place_names(try_get(lambda: msg[5][0][9])),
    }
    if include_depth:
        depth_b64 = extract_depth_b64(msg)
        if depth_b64:
            result["_depth"] = parse_depth(depth_b64)
    return result



def panorama_by_id_response(response, include_depth=True):
    """Wyciąga panoramę z odpowiedzi ``photometa`` (``None`` przy braku danych)."""
    code = try_get(lambda: response[1][0][0][0])
    if code not in (1, 3):
        return None
    return parse_panorama_message(response[1][0], include_depth=include_depth)


def search_response_pano(response, include_depth=False):
    """Wyciąga panoramę z odpowiedzi ``SingleImageSearch`` po promieniu."""
    code = try_get(lambda: response[0][0][0])
    if code != 0:
        return None
    return parse_panorama_message(response[0][1], include_depth=include_depth)


def coverage_tile_panos(tile):
    """Lista panoram z odpowiedzi kafla pokrycia (``photometa/ac/v1``)."""
    if not tile:
        return []
    entries = try_get(lambda: tile[1][1], []) or []
    panos = []
    for entry in entries:
        if try_get(lambda: entry[0][0][0]) == 1:
            continue
        panoid = try_get(lambda: entry[0][0][1])
        if not panoid:
            continue
        panos.append({
            "panoid": panoid,
            "lat": try_get(lambda: float(entry[0][2][0][2])),
            "lon": try_get(lambda: float(entry[0][2][0][3])),
            "elevation_m": try_get(lambda: entry[0][2][1][0]),
            "heading_deg": _degrees(try_get(lambda: float(entry[0][2][2][0]))),
            "pitch_deg": _degrees(try_get(lambda: 90.0 - float(entry[0][2][2][1]))),
            "roll_deg": _degrees(try_get(lambda: float(entry[0][2][2][2]))),
            "photos_in_place": try_get(lambda: entry[1][1]),
        })
    return panos
