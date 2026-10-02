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
* **Geokodowanie adresów** ([`geocode.py`](gsvgrab/geocode.py)) — wyszukiwanie po adresie przez OpenStreetMap Nominatim (bez klucza API), a w trybie `--api-key` przez Google Geocoding API.
* **Statyczna przeglądarka HTML** — automatycznie generowany plik `index.html` z interaktywnym podglądem panoramy i mapy głębi.
* **Pełny zestaw testów offline** ([`selftest.py`](gsvgrab/selftest.py)) — 62 weryfikacje uruchamiane bez połączenia z siecią (m.in. analityczna kontrola dekodera głębi).

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

# Z adresami i datami zdjęć (dokłada metadane z photometa):
python3 -m gsvgrab find --address "Piasta 10c, Milanówek" --with-metadata --limit 5

# Z podglądem przejazdu (wzdłuż ulicy, 6 połączonych panoram):
python3 -m gsvgrab find --address "Piasta 10c, Milanówek" --walk --walk-count 6
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

### 5. Przejście ulicą i sklejanie chmur punktów (`walk`, `merge`)

Przechodzi po panoramach wzdłuż ulicy (z utrzymaniem kierunku marszu) i scala ich
chmury punktów w jeden plik PLY we wspólnym układzie ENU (X = wschód, Y = północ, Z = w górę):

```bash
# 10 kroków w przód i 10 z powrotem po ul. Piasta w Milanówku
python3 -m gsvgrab walk --address "Piasta 10c, Milanówek" --forward 10 --back 10

# sama trasa, bez pobierania głębi (szybki podgląd kierunku marszu)
python3 -m gsvgrab walk --address "Piasta 10c, Milanówek" --forward 25 --back 25 --route-only

# przesklejenie chmur z wcześniej pobranego katalogu
python3 -m gsvgrab merge --in-dir trasa_piasta --stride 2
```

Wynik: `trasa.json` (krok po kroku), `chmura_sklejona.ply` + `.json`.
### 6. Generowanie siatki 3D (`mesh`)

Generuje spójną siatkę trójkątów 3D (w formatach `.obj` i `.ply`) z map głębi dla całej trasy lub katalogu panoram:

```bash
# Wygenerowanie siatki trasy:
python3 -m gsvgrab mesh --in-dir ./trasa_piasta

# Z własnymi parametrami:
python3 -m gsvgrab mesh --in-dir ./trasa_piasta --stride 2 --max-depth 50.0 --out-obj model.obj
```

Wynik: `mesh_trasa.obj` (z wektorami normalnymi i UV) oraz `mesh_trasa.ply`.

---

## Dokumentacja

Pełny opis realizacji, zweryfikowanych endpointów, formatu głębi, konwencji układu
współrzędnych oraz ograniczeń: [`DOKUMENTACJA.md`](DOKUMENTACJA.md).

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

## Jak to działa — zweryfikowane punkty końcowe

Aplikacja **nie wymaga klucza API**. Poniższe adresy zostały sprawdzone empirycznie
(stan: październik 2026) i są używane przez narzędzia open source (np. `streetlevel`,
`ZenSVI`), ponieważ Google nie udostępnia ich oficjalnie:

| Zastosowanie | Adres |
|---|---|
| Wszystkie metadane + **mapa głębi** po `panoid` | `https://www.google.com/maps/photometa/v1?authuser=0&hl=..&gl=..&pb=<protobuf>` |
| Lista panoram w kaflu pokrycia (zoom 17) | `https://www.google.com/maps/photometa/ac/v1?pb=!1m1!1smaps_sv.tactile!6m3!1iX!2iY!3i17!8b1` |
| Najbliższa panorama w promieniu (JSONP) | `https://maps.googleapis.com/maps/api/js/GeoPhotoService.SingleImageSearch?pb=<protobuf>` |
| Kafel obrazu 512×512 px | `https://streetviewpixels-pa.googleapis.com/v1/tile?cb_client=maps_sv.tactile&panoid=..&x=..&y=..&zoom=..` |
| Podgląd panoramy (do 1024×512 px) | `https://streetviewpixels-pa.googleapis.com/v1/thumbnail?panoid=..&w=..&h=..` |

Klucz Google (`--api-key` / `GOOGLE_MAPS_API_KEY`) jest **opcjonalny** i służy wyłącznie
geokodowaniu (`Geocoding API`) oraz — jeśli chcesz — oficjalnemu `Street View Static API`.

Siatka kafli panoramy (zweryfikowana na żywych danych): `2^zoom` kolumn × `2^(zoom-1)` wierszy,
kafel 512×512 px, czyli zoom 5 = 16384×8192 px. Puste kafle mają 1184 B i są pomijane
przy sklejaniu.

### Przykład: Piasta 10c, Milanówek

```
$ python3 -m gsvgrab grab --address "Piasta 10c, Milanówek" --pointcloud
Geokodowanie: Piasta 10c, Milanówek
  -> 10C, Piasta, Milanówek, ... , 05-822, Polska
  -> 52.127915, 20.669205 (nominatim)
[1/1] bf2xn0xEg2s5ZDQ2fwQlOA
  adres: 13 Piasta | zdjecie: 2025-10 | copyright: © 2026 Google
  glebia: 512x256, pokrycie 54.1%, mediana 3.49 m, max 104.28 m (kodowanie: raw)
  podglad: 152.9 kB
  panorama: 2048x1024 px, kafli: 8 (pustych 0), srednia jasnosc RGB: [139.7, 138.9, 130.9]
  chmura punktow: 17663 punktow (z kolorem)
```

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
* [`gsvgrab/geocode.py`](gsvgrab/geocode.py) — geokodowanie Nominatim (domyślnie) i Google Geocoding (z kluczem).
* [`gsvgrab/geo.py`](gsvgrab/geo.py) — obliczenia geodezyjne (odległość haversine, azymut, kafelkowanie XYZ).
* [`gsvgrab/http.py`](gsvgrab/http.py) — warstwa transportowa HTTP (urllib, retry, nagłówki).
* [`gsvgrab/selftest.py`](gsvgrab/selftest.py) — zestaw testów jednostkowych offline.
