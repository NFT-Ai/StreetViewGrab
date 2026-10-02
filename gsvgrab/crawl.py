"""
Przechodzenie ulica po panoramach Street View (tzw. crawl).

Najprostsze podejscie - losowe skoki po sasiadach - gubi ulice, bo prowadzi
czesto na druga jezdnie. Dlatego Crawler:

* utrzymuje **kierunek marszu** (azymut w stopniach),
* wybiera sasiada, ktory najlepiej kontynuuje ten kierunek,
* wymaga zgodnosci naglowka sasiedniej panoramy z kierunkiem marszu
  (domyslnie +-70 stopni), dzieki czemu trzyma sie jednej jezdni,
* premiuje kroki o dlugosci bliskiej typowej odleglosci miedzy panoramami,
* przy powrocie odwraca kierunek o 180 stopni.

Kazdy krok to jedno zapytanie o metadane (bez glebi), wiec przejscie ulicy
jest tanie.
"""

from .geo import bearing_deg, distance_m
from .parse import panorama_by_id_response

#: Typowa odleglosc miedzy sasiednimi panoramami na ulicy (metry).
DEFAULT_STEP_M = 12.0


def _angle_difference(first, second):
    """Roznica katow w zakresie -180..180 stopni."""
    return abs(((float(second) - float(first) + 540.0) % 360.0) - 180.0)


def _axis_difference(first, second):
    """Roznica katow modulo 180 stopni.

    Kierunek przejazdu nie ma znaczenia dla "czyjezdni" - panorama z przeciwleglej
    strony ulicy ma naglowek o 180 stopni inny. Na ul. Piasta wszystkie panoramy
    maja naglowek ok. 25 stopni, wiec porownanie modulo 180 jest tu poprawne.
    """
    difference = _angle_difference(first, second)
    return min(difference, 180.0 - difference)


class Crawler:
    """Przechodzi po panoramach wzdluz kierunku."""

    def __init__(self, api, verbose=False, step_m=DEFAULT_STEP_M,
                 min_step_m=5.0, max_step_m=30.0, align_tol_deg=70.0, max_turn_deg=55.0):
        self.api = api
        self.verbose = verbose
        self.step_m = step_m
        self.min_step_m = min_step_m
        self.max_step_m = max_step_m
        self.align_tol_deg = align_tol_deg
        self.max_turn_deg = max_turn_deg
        self._metadata = {}

    def log(self, message):
        if self.verbose:
            print("    %s" % message, flush=True)

    def metadata(self, panoid):
        """Metadane panoramy (z cache w ramach przejscia)."""
        if panoid not in self._metadata:
            raw = self.api.panorama_by_id(panoid, download_depth=False)
            self._metadata[panoid] = panorama_by_id_response(raw, include_depth=False)
        return self._metadata[panoid]

    def start(self, panoid, heading_deg=None):
        """Ustala punkt startowy i poczatkowy kierunek marszu."""
        pano = self.metadata(panoid)
        heading = float(heading_deg if heading_deg is not None else pano.get("heading_deg") or 0.0)
        return {"panoid": panoid, "lat": pano["lat"], "lon": pano["lon"],
                "pano_heading_deg": pano.get("heading_deg"),
                "travel_heading_deg": heading % 360.0, "step": 0, "leg": "start",
                "distance_from_prev_m": 0.0, "side": "-"}

    def step(self, current, travel_heading, visited, allow_revisit=False):
        """Jeden krok w zadanym kierunku. Zwraca ``(nowy_wezel, nowy_kierunek)``."""
        pano = self.metadata(current["panoid"])
        candidates = []
        for neighbour in pano.get("neighbors", []):
            panoid = neighbour.get("panoid")
            lat = neighbour.get("lat")
            lon = neighbour.get("lon")
            if not panoid or lat is None or lon is None:
                continue
            revisit = panoid in visited
            if revisit and not allow_revisit:
                continue
            distance = distance_m(current["lat"], current["lon"], lat, lon)
            if not (self.min_step_m <= distance <= self.max_step_m):
                continue
            neighbour_heading = neighbour.get("heading_deg")
            if neighbour_heading is None:
                continue
            if _axis_difference(travel_heading, neighbour_heading) > self.align_tol_deg:
                continue
            to_bearing = bearing_deg(current["lat"], current["lon"], lat, lon)
            turn = _angle_difference(travel_heading, to_bearing)
            if turn > self.max_turn_deg:
                continue
            length_penalty = abs(distance - self.step_m) / float(self.step_m)
            # powtorka jest ostrawniejsza niz nowa panorama (ma ten sam obraz)
            revisit_penalty = 40.0 if revisit else 0.0
            candidates.append((turn + length_penalty * 12.0 + revisit_penalty, panoid, lat, lon,
                               neighbour_heading, distance, to_bearing, revisit))
        if not candidates:
            return None, travel_heading
        candidates.sort(key=lambda item: item[0])
        (_, panoid, lat, lon, neighbour_heading, distance, to_bearing,
         revisit) = candidates[0]
        self.log("krok %.1f m, odchylenie %.1f stopni%s -> %s"
                 % (distance, _angle_difference(travel_heading, to_bearing),
                    " [powrotka]" if revisit else "", panoid))
        new_heading = (0.7 * to_bearing + 0.3 * travel_heading) % 360.0
        return ({"panoid": panoid, "lat": lat, "lon": lon,
                 "pano_heading_deg": neighbour_heading,
                 "travel_heading_deg": new_heading,
                 "distance_from_prev_m": round(distance, 2),
                 "bearing_to_prev_deg": round(to_bearing, 2),
                 "revisit": revisit}, new_heading)
    def traverse(self, start_panoid, forward_steps=10, back_steps=0, heading_deg=None):
        """Przechodzi ``forward_steps`` krokow do przodu, potem ``back_steps`` z powrotem."""
        route = []
        current = self.start(start_panoid, heading_deg)
        visited = {current["panoid"]}
        route.append(current)
        heading = current["travel_heading_deg"]

        for leg, count, reverse in (("forward", forward_steps, False),
                                    ("back", back_steps, True)):
            if count <= 0:
                continue
            if reverse:
                heading = (heading + 180.0) % 360.0
                self.log("odwracam kierunek na %.1f stopni" % heading)
            allow_revisit = False
            for index in range(count):
                next_node, heading = self.step(current, heading, visited,
                                               allow_revisit=allow_revisit)
                if next_node is None and not allow_revisit:
                    # Na ulicach z jednym pasem zdjec powrot musi uzyc tych samych
                    # panoram - wtedy dopuszczamy powrotki (z wyraznym oznaczeniem).
                    allow_revisit = True
                    self.log("brak nowych panoram w tym kierunku - wracam tymi samymi")
                    next_node, heading = self.step(current, heading, visited,
                                                   allow_revisit=True)
                if next_node is None:
                    self.log("koniec trasy po %d krokach" % index)
                    break
                next_node["step"] = len(route)
                next_node["leg"] = leg
                next_node["side"] = (
                    "ta sama jezdnia"
                    if _axis_difference(heading, next_node["pano_heading_deg"])
                    <= self.align_tol_deg else "druga jezdnia")
                visited.add(next_node["panoid"])
                route.append(next_node)
                current = next_node
        return route

    def route_length(self, route):
        """Dlugosc trasy w metrach (suma krokow)."""
        return round(sum((node.get("distance_from_prev_m") or 0.0) for node in route[1:]), 2)

    def route_extent(self, route):
        """Zasieg trasy (poludnie-poln, wschod-zachod) w metrach."""
        lats = [node["lat"] for node in route if node.get("lat") is not None]
        lons = [node["lon"] for node in route if node.get("lon") is not None]
        if not lats:
            return {}
        north = distance_m(min(lats), lons[0], max(lats), lons[0])
        east = distance_m(lats[0], min(lons), lats[0], max(lons))
        return {"north_south_m": round(north, 1), "east_west_m": round(east, 1)}