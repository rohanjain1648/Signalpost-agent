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
