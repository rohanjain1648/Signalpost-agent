"""Public-only, request-counting HTTP client.

Every outbound call goes through here so the run's 2,000-request budget
(redirects and retries included) is enforced in one place, and every fetch
is guarded against resolving to a private/loopback/link-local address
(SSRF protection), per the starter kit's `assert_public_url` policy.
"""
from __future__ import annotations

import hashlib
import ipaddress
import socket
import time
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

import httpx

USER_AGENT = "signalpost-agent/0.1 (+https://github.com/; company research)"

Resolver = Callable[[str], list[str]]


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


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class CountingClient:
    """Wraps an httpx client, enforcing a public-URL policy and a hard request budget."""

    def __init__(
        self,
        *,
        transport: httpx.BaseTransport | None = None,
        max_requests: int = 1900,
        timeout: float = 20.0,
        resolver: Resolver = _default_resolver,
    ) -> None:
        self._client = httpx.Client(
            transport=transport,
            timeout=timeout,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            follow_redirects=True,
        )
        self.max_requests = max_requests
        self.requests_made = 0
        self._resolver = resolver

    def fetch_json(self, url: str) -> FetchResult:
        if self.requests_made >= self.max_requests:
            raise BudgetExceeded(
                f"Request budget of {self.max_requests} exhausted before fetching {url!r}"
            )
        assert_public_url(url, resolver=self._resolver)
        self.requests_made += 1
        started = time.monotonic()
        try:
            response = self._client.get(url)
        except httpx.HTTPError as exc:
            elapsed = int((time.monotonic() - started) * 1000)
            return FetchResult(
                url=url,
                status=0,
                elapsed_ms=elapsed,
                bytes_received=0,
                error=type(exc).__name__,
                retrieved_at=_utc_now(),
            )
        elapsed = int((time.monotonic() - started) * 1000)
        raw = response.content
        body = None
        error = None
        if response.status_code == 200:
            try:
                body = response.json()
            except ValueError:
                error = "invalid_json"
        else:
            error = f"HTTP {response.status_code}"
        return FetchResult(
            url=url,
            status=response.status_code,
            elapsed_ms=elapsed,
            bytes_received=len(raw),
            body=body,
            error=error,
            content_sha256=hashlib.sha256(raw).hexdigest() if raw else None,
            retrieved_at=_utc_now(),
            final_url=str(response.url),
        )

    def close(self) -> None:
        self._client.close()
