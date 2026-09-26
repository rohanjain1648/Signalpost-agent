import httpx
import pytest

from signalpost.net import BudgetExceeded, CountingClient, assert_public_url


def test_assert_public_url_rejects_localhost():
    with pytest.raises(ValueError, match="[Ll]ocal"):
        assert_public_url("http://localhost:8000/", resolver=lambda host: ["127.0.0.1"])


def test_assert_public_url_rejects_private_ip():
    with pytest.raises(ValueError, match="[Pp]rivate"):
        assert_public_url("https://internal.example/", resolver=lambda host: ["10.0.0.5"])


def test_assert_public_url_allows_public_ip():
    # should not raise
    assert_public_url("https://example.no/", resolver=lambda host: ["8.8.8.8"])


def test_assert_public_url_rejects_non_http_scheme():
    with pytest.raises(ValueError):
        assert_public_url("ftp://example.no/", resolver=lambda host: ["8.8.8.8"])


def _client_with_transport(handler, max_requests=10):
    transport = httpx.MockTransport(handler)
    return CountingClient(transport=transport, max_requests=max_requests, resolver=lambda host: ["8.8.8.8"])


def test_fetch_increments_request_count():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    client = _client_with_transport(handler)
    result = client.fetch_json("https://example.no/a")
    assert result.status == 200
    assert result.body == {"ok": True}
    assert client.requests_made == 1


def test_fetch_raises_budget_exceeded_after_max_requests():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    client = _client_with_transport(handler, max_requests=1)
    client.fetch_json("https://example.no/a")
    with pytest.raises(BudgetExceeded):
        client.fetch_json("https://example.no/b")


def test_fetch_json_reports_404_as_not_found_status():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="nope")

    client = _client_with_transport(handler)
    result = client.fetch_json("https://example.no/missing")
    assert result.status == 404
    assert result.body is None


# ---- per-endpoint throttling ---------------------------------------------------


class FakeClock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def _throttled_client(clock, throttles):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])

    return CountingClient(
        transport=httpx.MockTransport(handler),
        max_requests=50,
        resolver=lambda host: ["8.8.8.8"],
        throttles=throttles,
        clock=clock.monotonic,
        sleeper=clock.sleep,
    )


def test_throttled_endpoint_waits_between_consecutive_requests():
    clock = FakeClock()
    client = _throttled_client(clock, {"/aarsregnskap/kopi/": 2.1})
    client.fetch_json("https://data.brreg.no/x/aarsregnskap/kopi/1/aar")
    client.fetch_json("https://data.brreg.no/x/aarsregnskap/kopi/2/aar")
    assert len(clock.sleeps) == 1
    assert clock.sleeps[0] == pytest.approx(2.1)


def test_throttle_does_not_sleep_when_enough_time_has_already_passed():
    clock = FakeClock()
    client = _throttled_client(clock, {"/aarsregnskap/kopi/": 2.1})
    client.fetch_json("https://data.brreg.no/x/aarsregnskap/kopi/1/aar")
    clock.now += 5.0
    client.fetch_json("https://data.brreg.no/x/aarsregnskap/kopi/2/aar")
    assert clock.sleeps == []


def test_unthrottled_endpoints_never_sleep():
    clock = FakeClock()
    client = _throttled_client(clock, {"/aarsregnskap/kopi/": 2.1})
    client.fetch_json("https://data.brreg.no/enhetsregisteret/api/enheter/1")
    client.fetch_json("https://data.brreg.no/enhetsregisteret/api/enheter/2")
    assert clock.sleeps == []


# ---- redirects are counted, SSRF-checked, and bounded --------------------------


def _redirecting_client(routes, resolver=None, max_requests=20):
    def handler(request: httpx.Request) -> httpx.Response:
        target = routes[str(request.url)]
        if isinstance(target, int):
            return httpx.Response(target, text="x")
        if target.startswith("BODY:"):
            return httpx.Response(200, text=target[5:], headers={"content-type": "text/html; charset=utf-8"})
        return httpx.Response(301, headers={"location": target})

    return CountingClient(
        transport=httpx.MockTransport(handler),
        max_requests=max_requests,
        resolver=resolver or (lambda host: ["8.8.8.8"]),
    )


def test_each_redirect_hop_counts_against_the_request_budget():
    client = _redirecting_client({
        "http://a.no/": "https://a.no/",
        "https://a.no/": "https://www.a.no/",
        "https://www.a.no/": "BODY:<html>hi</html>",
    })
    result = client.fetch_page("http://a.no/")
    assert result.status == 200
    assert client.requests_made == 3
    assert result.redirect_chain == ["http://a.no/", "https://a.no/", "https://www.a.no/"]
    assert result.final_url == "https://www.a.no/"


def test_redirect_to_private_address_is_blocked_not_followed():
    def resolver(host):
        return ["10.0.0.9"] if host == "intranet.a.no" else ["8.8.8.8"]

    client = _redirecting_client({"https://a.no/": "https://intranet.a.no/"}, resolver=resolver)
    result = client.fetch_page("https://a.no/")
    assert result.status == 0
    assert "blocked" in (result.error or "").lower()
    assert client.requests_made == 1


def test_redirect_loop_is_cut_off_after_max_redirects():
    client = _redirecting_client({"https://a.no/": "https://a.no/"})
    result = client.fetch_page("https://a.no/", max_redirects=3)
    assert result.status == 0
    assert "redirect" in (result.error or "").lower()
    assert client.requests_made == 4


def test_fetch_page_returns_decoded_text_and_content_type():
    client = _redirecting_client({"https://a.no/": "BODY:<html><title>Blåbær</title></html>"})
    result = client.fetch_page("https://a.no/")
    assert "Blåbær" in result.text
    assert result.content_type.startswith("text/html")
    assert result.content_sha256


def test_fetch_page_truncates_oversized_bodies():
    client = _redirecting_client({"https://a.no/": "BODY:" + "x" * 5000})
    result = client.fetch_page("https://a.no/", max_bytes=1000)
    assert len(result.text) == 1000
    assert result.truncated is True


def test_budget_is_checked_before_following_a_redirect():
    client = _redirecting_client(
        {"https://a.no/": "https://b.no/", "https://b.no/": "BODY:ok"}, max_requests=1
    )
    with pytest.raises(BudgetExceeded):
        client.fetch_page("https://a.no/")


def test_decodes_latin1_pages_declared_via_meta_and_falls_back_for_undeclared_bytes():
    from signalpost.net import _decode

    latin1 = "<html><meta charset=iso-8859-1><title>Bjørkås Rørlegger</title></html>".encode("iso-8859-1")
    assert "Bjørkås Rørlegger" in _decode(latin1, "text/html")
    undeclared = "Blåbær".encode("cp1252")  # invalid UTF-8, no declaration anywhere
    assert _decode(undeclared, "text/html") == "Blåbær"
    assert _decode("Blåbær".encode("utf-8"), "text/html") == "Blåbær"


# ---- per-hop permission hook (used for robots.txt) -----------------------------


def test_allow_hook_can_veto_the_first_request_before_any_page_is_fetched():
    client = _redirecting_client({"https://a.no/": "BODY:secret"})
    result = client.fetch_page("https://a.no/", allow=lambda url: False)
    assert result.status == 0
    assert "robots" in (result.error or "").lower()
    assert client.requests_made == 0


def test_allow_hook_is_consulted_for_every_redirect_hop():
    client = _redirecting_client({"https://a.no/": "https://b.no/", "https://b.no/": "BODY:ok"})
    result = client.fetch_page("https://a.no/", allow=lambda url: "b.no" not in url)
    assert result.status == 0
    assert "robots" in (result.error or "").lower()
    assert client.requests_made == 1  # a.no was fetched; b.no was vetoed before being requested


def test_requests_made_by_the_allow_hook_itself_are_counted_and_budgeted():
    client = _redirecting_client({"https://a.no/": "BODY:ok", "https://a.no/robots.txt": "BODY:x"}, max_requests=1)

    def allow(url):
        client.fetch_page("https://a.no/robots.txt")  # consumes the whole budget
        return True

    with pytest.raises(BudgetExceeded):
        client.fetch_page("https://a.no/", allow=allow)


# ---- DNS helper ------------------------------------------------------------------


def test_resolves_uses_the_resolver_costs_no_http_request_and_counts_lookups():
    def resolver(host):
        if host == "dead.no":
            import socket
            raise socket.gaierror("no such host")
        return ["8.8.8.8"]

    client = CountingClient(transport=httpx.MockTransport(lambda r: httpx.Response(200)), resolver=resolver)
    assert client.resolves("alive.no") is True
    assert client.resolves("dead.no") is False
    assert client.requests_made == 0
    assert client.dns_lookups == 2


def test_private_addresses_do_not_count_as_resolving_candidates():
    client = CountingClient(transport=httpx.MockTransport(lambda r: httpx.Response(200)),
                            resolver=lambda host: ["10.0.0.1"])
    assert client.resolves("intranet.no") is False


# ---- custom request headers (API tokens, conditional requests) -------------------


def test_fetch_json_sends_custom_headers_and_still_counts_the_request():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(request.headers)
        return httpx.Response(200, json={"ok": True})

    client = CountingClient(transport=httpx.MockTransport(handler), resolver=lambda h: ["8.8.8.8"])
    result = client.fetch_json("https://feed.example/api", headers={"Authorization": "Bearer abc",
                                                                   "If-Modified-Since": "Sat, 26 Sep 2026 00:00:00 GMT"})
    assert result.body == {"ok": True}
    assert seen["authorization"] == "Bearer abc"
    assert seen["if-modified-since"].startswith("Sat, 26 Sep")
    assert client.requests_made == 1


def test_custom_headers_are_not_forwarded_across_a_redirect_to_another_host():
    hosts = []

    def handler(request: httpx.Request) -> httpx.Response:
        hosts.append((request.url.host, request.headers.get("authorization")))
        if request.url.host == "feed.example":
            return httpx.Response(302, headers={"location": "https://other.example/x"})
        return httpx.Response(200, json={})

    client = CountingClient(transport=httpx.MockTransport(handler), resolver=lambda h: ["8.8.8.8"])
    client.fetch_json("https://feed.example/api", headers={"Authorization": "Bearer secret"})
    assert hosts == [("feed.example", "Bearer secret"), ("other.example", None)]


# ---- thread safety ---------------------------------------------------------------


def test_concurrent_fetches_never_exceed_the_request_budget():
    from concurrent.futures import ThreadPoolExecutor

    import time

    def slow_resolver(host):  # widens the window between the budget check and the increment
        time.sleep(0.002)
        return ["8.8.8.8"]

    client = CountingClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})),
                            max_requests=30, resolver=slow_resolver)

    def call(i):
        try:
            client.fetch_json(f"https://example.no/{i}")
            return "ok"
        except BudgetExceeded:
            return "budget"

    with ThreadPoolExecutor(max_workers=16) as pool:
        outcomes = list(pool.map(call, range(120)))
    assert outcomes.count("ok") == 30
    assert client.requests_made == 30


def test_budget_is_never_exceeded_even_when_threads_wait_on_a_throttle():
    """The throttle wait sits between the budget check and the increment: the real race window."""
    import time
    from concurrent.futures import ThreadPoolExecutor

    client = CountingClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})), max_requests=10,
        resolver=lambda h: ["8.8.8.8"], throttles={"/slow/": 0.003},
        clock=time.monotonic, sleeper=time.sleep,
    )

    def call(i):
        try:
            client.fetch_json(f"https://example.no/slow/{i}")
            return "ok"
        except BudgetExceeded:
            return "budget"

    with ThreadPoolExecutor(max_workers=16) as pool:
        outcomes = list(pool.map(call, range(48)))
    assert outcomes.count("ok") == 10
    assert client.requests_made == 10
