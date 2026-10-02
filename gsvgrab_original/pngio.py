"""
Minimalny czytnik i zapis PNG (tylko biblioteka standardowa).

Obsługiwane przy zapisie: skala szarości 8/16 bit, RGB 8 bit.
Przy odczycie: 8/16 bit oraz typy koloru 0, 2, 4, 6, wszystkie filtry (0-4),
obrazy nieprzeplatane. Wystarcza to do kafli Street View (RGB8) i map głębi (16 bit).
"""

import struct
import zlib

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

COLOR_TYPE_CHANNELS = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}
CHANNELS_COLOR_TYPE = {1: 0, 3: 2, 4: 6}


def _chunk(tag, payload):
    return (struct.pack(">I", len(payload)) + tag + payload
            + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF))


def write_png(path, width, height, pixels, channels=1, bitdepth=8):
    """Zapisuje plik PNG. ``pixels`` to ciąg bajtów wiersz po wierszu.

    Dla ``bitdepth=16`` dane muszą być w kolejności big-endian (tak jak w PNG).
    """
    if channels not in CHANNELS_COLOR_TYPE:
        raise ValueError("obslugiwane liczby kanalow: 1, 3, 4")
    if bitdepth not in (8, 16):
        raise ValueError("obslugiwane glebokosci bitowe: 8, 16")
    color_type = CHANNELS_COLOR_TYPE[channels]
    bytes_per_pixel = channels * (bitdepth // 8)
    stride = width * bytes_per_pixel
    if len(pixels) != stride * height:
        raise ValueError("zly rozmiar danych: %d B, oczekiwano %d B"
                         % (len(pixels), stride * height))
    raw = bytearray()
    for row in range(height):
        raw.append(0)  # filtr "None"
        raw += pixels[row * stride:(row + 1) * stride]
    header = struct.pack(">IIBBBBB", width, height, bitdepth, color_type, 0, 0, 0)
    payload = (PNG_SIGNATURE + _chunk(b"IHDR", header)
               + _chunk(b"IDAT", zlib.compress(bytes(raw), 6))
               + _chunk(b"IEND", b""))
    with open(path, "wb") as handle:
        handle.write(payload)
    return len(payload)


def _paeth(left, up, upper_left):
    estimate = left + up - upper_left
    distance_left = abs(estimate - left)
    distance_up = abs(estimate - up)
    distance_upper_left = abs(estimate - upper_left)
    if distance_left <= distance_up and distance_left <= distance_upper_left:
        return left
    if distance_up <= distance_upper_left:
        return up
    return upper_left


def _unfilter(raw, width, height, stride, bytes_per_pixel):
    out = bytearray(stride * height)
    previous = bytearray(stride)
    position = 0
    for row in range(height):
        filter_type = raw[position]
        position += 1
        line = bytearray(raw[position:position + stride])
        position += stride
        if filter_type == 1:
            for index in range(bytes_per_pixel, stride):
                line[index] = (line[index] + line[index - bytes_per_pixel]) & 0xFF
        elif filter_type == 2:
            for index in range(stride):
                line[index] = (line[index] + previous[index]) & 0xFF
        elif filter_type == 3:
            for index in range(stride):
                left = line[index - bytes_per_pixel] if index >= bytes_per_pixel else 0
                line[index] = (line[index] + ((left + previous[index]) >> 1)) & 0xFF
        elif filter_type == 4:
            for index in range(stride):
                left = line[index - bytes_per_pixel] if index >= bytes_per_pixel else 0
                upper_left = previous[index - bytes_per_pixel] \
                    if index >= bytes_per_pixel else 0
                line[index] = (line[index] + _paeth(left, previous[index], upper_left)) & 0xFF
        elif filter_type != 0:
            raise ValueError("nieznany filtr PNG: %d" % filter_type)
        out[row * stride:(row + 1) * stride] = line
        previous = line
    return bytes(out)



def read_png(path):
    """Wczytuje PNG i zwraca słownik z polami ``width``, ``height``, ``bitdepth``,
    ``color_type``, ``channels``, ``stride`` oraz ``data`` (wiersz po wierszu)."""
    with open(path, "rb") as handle:
        payload = handle.read()
    if payload[:8] != PNG_SIGNATURE:
        raise ValueError("to nie jest plik PNG: %s" % path)
    position = 8
    header = None
    idat = bytearray()
    while position < len(payload):
        length = struct.unpack_from(">I", payload, position)[0]
        tag = payload[position + 4:position + 8]
        body = payload[position + 8:position + 8 + length]
        position += 12 + length
        if tag == b"IHDR":
            header = struct.unpack(">IIBBBBB", body)
        elif tag == b"IDAT":
            idat += body
        elif tag == b"IEND":
            break
    if header is None:
        raise ValueError("brak naglowka IHDR w %s" % path)
    width, height, bitdepth, color_type, _, _, interlace = header
    if interlace:
        raise ValueError("obrazy przeplatane (interlaced) nie sa obslugiwane")
    if color_type not in COLOR_TYPE_CHANNELS:
        raise ValueError("nieobslugiwany typ koloru PNG: %d" % color_type)
    channels = COLOR_TYPE_CHANNELS[color_type]
    bytes_per_pixel = max(1, channels * (bitdepth // 8))
    stride = (width * channels * bitdepth + 7) // 8
    raw = zlib.decompress(bytes(idat))
    data = _unfilter(raw, width, height, stride, bytes_per_pixel)
    return {"width": width, "height": height, "bitdepth": bitdepth,
            "color_type": color_type, "channels": channels, "data": data, "stride": stride}


def png_to_rgb8(image):
    """Zamienia wczytany PNG na bajty RGB (8 bit na kanał)."""
    width = image["width"]
    height = image["height"]
    channels = image["channels"]
    bitdepth = image["bitdepth"]
    data = image["data"]
    out = bytearray(width * height * 3)
    if bitdepth == 8:
        for index in range(width * height):
            source = index * channels
            target = index * 3
            if channels <= 2:
                gray = data[source]
                out[target] = out[target + 1] = out[target + 2] = gray
            else:
                out[target] = data[source]
                out[target + 1] = data[source + 1]
                out[target + 2] = data[source + 2]
    else:  # 16 bit - bierzemy starszy bajt
        step = channels * 2
        for index in range(width * height):
            source = index * step
            target = index * 3
            if channels <= 2:
                gray = data[source]
                out[target] = out[target + 1] = out[target + 2] = gray
            else:
                out[target] = data[source]
                out[target + 1] = data[source + 2]
                out[target + 2] = data[source + 4]
    return bytes(out)


class RgbCanvas:
    """Płótno RGB8 do sklejania kafli panoramy."""

    def __init__(self, width, height, background=(128, 128, 128)):
        self.width = width
        self.height = height
        self.data = bytearray(bytes(background) * (width * height))

    def paste_rgb(self, tile, tile_width, tile_height, offset_x, offset_y):
        """Wkleja kafel RGB w podane miejsce (przycięte do rozmiaru płótna)."""
        for row in range(tile_height):
            target_y = offset_y + row
            if not (0 <= target_y < self.height):
                continue
            copy_width = min(tile_width, self.width - offset_x)
            if copy_width <= 0:
                continue
            source_start = row * tile_width * 3
            target_start = (target_y * self.width + offset_x) * 3
            self.data[target_start:target_start + copy_width * 3] = \
                tile[source_start:source_start + copy_width * 3]

    def to_bytes(self):
        return bytes(self.data)

    def mean_rgb(self):
        """Średnia jasność kanałów - szybka kontrola, czy obraz nie jest pusty."""
        total = len(self.data)
        if not total:
            return (0.0, 0.0, 0.0)
        step = max(1, total // 30000)
        red = green = blue = 0
        count = 0
        for index in range(0, total - 2, 3 * step):
            red += self.data[index]
            green += self.data[index + 1]
            blue += self.data[index + 2]
            count += 1
        return (red / count, green / count, blue / count)
