# gsvgrab — Google Street View Grabber

Narzędzie wiersza poleceń w języku Python (3.8+) do pobierania panoram **Google Street View** wraz z pełnymi metadanymi, siatką kafli, sklejonymi zdjęciami equirectangularnymi, **mapami głębi (depth maps)** oraz **chmurami punktów 3D (`.ply`)**.

---

## Główne cechy

* **Zero zewnętrznych zależności obowiązkowych** — działa w oparciu o bibliotekę standardową Pythona (`urllib`, `struct`, `zlib`, `math`).
* **Własny enkoder parametrów protobuf URL** ([`protobuf_url.py`](gsvgrab/protobuf_url.py)) — generowanie zapytań do nieoficjalnego API Google (`photometa/v1`) bez zewnętrznych bibliotek proto.
* **Autonomiczny czytnik i zapisywacz PNG** ([`pngio.py`](gsvgrab/pngio.py)) — zapisuje 8-bit RGB oraz **16-bit grayscale** (do reprezentacji głębi z precyzją milimetrową).
* **Dekoder map głębi 3D** ([`depth.py`](gsvgrab/depth.py)) — dekompresja i analityczne wyznaczanie odległości promieni sferycznych z płaszczyznami 3D (`depth = |d / (v · n)|`).
* **Eksport chmury punktów 3D** ([`pointcloud.py`](gsvgrab/pointcloud.py)) — generowanie plików Polygon File Format (`.ply`) w układzie współrzędnych metrycznych (opcjonalnie z próbkowaniem barw RGB).
* **Sklejanie panoram (equirectangular)** ([`stitch.py`](gsvgrab/stitch.py)) — pobieranie kafli 512×512 px i łączenie w panoramę. Na macOS wykorzystuje natywne narzędzie systemowe `sips`, a przy obecności biblioteki `Pillow` automatycznie korzysta z biblioteki PIL.
* **Geokodowanie adresów** ([`geocode.py`](gsvgrab/geocode.py)) — obsługa wyszukiwania po adresie przez darmowe usługi OpenStreetMap Nominatim oraz Komoot Photon (bez klucza API).
* **Statyczna przeglądarka HTML** — automatycznie generowany plik `index.html` z interaktywnym podglądem panoramy i mapy głębi.
* **Pełny zestaw testów offline** ([`selftest.py`](gsvgrab/selftest.py)) — 35 weryfikacji jednostkowych uruchamianych bez połączenia z siecią.

---

## Wymagania i uruchomienie

Wymagany jest jedynie **Python 3.8+**.

### Uruchomienie bezpośrednie (zalecane)

```bash
python3 -m gsvgrab --help
```

### Opcjonalna instalacja jako pakiet CLI

```bash
pip install -e .
gsvgrab --help
```

*(Opcjonalnie można doinstalować `pip install Pillow` dla przyspieszenia sklejania panoram na systemach innych niż macOS).*

---

## Dostępne komendy

### 1. Testy jednostkowe offline (`selftest`)

Weryfikuje dekoder głębi, kodowanie protobuf, geometrię, odczyt/zapis PNG, chmurę punktów i parser metadanych:

```bash
python3 -m gsvgrab selftest
```

### 2. Wyszukiwanie panoram (`find`)

Wyszukuje panoramy wokół zadanego adresu lub współrzędnych:

```bash
# Po adresie:
python3 -m gsvgrab find --address "Piasta 10c, Milanówek"

# Po współrzędnych:
python3 -m gsvgrab find --lat 52.127830 --lon 20.669474 --radius 500

# Z podglądem przejazdu (wzdłuż trasy):
python3 -m gsvgrab find --address "Marszałkowska, Warszawa" --walk --limit 5
```

### 3. Szczegółowe metadane panoramy (`info`)

Wyświetla informacje o konkretnej panoramie (ID, data, współrzędne, sąsiedzi, obecność mapy głębi):

```bash
python3 -m gsvgrab info --pano <PANOID>
```

### 4. Pobieranie danych (`grab`)

Pobiera pełen pakiet danych (kafle, sklejona panorama, głębia, chmura punktów, metadane):

```bash
# Pojedyncza panorama z adresem i chmurą punktów:
python3 -m gsvgrab grab --address "Piasta 10c, Milanówek" --pointcloud --out ./wyniki

# Ciąg powiązanych panoram wzdłuż ulicy (tryb --walk):
python3 -m gsvgrab grab --address "Krakowskie Przedmieście, Warszawa" --max-panos 5 --walk --pointcloud --out ./trasa

# Pobranie konkretnego ID:
python3 -m gsvgrab grab --pano <PANOID> --pointcloud --out ./pano_data
```

---

## Struktura katalogu wyjściowego

Po uruchomieniu komendy `grab`, katalog wynikowy ma postać:

```
<out>/
  manifest.json                 # Zbiorczy opis pobranych panoram
  index.html                    # Przeglądarka HTML (panorama + mapa głębi)
  README.txt                    # Opis zrzutu, współrzędne i atrybucja
  panoramy/<panoid>/
    metadata.json               # Ustrukturyzowane metadane w formacie JSON
    metadata_raw.json           # Surowa odpowiedź photometa Google
    preview.jpg                 # Szybki podgląd (1024x512)
    pano_equirect_z2.png        # Sklejone zdjęcie sferyczne equirectangularne
    tiles/z2/                   # Pojedyncze kafle źródłowe (512x512 JPEG)
      0_0.jpg
      ...
    depth/                      # Dane mapy głębi
      depth_color.png           # Kolorowa wizualizacja (niebieski=blisko, czerwony=daleko)
      depth_16bit.png           # 16-bitowy PNG grayscale (1 jednostka = 1 mm odległości)
      depth_f32.bin             # Surowe wartości float32 LE (odległość w metrach, -1 = niebo)
      depth.json                # Statystyki, percentyle, parametry płaszczyzn 3D
    pointcloud.ply              # Chmura punktów 3D z głębi w formacie PLY (jeśli włączona)
```

---

## Format mapy głębi Street View

Google Street View nie przechowuje prostej mapy piksel-odległość, lecz przybliżenie sceny zbiorem płaszczyzn 3D:
1. Dane binarne zawierają **nagłówek** (wymiary, liczbę płaszczyzn, offset).
2. Siatkę indeksów pikseli wskazujących na daną płaszczyznę (indeks `0` oznacza brak danych / niebo).
3. Tablicę płaszczyzn `(nx, ny, nz, d)`, gdzie `(nx, ny, nz)` to wektor normalny powierzchni, a `d` to jej odległość od środka kamery.
4. Odległość promienia piksela o wektorze kierunkowym `v` wyznaczana jest wzorem:
   $$\text{depth} = \left|\frac{d}{v \cdot n}\right|$$

---

## Struktura kodu źródłowego

* [`gsvgrab/__main__.py`](gsvgrab/__main__.py) — punkt startowy wywołania modułowego (`python3 -m gsvgrab`).
* [`gsvgrab/cli.py`](gsvgrab/cli.py) — parser argumentów CLI (`find`, `info`, `grab`, `selftest`), orkiestracja zadań i generator raportów HTML.
* [`gsvgrab/api.py`](gsvgrab/api.py) — komunikacja z API Street View (`photometa/v1`, kafle, miniatury).
* [`gsvgrab/depth.py`](gsvgrab/depth.py) — dekodowanie binarne map głębi i konwersje formatów (RGB8, Gray16, Float32).
* [`gsvgrab/pointcloud.py`](gsvgrab/pointcloud.py) — rzutowanie sferyczne na chmurę punktów 3D i zapis w formacie `.ply`.
* [`gsvgrab/stitch.py`](gsvgrab/stitch.py) — montaż siatki kafli equirectangular (backend `sips` / `pillow`).
* [`gsvgrab/pngio.py`](gsvgrab/pngio.py) — niskopoziomowa obsługa PNG (kodowanie i dekodowanie chunków IDAT, IHDR z `zlib`).
* [`gsvgrab/protobuf_url.py`](gsvgrab/protobuf_url.py) — enkoder struktur w notacji parametrów URL Google (`!1s...`, `!2i...`).
* [`gsvgrab/geocode.py`](gsvgrab/geocode.py) — geokodowanie Nominatim i Photon.
* [`gsvgrab/geo.py`](gsvgrab/geo.py) — obliczenia geodezyjne (odległość Haversine/Vincenty, azymuty, kafelkowanie XYZ).
* [`gsvgrab/http.py`](gsvgrab/http.py) — warstwa transportowa HTTP (urllib, retry, nagłówki).
* [`gsvgrab/selftest.py`](gsvgrab/selftest.py) — zestaw testów jednostkowych offline.
