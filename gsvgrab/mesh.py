"""
Generowanie siatki 3D (Mesh: OBJ i PLY z trójkątami) z map głębi Street View.

Każda panorama z mapą głębi stanowi siatkę sferyczną (zwykle 512x256 px).
Sąsiednie piksele siatki o zbliżonej głębi tworzą powierzchnie ciągłe
(jezdnie, chodniki, fasady budynków, ogrodzenia). Skok odległości oznacza
krawędź obiektu lub przerwę w widoczności – wówczas trójkąt nie jest tworzony.

Dzięki temu otrzymujemy czysty model 3D bez artefaktów rozciągania na krawędziach.
"""

import json
import math
import os
import struct
import time


def _normalize_deg(value):
    return float(value or 0.0) % 360.0


def triangulate_depth_grid(depth_data, width, height, heading_deg=0.0,
                           east=0.0, north=0.0, up=0.0, stride=2,
                           max_depth=50.0, min_depth=0.5, max_edge_ratio=0.18):
    """Generuje wierzchołki i trójkąty dla pojedynczej panoramy.

    Zwraca ``(vertices, faces, uvs)``, gdzie:
    * ``vertices``: lista ``(x, y, z, depth)`` w metrycznym układzie ENU,
    * ``faces``: lista indeksów trójkątów ``(i0, i1, i2)``,
    * ``uvs``: współrzędne tekstury ``(u, v)`` w zakresie 0..1 dla każdego wierzchołka.
    """
    heading = math.radians(_normalize_deg(heading_deg))
    gw = width // stride
    gh = height // stride

    vertices = []
    uvs = []
    grid = {}

    for gy in range(gh):
        y = gy * stride
        theta = (height - y - 0.5) / height * math.pi
        sin_theta = math.sin(theta)
        cos_theta = math.cos(theta)
        row = y * width
        v_coord = y / float(height)

        for gx in range(gw):
            x = gx * stride
            d = depth_data[row + x]
            if d <= min_depth or d > max_depth:
                continue

            phi = (x + 0.5) / width * 2.0 * math.pi + math.pi / 2.0
            compass = heading + (phi - 1.5 * math.pi)
            horizontal = d * sin_theta

            vx = east + horizontal * math.sin(compass)
            vy = north + horizontal * math.cos(compass)
            vz = up - d * cos_theta
            u_coord = x / float(width)

            idx = len(vertices)
            vertices.append((vx, vy, vz, d))
            uvs.append((u_coord, 1.0 - v_coord))
            grid[(gx, gy)] = idx

    def dist_sq(i1, i2):
        p1 = vertices[i1]
        p2 = vertices[i2]
        return (p1[0] - p2[0]) ** 2 + (p1[1] - p2[1]) ** 2 + (p1[2] - p2[2]) ** 2

    def ok_edge(i1, i2):
        d1 = vertices[i1][3]
        d2 = vertices[i2][3]
        threshold = max(0.45, min(d1, d2) * max_edge_ratio)
        return dist_sq(i1, i2) < (threshold * threshold)

    faces = []
    for gy in range(gh - 1):
        for gx in range(gw):
            gx_next = (gx + 1) % gw
            tl = grid.get((gx, gy))
            tr = grid.get((gx_next, gy))
            bl = grid.get((gx, gy + 1))
            br = grid.get((gx_next, gy + 1))

            # Trójkąt 1: górny-lewy, prawy, dolny-lewy
            if tl is not None and tr is not None and bl is not None:
                if ok_edge(tl, tr) and ok_edge(tl, bl) and ok_edge(tr, bl):
                    faces.append((tl, tr, bl))

            # Trójkąt 2: górny-prawy, dolny-prawy, dolny-lewy
            if tr is not None and br is not None and bl is not None:
                if ok_edge(tr, br) and ok_edge(br, bl) and ok_edge(tr, bl):
                    faces.append((tr, br, bl))

    return vertices, faces, uvs


def compute_vertex_normals(vertices, faces):
    """Oblicza znormalizowane wektory normalne dla każdego wierzchołka."""
    normals = [[0.0, 0.0, 0.0] for _ in range(len(vertices))]

    for i0, i1, i2 in faces:
        p0 = vertices[i0]
        p1 = vertices[i1]
        p2 = vertices[i2]

        ax = p1[0] - p0[0]
        ay = p1[1] - p0[1]
        az = p1[2] - p0[2]

        bx = p2[0] - p0[0]
        by = p2[1] - p0[1]
        bz = p2[2] - p0[2]

        nx = ay * bz - az * by
        ny = az * bx - ax * bz
        nz = ax * by - ay * bx

        length = math.sqrt(nx * nx + ny * ny + nz * nz)
        if length > 1e-9:
            inv = 1.0 / length
            nx *= inv
            ny *= inv
            nz *= inv
            for i in (i0, i1, i2):
                normals[i][0] += nx
                normals[i][1] += ny
                normals[i][2] += nz

    for i, norm in enumerate(normals):
        length = math.sqrt(norm[0] * norm[0] + norm[1] * norm[1] + norm[2] * norm[2])
        if length > 1e-9:
            inv = 1.0 / length
            normals[i] = (norm[0] * inv, norm[1] * inv, norm[2] * inv)
        else:
            normals[i] = (0.0, 0.0, 1.0)

    return normals


def write_obj(path, vertices, faces, normals=None, uvs=None, comments=()):
    """Zapisuje siatkę w uniwersalnym formacie Wavefront OBJ."""
    with open(path, "w", encoding="utf-8") as f:
        for c in comments:
            f.write("# %s\n" % c)
        f.write("# Wygenerowano przez gsvgrab\n")

        for v in vertices:
            f.write("v %.4f %.4f %.4f\n" % (v[0], v[1], v[2]))

        if uvs and len(uvs) == len(vertices):
            for uv in uvs:
                f.write("vt %.4f %.4f\n" % (uv[0], uv[1]))

        if normals and len(normals) == len(vertices):
            for n in normals:
                f.write("vn %.4f %.4f %.4f\n" % (n[0], n[1], n[2]))

        has_vt = bool(uvs and len(uvs) == len(vertices))
        has_vn = bool(normals and len(normals) == len(vertices))

        if has_vt and has_vn:
            for i0, i1, i2 in faces:
                # W OBJ indeksy są 1-based
                f.write("f %d/%d/%d %d/%d/%d %d/%d/%d\n" % (
                    i0 + 1, i0 + 1, i0 + 1,
                    i1 + 1, i1 + 1, i1 + 1,
                    i2 + 1, i2 + 1, i2 + 1))
        elif has_vn:
            for i0, i1, i2 in faces:
                f.write("f %d//%d %d//%d %d//%d\n" % (
                    i0 + 1, i0 + 1,
                    i1 + 1, i1 + 1,
                    i2 + 1, i2 + 1))
        else:
            for i0, i1, i2 in faces:
                f.write("f %d %d %d\n" % (i0 + 1, i1 + 1, i2 + 1))


def write_ply_mesh(path, vertices, faces, normals=None, comments=(), binary=True):
    """Zapisuje siatkę w formacie PLY (wierzchołki + wielokąty / faces)."""
    header = [
        "ply",
        "format binary_little_endian 1.0" if binary else "format ascii 1.0",
    ]
    for c in comments:
        header.append("comment %s" % c)
    header.append("element vertex %d" % len(vertices))
    header.append("property float x")
    header.append("property float y")
    header.append("property float z")
    if normals and len(normals) == len(vertices):
        header.append("property float nx")
        header.append("property float ny")
        header.append("property float nz")
    header.append("element face %d" % len(faces))
    header.append("property list uchar int vertex_indices")
    header.append("end_header\n")

    header_bytes = "\n".join(header).encode("ascii")

    with open(path, "wb") as f:
        f.write(header_bytes)
        if binary:
            # Zapis wierzchołków
            has_norm = bool(normals and len(normals) == len(vertices))
            if has_norm:
                buf = bytearray()
                for i, v in enumerate(vertices):
                    n = normals[i]
                    buf += struct.pack("<ffffff", v[0], v[1], v[2], n[0], n[1], n[2])
                    if len(buf) > 65536:
                        f.write(buf)
                        buf = bytearray()
                if buf:
                    f.write(buf)
            else:
                buf = bytearray()
                for v in vertices:
                    buf += struct.pack("<fff", v[0], v[1], v[2])
                    if len(buf) > 65536:
                        f.write(buf)
                        buf = bytearray()
                if buf:
                    f.write(buf)

            # Zapis trójkątów: uchar(3) + int(i0) + int(i1) + int(i2) -> 13 bajtów
            f_buf = bytearray()
            for i0, i1, i2 in faces:
                f_buf += struct.pack("<Biii", 3, i0, i1, i2)
                if len(f_buf) > 65536:
                    f.write(f_buf)
                    f_buf = bytearray()
            if f_buf:
                f.write(f_buf)
        else:
            has_norm = bool(normals and len(normals) == len(vertices))
            for i, v in enumerate(vertices):
                if has_norm:
                    n = normals[i]
                    f.write(("%.4f %.4f %.4f %.4f %.4f %.4f\n" % (
                        v[0], v[1], v[2], n[0], n[1], n[2])).encode("ascii"))
                else:
                    f.write(("%.4f %.4f %.4f\n" % (v[0], v[1], v[2])).encode("ascii"))
            for i0, i1, i2 in faces:
                f.write(("3 %d %d %d\n" % (i0, i1, i2)).encode("ascii"))


def build_route_mesh(route_dir, out_obj=None, out_ply=None, stride=2,
                     max_depth=50.0, max_edge_ratio=0.18, calculate_normals=True):
    """Buduje spójną siatkę 3D dla trasy z wieloma panoramami."""
    chmura_json = os.path.join(route_dir, "chmura_sklejona.json")
    if not os.path.exists(chmura_json):
        raise FileNotFoundError("Brak pliku %s" % chmura_json)

    with open(chmura_json, "r", encoding="utf-8") as f:
        meta = json.load(f)

    panoramas = meta.get("per_panorama", [])
    if not panoramas:
        raise ValueError("Brak panoram w %s" % chmura_json)

    print("Generowanie siatki 3D dla %d panoram..." % len(panoramas))
    start_time = time.time()

    all_vertices = []
    all_faces = []
    all_uvs = []
    width, height = 512, 256
    per_pano_stats = []

    for pano in panoramas:
        pid = pano["panoid"]
        east = pano.get("east_m", 0.0)
        north = pano.get("north_m", 0.0)
        up = pano.get("up_m", 0.0)

        bin_path = os.path.join(route_dir, "panoramy", pid, "depth", "depth_f32.bin")
        meta_path = os.path.join(route_dir, "panoramy", pid, "metadata.json")

        if not os.path.exists(bin_path) or not os.path.exists(meta_path):
            print("  [pomijam] %s (brak danych binarnych)" % pid)
            continue

        with open(bin_path, "rb") as bf:
            depth_data = struct.unpack(f"<{width * height}f", bf.read())

        with open(meta_path, "r", encoding="utf-8") as mf:
            p_meta = json.load(mf)

        heading = float(p_meta.get("heading_deg") or 0.0)

        v_offset = len(all_vertices)
        verts, faces, uvs = triangulate_depth_grid(
            depth_data, width, height, heading_deg=heading,
            east=east, north=north, up=up, stride=stride,
            max_depth=max_depth, max_edge_ratio=max_edge_ratio)

        for v in verts:
            all_vertices.append(v)
        for uv in uvs:
            all_uvs.append(uv)
        for i0, i1, i2 in faces:
            all_faces.append((i0 + v_offset, i1 + v_offset, i2 + v_offset))

        per_pano_stats.append({
            "panoid": pid,
            "vertices": len(verts),
            "faces": len(faces)
        })
        print("  -> %s: %d wierzcholkow, %d trojkatow" % (pid, len(verts), len(faces)))

    normals = None
    if calculate_normals and all_faces:
        print("Obliczanie wektorow normalnych...")
        normals = compute_vertex_normals(all_vertices, all_faces)

    comments = [
        "gsvgrab 3D mesh model",
        "Zrodlo: %s" % route_dir,
        "Panoram: %d" % len(panoramas),
        "Wierzcholkow: %d, Trojkatow: %d" % (len(all_vertices), len(all_faces)),
        "Uklad: ENU (X=wschod, Y=polnoc, Z=w gore)",
        "Jednostki: metry",
    ]

    out_obj = out_obj or os.path.join(route_dir, "mesh_trasa.obj")
    out_ply = out_ply or os.path.join(route_dir, "mesh_trasa.ply")

    print("Zapisywanie siatki OBJ: %s..." % out_obj)
    write_obj(out_obj, all_vertices, all_faces, normals=normals, uvs=all_uvs, comments=comments)

    print("Zapisywanie siatki PLY: %s..." % out_ply)
    write_ply_mesh(out_ply, all_vertices, all_faces, normals=normals, comments=comments, binary=True)

    elapsed = round(time.time() - start_time, 2)
    stats = {
        "panoramas": len(panoramas),
        "vertices": len(all_vertices),
        "faces": len(all_faces),
        "seconds": elapsed,
        "files": {
            "obj": out_obj,
            "ply": out_ply
        },
        "bounds": {
            "min_x": round(min(v[0] for v in all_vertices), 2) if all_vertices else 0,
            "max_x": round(max(v[0] for v in all_vertices), 2) if all_vertices else 0,
            "min_y": round(min(v[1] for v in all_vertices), 2) if all_vertices else 0,
            "max_y": round(max(v[1] for v in all_vertices), 2) if all_vertices else 0,
            "min_z": round(min(v[2] for v in all_vertices), 2) if all_vertices else 0,
            "max_z": round(max(v[2] for v in all_vertices), 2) if all_vertices else 0,
        },
        "per_panorama": per_pano_stats
    }

    stats_path = os.path.join(route_dir, "mesh_info.json")
    with open(stats_path, "w", encoding="utf-8") as sf:
        json.dump(stats, sf, indent=2)

    print("Gotowe w %.2f s! Trójkątów: %d, Wierzchołków: %d" % (elapsed, len(all_faces), len(all_vertices)))
    return stats
