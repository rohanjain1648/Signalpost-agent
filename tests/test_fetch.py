import httpx

from signalpost.fetch import fetch_official_modules
from signalpost.net import CountingClient

ORG = "810034882"
ENTITY_URL = f"https://data.brreg.no/enhetsregisteret/api/enheter/{ORG}"
ROLES_URL = f"{ENTITY_URL}/roller"


def _client(handler, max_requests=100):
    transport = httpx.MockTransport(handler)
    return CountingClient(transport=transport, max_requests=max_requests, resolver=lambda host: ["8.8.8.8"])


def test_fetch_official_modules_returns_available_status_with_normalized_value():
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == ENTITY_URL
        return httpx.Response(200, json={"organisasjonsnummer": ORG, "navn": "SANDNES ELEKTRISKE AS"})

    client = _client(handler)
    records = fetch_official_modules(ORG, {"registry_live"}, client)

    record = records["registry_live"]
    assert record["status"] == "available"
    assert record["value"]["name"] == "SANDNES ELEKTRISKE AS"
    assert record["evidence"]["source_url"] == ENTITY_URL
    assert record["evidence"]["content_sha256"]
    assert record["evidence"]["retrieved_at"]


def test_fetch_official_modules_maps_404_to_not_found_and_keeps_no_value():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="not found")

    client = _client(handler)
    records = fetch_official_modules(ORG, {"registry_live"}, client)

    record = records["registry_live"]
    assert record["status"] == "not_found"
    assert record["value"] is None


def test_fetch_official_modules_maps_server_error_to_source_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="unavailable")

    client = _client(handler)
    records = fetch_official_modules(ORG, {"registry_live"}, client)

    assert records["registry_live"]["status"] == "source_error"


def test_fetch_official_modules_only_requests_the_modules_asked_for():
    seen_urls = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_urls.append(str(request.url))
        return httpx.Response(200, json={"organisasjonsnummer": ORG})

    client = _client(handler)
    fetch_official_modules(ORG, {"registry_live"}, client)

    assert seen_urls == [ENTITY_URL]
    assert client.requests_made == 1


def test_fetch_official_modules_normalizes_roles_module():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "rollegrupper": [
                    {
                        "sistEndret": "2016-07-11",
                        "type": {"kode": "DAGL", "beskrivelse": "Daglig leder"},
                        "roller": [
                            {
                                "avregistrert": False,
                                "person": {"navn": {"fornavn": "Morten", "etternavn": "Aarrestad"}},
                                "type": {"kode": "DAGL", "beskrivelse": "Daglig leder"},
                            }
                        ],
                    }
                ]
            },
        )

    client = _client(handler)
    records = fetch_official_modules(ORG, {"roles"}, client)

    roles = records["roles"]["value"]["roles"]
    assert roles[0]["name"] == "Morten Aarrestad"
