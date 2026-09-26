"""Public-only, request-counting HTTP client.

Every outbound call goes through here so the run's 2,000-request budget
(redirects and retries included) is enforced in one place, and every fetch
is guarded against resolving to a private/loopback/link-local address
(SSRF protection), per the starter kit's `assert_public_url` policy.
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import re
import socket
import threading
import time
import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

import httpx

USER_AGENT = "signalpost-agent/0.1 (+https://github.com/; company research)"

Resolver = Callable[[str], list[str]]

# BRREG's filing-year endpoint allows roughly 30 request starts per minute; stay under it.
PRODUCTION_THROTTLES: dict[str, float] = {"/aarsregnskap/kopi/": 2.1}


def _default_resolver(host: str) -> list[str]:
    return [item[4][0] for item in socket.getaddrinfo(host, None)]


def assert_public_url(url: str, *, resolver: Resolver = _default_resolver) -> None:
    parsed = urllib.parse.urlparse(url)
    host = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme not in {"http", "https"} or not host:
        raise ValueError(f"Only public HTTP(S) URLs are allowed, got {url!r}")
    if host in {"localhost"} or host.endswith(".localhost") or host.endswith(".local"):
        raise ValueError(f"Local hosts are blocked: {host!r}")
    try:
        addresses = resolver(host)
    except socket.gaierror as exc:
        raise ValueError(f"Hostname did not resolve: {host!r}") from exc
    if not addresses:
        raise ValueError(f"Hostname did not resolve: {host!r}")
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if not ip.is_global:
            raise ValueError(
                f"Private, loopback, link-local, multicast, or reserved addresses are blocked: {address!r}"
            )


class BudgetExceeded(RuntimeError):
    """Raised when a fetch would exceed the run's outbound-request budget."""


@dataclass
class FetchResult:
    url: str
    status: int
    elapsed_ms: int
    bytes_received: int
    body: Any = None
    error: str | None = None
    content_sha256: str | None = None
    retrieved_at: str | None = None
    final_url: str | None = None
    text: str | None = None
    content_type: str | None = None
    redirect_chain: list[str] = field(default_factory=list)
    truncated: bool = False


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _decode(raw: bytes, content_type: str) -> str:
    """Decode a body: declared charset, then <meta charset>, then UTF-8, then cp1252."""
    match = re.search(r"charset=([\w-]+)", content_type or "", re.I)
    codec = match.group(1) if match else None
    if codec is None:
        meta = re.search(rb"<meta[^>]+charset=[\"']?([\w-]+)", raw[:4096], re.I)
        codec = meta.group(1).decode("ascii") if meta else None
    for candidate in (codec, "utf-8"):
        if not candidate:
            continue
        try:
            return raw.decode(candidate)
        except (LookupError, UnicodeDecodeError):
            continue
    return raw.decode("cp1252", errors="replace")


class CountingClient:
    """Wraps an httpx client, enforcing a public-URL policy and a hard request budget.

    Redirects are followed manually so every hop is budget-counted and SSRF-checked.
    """

    def __init__(
        self,
        *,
        transport: httpx.BaseTransport | None = None,
        max_requests: int = 1900,
        timeout: float = 20.0,
        resolver: Resolver = _default_resolver,
        throttles: dict[str, float] | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self._client = httpx.Client(
            transport=transport,
            timeout=timeout,
            headers={"User-Agent": USER_AGENT},
            follow_redirects=False,
        )
        self.max_requests = max_requests
        self.requests_made = 0
        self._resolver = resolver
        self._throttles = throttles or {}
        self._clock = clock
        self._sleeper = sleeper
        self._throttle_lock = threading.Lock()
        self._last_request_at: dict[str, float] = {}

    def _wait_for_slot(self, url: str) -> None:
        """Space out requests to rate-limited endpoints (minimum seconds between starts)."""
        for prefix, interval in self._throttles.items():
            if prefix not in url:
                continue
            with self._throttle_lock:
                now = self._clock()
                last = self._last_request_at.get(prefix)
                if last is not None and interval - (now - last) > 0:
                    self._sleeper(interval - (now - last))
                    now = self._clock()
                self._last_request_at[prefix] = now

    def _get(self, url: str, *, accept: str, max_redirects: int, max_bytes: int) -> FetchResult:
        started = time.monotonic()
        chain: list[str] = []
        current = url

        def result(status: int = 0, error: str | None = None, raw: bytes = b"", content_type: str | None = None,
                   truncated: bool = False) -> FetchResult:
            return FetchResult(
                url=url, status=status, elapsed_ms=int((time.monotonic() - started) * 1000),
                bytes_received=len(raw), error=error,
                content_sha256=hashlib.sha256(raw).hexdigest() if raw else None,
                retrieved_at=_utc_now(), final_url=chain[-1] if chain else url,
                content_type=content_type, redirect_chain=chain, truncated=truncated,
            )

        for _ in range(max_redirects + 1):
            if self.requests_made >= self.max_requests:
                raise BudgetExceeded(f"Request budget of {self.max_requests} exhausted before fetching {current!r}")
            try:
                assert_public_url(current, resolver=self._resolver)
            except ValueError as exc:
                return result(error=f"blocked: {exc}")
            self._wait_for_slot(current)
            self.requests_made += 1
            chain.append(current)
            try:
                with self._client.stream("GET", current, headers={"Accept": accept}) as response:
                    raw = bytearray()
                    truncated = False
                    for piece in response.iter_bytes():
                        raw.extend(piece)
                        if len(raw) > max_bytes:
                            del raw[max_bytes:]
                            truncated = True
                            break
                    status = response.status_code
                    location = response.headers.get("location")
                    content_type = response.headers.get("content-type", "")
            except httpx.HTTPError as exc:
                return result(error=type(exc).__name__)
            if status in {301, 302, 303, 307, 308} and location:
                current = urllib.parse.urljoin(current, location)
                continue
            found = result(status=status, raw=bytes(raw), content_type=content_type, truncated=truncated)
            found.text = _decode(bytes(raw), content_type)
            if status != 200:
                found.error = f"HTTP {status}"
            return found
        return result(error="too many redirects")

    def fetch_json(self, url: str) -> FetchResult:
        found = self._get(url, accept="application/json", max_redirects=3, max_bytes=10_000_000)
        if found.status == 200:
            try:
                found.body = json.loads(found.text or "")
            except ValueError:
                found.error = "invalid_json"
        return found

    def fetch_page(self, url: str, *, max_redirects: int = 5, max_bytes: int = 1_500_000) -> FetchResult:
        return self._get(url, accept="text/html,application/xhtml+xml,*/*;q=0.8",
                         max_redirects=max_redirects, max_bytes=max_bytes)

    def close(self) -> None:
        self._client.close()
