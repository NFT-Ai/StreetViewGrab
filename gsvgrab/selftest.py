"""
Testy offline (bez dostępu do sieci) - ``python3 -m gsvgrab selftest``.

Sprawdzają dekoder głębi (z weryfikacją analityczną wartości), kodowanie protobufa,
geometrię, zapis/odczyt PNG, chmurę punktów oraz parser metadanych na
syntetycznej odpowiedzi API.
"""

import base64
import math
import os
import struct
import tempfile
import zlib

from . import protobuf_url as pb
from .api import tile_grid
from .depth import (INFINITELY_FAR, compute_depth_map, depth_to_gray16_mm,
                    depth_to_gray8, depth_to_rgb8, parse_depth)
from .geo import bearing_deg, distance_m, lonlat_to_tile
from .parse import panorama_by_id_response, try_get
from .pngio import read_png, write_png
from .pointcloud import build_pointcloud


def build_depth_payload(width=8, height=4, plane_index=1):
    """Buduje syntetyczną mapę głębi: płaszczyzna ``z=5`` (n=(0,0,1), d=-5)."""
    header = struct.pack("<BHHHH", 9, 2, width, height, 9)
    indices = bytes([plane_index] * (width * height))
    planes = struct.pack("<4f", 0.0, 0.0, 0.0, 0.0) + struct.pack("<4f", 0.0, 0.0, 1.0, -5.0)
    return header + indices + planes


class Checker:
    """Zbiera wyniki testów."""

    def __init__(self, verbose=True):
        self.verbose = verbose
        self.passed = 0
        self.failed = 0

    def check(self, name, condition, detail=""):
        if condition:
            self.passed += 1
            if self.verbose:
                print("  OK   %s" % name)
        else:
            self.failed += 1
            print("  FAIL %s %s" % (name, detail))

    def close(self, value, expected, name, tolerance=1e-6):
        difference = abs(value - expected)
        self.check(name, difference <= tolerance,
                   "(otrzymano %.6f, oczekiwano %.6f)" % (value, expected))


def test_tile_grid(checker):
    """Siatka kafli panoramy."""
    for zoom, expected in ((0, (1, 1)), (1, (2, 1)), (2, (4, 2)), (3, (8, 4)), (5, (32, 16))):
        checker.check("siatka kafli zoom=%d -> %s" % (zoom, expected),
                      tile_grid(zoom) == expected, str(tile_grid(zoom)))


def test_protobuf(checker):
    """Kodowanie protobufa w URL."""
    encoded = pb.to_protobuf_url({1: {1: "apiv3", 5: "US"}, 2: 5})
    checker.check("protobuf zawiera !1sapiv3", "!1sapiv3" in encoded, encoded)
    checker.check("protobuf zawiera !5sUS", "!5sUS" in encoded, encoded)
    checker.check("protobuf zawiera !2i5", "!2i5" in encoded, encoded)
    encoded_bool = pb.to_protobuf_url({1: {1: True, 2: False}})
    checker.check("bool kodowany jako 1/0", "b1" in encoded_bool and "b0" in encoded_bool,
                  encoded_bool)


def test_geo(checker):
    """Odległość, azymut i kafle."""
    distance = distance_m(52.1279153, 20.6692050, 52.1278306, 20.6694742)
    checker.check("odleglosc Piasta 10C -> panorama ok. 20-25 m", 19.0 < distance < 26.0,
                  "%.2f m" % distance)
    checker.close(bearing_deg(0.0, 0.0, 1.0, 0.0), 0.0, "azymut na polnoc", tolerance=0.01)
    checker.close(bearing_deg(0.0, 0.0, 0.0, 1.0), 90.0, "azymut na wschod", tolerance=0.01)
    checker.check("kafel XYZ dla Milanowka (zoom 17)",
                  lonlat_to_tile(20.6692050, 52.1279153, 17) == (73061, 43219),
                  str(lonlat_to_tile(20.6692050, 52.1279153, 17)))


def test_depth_decode(checker):
    """Dekoder głębi: wariant surowy i spakowany, weryfikacja analityczna."""
    payload = build_depth_payload(width=8, height=4)
    b64_raw = base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")
    depth = parse_depth(b64_raw)
    checker.check("naglowek: 8x4 px, 2 plaszczyzny",
                  (depth.width, depth.height, len(depth.planes)) == (8, 4, 2),
                  str((depth.width, depth.height, len(depth.planes))))
    checker.check("kodowanie rozpoznane jako surowe", depth.encoding == "raw", depth.encoding)

    # Wartość w kolumnie cx pochodzi z promienia x = width-1-cx, więc
    # depth = |d / (v . n)| = 5 / |cos(theta)|.
    for cx, cy in ((0, 0), (3, 1), (7, 3)):
        theta = (depth.height - cy - 0.5) / depth.height * math.pi
        expected = 5.0 / abs(math.cos(theta))
        checker.close(depth.at(cx, cy), expected, "glebia w pikselu %d,%d" % (cx, cy),
                      tolerance=1e-4)

    b64_zlib = base64.urlsafe_b64encode(zlib.compress(payload)).decode("ascii").rstrip("=")
    depth_zlib = parse_depth(b64_zlib)
    checker.check("wariant spakowany zlib dekoduje sie", depth_zlib.encoding.startswith("zlib"),
                  depth_zlib.encoding)
    checker.close(depth_zlib.at(0, 0), depth.at(0, 0), "zgodnosc wariantu zlib")

    empty = parse_depth(base64.urlsafe_b64encode(
        build_depth_payload(plane_index=0)).decode("ascii").rstrip("="))
    checker.check("indeks 0 oznacza brak danych (-1)",
                  all(value == INFINITELY_FAR for value in empty.data))
    stats = empty.stats()
    checker.check("statystyki bez danych glebi", stats["pixels_with_depth"] == 0,
                  str(stats["pixels_with_depth"]))

    stats = depth.stats()
    checker.check("statystyki: 32 px z glebia", stats["pixels_with_depth"] == 32,
                  str(stats["pixels_with_depth"]))
    checker.check("wizualizacje maja wlasciwy rozmiar",
                  len(depth_to_rgb8(depth)) == 32 * 3 and len(depth_to_gray8(depth)) == 32
                  and len(depth_to_gray16_mm(depth)) == 64)



def test_png(checker, workdir):
    """Zapis i odczyt PNG (8 i 16 bit)."""
    gray8 = bytes([0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 250, 251, 252, 253, 254, 255])
    path = os.path.join(workdir, "gray8.png")
    write_png(path, 4, 4, gray8, channels=1, bitdepth=8)
    image = read_png(path)
    checker.check("PNG 8-bit: rozmiar i dane",
                  (image["width"], image["height"], image["channels"]) == (4, 4, 1)
                  and image["data"] == gray8,
                  "%dx%d" % (image["width"], image["height"]))

    samples = [1, 2, 300, 400, 5000, 60000]
    gray16 = b"".join(struct.pack(">H", sample) for sample in samples)
    path16 = os.path.join(workdir, "gray16.png")
    write_png(path16, 3, 2, gray16, channels=1, bitdepth=16)
    image16 = read_png(path16)
    checker.check("PNG 16-bit: rozmiar i glebokosc",
                  (image16["width"], image16["height"], image16["bitdepth"]) == (3, 2, 16),
                  str(image16["bitdepth"]))
    checker.check("PNG 16-bit: dane bez zmian", image16["data"] == gray16)

    rgb = bytes([10, 20, 30, 40, 50, 60, 70, 80, 90, 100, 110, 120])
    path_rgb = os.path.join(workdir, "rgb8.png")
    write_png(path_rgb, 2, 2, rgb, channels=3, bitdepth=8)
    image_rgb = read_png(path_rgb)
    checker.check("PNG RGB: rozmiar i dane",
                  (image_rgb["width"], image_rgb["height"], image_rgb["channels"]) == (2, 2, 3)
                  and image_rgb["data"] == rgb)

    gradient = bytes([value % 256 for value in range(256)])
    path_gradient = os.path.join(workdir, "gradient.png")
    write_png(path_gradient, 16, 16, gradient, channels=1, bitdepth=8)
    checker.check("PNG: gradient 16x16 odczytany poprawnie",
                  read_png(path_gradient)["data"] == gradient)


def test_pointcloud(checker, workdir):
    """Chmura punktów z mapy głębi."""
    payload = build_depth_payload(width=8, height=4)
    depth = parse_depth(base64.urlsafe_b64encode(payload).decode("ascii").rstrip("="))
    path = os.path.join(workdir, "cloud.ply")
    result = build_pointcloud(depth, path, stride=1, comments=["test gsvgrab"])
    checker.check("chmura: 32 punkty", result["points"] == 32, str(result["points"]))
    with open(path, "rb") as handle:
        head = handle.read(400)
    header_end = head.index(b"end_header") + len("end_header\n")
    checker.check("PLY: naglowek z liczba punktow",
                  b"element vertex 32" in head and head.startswith(b"ply"), head[:20])
    checker.check("PLY: rozmiar pliku = naglowek + 32*16 B",
                  os.path.getsize(path) == header_end + 32 * 16,
                  "%d B" % os.path.getsize(path))


def test_parse(checker):
    """Parser odpowiedzi photometa na danych syntetycznych."""
    msg = [
        [1],
        [None, "TEST_PANOID_12345"],
        [None, None, None, [[(512, 256), (1024, 512)], [512, 512]]],
        [None, None, [["Piasta 10c, Milanówek", "pl"]]],
        [[[["Google"]]], [[["Autor"]], None, "http://icon"]],
        [
            [
                None,
                [[None, None, 52.12783, 20.66947], [100.0], [180.0, 90.0, 0.0], None, "PL"],
                None,
                [[]],
            ]
        ],
        [None, None, None, None, None, [None, None, "launch"], [None, None, None, None, None, None, None, [2022, 5]], [2022, 6]],
        ["http://report_url"]
    ]
    synthetic_response = [None, [msg]]
    parsed = panorama_by_id_response(synthetic_response, include_depth=False)
    checker.check("parser metadanych rozpoznaje panoid",
                  bool(parsed and parsed.get("panoid") == "TEST_PANOID_12345"),
                  str(parsed.get("panoid") if parsed else None))
    checker.check("parser wyciaga wspolrzedne",
                  bool(parsed and parsed.get("lat") and abs(parsed["lat"] - 52.12783) < 1e-4),
                  str(parsed.get("lat") if parsed else None))
    checker.check("parser wyciaga adres",
                  bool(parsed and len(parsed.get("address", [])) == 1
                       and parsed["address"][0]["text"] == "Piasta 10c, Milanówek"),
                  str(parsed.get("address") if parsed else None))


def run_selftest(verbose=True):
    """Uruchamia wszystkie testy jednostkowe offline."""
    checker = Checker(verbose=verbose)
    if verbose:
        print("Uruchamianie testow offline gsvgrab...")

    test_tile_grid(checker)
    test_protobuf(checker)
    test_geo(checker)
    test_depth_decode(checker)
    test_parse(checker)

    with tempfile.TemporaryDirectory(prefix="gsvgrab-test-") as workdir:
        test_png(checker, workdir)
        test_pointcloud(checker, workdir)

    if verbose:
        print("\nWynik: %d testow zaliczonych, %d bledow." % (checker.passed, checker.failed))

    return 0 if checker.failed == 0 else 1


if __name__ == "__main__":
    import sys

def build_fake_response(depth_b64):
    """Buduje syntetyczną odpowiedź ``photometa`` o strukturze zgodnej z API."""
    others = [[[2, "NEIGHBOR00000000000001"], None,
                [[None, None, 52.128, 20.669], [107.0, None, 138.9], [20.0, -1.0, 1.0]]]]
    message = [None] * 20
    message[0] = [1]  # kod odpowiedzi: 1 = OK
    message[1] = [1, "TESTPANOID000000000001"]
    # msg[2][3] = [lista rozmiarow obrazu, rozmiar kafla]
    message[2] = [2, 2, [8192, 16384], [[[[256, 512]], [[512, 1024]]], [512, 512]]]
    message[3] = [3, None, [["13 Piasta", "pl"], ["Milanówek, mazowieckie", "pl"]]]
    message[4] = [[[[u"\u00a9 2026 Google"]]], [["Google", None, "//icon"]]]
    # msg[5][0] to tablica pol: 1 - geografia, 3 - sasiedzi, 5 - glebia, 6 - polaczenia,
    # 8 - daty historyczne, 12 - etykiety ulic
    message[5] = [[
        [0, 0, 0],
        [[None, None, 52.1279153, 20.6692050], [106.75], [26.0, 89.0, 2.0], None, "PL"],
        None,
        [others],
        None,
        [None, [None, None, depth_b64]],
        [[0, [0, 0, 0, 180.0]]],
        None,
        [[0, [2015, 6]]],
        None, None, None,
        [[[[0, 0, ["Piasta", "pl"]]], [24.57, 204.34]]],
    ]]
    message[6] = [None, None, None, None, None, [None, None, "launch"], None, [2025, 10]]
    message[7] = ["//www.google.com/local/imagery/report/?cb_client=maps_sv.tactile"]
    return [None, [message]]


def test_parser(checker):
    """Parser metadanych na syntetycznej odpowiedzi."""
    payload = build_depth_payload(width=16, height=8)
    depth_b64 = base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")
    pano = panorama_by_id_response(build_fake_response(depth_b64), include_depth=True)
    checker.check("parser: panoid", pano.get("panoid") == "TESTPANOID000000000001",
                  str(pano.get("panoid")))
    checker.close(pano.get("lat") or 0.0, 52.1279153, "parser: szerokosc", tolerance=1e-7)
    checker.close(pano.get("heading_deg") or 0.0, 26.0, "parser: naglowek", tolerance=1e-6)
    checker.close(pano.get("pitch_deg") or 0.0, 1.0, "parser: pochylenie", tolerance=1e-6)
    checker.check("parser: adres", pano["address"][0]["text"] == "13 Piasta",
                  str(pano.get("address")))
    checker.check("parser: copyright", pano["copyright"].endswith("Google"),
                  str(pano.get("copyright")))
    checker.check("parser: data zdjecia",
                  pano["capture_date"] == {"year": 2025, "month": 10, "day": None},
                  str(pano.get("capture_date")))
    checker.check("parser: rozmiary obrazu", pano["image_sizes"][1] == {"x": 1024, "y": 512},
                  str(pano.get("image_sizes")))
    checker.check("parser: rozmiar kafla", pano["tile_size"] == {"x": 512, "y": 512},
                  str(pano.get("tile_size")))
    checker.check("parser: etykieta ulicy", pano["street_labels"][0]["name"] == "Piasta",
                  str(pano.get("street_labels")))
    checker.check("parser: historia zdjec",
                  pano["historical_captures"][0]["capture_date"] == {"year": 2015, "month": 6},
                  str(pano.get("historical_captures")))
    checker.check("parser: sasiedzi i historia",
                  len(pano["neighbors"]) + len(pano["historical_captures"]) == 1,
                  str(len(pano["neighbors"])))
    checker.check("parser: mapa glebi zdekodowana", pano["_depth"].width == 16,
                  str(pano.get("_depth")))
    checker.check("try_get zwraca wartosc domyslna",
                  try_get(lambda: [][5], "domyslna") == "domyslna")


def build_depth_flat_horizontal(width=8, height=4, distance=5.0):
    """Mapa glebi: plaszczyzna pozioma 5 m pod kamera (n = (0,0,1), d = -5).

    Plaszczyzna pozioma jest widoczna z wszystkich kierunkow, wiec wszystkie
    piksele otrzymuja poprawna glebie. Indeksy plaszczyzn sa 1-based.
    """
    header = struct.pack("<BHHHH", 9, 2, width, height, 9)
    indices = bytes([1] * (width * height))
    planes = (struct.pack("<4f", 0.0, 0.0, 0.0, 0.0)
              + struct.pack("<4f", 0.0, 0.0, 1.0, -float(distance)))
    return header + indices + planes


def test_crawl_and_enu(checker):
    """Przechodzenie ulica (katy) i przejscie do ukladu ENU."""
    from .crawl import _angle_difference, _axis_difference
    from .pointcloud import (curvature_drop, depth_to_enu_points, local_offset,
                             merge_panoramas)

    checker.close(_angle_difference(0, 0), 0.0, "kat: 0 vs 0", tolerance=1e-9)
    checker.close(_angle_difference(350, 10), 20.0, "kat: 350 vs 10 (przez 0)", tolerance=1e-9)
    checker.close(_angle_difference(0, 180), 180.0, "kat: 0 vs 180", tolerance=1e-9)
    checker.close(_axis_difference(25, 205), 0.0, "kat modulo 180: 25 vs 205", tolerance=1e-9)
    checker.close(_axis_difference(0, 90), 90.0, "kat modulo 180: 0 vs 90", tolerance=1e-9)

    east, north = local_offset(52.1279153, 20.6692050, 52.1280000, 20.6693000)
    checker.check("przesuniecie lokalne zgodne z haversine",
                  abs((east ** 2 + north ** 2) ** 0.5
                      - distance_m(52.1279153, 20.6692050, 52.1280000, 20.6693000)) < 1e-6,
                  "%.6f / %.6f" % (east, north))
    checker.check("przesuniecie na polnoc dodatnie", north > 0, "%.4f" % north)
    checker.check("spadek horyzontu na 100 m ~ 0.8 mm",
                  abs(curvature_drop(100.0) - 0.0008) < 0.0002, "%.6f" % curvature_drop(100.0))

    payload = build_depth_flat_horizontal(width=16, height=8, distance=5.0)
    depth = parse_depth(base64.urlsafe_b64encode(payload).decode("ascii").rstrip("="))
    points = depth_to_enu_points(depth, heading_deg=0.0)
    checker.check("ENU: plaszczyzna pozioma 5 m pod kamera",
                  all(abs(abs(p[2]) - 5.0) < 1e-6 for p in points), str(points[0]))
    checker.check("ENU: 128 punktow dla mapy 16x8", len(points) == 128, str(len(points)))
    checker.check("ENU: wiersz 0 obrazu (zenit) ma Z > 0",
                  all(p[2] > 0 for p in points if p[5] == 0),
                  str([p for p in points if p[5] == 0][:1]))
    checker.check("ENU: ostatni wiersz obrazu (nadir) ma Z < 0",
                  all(p[2] < 0 for p in points if p[5] == 7),
                  str([p for p in points if p[5] == 7][:1]))
    checker.check("ENU: wspolrzedne E zalezne od piksela (kolumna i wiersz)",
                  len({round(p[0], 3) for p in points}) >= 16,
                  str(len({round(p[0], 3) for p in points})))

    merged = merge_panoramas([
        {"depth": depth, "panoid": "A", "lat": 52.0, "lon": 20.0,
         "elevation_m": 100.0, "heading_deg": 0.0},
        {"depth": depth, "panoid": "B", "lat": 52.0, "lon": 20.0,
         "elevation_m": 100.0, "heading_deg": 90.0},
    ], stride=1)
    checker.check("scalanie: 2 panoramy, 256 punktow (stride 1)",
                  merged["panoramas"] == 2 and merged["points"] == 256,
                  str((merged["panoramas"], merged["points"])))
    checker.check("scalanie: identyczna lokalizacja = zero przesuniecia",
                  merged["per_panorama"][1]["distance_m"] == 0.0,
                  str(merged["per_panorama"][1]))
    checker.check("scalanie: probkowanie stride=2 redukuje liczbe punktow 4x",
                  merge_panoramas([
                      {"depth": depth, "panoid": "A", "lat": 52.0, "lon": 20.0,
                       "elevation_m": 100.0, "heading_deg": 0.0}], stride=2)["points"] == 32,
                  "spodziewano 32")
def run_selftest(verbose=True):
    """Uruchamia wszystkie testy offline. Zwraca 0 (sukces) lub 1."""
    import shutil

    checker = Checker(verbose=verbose)
    print("gsvgrab selftest - testy offline")
    workdir = tempfile.mkdtemp(prefix="gsvgrab-selftest-")
    try:
        print("\n[1] siatka kafli")
        test_tile_grid(checker)
        print("[2] kodowanie protobufa")
        test_protobuf(checker)
        print("[3] geometria")
        test_geo(checker)
        print("[4] dekoder glebi")
        test_depth_decode(checker)
        print("[5] PNG")
        test_png(checker, workdir)
        print("[6] chmura punktow")
        test_pointcloud(checker, workdir)
        print("[7] parser metadanych")
        test_parser(checker)
        print("[8] przechodzenie ulicy i uklad ENU")
        test_crawl_and_enu(checker)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    print("\nWynik: %d OK, %d FAIL" % (checker.passed, checker.failed))
    return 0 if checker.failed == 0 else 1
    sys.exit(run_selftest(verbose=True))
