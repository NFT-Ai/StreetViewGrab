"""
Zamiana mapy głębi panoramy na chmurę punktów 3D (PLY).

Piksel ``(x, y)`` mapy głębi odpowiada kierunkowi na sferze:

* ``theta = (h - y - 0.5) / h * pi``  - kąt od zenitu,
* ``phi = (w - x - 0.5) / w * 2*pi + pi/2``.

Punkt to ``(sin(theta)cos(phi), sin(theta)sin(phi), cos(theta)) * glebia``
w lokalnym układzie kamery, gdzie ``Z`` wskazuje górę, a środek panoramy
(``x = w/2``) leży na osi ``+Y``. Jednostki: metry.

Opcjonalnie obracamy chmurę o kąt ``yaw_deg`` wokół osi ``Z``, np. o nagłówek
panoramy, aby przybliżyć orientację względem północy.
"""

import math
import struct

from .pngio import png_to_rgb8, read_png


def depth_to_points(depth_map, stride=1, min_depth=None, max_depth=None, yaw_deg=0.0):
    """Zwraca listę ``(x, y, z, glebia, px, py)`` dla pikseli z poprawną głębią."""
    width = depth_map.width
    height = depth_map.height
    yaw = math.radians(yaw_deg or 0.0)
    cos_yaw = math.cos(yaw)
    sin_yaw = math.sin(yaw)
    points = []
    for y in range(0, height, stride):
        theta = (height - y - 0.5) / height * math.pi
        sin_theta = math.sin(theta)
        cos_theta = math.cos(theta)
        row = y * width
        for x in range(0, width, stride):
            depth = depth_map.data[row + x]
            if depth <= 0:
                continue
            if min_depth is not None and depth < min_depth:
                continue
            if max_depth is not None and depth > max_depth:
                continue
            # Mapa głębi jest zapisana z odbiciem lustrzanym względem kierunków promieni
            # (dekoder zapisuje wartość z promienia ``x`` w kolumnie ``w-1-x``), dlatego
            # dla kolumny ``x`` kierunek liczymy jak dla promienia ``w-1-x``.
            phi = (x + 0.5) / width * 2.0 * math.pi + math.pi / 2.0
            direction_x = sin_theta * math.cos(phi)
            direction_y = sin_theta * math.sin(phi)
            direction_z = cos_theta
            px = direction_x * depth
            py = direction_y * depth
            pz = direction_z * depth
            if yaw:
                rotated_x = px * cos_yaw - py * sin_yaw
                rotated_y = px * sin_yaw + py * cos_yaw
                px, py = rotated_x, rotated_y
            points.append((px, py, pz, depth, x, y))
    return points


def sample_colors(points, image_path, image_size=None):
    """Zwraca kolory RGB dla punktów na podstawie obrazu panoramy (PNG)."""
    image = read_png(image_path)
    rgb = png_to_rgb8(image)
    width = image["width"]
    height = image["height"]
    scale_x = width / image_size[0] if image_size else 1.0
    scale_y = height / image_size[1] if image_size else 1.0
    colors = []
    for _, _, _, _, px, py in points:
        sx = min(width - 1, max(0, int(px * scale_x)))
        sy = min(height - 1, max(0, int(py * scale_y)))
        index = (sy * width + sx) * 3
        colors.append((rgb[index], rgb[index + 1], rgb[index + 2]))
    return colors


def write_ply(path, points, colors=None, comments=(), binary=True):
    """Zapisuje chmurę punktów w formacie PLY (binarnym lub ASCII)."""
    has_color = colors is not None and len(colors) == len(points)
    header = ["ply"]
    header.append("format binary_little_endian 1.0" if binary else "format ascii 1.0")
    for comment in comments:
        header.append("comment %s" % comment)
    header.append("element vertex %d" % len(points))
    header.append("property float x")
    header.append("property float y")
    header.append("property float z")
    header.append("property float depth_m")
    if has_color:
        header.append("property uchar red")
        header.append("property uchar green")
        header.append("property uchar blue")
    header.append("end_header")
    text = "\n".join(header) + "\n"
    with open(path, "wb") as handle:
        handle.write(text.encode("ascii", "replace"))
        if binary:
            body = bytearray()
            for index, point in enumerate(points):
                body += struct.pack("<ffff", point[0], point[1], point[2], point[3])
                if has_color:
                    red, green, blue = colors[index]
                    body += bytes((red & 0xFF, green & 0xFF, blue & 0xFF))
            handle.write(bytes(body))
        else:
            lines = []
            for index, point in enumerate(points):
                line = "%.4f %.4f %.4f %.4f" % (point[0], point[1], point[2], point[3])
                if has_color:
                    line += " %d %d %d" % colors[index]
                lines.append(line)
            handle.write(("\n".join(lines) + "\n").encode("ascii"))
    return len(points)


def build_pointcloud(depth_map, out_path, stride=1, max_depth=None, min_depth=None,
                     color_image=None, yaw_deg=0.0, comments=(), binary=True):
    """Buduje i zapisuje chmurę punktów. Zwraca statystyki."""
    points = depth_to_points(depth_map, stride=stride, min_depth=min_depth,
                             max_depth=max_depth, yaw_deg=yaw_deg)
    colors = None
    if color_image and points:
        colors = sample_colors(points, color_image,
                               image_size=(depth_map.width, depth_map.height))
    count = write_ply(out_path, points, colors=colors, comments=comments, binary=binary)
    return {"points": count, "colored": colors is not None, "stride": stride,
            "yaw_deg": yaw_deg, "path": out_path}
