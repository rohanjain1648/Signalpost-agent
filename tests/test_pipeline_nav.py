from pathlib import Path

import httpx

from signalpost.nav import NavIndex, NavJobs
from signalpost.net import CountingClient
from signalpost.official import (
    BRREG_ACCOUNT_YEARS, BRREG_ACCOUNTS, BRREG_ENTITY, BRREG_ROLES, BRREG_SUBUNITS,
)
from signalpost.pipeline import WebResearch, process_company
from signalpost.robots import RobotsCache
from test_nav import Feed, detail
from test_pipeline_web import HOME_WITH_BRAND, site_routes

FIXTURES = Path(__file__).parent / "fixtures" / "brreg"
ORG = "810034882"
SUBUNIT = "973150480"  # a registered sub-unit in the recorded fixture
T1, T2 = "2026-10-01T00:00:00Z", "2026-10-02T00:00:00Z"
BRREG = {
    BRREG_ENTITY.format(org=ORG): f"entity_{ORG}.json",
    BRREG_ROLES.format(org=ORG): f"roller_{ORG}.json".replace("roller", "roles"),
    BRREG_SUBUNITS.format(org=ORG): f"subunits_{ORG}.json",
    BRREG_ACCOUNTS.format(org=ORG): f"accounts_{ORG}.json",
    BRREG_ACCOUNT_YEARS.format(org=ORG): f"years_{ORG}.json",
}
INDEX = {"sandnes elektriske": ["a1"]}


def make(feed, by_name=None, with_site=False, site_overrides=None):
    routes = {**site_routes(), **(site_overrides or {})}

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url in BRREG:
            return httpx.Response(200, content=(FIXTURES / BRREG[url]).read_bytes(),
                                  headers={"content-type": "application/json"})
        if "pam-stilling-feed" in url:
            return feed.handler(request)
        if url in routes:
            status, body = routes[url]
            if status in (301, 302):
                return httpx.Response(status, headers={"location": body})
            return httpx.Response(status, text=body, headers={"content-type": "text/html; charset=utf-8"})
        return httpx.Response(404, text="nope")

    client = CountingClient(transport=httpx.MockTransport(handler), max_requests=300, resolver=lambda h: ["8.8.8.8"])
    nav = NavJobs(NavIndex(built_at="2026-09-20T10:00:00Z", since="2026-03-26",
                           by_name=dict(by_name if by_name is not None else INDEX)), client)
    nav.prepare()
    web = WebResearch(robots=RobotsCache(client), multiplicity={}, discover=False) if with_site else None
    return client, nav, web


def run(bundle, prev=None, ts=T1):
    client, nav, web = bundle
    return process_company(ORG, client, run_id="r1", run_ts=ts, prev_state=prev or {}, web=web, nav=nav)


def hiring(envelope):
    return {c.field: c for c in envelope.claims}["hiring"]


def evidence(envelope):
    return {e.id: e for e in envelope.evidence}


def feed_with(*ads, **kw):
    return Feed(details={a["uuid"]: a for a in ads}, **kw)


def test_a_nav_ad_with_this_companys_number_becomes_a_cited_job():
    envelope, _ = run(make(feed_with(detail("a1", ORG))))
    claim = hiring(envelope)
    assert claim.availability == "available"
    job = claim.value["jobs"][0]
    assert job["title"] == "Elektriker" and job["source"] == "nav_feed"
    ev = evidence(envelope)[claim.evidence_ids[0]]
    assert ev.source_class == "job_feed_api" and ev.source_url.endswith("/api/v1/feedentry/a1")
    assert ORG in ev.claim_span and "Elektriker" in ev.claim_span


def test_a_namesake_ad_with_another_number_is_never_published():
    envelope, _ = run(make(feed_with(detail("a1", "999999999"))))
    claim = hiring(envelope)
    assert claim.availability == "not_available" and claim.value is None
    assert "999999999" not in envelope.model_dump_json()
    assert not any(e.source_class == "job_feed_api" for e in envelope.evidence)


def test_an_ad_from_a_registered_sub_unit_is_attributed_to_the_company():
    envelope, _ = run(make(feed_with(detail("a1", SUBUNIT))))
    assert hiring(envelope).availability == "available"


def test_the_same_posting_on_the_site_and_in_nav_is_listed_once_with_both_sources():
    site_home = HOME_WITH_BRAND
    careers = ('<html><title>Karriere</title><script type="application/ld+json">{"@type":"JobPosting",'
               '"title":"Elektriker","datePosted":"2026-09-01","url":"https://www.sandneselektriske.no/karriere/1",'
               '"jobLocation":{"address":{"addressLocality":"SANDNES"}}}</script></html>')
    bundle = make(feed_with(detail("a1", ORG)), with_site=True,
                  site_overrides={"https://www.sandneselektriske.no/": (200, site_home),
                                  "https://www.sandneselektriske.no/karriere": (200, careers)})
    envelope, _ = run(bundle)
    jobs = hiring(envelope).value["jobs"]
    assert len(jobs) == 1
    assert jobs[0]["source"] == "nav_feed"
    assert jobs[0]["also_listed_at"] == "https://www.sandneselektriske.no/karriere/1"


def test_distinct_postings_from_both_sources_are_all_kept():
    careers = ('<html><title>Karriere</title><script type="application/ld+json">{"@type":"JobPosting",'
               '"title":"Lærling","url":"https://www.sandneselektriske.no/karriere/2"}</script></html>')
    bundle = make(feed_with(detail("a1", ORG)), with_site=True,
                  site_overrides={"https://www.sandneselektriske.no/karriere": (200, careers)})
    envelope, _ = run(bundle)
    assert {j["title"] for j in hiring(envelope).value["jobs"]} == {"Elektriker", "Lærling"}


def test_nav_jobs_are_new_once_then_quiet_and_closed_when_the_ad_ends():
    _, state1 = None, None
    envelope1, state1 = run(make(feed_with(detail("a1", ORG))), ts=T1)
    assert "new_job" in {c["type"] for c in envelope1.changes}
    envelope2, state2 = run(make(feed_with(detail("a1", ORG))), prev=state1, ts=T2)
    assert envelope2.changes == []
    envelope3, _ = run(make(feed_with(detail("a1", ORG, status="INACTIVE"))), prev=state2, ts="2026-10-03T00:00:00Z")
    assert [c["type"] for c in envelope3.changes] == ["closed_job"]


def test_a_nav_outage_keeps_the_last_jobs_as_stale_and_reports_no_change():
    _, state1 = run(make(feed_with(detail("a1", ORG))), ts=T1)
    envelope2, state2 = run(make(Feed(token_status=503)), prev=state1, ts=T2)
    assert hiring(envelope2).availability == "failed"
    assert envelope2.changes == []
    nav_claims = [c for c in state2.values() if c["family"] == "nav_jobs"]
    assert nav_claims and all(c["status"] == "stale" for c in nav_claims)


def test_nav_requests_are_counted_in_the_envelope():
    bundle = make(feed_with(detail("a1", ORG)))
    before = bundle[0].requests_made
    envelope, _ = run(bundle)
    assert envelope.operations.requests == bundle[0].requests_made - before >= 6  # 5 official + 1 ad


def test_without_nav_the_pipeline_is_unchanged_and_makes_no_feed_requests():
    client, _, _ = make(feed_with())
    envelope, _ = process_company(ORG, client, run_id="r1", run_ts=T1, prev_state={})
    assert "hiring" not in {c.field for c in envelope.claims}


def test_nav_lookups_are_skipped_after_the_soft_deadline():
    from signalpost.pipeline import TimeBudget

    bundle = make(feed_with(detail("a1", ORG)))
    client, nav, web = bundle
    before = client.requests_made
    envelope, _ = process_company(ORG, client, run_id="r1", run_ts=T1, prev_state={}, web=web, nav=nav,
                                  budget=TimeBudget(soft_deadline=5.0, clock=lambda: 10.0))
    claim = hiring(envelope)
    assert claim.availability == "failed" and "time budget" in claim.note
    assert client.requests_made - before == 5  # official only: no NAV ad lookups
