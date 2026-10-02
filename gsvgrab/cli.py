"""
Interfejs wiersza poleceń ``gsvgrab``.

Komendy::

    python3 -m gsvgrab find  --address "Piasta 10c, Milanówek"
    python3 -m gsvgrab info  --pano <PANOID>
    python3 -m gsvgrab grab  --address "Piasta 10c, Milanówek" --max-panos 3 --pointcloud
    python3 -m gsvgrab selftest

Struktura katalogu wynikowego::

    <out>/
      manifest.json                 - zbiorczy opis pobranych panoram
      index.html                    - przeglądarka (zdjęcie + wizualizacja głębi)
      panoramy/<panoid>/
        metadata.json               - wszystkie metadane panoramy
        metadata_raw.json           - surowa odpowiedź photometa (pełna)
        preview.jpg                 - podgląd panoramy (1024x512)
        pano_equirect_z2.png        - sklejone zdjęcie panoramy (equirectangularne)
        tiles/z2/x_y.jpg            - pojedyncze kafle źródłowe
        depth/depth_color.png       - kolorowa wizualizacja głębi
        depth/depth_16bit.png       - głębia w skali 1 mm (16 bit)
        depth/depth_f32.bin         - surowa głębia float32 LE (metry, -1 = niebo)
        depth/depth.json            - statystyki, płaszczyzny, nagłówek
        pointcloud.ply              - chmura punktów 3D z głębi (opcjonalnie)
"""

import argparse
import json
import os
import re
import sys
import time

from . import __version__
from .api import StreetViewApi
from .crawl import Crawler
from .depth import depth_to_float32, depth_to_gray16_mm, depth_to_rgb8, load_depth_f32
from .geocode import GeocodeError, geocode
from .geo import distance_m
from .http import HttpClient
from .parse import panorama_by_id_response
from .pngio import write_png
from .pointcloud import build_pointcloud, merge_panoramas
from .stitch import jpeg_backend, stitch_equirect


def slugify(text, fallback="panorama"):
    """Zamienia tekst na bezpieczną nazwę katalogu."""
    text = (text or "").lower()
    for source, target in (("ą", "a"), ("ć", "c"), ("ę", "e"), ("ł", "l"), ("ń", "n"),
                           ("ó", "o"), ("ś", "s"), ("ż", "z"), ("ź", "z")):
        text = text.replace(source, target)
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text[:60] or fallback


def human_bytes(size):
    for unit in ("B", "kB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return "%.1f %s" % (size, unit)
        size /= 1024.0
    return "%d B" % size


def write_json(path, payload):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, default=str)
    return os.path.getsize(path)


def add_target_arguments(parser):
    parser.add_argument("--address", "-a", help="adres, np. \"Piasta 10c, Milanówek\"")
    parser.add_argument("--lat", type=float, help="szerokość geograficzna")
    parser.add_argument("--lon", type=float, help="długość geograficzna")
    parser.add_argument("--pano", help="identyfikator panoramy (panoid)")


def add_common_arguments(parser):
    parser.add_argument("--out", "-o", default="streetview_output",
                        help="katalog wynikowy (domyślnie: streetview_output)")
    parser.add_argument("--api-key", default=os.environ.get("GOOGLE_MAPS_API_KEY"),
                        help="klucz Google Maps Platform (opcjonalny); "
                             "domyślnie ze zmiennej GOOGLE_MAPS_API_KEY")
    parser.add_argument("--locale", default="pl-PL", help="język odpowiedzi API (domyślnie pl-PL)")
    parser.add_argument("--cache-dir", default=os.path.join("streetview_output", ".cache"),
                        help="katalog cache HTTP")
    parser.add_argument("--no-cache", action="store_true", help="wyłącz cache HTTP")
    parser.add_argument("--verbose", "-v", action="store_true", help="szczegółowe komunikaty")



def build_parser():
    """Buduje parser argumentów."""
    parser = argparse.ArgumentParser(
        prog="gsvgrab",
        description="Pobieranie zdjęć Google Street View wraz z metadanymi i mapami głębi.",
        epilog="Przykład: python3 -m gsvgrab grab --address \"Piasta 10c, Milanówek\" "
               "--max-panos 3 --pointcloud")
    parser.add_argument("--version", action="version", version="gsvgrab %s" % __version__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    find = subparsers.add_parser("find", help="znajdź panoramy wokół adresu/współrzędnych")
    add_target_arguments(find)
    add_common_arguments(find)
    find.add_argument("--radius", type=float, default=800.0,
                      help="promień wyszukiwania w metrach (domyślnie 800)")
    find.add_argument("--rings", type=int, default=1,
                      help="ile sąsiednich kafli pokrycia (domyślnie 1)")
    find.add_argument("--limit", type=int, default=20, help="ile pozycji wypisać")
    find.add_argument("--with-metadata", action="store_true",
                      help="dopisz adres i date zdjecia (1 zapytanie na pozycje)")
    find.add_argument("--walk-count", type=int, default=5,
                      help="ile panoram pokazac w trybie --walk (domyslnie 5)")
    find.add_argument("--walk", action="store_true",
                      help="pokaż też panoramy połączone (przejazd wzdłuż ulicy)")

    info = subparsers.add_parser("info", help="pokaż metadane i statystyki głębi")
    add_target_arguments(info)
    add_common_arguments(info)
    info.add_argument("--radius", type=float, default=800.0, help="promień szukania panoramy")
    info.add_argument("--no-depth", action="store_true", help="nie pobieraj mapy głębi")

    grab = subparsers.add_parser("grab", help="pobierz zdjęcia, metadane i głębię")
    add_target_arguments(grab)
    add_common_arguments(grab)
    grab.add_argument("--radius", type=float, default=800.0, help="promień szukania panoram")
    grab.add_argument("--rings", type=int, default=1, help="ile sąsiednich kafli pokrycia")
    grab.add_argument("--max-panos", type=int, default=1, help="ile panoram pobrać")
    grab.add_argument("--walk", action="store_true",
                      help="zbieraj panoramy połączone (przejazd wzdłuż ulicy)")
    grab.add_argument("--equirect-zoom", type=int, default=2,
                      help="poziom zoom zdjęcia panoramy 0-5 (domyślnie 2 = 2048x1024)")
    grab.add_argument("--no-equirect", action="store_true", help="nie sklejaj zdjęcia panoramy")
    grab.add_argument("--no-depth", action="store_true", help="nie zapisuj danych głębi")
    grab.add_argument("--no-preview", action="store_true", help="nie pobieraj podglądu JPEG")
    grab.add_argument("--pointcloud", action="store_true", help="zapisz chmurę punktów PLY")
    grab.add_argument("--pointcloud-stride", type=int, default=2,
                      help="co ile pikseli próbkować chmurę punktów (domyślnie 2)")
    grab.add_argument("--pointcloud-max-depth", type=float, default=None,
                      help="maksymalna głębia w chmurze punktów (metry)")
    grab.add_argument("--force", action="store_true", help="pozwól na bardzo duże obrazy")

    walk = subparsers.add_parser("walk", help="przejdz ulice (krok w przód i z powrotem)")
    add_target_arguments(walk)
    add_common_arguments(walk)
    walk.add_argument("--forward", type=int, default=10, help="krokow do przodu (domyslnie 10)")
    walk.add_argument("--back", type=int, default=10, help="krokow z powrotem (domyslnie 10)")
    walk.add_argument("--heading", type=float, default=None,
                      help="kierunek marszu w stopniach (domyslnie naglowek startu)")
    walk.add_argument("--stride", type=int, default=2, help="co ile pikseli w chmurze punktow")
    walk.add_argument("--max-depth", type=float, default=None,
                      help="maksymalna glebia w chmurze (metry; domyslnie bez limitu)")
    walk.add_argument("--no-merge", action="store_true", help="nie sklejaj chmur punktow")
    walk.add_argument("--route-only", action="store_true",
                      help="tylko przejscie trasy, bez pobierania glebi")
    walk.add_argument("--step-m", type=float, default=12.0,
                      help="preferowana dlugosc kroku w metrach")

    merge = subparsers.add_parser("merge", help="sklej chmury punktow z pobranego katalogu")
    add_common_arguments(merge)
    merge.add_argument("--in-dir", default=None,
                       help="katalog pobrany komenda grab/walk (domyslnie: --out)")
    merge.add_argument("--stride", type=int, default=2, help="co ile pikseli probkowac")
    merge.add_argument("--max-depth", type=float, default=None, help="maksymalna glebia w metrach")
    merge.add_argument("--out-file", default="chmura_sklejona.ply", help="nazwa pliku wynikowego")

    mesh = subparsers.add_parser("mesh", help="wygeneruj siatke 3D (OBJ/PLY) z trasy lub panoram")
    add_common_arguments(mesh)
    mesh.add_argument("--in-dir", default=None,
                      help="katalog pobrany komenda walk/grab (domyslnie: --out)")
    mesh.add_argument("--stride", type=int, default=2, help="probkowanie siatki (co ile pikseli)")
    mesh.add_argument("--max-depth", type=float, default=50.0, help="maksymalna glebia w metrach")
    mesh.add_argument("--max-edge-ratio", type=float, default=0.18, help="prog rozrywania krawedzi")
    mesh.add_argument("--out-obj", default=None, help="sciezka do wynikowego pliku .obj")
    mesh.add_argument("--out-ply", default=None, help="sciezka do wynikowego pliku .ply")

    subparsers.add_parser("selftest", help="testy offline (bez sieci)")
    return parser



def make_client(args):
    """Tworzy klienta HTTP z cache (o ile nie wyłączono)."""
    cache_dir = None if getattr(args, "no_cache", False) else getattr(args, "cache_dir", None)
    return HttpClient(cache_dir=cache_dir, verbose=getattr(args, "verbose", False))


def resolve_target(args, client, api):
    """Ustala punkt startowy: panorama, współrzędne albo adres."""
    if getattr(args, "pano", None):
        return {"panoid": args.pano, "lat": getattr(args, "lat", None),
                "lon": getattr(args, "lon", None), "label": args.pano}
    if getattr(args, "lat", None) is not None and getattr(args, "lon", None) is not None:
        return {"panoid": None, "lat": args.lat, "lon": args.lon,
                "label": "%.6f, %.6f" % (args.lat, args.lon)}
    if getattr(args, "address", None):
        print("Geokodowanie: %s" % args.address)
        try:
            result = geocode(args.address, client, api_key=getattr(args, "api_key", None))
        except GeocodeError as error:
            raise SystemExit("Blad geokodowania: %s" % error)
        print("  -> %s" % result["display_name"])
        print("  -> %.6f, %.6f (%s)" % (result["lat"], result["lon"], result["source"]))
        return {"panoid": None, "lat": result["lat"], "lon": result["lon"],
                "label": result["display_name"], "geocode": result}
    raise SystemExit("Podaj lokalizacje: --address, --lat/--lon albo --pano.")


def _nearest_pano_for(api, lat, lon, radius, max_results=50):
    """Najbliższa panorama wokół punktu (kafle pokrycia, potem wyszukiwanie)."""
    panos = api.panos_near(lat, lon, radius_m=radius, zoom=17, rings=1,
                           max_results=max_results)
    if not panos and radius > 50:
        from .parse import search_response_pano

        response, error = api.find_panorama(lat, lon, radius=radius)
        if not error and response:
            pano = search_response_pano(response)
            if pano:
                panos = [pano]
    if not panos:
        raise SystemExit("Brak zdjec Street View w promieniu %.0f m od punktu %.6f, %.6f."
                         % (radius, lat, lon))
    return panos


def collect_panos(args, api, target):
    """Zwraca listę panoram do przetworzenia (najbliższe + opcjonalny przejazd)."""
    max_panos = max(1, getattr(args, "max_panos", 1))
    if target.get("panoid"):
        start = [{"panoid": target["panoid"], "lat": target.get("lat"),
                  "lon": target.get("lon"), "distance_m": 0.0}]
        if not getattr(args, "walk", False) or max_panos == 1:
            return start
    else:
        found = _nearest_pano_for(api, target["lat"], target["lon"],
                                  getattr(args, "radius", 800.0), max_results=200)
        if not getattr(args, "walk", False):
            return found[:max_panos]
        start = found[:1]

    # Tryb przejazdu: rozszerzamy zbiór o panoramy połączone z już znalezionymi.
    origin_lat = target.get("lat")
    origin_lon = target.get("lon")
    radius = getattr(args, "radius", 800.0)
    selected = {pano["panoid"]: pano for pano in start}
    queue = list(start)
    visits = 0
    max_visits = max_panos * 4 + 8
    while queue and len(selected) < max_panos and visits < max_visits:
        current = queue.pop(0)
        visits += 1
        try:
            raw = api.panorama_by_id(current["panoid"], download_depth=False)
        except Exception as error:  # noqa: BLE001 - brak metadanych nie przerywa przejazdu
            if getattr(args, "verbose", False):
                print("  (pomijam %s: %s)" % (current["panoid"], error))
            continue
        pano = panorama_by_id_response(raw, include_depth=False)
        if not pano:
            continue
        candidates = list(pano.get("neighbors", [])) + list(pano.get("historical_captures", []))
        for neighbour in candidates:
            panoid = neighbour.get("panoid")
            lat = neighbour.get("lat")
            lon = neighbour.get("lon")
            if not panoid or panoid in selected or lat is None or lon is None:
                continue
            if origin_lat is None:
                continue
            distance = distance_m(origin_lat, origin_lon, lat, lon)
            if distance > radius * 2:
                continue
            entry = {"panoid": panoid, "lat": lat, "lon": lon,
                     "heading_deg": neighbour.get("heading_deg"), "distance_m": distance}
            selected[panoid] = entry
            queue.append(entry)
            if len(selected) >= max_panos:
                break
    panos = sorted(selected.values(), key=lambda item: item.get("distance_m") or 0.0)
    return panos[:max_panos]


def save_depth_artifacts(depth, depth_dir, stats=None):
    """Zapisuje mapę głębi w kilku formatach + opis JSON."""
    os.makedirs(depth_dir, exist_ok=True)
    stats = stats or depth.stats()
    files = {}

    color_path = os.path.join(depth_dir, "depth_color.png")
    write_png(color_path, depth.width, depth.height, depth_to_rgb8(depth),
              channels=3, bitdepth=8)
    files["depth_color_png"] = color_path

    gray16_path = os.path.join(depth_dir, "depth_16bit.png")
    write_png(gray16_path, depth.width, depth.height, depth_to_gray16_mm(depth),
              channels=1, bitdepth=16)
    files["depth_16bit_png"] = gray16_path

    raw_path = os.path.join(depth_dir, "depth_f32.bin")
    with open(raw_path, "wb") as handle:
        handle.write(depth_to_float32(depth))
    files["depth_float32_bin"] = raw_path

    description = dict(stats)
    description.update({
        "units": "metry",
        "no_data_value": -1.0,
        "raw_format": "float32 little-endian, wiersz po wierszu (szerokosc x wysokosc)",
        "png16_scale": "1 jednostka = 1 mm, 0 = brak danych",
        "depth_map_orientation": ("kolumna x odpowiada pikselowi zdjecia o wspolrzednej x; "
                                  "kierunki promieni w dekoderze sa lustrzane"),
        "planes": depth.planes_as_list(),
        "source": "https://www.google.com/maps/photometa/v1 (pole msg[5][0][5][1][2])",
    })
    json_path = os.path.join(depth_dir, "depth.json")
    write_json(json_path, description)
    files["depth_json"] = json_path
    return files, description


def save_pano(args, api, stub, root, index, total):
    """Pobiera wszystkie dane jednej panoramy. Zwraca słownik podsumowania."""
    panoid = stub["panoid"]
    pano_dir = os.path.join(root, "panoramy", panoid)
    os.makedirs(pano_dir, exist_ok=True)
    print("\n[%d/%d] %s" % (index, total, panoid))
    started = time.time()

    want_depth = not getattr(args, "no_depth", False)
    raw = api.panorama_by_id(panoid, download_depth=want_depth)
    write_json(os.path.join(pano_dir, "metadata_raw.json"), raw)

    pano = panorama_by_id_response(raw, include_depth=want_depth)
    if not pano:
        raise SystemExit("Nie udalo sie odczytac metadanych panoramy %s." % panoid)
    depth = pano.pop("_depth", None)

    address = pano.get("address") or []
    address_text = address[0]["text"] if address else "?"
    capture = pano.get("capture_date") or {}
    print("  adres: %s | zdjecie: %s-%s | copyright: %s"
          % (address_text, capture.get("year", "?"), capture.get("month", "?"),
             pano.get("copyright", "?")))
    if stub.get("distance_m") is not None:
        pano["distance_from_query_m"] = round(stub["distance_m"], 2)
    pano["downloaded_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    pano["api_sources"] = {
        "metadata_and_depth": "https://www.google.com/maps/photometa/v1",
        "tiles": "https://streetviewpixels-pa.googleapis.com/v1/tile",
        "thumbnail": "https://streetviewpixels-pa.googleapis.com/v1/thumbnail",
    }
    files = {}

    if depth is not None:
        depth_files, stats = save_depth_artifacts(depth, os.path.join(pano_dir, "depth"))
        files.update(depth_files)
        print("  glebia: %dx%d, pokrycie %.1f%%, mediana %.2f m, max %.2f m (kodowanie: %s)"
              % (depth.width, depth.height, stats["coverage_percent"],
                 stats["percentiles_m"]["p50"], stats["max_m"], depth.encoding))
        pano["depth_summary"] = {key: value for key, value in stats.items()
                                 if key not in ("histogram", "planes")}
    elif want_depth:
        print("  glebia: BRAK dla tej panoramy (Google nie udostepnia mapy glebi)")

    if not getattr(args, "no_preview", False):
        try:
            preview = api.download_thumbnail(panoid)
            preview_path = os.path.join(pano_dir, "preview.jpg")
            with open(preview_path, "wb") as handle:
                handle.write(preview)
            files["preview_jpg"] = preview_path
            print("  podglad: %s" % human_bytes(len(preview)))
        except Exception as error:  # noqa: BLE001 - podglad jest opcjonalny
            print("  podglad: pominieto (%s)" % error)

    equirect_path = None
    if not getattr(args, "no_equirect", False):
        zoom = max(0, min(5, getattr(args, "equirect_zoom", 2)))
        equirect_path = os.path.join(pano_dir, "pano_equirect_z%d.png" % zoom)
        tiles_dir = os.path.join(pano_dir, "tiles", "z%d" % zoom)
        print("  kafle z zoom=%d (dekoder JPEG: %s)..." % (zoom, jpeg_backend() or "brak"))

        def progress(x, y, cols, rows):
            if getattr(args, "verbose", False):
                print("    kafel %d,%d z %dx%d" % (x, y, cols, rows))

        stats, canvas = stitch_equirect(
            api, panoid, zoom, tiles_dir, out_png=equirect_path,
            force=getattr(args, "force", False), on_progress=progress)
        pano["equirect"] = stats
        if canvas is None:
            print("  panorama: %s" % stats.get("skipped", "nie udalo sie skleic"))
            equirect_path = None
        else:
            print("  panorama: %dx%d px, kafli: %d (pustych %d), srednia jasnosc RGB: %s"
                  % (stats["width"], stats["height"], stats["tiles_used"],
                     stats["tiles_blank"], stats["mean_rgb"]))
            files["equirect_png"] = equirect_path

    if getattr(args, "pointcloud", False) and depth is not None:
        ply_path = os.path.join(pano_dir, "pointcloud.ply")
        comments = ["gsvgrab - chmura punktow z mapy glebi Google Street View",
                    "panoid %s" % panoid,
                    "lat %.7f lon %.7f" % (pano.get("lat") or 0.0, pano.get("lon") or 0.0),
                    "zdjecie %s-%s" % (capture.get("year", "?"), capture.get("month", "?")),
                    "copyright %s" % pano.get("copyright", "?"),
                    "uklad lokalny kamery, Z do gory, jednostki metry"]
        cloud = build_pointcloud(
            depth, ply_path, stride=max(1, getattr(args, "pointcloud_stride", 2)),
            max_depth=getattr(args, "pointcloud_max_depth", None),
            color_image=equirect_path, comments=comments)
        files["pointcloud_ply"] = ply_path
        pano["pointcloud"] = cloud
        print("  chmura punktow: %d punktow%s"
              % (cloud["points"], " (z kolorem)" if cloud["colored"] else ""))

    pano["files"] = {key: os.path.relpath(value, root) for key, value in files.items()}
    write_json(os.path.join(pano_dir, "metadata.json"), pano)
    return {
        "panoid": panoid,
        "address": address_text,
        "address_all": address,
        "lat": pano.get("lat"),
        "lon": pano.get("lon"),
        "distance_from_query_m": pano.get("distance_from_query_m"),
        "capture_date": pano.get("capture_date"),
        "copyright": pano.get("copyright"),
        "directory": os.path.relpath(pano_dir, root),
        "has_depth": depth is not None,
        "files": pano["files"],
        "seconds": round(time.time() - started, 1),
    }


VIEWER_HEAD = """<!DOCTYPE html>
<html lang="pl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>gsvgrab - pobrane panoramy Street View</title>
<style>
  body { font-family: -apple-system, "Segoe UI", Roboto, sans-serif; margin: 0;
         background: #14161a; color: #e8eaed; }
  header { padding: 18px 22px; background: #1e2126; border-bottom: 1px solid #2e3238; }
  h1 { margin: 0 0 4px; font-size: 19px; }
  header p { margin: 0; color: #9aa0a6; font-size: 13px; }
  .card { margin: 22px; background: #1b1e23; border: 1px solid #2b2f36; border-radius: 10px;
          overflow: hidden; }
  .card h2 { margin: 0; padding: 14px 18px; font-size: 16px; background: #22262c;
             border-bottom: 1px solid #2b2f36; }
  .row { display: flex; flex-wrap: wrap; gap: 14px; padding: 14px 18px; }
  .viewer { position: relative; overflow: hidden; background: #000; border-radius: 8px;
            cursor: grab; user-select: none; }
  .viewer img { display: block; transform-origin: center; pointer-events: none; }
  .pane { flex: 1 1 420px; min-width: 300px; }
  .pane figcaption { font-size: 12px; color: #9aa0a6; margin-top: 6px; }
  table { border-collapse: collapse; font-size: 13px; width: 100%; }
  td { padding: 4px 8px; vertical-align: top; border-bottom: 1px solid #262a30; }
  td:first-child { color: #9aa0a6; width: 190px; }
  .hint { padding: 10px 22px 16px; color: #9aa0a6; font-size: 12px; }
</style>
</head>
<body>
<header>
  <h1>gsvgrab &mdash; pobrane panoramy Street View</h1>
  <p>Przeciągnij zdjęcie myszą, aby obracać panoramę; kółkiem myszy zmieniasz zoom.</p>
</header>
"""

VIEWER_TAIL = """
<script>
document.querySelectorAll('.viewer').forEach(function (viewer) {
  var img = viewer.querySelector('img');
  var scale = 1, offsetX = 0, offsetY = 0, dragging = false;
  var startX = 0, startY = 0, baseX = 0, baseY = 0;
  function draw() {
    img.style.transform = 'translate(' + offsetX + 'px,' + offsetY + 'px) scale(' + scale + ')';
  }
  function fit() {
    scale = viewer.clientWidth / img.naturalWidth;
    offsetX = 0; offsetY = 0; draw();
  }
  viewer.addEventListener('mousedown', function (event) {
    dragging = true; startX = event.clientX; startY = event.clientY;
    baseX = offsetX; baseY = offsetY; viewer.style.cursor = 'grabbing';
  });
  window.addEventListener('mouseup', function () {
    dragging = false; viewer.style.cursor = 'grab';
  });
  window.addEventListener('mousemove', function (event) {
    if (!dragging) { return; }
    offsetX = baseX + (event.clientX - startX);
    offsetY = baseY + (event.clientY - startY);
    draw();
  });
  viewer.addEventListener('wheel', function (event) {
    event.preventDefault();
    scale = Math.max(0.2, Math.min(4, scale * (event.deltaY < 0 ? 1.12 : 0.89)));
    draw();
  });
  window.addEventListener('resize', fit);
  img.addEventListener('load', fit);
  if (img.complete) { fit(); }
});
</script>
</body>
</html>
"""


def write_viewer(root, manifest):
    """Zapisuje prostą przeglądarkę HTML z pobranymi panoramami."""
    parts = [VIEWER_HEAD,
             '<p class="hint">Lokalizacja: %s &middot; panoram: %d &middot; wygenerowano: %s</p>\n'
             % (manifest.get("query", {}).get("label", "?"),
                len(manifest.get("panoramas", [])), manifest.get("generated_at", "?"))]
    for item in manifest.get("panoramas", []):
        parts.append('<section class="card">\n<h2>%s &mdash; %s</h2>\n<div class="row">\n'
                     % (item.get("address", "?"), item.get("panoid", "?")))
        files = item.get("files", {})
        if files.get("equirect_png"):
            parts.append('<figure class="pane viewer" style="height:340px">'
                         '<img src="%s" alt="panorama"></figure>\n' % files["equirect_png"])
        elif files.get("preview_jpg"):
            parts.append('<figure class="pane"><img src="%s" style="width:100%%" '
                         'alt="podglad"><figcaption>podgląd JPEG 1024x512</figcaption>'
                         '</figure>\n' % files["preview_jpg"])
        if files.get("depth_color_png"):
            parts.append('<figure class="pane"><img src="%s" style="width:100%%" '
                         'alt="glebia"><figcaption>mapa głębi (niebieski = blisko, '
                         'czerwony = daleko, czarny = niebo/horyzont)</figcaption></figure>\n'
                         % files["depth_color_png"])
        parts.append('</div>\n<div class="row">\n<table>\n')
        capture = item.get("capture_date") or {}
        rows = [
            ("Identyfikator panoramy", item.get("panoid")),
            ("Współrzędne", "%.7f, %.7f" % (item.get("lat") or 0.0, item.get("lon") or 0.0)),
            ("Odległość od zapytania", "%s m" % item.get("distance_from_query_m")),
            ("Data zdjęcia", "%s-%s" % (capture.get("year", "?"), capture.get("month", "?"))),
            ("Copyright", item.get("copyright")),
            ("Mapa głębi", "tak" if item.get("has_depth") else "brak"),
        ]
        for label, value in rows:
            parts.append("<tr><td>%s</td><td>%s</td></tr>\n" % (label, value))
        parts.append("</table>\n</div>\n</section>\n")
    parts.append(VIEWER_TAIL)
    path = os.path.join(root, "index.html")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("".join(parts))
    return path


README_TEXT = """gsvgrab - pobrane zdjęcia Street View z metadanymi i głębią
========================================================

Lokalizacja zapytania : {label}
Współrzędne            : {lat}, {lon}
Liczba panoram         : {count}
Wygenerowano           : {generated_at}

Zawartość katalogu każdej panoramy
----------------------------------
metadata.json         - wszystkie metadane (adres, data, copyright, historia, sąsiedzi)
metadata_raw.json     - pełna surowa odpowiedź endpointu metadanych
preview.jpg           - podgląd panoramy (JPEG)
pano_equirect_z*.png  - sklejone zdjęcie panoramy (equirectangularne, PNG)
tiles/                - pojedyncze kafle źródłowe 512x512 px
depth/depth_color.png - kolorowa wizualizacja głębi
depth/depth_16bit.png - głębia w skali 1 mm (16 bit, 0 = brak danych)
depth/depth_f32.bin   - surowa głębia float32 LE w metrach (-1 = niebo/horyzont)
depth/depth.json      - statystyki, płaszczyzny, nagłówek, opis formatu
pointcloud.ply        - chmura punktów 3D (jeśli włączono --pointcloud)

Uwagi prawne
------------
Zdjęcia i metadane pochodzą z usług Google (Google Street View) i są chronione
prawem autorskim Google oraz podmiotów trzecich - właściciela wskazuje pole
copyright w metadanych. Wykorzystuj je zgodnie z Warunkami korzystania z Google
Maps Platform oraz obowiązującym prawem; nie usuwaj oznaczeń autorskich.
Dane głębi pochodzą z nieoficjalnego punktu końcowego i mogą zniknąć lub zmienić
format w dowolnym momencie.
"""





def _print_pano_line(pano, distance):
    """Wypisuje jedną linię z opisem panoramy (dane z kafla pokrycia są niepełne)."""
    address = pano.get("address") or []
    label = ", ".join(item["text"] for item in address) if address else "-"
    date = pano.get("capture_date") or {}
    captured = ("%s-%s" % (date["year"], date["month"])) if date.get("year") else "-"
    print("  %-8s %-10s %-7s %-24s %-10s %s"
          % ("%.1f m" % distance if distance else "-",
             (pano.get("panoid") or "?")[:10],
             ("%.1f" % pano["heading_deg"]) if pano.get("heading_deg") is not None else "-",
             "%.5f, %.5f" % (pano.get("lat") or 0.0, pano.get("lon") or 0.0),
             captured, label))


def cmd_find(args):
    """Wypisuje panoramy wokół zadanego punktu."""
    client = make_client(args)
    api = StreetViewApi(client, locale=args.locale, api_key=args.api_key)
    target = resolve_target(args, client, api)

    if target.get("panoid") and not args.walk:
        pano = panorama_by_id_response(
            api.panorama_by_id(target["panoid"], download_depth=False), include_depth=False)
        if not pano:
            print("Nie znaleziono panoramy %s." % target["panoid"])
            return 1
        _print_pano_line(pano, 0.0)
        return 0

    panos = _nearest_pano_for(api, target["lat"], target["lon"], args.radius, max_results=200)
    within = [pano for pano in panos if pano["distance_m"] <= args.radius]
    panos = within or panos
    print("\nZnaleziono %d panoram w promieniu %.0f m:" % (len(panos), args.radius))
    print("  %-8s %-10s %-7s %-24s %-10s %s"
          % ("ODLEG.", "ID", "KIER.", "WSPOLRZEDNE", "ZDIEC", "ADRES"))
    for pano in panos[:args.limit]:
        if getattr(args, "with_metadata", False):
            try:
                full = panorama_by_id_response(
                    api.panorama_by_id(pano["panoid"], download_depth=False),
                    include_depth=False)
                if full:
                    pano = dict(pano, address=full.get("address"),
                                capture_date=full.get("capture_date"),
                                elevation_m=full.get("elevation_m"))
            except Exception:  # noqa: BLE001 - brak metadanych nie psuje listy
                pass
        _print_pano_line(pano, pano["distance_m"])
    if len(panos) > args.limit:
        print("  ... i %d wiecej (uzyj --limit, aby zwiekszyc)" % (len(panos) - args.limit))
    if args.walk:
        print("\nTryb --walk: panorama startowa plus polaczone (przejazd wzdloz ulicy):")
        args.max_panos = max(1, args.walk_count)
        for pano in collect_panos(args, api, target):
            print("  %-24s %8s m"
                  % (pano["panoid"], "%.1f" % (pano.get("distance_m") or 0.0)))
    print("\nAby pobrac zdjecia i glebie, uzyj: gsvgrab grab --pano <ID>")
    return 0



def cmd_info(args):
    """Pokazuje metadane i statystyki głębi dla jednej panoramy."""
    client = make_client(args)
    api = StreetViewApi(client, locale=args.locale, api_key=args.api_key)
    target = resolve_target(args, client, api)
    if not target.get("panoid"):
        target["panoid"] = _nearest_pano_for(api, target["lat"], target["lon"],
                                             args.radius)[0]["panoid"]
    print("Panorama: %s" % target["panoid"])
    raw = api.panorama_by_id(target["panoid"], download_depth=not args.no_depth)
    pano = panorama_by_id_response(raw, include_depth=not args.no_depth)
    if not pano:
        print("Nie udalo sie pobrac metadanych.")
        return 1
    depth = pano.pop("_depth", None)
    print("\n--- metadane ---")
    for key in ("lat", "lon", "elevation_m", "heading_deg", "pitch_deg", "roll_deg",
                "country_code", "copyright", "uploader", "source", "capture_date",
                "upload_date", "tile_size", "image_sizes"):
        print("  %-16s %s" % (key, json.dumps(pano.get(key), ensure_ascii=False)))
    print("  %-16s %s" % ("adres", json.dumps(pano.get("address"), ensure_ascii=False)))
    print("  %-16s %s" % ("ulice", json.dumps(pano.get("street_labels"), ensure_ascii=False)))
    print("  %-16s %s" % ("obiekty", json.dumps(pano.get("place_names"), ensure_ascii=False)))
    print("  %-16s %d (polaczenia: %d)"
          % ("sasiedzi", len(pano.get("neighbors", [])), len(pano.get("links", []))))
    print("  %-16s %s" % ("historia zdjec", json.dumps(
        [{"panoid": item["panoid"], "data": item.get("capture_date"),
          "odleglosc_m": item.get("distance_m")}
         for item in pano.get("historical_captures", [])], ensure_ascii=False)))
    if depth is None:
        print("\n--- glebia ---\n  brak danych glebi dla tej panoramy")
        return 0
    stats = depth.stats()
    print("\n--- glebia ---")
    print("  rozmiar          %dx%d (%s, %d plaszczyzn)"
          % (depth.width, depth.height, depth.encoding, len(depth.planes)))
    print("  pokrycie         %s%% (%d px z glebia, %d px niebo/horyzont)"
          % (stats["coverage_percent"], stats["pixels_with_depth"], stats["pixels_far_or_sky"]))
    print("  min/mediana/max  %.2f / %.2f / %.2f m"
          % (stats["min_m"], stats["percentiles_m"]["p50"], stats["max_m"]))
    print("  percentyle       %s"
          % {key: round(value, 2) for key, value in stats["percentiles_m"].items()})


def cmd_grab(args):
    """Pobiera zdjęcia, metadane, głębię i dodatki."""
    client = make_client(args)
    api = StreetViewApi(client, locale=args.locale, api_key=args.api_key)
    target = resolve_target(args, client, api)
    os.makedirs(args.out, exist_ok=True)

    panos = collect_panos(args, api, target)
    print("\nDo pobrania: %d panorama/e." % len(panos))
    summaries = []
    for index, stub in enumerate(panos, start=1):
        try:
            summaries.append(save_pano(args, api, stub, args.out, index, len(panos)))
        except SystemExit:
            raise
        except Exception as error:  # noqa: BLE001 - jedna panorama nie przerywa calosci
            print("  BLAD przy %s: %s" % (stub["panoid"], error))

    manifest = {
        "tool": "gsvgrab %s" % __version__,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "query": {"label": target.get("label"), "lat": target.get("lat"), "lon": target.get("lon"),
                  "geocode": target.get("geocode"), "radius_m": getattr(args, "radius", None),
                  "walk": bool(getattr(args, "walk", False)),
                  "equirect_zoom": getattr(args, "equirect_zoom", None),
                  "depth": not getattr(args, "no_depth", False)},
        "panoramas": summaries,
        "totals": {
            "panoramas": len(summaries),
            "with_depth": sum(1 for item in summaries if item.get("has_depth")),
            "seconds": round(sum(item.get("seconds") or 0.0 for item in summaries), 1),
        },
    }
    manifest_path = os.path.join(args.out, "manifest.json")
    write_json(manifest_path, manifest)
    viewer_path = write_viewer(args.out, manifest)
    readme_path = os.path.join(args.out, "README.txt")
    with open(readme_path, "w", encoding="utf-8") as handle:
        handle.write(README_TEXT.format(
            label=target.get("label"), lat=target.get("lat"), lon=target.get("lon"),
            count=len(summaries), generated_at=manifest["generated_at"]))

    print("\nGotowe.")
    print("  panoram:            %d (z glebia: %d)"
          % (manifest["totals"]["panoramas"], manifest["totals"]["with_depth"]))
    print("  manifest:           %s" % manifest_path)
    print("  przegladarka:       %s" % viewer_path)
    print("  opis i licencje:    %s" % readme_path)
    return 0


def cmd_walk(args):
    """Przechodzi ulica (kroki do przodu i z powrotem) i skleja chmury punktow."""
    client = make_client(args)
    api = StreetViewApi(client, locale=args.locale, api_key=args.api_key)
    target = resolve_target(args, client, api)
    if not target.get("panoid"):
        target["panoid"] = _nearest_pano_for(api, target["lat"], target["lon"],
                                             getattr(args, "radius", 800.0))[0]["panoid"]
    os.makedirs(args.out, exist_ok=True)

    crawler = Crawler(api, verbose=True, step_m=args.step_m)
    print("\nPrzechodze od %s: %d krokow do przodu, %d z powrotem"
          % (target["panoid"], args.forward, args.back))
    route = crawler.traverse(target["panoid"], forward_steps=args.forward,
                             back_steps=args.back, heading_deg=args.heading)
    length = crawler.route_length(route)
    extent = crawler.route_extent(route)
    unique = []
    seen = set()
    for node in route:
        if node["panoid"] not in seen:
            seen.add(node["panoid"])
            unique.append(node)

    print()
    print("  %-3s %-7s %-24s %-9s %-9s %-9s %s"
          % ("#", "etap", "panoid", "odl.[m]", "kier.[d]", "hlad.[d]", "uwagi"))
    for node in route:
        notes = node["side"] + (" (powrotka)" if node.get("revisit") else "")
        print("  %-3d %-7s %-24s %-9.1f %-9.1f %-9.1f %s"
              % (node["step"], node["leg"], node["panoid"], node["distance_from_prev_m"],
                 node["travel_heading_deg"], node["pano_heading_deg"] or 0.0, notes))
    print("\n  wezlow: %d | unikalnych panoram: %d | dlugosc trasy: %.1f m"
          % (len(route), len(unique), length))
    print("  zasieg trasy: %.1f m (poludnie-poln) x %.1f m (wschod-zachod)"
          % (extent.get("north_south_m", 0.0), extent.get("east_west_m", 0.0)))

    write_json(os.path.join(args.out, "trasa.json"),
               {"start_panoid": target["panoid"], "query": target.get("label"),
                "lat": target.get("lat"), "lon": target.get("lon"),
                "forward_steps": args.forward, "back_steps": args.back,
                "length_m": length, "extent": extent, "route": route})
    if args.route_only:
        print("\nZapisano trase: %s" % os.path.join(args.out, "trasa.json"))
        return 0

    print("\nPobieram glebie dla %d unikalnych panoram..." % len(unique))
    entries = []
    for index, node in enumerate(unique, start=1):
        panoid = node["panoid"]
        print("  [%d/%d] %s" % (index, len(unique), panoid))
        pano = panorama_by_id_response(api.panorama_by_id(panoid, download_depth=True),
                                       include_depth=True)
        if not pano:
            print("      pominieto (brak metadanych)")
            continue
        depth = pano.pop("_depth", None)
        pano_dir = os.path.join(args.out, "panoramy", panoid)
        pano["route_step"] = node["step"]
        pano["route_leg"] = node["leg"]
        pano["downloaded_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        files = {}
        if depth is not None:
            depth_files, stats = save_depth_artifacts(depth, os.path.join(pano_dir, "depth"))
            files.update(depth_files)
            pano["depth_summary"] = {key: value for key, value in stats.items()
                                     if key not in ("histogram", "planes")}
            print("      glebia %dx%d, pokrycie %.1f%%, mediana %.2f m"
                  % (depth.width, depth.height, stats["coverage_percent"],
                     stats["percentiles_m"]["p50"]))
            entries.append({"depth": depth, "panoid": panoid, "lat": pano.get("lat"),
                            "lon": pano.get("lon"), "elevation_m": pano.get("elevation_m"),
                            "heading_deg": pano.get("heading_deg")})
        else:
            print("      brak mapy glebi")
        pano["files"] = {key: os.path.relpath(value, args.out) for key, value in files.items()}
        write_json(os.path.join(pano_dir, "metadata.json"), pano)

    if args.no_merge:
        print("\nPominieto sklejanie chmur punktow (--no-merge).")
        return 0
    if not entries:
        print("\nBrak danych glebi - nie ma czego sklejac.")
        return 1

    out_ply = os.path.join(args.out, "chmura_sklejona.ply")
    comments = ["gsvgrab - sklejona chmura punktow: %s" % (target.get("label") or "?"),
                "panoram: %d (kroki: %d do przodu + %d z powrotem)"
                % (len(entries), args.forward, args.back),
                "dlugosc trasy: %.1f m" % length,
                "uklad ENU: X=wschod, Y=polnoc, Z=w gore; poczatek = kamera 1. panoramy",
                "jednostki: metry"]
    print("\nSklejam %d chmur punktow..." % len(entries))
    result = merge_panoramas(entries, out_ply, stride=max(1, args.stride),
                             max_depth=args.max_depth, comments=comments)
    write_json(os.path.join(args.out, "chmura_sklejona.json"), result)
    print("  punktow: %d | kolor: %s" % (result["points"],
                                         "tak" if result["colored"] else "nie"))
    xs = [item["east_m"] for item in result["per_panorama"]]
    ys = [item["north_m"] for item in result["per_panorama"]]
    print("  zasieg chmury: %.1f m (E-W) x %.1f m (N-S)"
          % (max(xs) - min(xs), max(ys) - min(ys)))
    print("  plik: %s" % out_ply)
    print("Gotowe. Trasa: %s" % os.path.join(args.out, "trasa.json"))
    return 0


def cmd_merge(args):
    """Scala chmury punktow zapisane w katalogu pobranym komenda grab/walk."""
    client = make_client(args)
    api = StreetViewApi(client, locale=args.locale, api_key=args.api_key)
    in_dir = args.in_dir or args.out
    panoramas_dir = os.path.join(in_dir, "panoramy")
    if not os.path.isdir(panoramas_dir):
        raise SystemExit("Nie znaleziono katalogu %s (uruchom najpierw grab lub walk)."
                         % panoramas_dir)
    entries = []
    skipped = []
    for panoid in sorted(os.listdir(panoramas_dir)):
        pano_dir = os.path.join(panoramas_dir, panoid)
        meta_path = os.path.join(pano_dir, "metadata.json")
        if not os.path.exists(meta_path):
            continue
        with open(meta_path, encoding="utf-8") as handle:
            meta = json.load(handle)
        summary = meta.get("depth_summary") or {}
        width, height = summary.get("width"), summary.get("height")
        depth = None
        if width and height:
            try:
                depth = load_depth_f32(os.path.join(pano_dir, "depth", "depth_f32.bin"),
                                       width, height)
            except Exception:  # noqa: BLE001 - w razie problemu pobieramy ponownie
                depth = None
        if depth is None:
            pano = panorama_by_id_response(api.panorama_by_id(panoid, download_depth=True),
                                           include_depth=True)
            depth = (pano or {}).get("_depth")
            if depth is None:
                skipped.append(panoid)
                continue
        entries.append({"depth": depth, "panoid": panoid, "lat": meta.get("lat"),
                        "lon": meta.get("lon"), "elevation_m": meta.get("elevation_m"),
                        "heading_deg": meta.get("heading_deg")})
    print("Znaleziono %d panoram z glebia w %s" % (len(entries), in_dir))
    if skipped:
        print("  pominieto (brak glebi): %s" % ", ".join(skipped))
    if not entries:
        raise SystemExit("Brak danych do scalenia.")
    out_path = os.path.join(in_dir, args.out_file)
    result = merge_panoramas(entries, out_path, stride=max(1, args.stride),
                             max_depth=args.max_depth,
                             comments=["gsvgrab - sklejona chmura punktow",
                                       "zrodlo: %s" % in_dir,
                                       "uklad ENU: X=wschod, Y=polnoc, Z=w gore"])
    write_json(out_path.replace(".ply", ".json"), result)
    xs = [item["east_m"] for item in result["per_panorama"]]
    ys = [item["north_m"] for item in result["per_panorama"]]
    print("Sklejono %d punktow z %d panoram" % (result["points"], result["panoramas"]))
    print("  zasieg: %.1f m (E-W) x %.1f m (N-S)" % (max(xs) - min(xs), max(ys) - min(ys)))
    print("  plik: %s" % out_path)
    return 0


def cmd_mesh(args):
    """Generuje siatkę trójkątów 3D (OBJ i PLY) ze sklejonych panoram."""
    from .mesh import build_route_mesh

    in_dir = args.in_dir or args.out
    if not os.path.isdir(in_dir):
        raise SystemExit("Katalog %s nie istnieje." % in_dir)

    build_route_mesh(in_dir, out_obj=args.out_obj, out_ply=args.out_ply,
                     stride=max(1, args.stride), max_depth=args.max_depth,
                     max_edge_ratio=args.max_edge_ratio)
    return 0


def main(argv=None):
    """Punkt wejścia CLI."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "find":
        return cmd_find(args)
    if args.command == "info":
        return cmd_info(args)
    if args.command == "grab":
        return cmd_grab(args)
    if args.command == "walk":
        return cmd_walk(args)
    if args.command == "merge":
        return cmd_merge(args)
    if args.command == "mesh":
        return cmd_mesh(args)
    if args.command == "selftest":
        from .selftest import run_selftest

        return run_selftest(verbose=True)
    parser.error("nieznana komenda")
    return 2


if __name__ == "__main__":
    sys.exit(main())
