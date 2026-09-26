"""robots.txt policy per RFC 9309, cached per origin and fetched through the counted client.

  * 2xx   -> parse and obey
  * 4xx   -> "unavailable": no restrictions
  * 5xx / network failure -> "unreachable": assume everything is disallowed
"""
from __future__ import annotations

import urllib.parse
import urllib.robotparser

from .net import CountingClient

AGENT_TOKEN = "signalpost-agent"


class RobotsCache:
    def __init__(self, client: CountingClient) -> None:
        self._client = client
        self._parsers: dict[str, urllib.robotparser.RobotFileParser | bool] = {}

    def _load(self, origin: str) -> urllib.robotparser.RobotFileParser | bool:
        result = self._client.fetch_page(f"{origin}/robots.txt", max_bytes=500_000)
        if result.status == 200:
            parser = urllib.robotparser.RobotFileParser()
            parser.parse((result.text or "").splitlines())
            return parser
        if 400 <= result.status < 500:
            return True  # unavailable: everything allowed
        return False  # unreachable or 5xx: everything disallowed

    def allowed(self, url: str) -> bool:
        parsed = urllib.parse.urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        if origin not in self._parsers:
            self._parsers[origin] = self._load(origin)
        rules = self._parsers[origin]
        if isinstance(rules, bool):
            return rules
        return rules.can_fetch(AGENT_TOKEN, url)
