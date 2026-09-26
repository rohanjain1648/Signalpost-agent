"""robots.txt policy per RFC 9309, cached per origin and fetched through the counted client.

  * 2xx   -> parse and obey
  * 4xx   -> "unavailable": no restrictions
  * 5xx   -> "unreachable": assume everything is disallowed
  * connection failure -> the host is down, not disallowing: allow (the page fetch will fail on its own)
"""
from __future__ import annotations

import threading
import urllib.parse
import urllib.robotparser

from .net import CountingClient

AGENT_TOKEN = "signalpost-agent"


class RobotsCache:
    def __init__(self, client: CountingClient) -> None:
        self._client = client
        self._parsers: dict[str, urllib.robotparser.RobotFileParser | bool] = {}
        self._guard = threading.Lock()
        self._origin_locks: dict[str, threading.Lock] = {}

    def _load(self, origin: str, timeout: float | None) -> urllib.robotparser.RobotFileParser | bool:
        result = self._client.fetch_page(f"{origin}/robots.txt", max_bytes=500_000, timeout=timeout)
        if result.status == 200:
            parser = urllib.robotparser.RobotFileParser()
            parser.parse((result.text or "").splitlines())
            return parser
        if result.status == 0 or 400 <= result.status < 500:
            # 4xx: unavailable, no restrictions. status 0: the host itself is unreachable (DNS, refused, TLS),
            # which is not a disallow; the page fetch that follows will report the real failure.
            return True
        return False  # 5xx: the server answered but robots.txt is unavailable -> assume disallowed (RFC 9309)

    def allowed(self, url: str, *, timeout: float | None = None) -> bool:
        parsed = urllib.parse.urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        with self._guard:
            origin_lock = self._origin_locks.setdefault(origin, threading.Lock())
        with origin_lock:  # one robots.txt fetch per origin, however many threads ask at once
            if origin not in self._parsers:
                self._parsers[origin] = self._load(origin, timeout)
        rules = self._parsers[origin]
        if isinstance(rules, bool):
            return rules
        return rules.can_fetch(AGENT_TOKEN, url)
