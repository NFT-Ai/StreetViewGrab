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
    header = struct.pack("<BHHHH", 8, 2, width, height, 8)
    indices = bytes([plane_index] * (width * height))
    planes = struct.pack("<4f", 0.0, 0.0, 1.0, -5.0) + struct.pack("<4f", 0.0, 1.0, 0.0, -3.0)
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
