"""
Generowanie siatki 3D (Mesh: OBJ z teksturami i PLY z kolorami RGB) z map głębi Street View.

Cechy:
* Prawidłowa orientacja ścianek (winding order CCW) i wektorów normalnych skierowanych ku obserwatorowi.
* Brak rozciągania tekstury na szwie sfery (pomijanie połączenia ostatniej kolumny z pierwszą).
* Pełna obsługa materiałów i tekstur w Wavefront OBJ (.obj + .mtl z mapowaniem map_Kd dla każdej panoramy).
* Próbkowanie barw RGB ze zdjęć sferycznych i zapis w binarnym formacie PLY (wierzchołki z kolorami).
"""

import json
import math
import os
import struct
import time

from .pngio import png_to_rgb8, read_png


def _normalize_deg(value):
    return float(value or 0.0) % 360.0


def triangulate_depth_grid(depth_data, width, height, heading_deg=0.0,
                           east=0.0, north=0.0, up=0.0, stride=2,
                           max_depth=50.0, min_depth=0.5, max_edge_ratio=0.18,
                           texture_rgb=None, texture_w=1024, texture_h=512):
    """Generuje wierzchołki, trójkąty, współrzędne UV i kolory RGB dla panoramy.

    Zwraca ``(vertices, faces, uvs, colors)``.
    """
    heading = math.radians(_normalize_deg(heading_deg))
    gw = width // stride
    gh = height // stride

    vertices = []
    uvs = []
    colors = []
    grid = {}

    has_texture = bool(texture_rgb and texture_w and texture_h)

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
            # Współrzędne UV: u od 0 do 1, v od dołu (0) do góry (1)
            uvs.append((u_coord, 1.0 - v_coord))

            if has_texture:
                tx = min(texture_w - 1, max(0, int(u_coord * texture_w)))
                ty = min(texture_h - 1, max(0, int(v_coord * texture_h)))
                pix_idx = (ty * texture_w + tx) * 3
                colors.append((
                    texture_rgb[pix_idx],
                    texture_rgb[pix_idx + 1],
                    texture_rgb[pix_idx + 2]
                ))

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
    # Nie łączymy ostatniej kolumny (gx == gw - 1) z pierwszą, aby zapobiec
    # rozciąganiu tekstury wszerz całego zdjęcia (szew sfery)
    for gy in range(gh - 1):
        for gx in range(gw - 1):
            gx_next = gx + 1
            tl = grid.get((gx, gy))
            tr = grid.get((gx_next, gy))
            bl = grid.get((gx, gy + 1))
            br = grid.get((gx_next, gy + 1))

            # Orientacja CCW patrząc z kamery na powierzchnię:
            # Trójkąt 1: tl -> bl -> tr
            if tl is not None and bl is not None and tr is not None:
                if ok_edge(tl, tr) and ok_edge(tl, bl) and ok_edge(tr, bl):
                    faces.append((tl, bl, tr))

            # Trójkąt 2: tr -> bl -> br
            if tr is not None and bl is not None and br is not None:
                if ok_edge(tr, br) and ok_edge(br, bl) and ok_edge(tr, bl):
                    faces.append((tr, bl, br))

    return vertices, faces, uvs, colors


def compute_vertex_normals(vertices, faces, camera_pos=(0.0, 0.0, 0.0)):
    """Oblicza znormalizowane wektory normalne skierowane ku kamerze."""
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

    cam_x, cam_y, cam_z = camera_pos
    for i, norm in enumerate(normals):
        length = math.sqrt(norm[0] * norm[0] + norm[1] * norm[1] + norm[2] * norm[2])
        if length > 1e-9:
            nx = norm[0] / length
            ny = norm[1] / length
            nz = norm[2] / length
            # Wektor od wierzchołka do kamery
            vx = cam_x - vertices[i][0]
            vy = cam_y - vertices[i][1]
            vz = cam_z - vertices[i][2]
            # Upewniamy się, że normalna wskazuje w stronę kamery (dodatni iloczyn skalarny)
            if (nx * vx + ny * vy + nz * vz) < 0:
                nx, ny, nz = -nx, -ny, -nz
            normals[i] = (nx, ny, nz)
        else:
            normals[i] = (0.0, 0.0, 1.0)

    return normals


def write_mtl(path, materials):
    """Zapisuje plik materiałów MTL definiujący tekstury dla panoram."""
    with open(path, "w", encoding="utf-8") as f:
        f.write("# gsvgrab material definitions\n\n")
        for mat in materials:
            f.write("newmtl %s\n" % mat["name"])
            f.write("Ka 1.000 1.000 1.000\n")
            f.write("Kd 1.000 1.000 1.000\n")
            f.write("Ks 0.000 0.000 0.000\n")
            f.write("Ns 10.000\n")
            f.write("d 1.0\n")
            f.write("illum 1\n")
            if mat.get("texture_path"):
                f.write("map_Kd %s\n" % mat["texture_path"])
            f.write("\n")


def write_obj(path, vertices, groups, normals=None, uvs=None, mtl_filename=None, comments=()):
    """Zapisuje siatkę OBJ z grupami, materiałami MTL, wektorami normalnymi i UV."""
    with open(path, "w", encoding="utf-8") as f:
        for c in comments:
            f.write("# %s\n" % c)
        f.write("# Wygenerowano przez gsvgrab\n")

        if mtl_filename:
            f.write("mtllib %s\n\n" % mtl_filename)

        # 1. Zapis geometrii wierzchołków
        for v in vertices:
            f.write("v %.4f %.4f %.4f\n" % (v[0], v[1], v[2]))

        # 2. Zapis współrzędnych tekstury
        if uvs and len(uvs) == len(vertices):
            for uv in uvs:
                f.write("vt %.4f %.4f\n" % (uv[0], uv[1]))

        # 3. Zapis wektorów normalnych
        if normals and len(normals) == len(vertices):
            for n in normals:
                f.write("vn %.4f %.4f %.4f\n" % (n[0], n[1], n[2]))

        has_vt = bool(uvs and len(uvs) == len(vertices))
        has_vn = bool(normals and len(normals) == len(vertices))

        # 4. Zapis grup i ścianek (faces)
        for group in groups:
            f.write("\ng %s\n" % group["name"])
            if group.get("material"):
                f.write("usemtl %s\n" % group["material"])
            f.write("s 1\n")

            for i0, i1, i2 in group["faces"]:
                # Indeksy w OBJ są 1-based
                if has_vt and has_vn:
                    f.write("f %d/%d/%d %d/%d/%d %d/%d/%d\n" % (
                        i0 + 1, i0 + 1, i0 + 1,
                        i1 + 1, i1 + 1, i1 + 1,
                        i2 + 1, i2 + 1, i2 + 1))
                elif has_vn:
                    f.write("f %d//%d %d//%d %d//%d\n" % (
                        i0 + 1, i0 + 1,
                        i1 + 1, i1 + 1,
                        i2 + 1, i2 + 1))
                else:
                    f.write("f %d %d %d\n" % (i0 + 1, i1 + 1, i2 + 1))


def write_ply_mesh(path, vertices, faces, normals=None, colors=None, comments=(), binary=True):
    """Zapisuje siatkę w formacie PLY (wierzchołki z wektorami normalnymi i kolorami RGB + trójkąty)."""
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
    if colors and len(colors) == len(vertices):
        header.append("property uchar red")
        header.append("property uchar green")
        header.append("property uchar blue")
    header.append("element face %d" % len(faces))
    header.append("property list uchar int vertex_indices")
    header.append("end_header\n")

    header_bytes = "\n".join(header).encode("ascii")

    with open(path, "wb") as f:
        f.write(header_bytes)
        has_norm = bool(normals and len(normals) == len(vertices))
        has_col = bool(colors and len(colors) == len(vertices))

        if binary:
            buf = bytearray()
            for i, v in enumerate(vertices):
                nx, ny, nz = normals[i] if has_norm else (0.0, 0.0, 1.0)
                if has_norm and has_col:
                    r, g, b = colors[i]
                    buf += struct.pack("<ffffffBBB", v[0], v[1], v[2], nx, ny, nz, r, g, b)
                elif has_norm:
                    buf += struct.pack("<ffffff", v[0], v[1], v[2], nx, ny, nz)
                elif has_col:
                    r, g, b = colors[i]
                    buf += struct.pack("<fffBBB", v[0], v[1], v[2], r, g, b)
                else:
                    buf += struct.pack("<fff", v[0], v[1], v[2])

                if len(buf) > 65536:
                    f.write(buf)
                    buf = bytearray()
            if buf:
                f.write(buf)

            # Zapis trójkątów: uchar(3) + 3 * int32
            f_buf = bytearray()
            for i0, i1, i2 in faces:
                f_buf += struct.pack("<Biii", 3, i0, i1, i2)
                if len(f_buf) > 65536:
                    f.write(f_buf)
                    f_buf = bytearray()
            if f_buf:
                f.write(f_buf)
        else:
            for i, v in enumerate(vertices):
                line = "%.4f %.4f %.4f" % (v[0], v[1], v[2])
                if has_norm:
                    line += " %.4f %.4f %.4f" % normals[i]
                if has_col:
                    line += " %d %d %d" % colors[i]
                f.write((line + "\n").encode("ascii"))
            for i0, i1, i2 in faces:
                f.write(("3 %d %d %d\n" % (i0, i1, i2)).encode("ascii"))


def build_route_mesh(route_dir, out_obj=None, out_ply=None, stride=2,
                     max_depth=50.0, max_edge_ratio=0.18, calculate_normals=True):
    """Buduje spójną, oteksturowaną siatkę 3D dla całej trasy panoram."""
    chmura_json = os.path.join(route_dir, "chmura_sklejona.json")
    if not os.path.exists(chmura_json):
        raise FileNotFoundError("Brak pliku %s" % chmura_json)

    with open(chmura_json, "r", encoding="utf-8") as f:
        meta = json.load(f)

    panoramas = meta.get("per_panorama", [])
    if not panoramas:
        raise ValueError("Brak panoram w %s" % chmura_json)

    print("Generowanie siatki 3D (z prawidłowymi normalnymi i teksturami) dla %d panoram..." % len(panoramas))
    start_time = time.time()

    all_vertices = []
    all_uvs = []
    all_colors = []
    all_normals = []
    all_groups = []
    materials = []
    width, height = 512, 256
    per_pano_stats = []

    out_obj = out_obj or os.path.join(route_dir, "mesh_trasa.obj")
    out_ply = out_ply or os.path.join(route_dir, "mesh_trasa.ply")
    mtl_filename = os.path.splitext(os.path.basename(out_obj))[0] + ".mtl"
    out_mtl = os.path.join(os.path.dirname(out_obj), mtl_filename)

    for pano in panoramas:
        pid = pano["panoid"]
        east = pano.get("east_m", 0.0)
        north = pano.get("north_m", 0.0)
        up = pano.get("up_m", 0.0)

        pano_dir = os.path.join(route_dir, "panoramy", pid)
        bin_path = os.path.join(pano_dir, "depth", "depth_f32.bin")
        meta_path = os.path.join(pano_dir, "metadata.json")

        if not os.path.exists(bin_path) or not os.path.exists(meta_path):
            print("  [pomijam] %s (brak danych binarnych)" % pid)
            continue

        with open(bin_path, "rb") as bf:
            depth_data = struct.unpack(f"<{width * height}f", bf.read())

        with open(meta_path, "r", encoding="utf-8") as mf:
            p_meta = json.load(mf)

        heading = float(p_meta.get("heading_deg") or 0.0)

        # Wczytanie tekstury panoramy (do próbkowania kolorów i materiału MTL)
        tex_path = os.path.join(pano_dir, "pano_equirect.png")
        tex_rgb = None
        tex_w, tex_h = 1024, 512
        rel_tex_path = None
        if os.path.exists(tex_path):
            try:
                tex_img = read_png(tex_path)
                tex_rgb = png_to_rgb8(tex_img)
                tex_w, tex_h = tex_img["width"], tex_img["height"]
                rel_tex_path = os.path.relpath(tex_path, os.path.dirname(out_obj))
            except Exception as e:
                print("  (ostrzeżenie: błąd odczytu tekstury %s: %s)" % (tex_path, e))

        mat_name = "Mat_%s" % pid
        materials.append({
            "name": mat_name,
            "texture_path": rel_tex_path
        })

        v_offset = len(all_vertices)
        verts, faces, uvs, colors = triangulate_depth_grid(
            depth_data, width, height, heading_deg=heading,
            east=east, north=north, up=up, stride=stride,
            max_depth=max_depth, max_edge_ratio=max_edge_ratio,
            texture_rgb=tex_rgb, texture_w=tex_w, texture_h=tex_h)

        # Obliczenie wektorów normalnych dla danej panoramy zorientowanych ku jej kamerze
        pano_normals = compute_vertex_normals(verts, faces, camera_pos=(east, north, up))

        for v in verts:
            all_vertices.append(v)
        for uv in uvs:
            all_uvs.append(uv)
        for c in colors:
            all_colors.append(c)
        for n in pano_normals:
            all_normals.append(n)

        # Przesunięcie indeksów ścianek do bufora globalnego
        global_faces = [(i0 + v_offset, i1 + v_offset, i2 + v_offset) for i0, i1, i2 in faces]

        all_groups.append({
            "name": "Pano_%s" % pid,
            "material": mat_name,
            "faces": global_faces
        })

        per_pano_stats.append({
            "panoid": pid,
            "vertices": len(verts),
            "faces": len(faces),
            "has_texture": bool(tex_rgb is not None)
        })
        print("  -> %s: %d wierzcholkow, %d trojkatow, tekstura: %s"
              % (pid, len(verts), len(faces), "TAK" if tex_rgb is not None else "BRAK"))

    total_faces_count = sum(len(g["faces"]) for g in all_groups)

    comments = [
        "gsvgrab 3D textured mesh model",
        "Zrodlo: %s" % route_dir,
        "Panoram: %d" % len(panoramas),
        "Wierzcholkow: %d, Trojkatow: %d" % (len(all_vertices), total_faces_count),
        "Uklad: ENU (X=wschod, Y=polnoc, Z=w gore)",
        "Jednostki: metry",
    ]

    print("Zapisywanie biblioteki materiałów MTL: %s..." % out_mtl)
    write_mtl(out_mtl, materials)

    print("Zapisywanie siatki OBJ: %s..." % out_obj)
    write_obj(out_obj, all_vertices, all_groups, normals=all_normals, uvs=all_uvs,
              mtl_filename=mtl_filename, comments=comments)

    all_faces_flat = []
    for g in all_groups:
        all_faces_flat.extend(g["faces"])

    print("Zapisywanie kolorowej siatki PLY: %s..." % out_ply)
    write_ply_mesh(out_ply, all_vertices, all_faces_flat, normals=all_normals,
                   colors=all_colors if len(all_colors) == len(all_vertices) else None,
                   comments=comments, binary=True)

    elapsed = round(time.time() - start_time, 2)
    stats = {
        "panoramas": len(panoramas),
        "vertices": len(all_vertices),
        "faces": total_faces_count,
        "seconds": elapsed,
        "files": {
            "obj": out_obj,
            "mtl": out_mtl,
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

    print("Gotowe w %.2f s! Trójkątów: %d, Wierzchołków: %d" % (elapsed, total_faces_count, len(all_vertices)))
    return stats
