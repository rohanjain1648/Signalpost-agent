import gzip
import json

import httpx
import pytest

from signalpost.nav import NavIndex, NavJobs, build_index, load_index, name_key, save_index
from signalpost.net import CountingClient

BASE = "https://pam-stilling-feed.nav.no"
ORG = "810034882"
ENTITY = {"organisation_number": ORG, "name": "SANDNES ELEKTRISKE AS",
          "historical_names": [{"navn": "SANDNES ELEKTRISKE FORRETNING AS"}]}
BUILT = "2026-09-20T10:00:00Z"


# ---- names and index ---------------------------------------------------------------


def test_name_key_ignores_case_legal_suffix_and_punctuation():
    assert name_key("SANDNES ELEKTRISKE AS") == name_key("Sandnes Elektriske") == "sandnes elektriske"
    assert name_key("Bjørkås & Sønn A/S") == "bjorkas sonn"


def test_build_index_keeps_only_the_latest_active_state_of_each_ad():
    state = {"since": "2026-03-26", "started_at": BUILT, "ads": {
        "a1": ["ACTIVE", "Sandnes Elektriske AS", "Elektriker", "SANDNES", "2026-09-01T00:00:00+02:00"],
        "a2": ["INACTIVE", "Sandnes Elektriske AS", "Gammel", "SANDNES", "2026-05-01T00:00:00+02:00"],
        "a3": ["ACTIVE", "Annen Bedrift", "Lærling", "OSLO", "2026-09-02T00:00:00+02:00"],
    }}
    index = build_index(state)
    assert index.by_name == {"sandnes elektriske": ["a1"], "annen bedrift": ["a3"]}
    assert index.built_at == BUILT


def test_index_round_trips_and_a_missing_file_loads_as_none(tmp_path):
    index = NavIndex(built_at=BUILT, since="2026-03-26", by_name={"x": ["a"]})
    save_index(index, tmp_path / "idx.json.gz")
    assert load_index(tmp_path / "idx.json.gz") == index
    assert load_index(tmp_path / "missing.json.gz") is None


# ---- a simulated feed ---------------------------------------------------------------


class Feed:
    """Simulated NAV feed: token endpoint, one tail page chain and ad detail records."""

    def __init__(self, tail_pages=None, details=None, token_status=200, detail_status=None):
        self.tail_pages = tail_pages or [[]]
        self.details = details or {}
        self.token_status = token_status
        self.detail_status = detail_status or {}
        self.calls = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.calls.append((url, request.headers.get("authorization"), request.headers.get("if-modified-since")))
        if url == f"{BASE}/api/publicToken":
            if self.token_status != 200:
                return httpx.Response(self.token_status, text="down")
            return httpx.Response(200, text="Current public token:\neyJtoken.abc.def\n")
        if "/api/v1/feedentry/" in url:
            uuid = url.rsplit("/", 1)[-1]
            if uuid in self.detail_status:
                return httpx.Response(self.detail_status[uuid], text="err")
            return httpx.Response(200, json=self.details[uuid])
        if url.startswith(f"{BASE}/api/v1/feed"):
            page = int(url.rsplit("/", 1)[-1]) if url.rsplit("/", 1)[-1].isdigit() else 0
            items = [{"id": u, "url": f"/api/v1/feedentry/{u}", "_feed_entry": {
                "uuid": u, "status": st, "businessName": name, "title": "t", "municipal": "X",
                "sistEndret": "2026-09-25T00:00:00+02:00"}} for (u, st, name) in self.tail_pages[page]]
            nxt = f"/api/v1/feed/{page + 1}" if page + 1 < len(self.tail_pages) else None
            return httpx.Response(200, json={"items": items, "next_url": nxt})
        return httpx.Response(404, text="?")


def detail(uuid, orgnr, status="ACTIVE", title="Elektriker", name="Sandnes Elektriske AS"):
    return {"uuid": uuid, "status": status, "sistEndret": "2026-09-25T08:00:00+02:00", "ad_content": {
        "uuid": uuid, "title": title, "jobtitle": title, "published": "2026-09-20T00:00:00+02:00",
        "expires": "2026-10-20T00:00:00+02:00", "applicationDue": "2026-10-15T00:00:00",
        "workLocations": [{"city": "SANDNES", "municipal": "SANDNES", "postalCode": "4307"}],
        "link": f"https://arbeidsplassen.nav.no/stillinger/stilling/{uuid}",
        "employer": {"name": name, "orgnr": orgnr}, "extent": "Heltid", "engagementtype": "Fast"}}


def make(feed, by_name=None, **kw):
    client = CountingClient(transport=httpx.MockTransport(feed.handler), max_requests=200,
                            resolver=lambda h: ["8.8.8.8"])
    index = NavIndex(built_at=BUILT, since="2026-03-26", by_name=by_name or {})
    return NavJobs(index, client, **kw), client


# ---- prepare: token + catching up since the index was built ---------------------------


def test_prepare_fetches_the_token_then_catches_up_from_the_index_build_time():
    feed = Feed(tail_pages=[[("n1", "ACTIVE", "Sandnes Elektriske AS")]])
    nav, client = make(feed)
    summary = nav.prepare()
    assert summary["ready"] is True
    token_call, feed_call = feed.calls[0], feed.calls[1]
    assert token_call[0].endswith("/api/publicToken")
    assert feed_call[1] == "Bearer eyJtoken.abc.def"
    assert feed_call[2] == "Sun, 20 Sep 2026 10:00:00 GMT"
    assert nav.index.by_name["sandnes elektriske"] == ["n1"]


def test_prepare_follows_pages_adds_active_ads_and_removes_inactive_ones():
    feed = Feed(tail_pages=[
        [("n1", "ACTIVE", "Sandnes Elektriske AS"), ("old", "ACTIVE", "Sandnes Elektriske AS")],
        [("old", "INACTIVE", "Sandnes Elektriske AS"), ("n2", "ACTIVE", "Annen AS")],
    ])
    nav, client = make(feed, by_name={"sandnes elektriske": ["old"]})
    summary = nav.prepare()
    assert summary["pages"] == 2
    assert nav.index.by_name["sandnes elektriske"] == ["n1"]
    assert nav.index.by_name["annen"] == ["n2"]
    assert client.requests_made == 3  # token + 2 pages


def test_prepare_marks_not_ready_when_the_token_cannot_be_fetched():
    nav, _ = make(Feed(token_status=503))
    assert nav.prepare()["ready"] is False
    result = nav.jobs_for(ENTITY, {ORG})
    assert result["status"] == "failed"


def test_prepare_marks_not_ready_when_catch_up_is_cut_off_by_the_page_cap():
    feed = Feed(tail_pages=[[("a", "ACTIVE", "X")], [("b", "ACTIVE", "X")], [("c", "ACTIVE", "X")]])
    nav, _ = make(feed, max_tail_pages=2)
    summary = nav.prepare()
    assert summary["ready"] is False and summary["truncated"] is True
    assert nav.jobs_for(ENTITY, {ORG})["status"] == "failed"


# ---- jobs_for: exact identity comes from the ad's own employer number -----------------


def ready(feed, by_name):
    nav, client = make(feed, by_name=by_name)
    assert nav.prepare()["ready"] is True
    client.requests_made = 0  # count only the per-company work
    return nav, client


def test_an_ad_is_published_only_when_its_employer_number_is_this_company():
    feed = Feed(details={"a1": detail("a1", ORG), "a2": detail("a2", "999999999")})
    nav, client = ready(feed, {"sandnes elektriske": ["a1", "a2"]})
    result = nav.jobs_for(ENTITY, {ORG})
    assert [j["id"] for j in result["jobs"]] == ["a1"]
    assert result["incomplete"] is False
    assert result["rejected"] == 1  # a namesake with a different organisation number
    assert client.requests_made == 2


def test_an_ad_from_a_registered_sub_unit_of_the_company_is_accepted():
    feed = Feed(details={"a1": detail("a1", "973150480")})
    nav, _ = ready(feed, {"sandnes elektriske": ["a1"]})
    result = nav.jobs_for(ENTITY, {ORG, "973150480"})
    assert [j["id"] for j in result["jobs"]] == ["a1"]


def test_historical_registry_names_also_nominate_candidates():
    feed = Feed(details={"a1": detail("a1", ORG)})
    nav, _ = ready(feed, {"sandnes elektriske forretning": ["a1"]})
    assert [j["id"] for j in nav.jobs_for(ENTITY, {ORG})["jobs"]] == ["a1"]


def test_inactive_ads_are_dropped_even_if_the_index_still_lists_them():
    feed = Feed(details={"a1": detail("a1", ORG, status="INACTIVE")})
    nav, _ = ready(feed, {"sandnes elektriske": ["a1"]})
    result = nav.jobs_for(ENTITY, {ORG})
    assert result["jobs"] == [] and result["status"] == "not_available"


def test_a_published_job_carries_dates_location_url_and_provenance():
    feed = Feed(details={"a1": detail("a1", ORG)})
    nav, _ = ready(feed, {"sandnes elektriske": ["a1"]})
    result = nav.jobs_for(ENTITY, {ORG})
    job = result["jobs"][0]
    assert job["title"] == "Elektriker"
    assert job["published"] == "2026-09-20T00:00:00+02:00"
    assert job["expires"] == "2026-10-20T00:00:00+02:00"
    assert job["application_due"] == "2026-10-15T00:00:00"
    assert job["location"] == "SANDNES"
    assert job["url"] == "https://arbeidsplassen.nav.no/stillinger/stilling/a1"
    assert job["employer_orgnr"] == ORG
    evidence = result["evidence"][job["id"]]
    assert evidence["source_url"].endswith("/api/v1/feedentry/a1")
    assert evidence["content_sha256"] and evidence["retrieved_at"]


def test_no_candidate_ads_means_not_available_without_any_request():
    nav, client = ready(Feed(), {})
    result = nav.jobs_for(ENTITY, {ORG})
    assert result["status"] == "not_available"
    assert client.requests_made == 0


def test_candidate_ads_per_company_are_capped():
    ids = [f"a{i}" for i in range(20)]
    feed = Feed(details={i: detail(i, ORG) for i in ids})
    nav, client = ready(feed, {"sandnes elektriske": ids})
    nav.max_ads_per_company = 5
    nav.jobs_for(ENTITY, {ORG})
    assert client.requests_made == 5


def test_a_server_error_on_every_ad_lookup_is_failed_not_empty():
    feed = Feed(details={}, detail_status={"a1": 503})
    nav, _ = ready(feed, {"sandnes elektriske": ["a1"]})
    assert nav.jobs_for(ENTITY, {ORG})["status"] == "failed"


def test_one_failed_lookup_does_not_hide_the_ads_that_did_load():
    feed = Feed(details={"a2": detail("a2", ORG)}, detail_status={"a1": 503})
    nav, _ = ready(feed, {"sandnes elektriske": ["a1", "a2"]})
    result = nav.jobs_for(ENTITY, {ORG})
    assert [j["id"] for j in result["jobs"]] == ["a2"]
    assert result["status"] == "available"
    assert result["incomplete"] is True  # a1 could not be checked: never treat the list as complete


# ---- the feed answers 404 when nothing changed since If-Modified-Since -------------


def test_a_404_on_the_first_catch_up_request_means_nothing_changed_since_the_index_was_built():
    feed = Feed()
    original = feed.handler

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == f"{BASE}/api/v1/feed":
            return httpx.Response(404, text="")
        return original(request)

    client = CountingClient(transport=httpx.MockTransport(handler), max_requests=50, resolver=lambda h: ["8.8.8.8"])
    nav = NavJobs(NavIndex(built_at=BUILT, since="2026-03-26", by_name={"sandnes elektriske": ["a1"]}), client)
    summary = nav.prepare()
    assert summary["ready"] is True
    assert summary["pages"] == 0 and summary["up_to_date"] is True
    assert nav.index.by_name == {"sandnes elektriske": ["a1"]}


def test_a_server_error_during_catch_up_is_not_mistaken_for_up_to_date():
    feed = Feed()
    original = feed.handler

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == f"{BASE}/api/v1/feed":
            return httpx.Response(503, text="down")
        return original(request)

    client = CountingClient(transport=httpx.MockTransport(handler), max_requests=50, resolver=lambda h: ["8.8.8.8"])
    nav = NavJobs(NavIndex(built_at=BUILT, since="2026-03-26", by_name={}), client)
    assert nav.prepare()["ready"] is False


def test_a_404_on_a_later_page_is_an_error_not_up_to_date():
    feed = Feed(tail_pages=[[("n1", "ACTIVE", "X")], [("n2", "ACTIVE", "X")]])
    original = feed.handler

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url).endswith("/api/v1/feed/1"):
            return httpx.Response(404, text="")
        return original(request)

    client = CountingClient(transport=httpx.MockTransport(handler), max_requests=50, resolver=lambda h: ["8.8.8.8"])
    nav = NavJobs(NavIndex(built_at=BUILT, since="2026-03-26", by_name={}), client)
    assert nav.prepare()["ready"] is False
