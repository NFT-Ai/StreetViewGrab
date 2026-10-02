"""
gsvgrab - pobieranie panoram Google Street View wraz z pełnymi metadanymi i mapami głębi.

Pakiet korzysta wyłącznie z biblioteki standardowej Pythona (brak zależności).

Przykład::

    from gsvgrab.http import HttpClient
    from gsvgrab.api import StreetViewApi
    from gsvgrab.parse import panorama_by_id_response

    client = HttpClient(verbose=True)
    api = StreetViewApi(client, locale="pl-PL")
    pano = panorama_by_id_response(api.panorama_by_id("PANOID"))
    print(pano["address"], pano["_depth"].stats())
"""

__version__ = "1.0.0"
__all__ = ["api", "depth", "geo", "http", "parse", "protobuf_url"]
