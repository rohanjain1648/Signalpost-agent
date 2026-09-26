import httpx

from signalpost.net import CountingClient
from signalpost.robots import RobotsCache


def _client(routes):
    def handler(request: httpx.Request) -> httpx.Response:
        route = routes.get(str(request.url))
        if route is None:
            return httpx.Response(404, text="")
        status, body = route
        if status == "connect_error":
            raise httpx.ConnectError("host unreachable")
        return httpx.Response(status, text=body, headers={"content-type": "text/plain"})

    return CountingClient(transport=httpx.MockTransport(handler), max_requests=50, resolver=lambda h: ["8.8.8.8"])


def test_disallowed_path_is_blocked_and_others_allowed():
    client = _client({"https://a.no/robots.txt": (200, "User-agent: *\nDisallow: /private/\n")})
    robots = RobotsCache(client)
    assert robots.allowed("https://a.no/private/x") is False
    assert robots.allowed("https://a.no/om-oss") is True


def test_robots_is_fetched_once_per_host_and_counted():
    client = _client({"https://a.no/robots.txt": (200, "User-agent: *\nDisallow:\n")})
    robots = RobotsCache(client)
    robots.allowed("https://a.no/a")
    robots.allowed("https://a.no/b")
    assert client.requests_made == 1


def test_missing_robots_file_means_everything_is_allowed():
    robots = RobotsCache(_client({}))
    assert robots.allowed("https://a.no/anything") is True


def test_unreachable_robots_file_means_disallow_all_per_rfc_9309():
    robots = RobotsCache(_client({"https://a.no/robots.txt": (503, "down")}))
    assert robots.allowed("https://a.no/") is False


def test_specific_agent_rules_apply_to_our_user_agent():
    body = "User-agent: signalpost-agent\nDisallow: /\n\nUser-agent: *\nDisallow:\n"
    robots = RobotsCache(_client({"https://a.no/robots.txt": (200, body)}))
    assert robots.allowed("https://a.no/") is False


def test_hosts_are_cached_independently():
    client = _client({
        "https://a.no/robots.txt": (200, "User-agent: *\nDisallow: /\n"),
        "https://b.no/robots.txt": (200, "User-agent: *\nDisallow:\n"),
    })
    robots = RobotsCache(client)
    assert robots.allowed("https://a.no/x") is False
    assert robots.allowed("https://b.no/x") is True


def test_unreachable_host_is_not_a_disallow_so_the_page_fetch_can_fail_naturally():
    robots = RobotsCache(_client({"https://a.no/robots.txt": ("connect_error", "")}))
    assert robots.allowed("https://a.no/") is True
