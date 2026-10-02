"""Minimalny klient HTTP (tylko biblioteka standardowa) z ponawianiem i opcjonalnym cache."""

import gzip
import hashlib
import os
import random
import time
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_TIMEOUT = 45
RETRY_STATUS = (408, 429, 500, 502, 503, 504)

USER_AGENTS = (
    "Mozilla/5.0 (Windows NT 11.0; Win64; x64; rv:151.0) Gecko/20100101 Firefox/151.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64; rv:150.0) Gecko/20100101 Firefox/150.0",
)


class HttpError(Exception):
    """Błąd HTTP, którego nie udało się ponowić."""

    def __init__(self, url, status, reason=""):
        super().__init__("HTTP %s dla %s %s" % (status, url, reason))
        self.url = url
        self.status = status


class Cache:
    """Prosty cache na dysku (klucz = sha1 adresu URL)."""

    def __init__(self, directory):
        self.directory = directory
        if directory:
            os.makedirs(directory, exist_ok=True)

    def path_for(self, url):
        digest = hashlib.sha1(url.encode("utf-8")).hexdigest()
        return os.path.join(self.directory, digest[:2], digest)

    def get(self, url):
        if not self.directory:
            return None
        path = self.path_for(url)
        if os.path.exists(path):
            with open(path, "rb") as handle:
                return handle.read()
        return None

    def put(self, url, payload):
        if not self.directory:
            return
        path = self.path_for(url)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "wb") as handle:
            handle.write(payload)
        os.replace(tmp, path)


class HttpClient:
    """Klient HTTP z rotacją User-Agentów, ponawianiem i cache."""

    def __init__(self, cache_dir=None, timeout=DEFAULT_TIMEOUT, retries=3, verbose=False,
                 user_agent=None):
        self.cache = Cache(cache_dir)
        self.timeout = timeout
        self.retries = retries
        self.verbose = verbose
        self.user_agent = user_agent or random.choice(USER_AGENTS)

    # -- wewnętrzne -------------------------------------------------------
    def _log(self, message):
        if self.verbose:
            print("[http] %s" % message, flush=True)

    def _request(self, url, referer=None, headers=None, accept="*/*"):
        merged = {
            "User-Agent": self.user_agent,
            "Accept": accept,
            "Accept-Language": "pl-PL,pl;q=0.9,en-US;q=0.8,en;q=0.7",
            "Accept-Encoding": "identity",
        }
        if referer:
            merged["Referer"] = referer
        if headers:
            merged.update(headers)
        request = urllib.request.Request(url, headers=merged)
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            payload = response.read()
            if response.headers.get("Content-Encoding") == "gzip":
                payload = gzip.decompress(payload)
            return response.status, payload

    # -- publiczne --------------------------------------------------------
    def get_bytes(self, url, referer=None, headers=None, accept="*/*", use_cache=True,
                  cache_only=False, allow_all_status=False):
        """Pobiera surowe bajty. Rzuca HttpError gdy status jest błędem trwałym."""
        if use_cache:
            cached = self.cache.get(url)
            if cached is not None:
                self._log("cache HIT %s" % url[:110])
                return cached
        if cache_only:
            raise HttpError(url, 0, "brak w cache")

        last_error = None
        for attempt in range(self.retries):
            try:
                status, payload = self._request(url, referer, headers, accept)
                if status >= 400 and not allow_all_status:
                    raise HttpError(url, status)
                if use_cache:
                    self.cache.put(url, payload)
                return payload
            except urllib.error.HTTPError as error:
                last_error = HttpError(url, error.code, error.reason or "")
                if error.code not in RETRY_STATUS and not allow_all_status:
                    # Google zwraca 404 dla nieistniejących kafli - nie ma sensu ponawiać
                    raise last_error
                if error.code >= 400 and allow_all_status:
                    try:
                        return error.read()
                    except Exception:  # noqa: BLE001 - brak treści
                        raise last_error
            except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as error:
                last_error = HttpError(url, 0, repr(error))
            if attempt + 1 < self.retries:
                delay = (1.5 ** attempt) + random.random()
                self._log("ponawiam za %.1fs (%s)" % (delay, last_error))
                time.sleep(delay)
        raise last_error

    def get_text(self, url, prefix_strip=0, **kwargs):
        """Pobiera tekst. ``prefix_strip`` ucina prefiks anty-XSSI (``)]}'``) itp."""
        payload = self.get_bytes(url, **kwargs)
        text = payload.decode("utf-8", "replace")
        if prefix_strip:
            text = text[prefix_strip:]
        return text
