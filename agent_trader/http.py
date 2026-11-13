"""Shared HTTP plumbing: retries, a simple rate limiter, and an on-disk JSON cache."""

import hashlib
import json
import threading
import time
from pathlib import Path
from urllib.parse import urlencode

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


def make_session(headers: dict[str, str]) -> requests.Session:
    session = requests.Session()
    session.headers.update(headers)
    retry = Retry(
        total=4,
        backoff_factor=1.0,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET",),
        respect_retry_after_header=True,
    )
    session.mount("https://", HTTPAdapter(max_retries=retry))
    return session


class RateLimiter:
    def __init__(self, per_second: float):
        self.interval = 1.0 / per_second
        self._next = 0.0
        self._lock = threading.Lock()

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            if now < self._next:
                time.sleep(self._next - now)
                now = self._next
            self._next = now + self.interval


class JsonCache:
    """Caches GET responses as files keyed by URL + params. Credentials never enter the key."""

    def __init__(self, root: Path):
        self.root = root

    def _path(self, url: str, params: dict | None) -> Path:
        full = url + ("?" + urlencode(sorted(params.items())) if params else "")
        digest = hashlib.sha256(full.encode()).hexdigest()[:24]
        host = url.split("/")[2]
        return self.root / host / f"{digest}.json"

    def get(self, url: str, params: dict | None, max_age: float) -> object | None:
        path = self._path(url, params)
        if not path.exists() or time.time() - path.stat().st_mtime > max_age:
            return None
        return json.loads(path.read_text())

    def put(self, url: str, params: dict | None, data: object) -> None:
        path = self._path(url, params)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data))
        tmp.replace(path)
