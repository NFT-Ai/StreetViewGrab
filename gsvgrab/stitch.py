"""
Sklejanie panoramy z kafli 512x512 px w jedno zdjęcie equirectangularne.

Pobieranie kafli nie wymaga klucza API. Do zamiany JPEG na piksele używamy
Pillow, jeśli jest zainstalowany, w przeciwnym razie wbudowanego w macOS
narzędzia ``sips`` (konwersja JPEG -> PNG, a PNG czytamy własnym kodem).
Dzięki temu aplikacja działa bez żadnych zależności.
"""

import os
import shutil
import subprocess
import tempfile

from .pngio import RgbCanvas, png_to_rgb8, read_png

#: Maksymalny rozmiar płótna (w pikselach RGB), powyżej którego wymagamy --force.
MAX_CANVAS_PIXELS = 40_000_000


def jpeg_backend():
    """Zwraca nazwę dostępnego backendu dekodowania JPEG albo ``None``."""
    try:
        import PIL  # noqa: F401
        return "pillow"
    except ImportError:
        pass
    if shutil.which("sips"):
        return "sips"
    return None


def _jpeg_to_rgb_via_pillow(jpeg_bytes):
    import io

    from PIL import Image  # type: ignore

    with Image.open(io.BytesIO(jpeg_bytes)) as image:
        image = image.convert("RGB")
        return image.width, image.height, image.tobytes()


def _jpeg_to_rgb_via_sips(jpeg_bytes, workdir):
    """Konwersja przez ``sips`` + własny czytnik PNG."""
    if workdir is None:
        workdir = tempfile.mkdtemp(prefix="gsvgrab-")
    source = os.path.join(workdir, "in.jpg")
    target = os.path.join(workdir, "out.png")
    with open(source, "wb") as handle:
        handle.write(jpeg_bytes)
    subprocess.run(["sips", "-s", "format", "png", "-o", target, source],
                   check=True, capture_output=True)
    image = read_png(target)
    return image["width"], image["height"], png_to_rgb8(image)


def jpeg_to_rgb8(jpeg_bytes, workdir=None):
    """Zwraca ``(szerokość, wysokość, bajty RGB8)`` dla danych JPEG."""
    backend = jpeg_backend()
    if backend == "pillow":
        return _jpeg_to_rgb_via_pillow(jpeg_bytes)
    if backend == "sips":
        return _jpeg_to_rgb_via_sips(jpeg_bytes, workdir)
    raise RuntimeError(
        "Brak dekodera JPEG: zainstaluj Pillow (pip install Pillow) "
        "albo uzyj macOS z narzedziem 'sips'. Same kafle .jpg sa zapisywane mimo to.")


def download_tiles(api, panoid, zoom, tiles_dir, use_cache=True, on_progress=None):
    """Pobiera wszystkie kafle danego zoomu. Zwraca statystyki."""
    from .api import tile_grid

    cols, rows = tile_grid(zoom)
    os.makedirs(tiles_dir, exist_ok=True)
    stats = {"tiles_total": cols * rows, "tiles_blank": 0, "bytes": 0, "files": []}
    for y in range(rows):
        for x in range(cols):
            path = os.path.join(tiles_dir, "%d_%d.jpg" % (x, y))
            if os.path.exists(path) and os.path.getsize(path) > 0:
                with open(path, "rb") as handle:
                    payload = handle.read()
                blank = len(payload) <= 1200
            else:
                payload, blank = api.download_tile(panoid, x, y, zoom, use_cache=use_cache)
                with open(path, "wb") as handle:
                    handle.write(payload)
            stats["bytes"] += len(payload)
            if blank:
                stats["tiles_blank"] += 1
                continue
            stats["files"].append((x, y, path))
            if on_progress:
                on_progress(x, y, cols, rows)
    return stats


def stitch_equirect(api, panoid, zoom, tiles_dir, out_png=None, force=False,
                    use_cache=True, on_progress=None):
    """Skleja panoramę i (opcjonalnie) zapisuje ją jako PNG.

    Zwraca ``(statystyki, płótno)``. ``płótno`` jest ``None``, gdy brak dekodera JPEG.
    """
    stats = download_tiles(api, panoid, zoom, tiles_dir, use_cache=use_cache,
                           on_progress=on_progress)
    from .api import tile_grid

    cols, rows = tile_grid(zoom)
    width, height = cols * 512, rows * 512
    stats.update({"zoom": zoom, "width": width, "height": height,
                  "tiles_used": len(stats["files"])})
    if not force and width * height > MAX_CANVAS_PIXELS:
        stats["skipped"] = ("uzyj --force, aby skleic obraz %dx%d (%d Mpx)"
                            % (width, height, width * height // 1_000_000))
        return stats, None

    if jpeg_backend() is None:
        stats["skipped"] = ("brak dekodera JPEG (Pillow lub sips) - kafle zapisane w %s"
                            % tiles_dir)
        return stats, None

    canvas = RgbCanvas(width, height)
    tmpdir = tempfile.mkdtemp(prefix="gsvgrab-")
    try:
        for x, y, path in stats["files"]:
            with open(path, "rb") as handle:
                jpeg_bytes = handle.read()
            tile_width, tile_height, rgb = jpeg_to_rgb8(jpeg_bytes, workdir=tmpdir)
            canvas.paste_rgb(rgb, tile_width, tile_height, x * tile_width, y * tile_height)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    stats["mean_rgb"] = [round(value, 1) for value in canvas.mean_rgb()]
    if out_png:
        from .pngio import write_png

        stats["png_bytes"] = write_png(out_png, width, height, canvas.to_bytes(),
                                       channels=3, bitdepth=8)
    return stats, canvas
