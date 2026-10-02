"""
Dekoder map głębi Google Street View.

Format danych (``photometa/v1``, pole ``msg[5][0][5][1][2]`` jako ciąg base64):

* nagłówek (8 B): ``headerSize(u8)``, ``numPlanes(u16 LE)``, ``width(u16 LE)``,
  ``height(u16 LE)``, ``offset(u16 LE)``;
* ``width*height`` bajtów indeksów płaszczyzn (0 = niebo/horyzont);
* ``numPlanes`` płaszczyzn, każda 16 B: ``nx, ny, nz, d`` (float32 LE),
  gdzie ``(nx, ny, nz)`` to wektor normalny, a ``d`` odległość płaszczyzny.

Głębię dla piksela wyznacza przecięcie promienia (kierunek piksela na sferze)
z płaszczyzną: ``depth = |d / (v . n)|``. Wartości są w **metrach**;
``-1`` oznacza horyzont/niebo (brak danych).

Typowy rozmiar mapy głębi to 512x256 (2x mniej niż najniższy zoom obrazu).
Dane w praktyce przychodzą **nieskompresowane** (sama base64), ale dekoder
obsługuje też warianty spakowane zlib/deflate, na wypadek zmian po stronie Google.

Algorytm na podstawie ``GSVPanoDepth.js`` (MIT, proog128) i implementacji
``streetlevel`` (MIT, sk-zk).
"""

import base64
import math
import struct
import zlib

INFINITELY_FAR = -1.0


class DepthMap:
    """Mapa głębi panoramy: ``width`` x ``height`` wartości w metrach."""

    __slots__ = ("width", "height", "data", "header", "planes", "encoding")

    def __init__(self, width, height, data, header=None, planes=None, encoding="raw"):
        self.width = width
        self.height = height
        self.data = data                 # lista float, wiersz po wierszu
        self.header = header or {}
        self.planes = planes or []
        self.encoding = encoding

    def __len__(self):
        return len(self.data)

    def at(self, x, y):
        """Głębia w metrach dla piksela (``x`` od lewej, ``y`` od góry)."""
        return self.data[y * self.width + x]

    def valid_values(self):
        """Wartości większe od zera (bez horyzontu)."""
        return [value for value in self.data if value > 0]

    def stats(self, percentiles=(1, 5, 25, 50, 75, 95, 99)):
        """Statystyki głębi: liczność, min/średnia/maks, percentyle, histogram."""
        total = len(self.data)
        valid = sorted(self.valid_values())
        result = {
            "width": self.width,
            "height": self.height,
            "encoding": self.encoding,
            "header": self.header,
            "planes_count": len(self.planes),
            "pixels_total": total,
            "pixels_with_depth": len(valid),
            "pixels_far_or_sky": total - len(valid),
            "coverage_percent": round(100.0 * len(valid) / total, 2) if total else 0.0,
        }
        if valid:
            result["min_m"] = valid[0]
            result["max_m"] = valid[-1]
            result["mean_m"] = sum(valid) / len(valid)
            result["percentiles_m"] = {}
            for percentile in percentiles:
                index = min(len(valid) - 1, int(round(percentile / 100.0 * (len(valid) - 1))))
                result["percentiles_m"]["p%02d" % percentile] = valid[index]
            # histogram w 16 przedziałach do maksimum
            top = valid[-1] or 1.0
            bins = [0] * 16
            for value in valid:
                bins[min(15, int(value / top * 16))] += 1
            result["histogram"] = {"bin_count": 16, "max_m": top, "counts": bins}
        return result

    def planes_as_list(self):
        return [{"nx": n[0], "ny": n[1], "nz": n[2], "d": n[3]} for n in self.planes]


# ---------------------------------------------------------------------------
# Dekodowanie
# ---------------------------------------------------------------------------
def _unpack_header(data):
    if len(data) < 8:
        raise ValueError("za maly naglowek mapy glebi (%d B)" % len(data))
    header_size, planes, width, height, offset = struct.unpack_from("<BHHHH", data, 0)
    return {"header_size": header_size, "num_planes": planes, "width": width,
            "height": height, "offset": offset}


def _header_is_sane(header, data):
    if not (0 < header["num_planes"] <= 512):
        return False
    if not (0 < header["width"] <= 4096 and 0 < header["height"] <= 4096):
        return False
    needed = header["offset"] + header["width"] * header["height"] + header["num_planes"] * 16
    return len(data) >= needed


def decode_b64_payload(b64_string):
    """Zwraca ``(bajty, nazwa_kodowania)`` - bez kompresji lub po dekompresji zlib."""
    padded = b64_string + "=" * ((4 - len(b64_string) % 4) % 4)
    raw = base64.urlsafe_b64decode(padded)
    candidates = [("raw", raw)]
    for wbits in (-15, 15, 47):
        try:
            candidates.append(("zlib%d" % wbits, zlib.decompress(raw, wbits)))
        except Exception:  # noqa: BLE001 - zły wariant, próbujemy następny
            continue
    for name, payload in candidates:
        try:
            header = _unpack_header(payload)
        except ValueError:
            continue
        if _header_is_sane(header, payload):
            return payload, name
    return raw, "nieznane"



def _direction_tables(width, height):
    """Tablice kierunków promieni dla siatki equirectangularnej (środek piksela)."""
    sin_theta = [0.0] * height
    cos_theta = [0.0] * height
    for y in range(height):
        theta = (height - y - 0.5) / height * math.pi
        sin_theta[y] = math.sin(theta)
        cos_theta[y] = math.cos(theta)
    sin_phi = [0.0] * width
    cos_phi = [0.0] * width
    for x in range(width):
        phi = (width - x - 0.5) / width * 2.0 * math.pi + math.pi / 2.0
        sin_phi[x] = math.sin(phi)
        cos_phi[x] = math.cos(phi)
    return sin_theta, cos_theta, sin_phi, cos_phi


def compute_depth_map(header, planes, indices):
    """Zamienia indeksy płaszczyzn na odległości w metrach (lista float)."""
    width = header["width"]
    height = header["height"]
    sin_theta, cos_theta, sin_phi, cos_phi = _direction_tables(width, height)
    out = [INFINITELY_FAR] * (width * height)
    for y in range(height):
        st = sin_theta[y]
        ct = cos_theta[y]
        row = y * width
        for x in range(width):
            plane_index = indices[row + x]
            if plane_index == 0 or plane_index >= len(planes):
                continue
            nx, ny, nz, d = planes[plane_index]
            vx = st * cos_phi[x]
            vy = st * sin_phi[x]
            vz = ct
            denominator = vx * nx + vy * ny + vz * nz
            if abs(denominator) > 1e-9:
                out[row + (width - x - 1)] = abs(d / denominator)
    return out


def parse_depth_bytes(payload, encoding="raw"):
    """Buduje :class:`DepthMap` z surowych bajtów."""
    header = _unpack_header(payload)
    width = header["width"]
    height = header["height"]
    offset = header["offset"]
    indices = payload[offset:offset + width * height]
    plane_count = header["num_planes"]
    base = offset + width * height
    planes = [struct.unpack_from("<4f", payload, base + index * 16) for index in range(plane_count)]
    data = compute_depth_map(header, planes, indices)
    return DepthMap(width, height, data, header=header, planes=planes, encoding=encoding)


# ---------------------------------------------------------------------------
# Konwersje do obrazów
# ---------------------------------------------------------------------------
#: Paleta typu "turbo": blisko = niebieski, daleko = czerwony.
_COLOR_STOPS = (
    (0.00, (28, 60, 160)),
    (0.25, (0, 170, 200)),
    (0.50, (60, 200, 90)),
    (0.75, (240, 200, 40)),
    (1.00, (200, 40, 30)),
)


def color_for(ratio):
    """Kolor (R, G, B) dla wartości 0..1."""
    ratio = 0.0 if ratio < 0.0 else (1.0 if ratio > 1.0 else ratio)
    for index in range(len(_COLOR_STOPS) - 1):
        left_pos, left_color = _COLOR_STOPS[index]
        right_pos, right_color = _COLOR_STOPS[index + 1]
        if ratio <= right_pos:
            span = right_pos - left_pos or 1.0
            weight = (ratio - left_pos) / span
            return tuple(int(round(left_color[channel] +
                                   (right_color[channel] - left_color[channel]) * weight))
                         for channel in range(3))
    return _COLOR_STOPS[-1][1]


def depth_to_gray16_mm(depth_map, clip_mm=65535):
    """16-bitowa skala szarości: 1 jednostka = 1 mm (0 = brak danych)."""
    out = bytearray(depth_map.width * depth_map.height * 2)
    for index, value in enumerate(depth_map.data):
        sample = 0
        if value > 0:
            sample = int(round(value * 1000.0))
            if sample > clip_mm:
                sample = clip_mm
        struct.pack_into(">H", out, index * 2, sample)
    return bytes(out)


def depth_to_gray8(depth_map, max_depth=None):
    """8-bitowa skala szarości - głębia znormalizowana (0 = brak danych)."""
    valid = depth_map.valid_values()
    top = max_depth or (valid[-1] if valid else 1.0)
    top = top or 1.0
    out = bytearray(len(depth_map.data))
    for index, value in enumerate(depth_map.data):
        if value > 0:
            out[index] = max(1, min(255, int(round(value / top * 255.0))))
    return bytes(out)


def depth_to_rgb8(depth_map, max_depth=None, gamma=0.5):
    """Kolorowa wizualizacja głębi (niebo/horyzont = czarny)."""
    valid = depth_map.valid_values()
    if max_depth is None:
        # percentyl 98 - odporny na pojedyncze bardzo dalekie piksele
        if valid:
            max_depth = valid[min(len(valid) - 1, int(round(0.98 * (len(valid) - 1))))]
        else:
            max_depth = 1.0
    max_depth = max_depth or 1.0
    out = bytearray(len(depth_map.data) * 3)
    for index, value in enumerate(depth_map.data):
        if value <= 0:
            continue
        red, green, blue = color_for((value / max_depth) ** gamma)
        out[index * 3] = red
        out[index * 3 + 1] = green
        out[index * 3 + 2] = blue
    return bytes(out)


def depth_to_float32(depth_map):
    """Surowy zapis float32 LE (metry) - do dalszego przetwarzania."""
    return struct.pack("<%df" % len(depth_map.data), *depth_map.data)


def load_depth_f32(path, width, height):
    """Wczytuje plik ``depth_f32.bin`` zapisany przez :func:`depth_to_float32`."""
    import os

    if not os.path.exists(path):
        raise IOError("nie znaleziono pliku glebi: %s" % path)
    with open(path, "rb") as handle:
        payload = handle.read()
    count = width * height
    if len(payload) < count * 4:
        raise IOError("plik glebi za krotki: %d B, oczekiwano %d B" % (len(payload), count * 4))
    values = struct.unpack_from("<%df" % count, payload, 0)
    return DepthMap(width, height, list(values), header={"width": width, "height": height},
                    encoding="plik")



def parse_depth(b64_string):
    """Buduje :class:`DepthMap` z ciągu base64 zwróconego przez ``photometa``."""
    payload, encoding = decode_b64_payload(b64_string)
    return parse_depth_bytes(payload, encoding=encoding)
