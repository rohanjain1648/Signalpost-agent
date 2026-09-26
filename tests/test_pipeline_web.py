from pathlib import Path

import httpx

from signalpost.net import CountingClient
from signalpost.official import (
    BRREG_ACCOUNT_YEARS, BRREG_ACCOUNTS, BRREG_ENTITY, BRREG_ROLES, BRREG_SUBUNITS,
)
from signalpost.pipeline import WebResearch, process_company
from signalpost.robots import RobotsCache
from test_web import ABOUT, CAREERS, CONTACT, HOME, HOST, NEWS

FIXTURES = Path(__file__).parent / "fixtures" / "brreg"
ORG = "810034882"
T1, T2 = "2026-10-01T00:00:00Z", "2026-10-02T00:00:00Z"
BARE = "https://sandneselektriske.no"

BRREG = {
    BRREG_ENTITY.format(org=ORG): f"entity_{ORG}.json",
    BRREG_ROLES.format(org=ORG): f"roles_{ORG}.json",
    BRREG_SUBUNITS.format(org=ORG): f"subunits_{ORG}.json",
    BRREG_ACCOUNTS.format(org=ORG): f"accounts_{ORG}.json",
    BRREG_ACCOUNT_YEARS.format(org=ORG): f"years_{ORG}.json",
}


def site_routes():
    ok = "User-agent: *\nDisallow:\n"
    return {
        f"{BARE}/": (301, f"{HOST}/"),
        f"{HOST}/robots.txt": (200, ok), f"{HOST}/": (200, HOME),
        f"{HOST}/kontakt": (200, CONTACT), f"{HOST}/om-oss": (200, ABOUT),
        f"{HOST}/karriere": (200, CAREERS), f"{HOST}/nyheter": (200, NEWS),
    }


def make(routes=None, table=None):
    merged = site_routes()
    merged.update(routes or {})
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        seen.append(url)
        if url in BRREG:
            return httpx.Response(200, content=(FIXTURES / BRREG[url]).read_bytes(),
                                  headers={"content-type": "application/json"})
        if url not in merged:
            return httpx.Response(404, text="nope")
        status, body = merged[url]
        if status in (301, 302):
            return httpx.Response(status, headers={"location": body})
        return httpx.Response(status, text=body, headers={"content-type": "text/html; charset=utf-8"})

    client = CountingClient(transport=httpx.MockTransport(handler), max_requests=200, resolver=lambda h: ["8.8.8.8"])
    return client, WebResearch(robots=RobotsCache(client), multiplicity={} if table is None else table), seen


def run(bundle, prev=None, ts=T1, web=True):
    client, research, _ = bundle
    return process_company(ORG, client, run_id="r1", run_ts=ts, prev_state=prev or {},
                           web=research if web else None)


def by_field(envelope):
    return {c.field: c for c in envelope.claims}


def evidence_by_id(envelope):
    return {e.id: e for e in envelope.evidence}


HOME_WITH_BRAND = HOME.replace(
    '<meta name="description"', '<meta property="og:site_name" content="Sandnes Elektriske"><meta name="description"'
)


def test_brand_is_never_guessed_from_the_page_title():
    envelope, _ = run(make())  # HOME declares no og:site_name and no JSON-LD name
    brand = by_field(envelope)["brand_name"]
    assert brand.availability == "not_available" and brand.value is None


def test_verified_site_yields_claims_that_cite_the_exact_pages():
    envelope, _ = run(make({f"{HOST}/": (200, HOME_WITH_BRAND)}))
    claims, evidence = by_field(envelope), evidence_by_id(envelope)

    site = claims["official_website"]
    assert site.availability == "available"
    assert site.value["url"] == f"{HOST}/" and site.value["proof_tier"] == "P3"
    assert evidence[site.evidence_ids[0]].source_class == "company_owned"
    assert evidence[site.evidence_ids[0]].claim_span  # the proof span is quoted

    assert claims["brand_name"].value == "Sandnes Elektriske"
    assert claims["company_profiles"].value == [
        {"platform": "facebook", "url": "https://www.facebook.com/sandneselektriske"}
    ]

    contact = claims["contact"]
    assert "post@sandneselektriske.no" in contact.value["emails"]
    assert evidence[contact.evidence_ids[0]].source_url.endswith("/kontakt")

    hiring = claims["hiring"]
    assert hiring.availability == "available" and hiring.value["jobs"][0]["title"] == "Elektriker"
    assert evidence[hiring.evidence_ids[0]].source_url.endswith("/karriere")

    for claim in envelope.claims:
        assert set(claim.evidence_ids) <= set(evidence)


def test_first_run_reports_web_changes_and_identical_rerun_reports_none():
    envelope1, state1 = run(make(), ts=T1)
    assert {"new_website", "new_social_profile", "new_job"} <= {c["type"] for c in envelope1.changes}
    envelope2, _ = run(make(), prev=state1, ts=T2)
    assert envelope2.changes == []


def test_shared_domain_publishes_no_site_facts_and_says_why():
    envelope, state = run(make(table={"sandneselektriske.no": 40}))
    claims = by_field(envelope)
    for field in ("official_website", "brand_name", "company_profiles", "contact", "hiring"):
        assert claims[field].availability == "ambiguous", field
        assert claims[field].value is None, field
    assert "shared" in claims["official_website"].note
    assert not any(c["family"] in {"website", "socials", "jobs", "contact"} for c in state.values())
    assert not any(e.source_class == "company_owned" for e in envelope.evidence)


def test_site_outage_on_refresh_is_failed_stale_and_reports_no_change():
    _, state1 = run(make(), ts=T1)
    outage = make({f"{BARE}/": (503, "")})
    envelope2, state2 = run(outage, prev=state1, ts=T2)
    assert by_field(envelope2)["official_website"].availability == "failed"
    assert envelope2.changes == []
    web_claims = [c for c in state2.values() if c["family"] in {"website", "socials", "jobs", "contact"}]
    assert web_claims and all(c["status"] == "stale" for c in web_claims)


def test_job_removed_from_a_read_careers_page_is_closed():
    _, state1 = run(make(), ts=T1)
    no_jobs = "<html><title>Karriere</title><body>Ingen ledige stillinger nå</body></html>"
    envelope2, _ = run(make({f"{HOST}/karriere": (200, no_jobs)}), prev=state1, ts=T2)
    assert [c["type"] for c in envelope2.changes] == ["closed_job"]


def test_job_is_not_closed_when_the_careers_page_could_not_be_read():
    _, state1 = run(make(), ts=T1)
    envelope2, state2 = run(make({f"{HOST}/karriere": (503, "")}), prev=state1, ts=T2)
    assert envelope2.changes == []
    assert not any(c["status"] == "closed" for c in state2.values())


def test_web_disabled_emits_no_website_claims_and_makes_no_site_requests():
    bundle = make()
    envelope, _ = run(bundle, web=False)
    assert "official_website" not in by_field(envelope)
    assert bundle[0].requests_made == 5


def test_site_requests_are_counted_in_the_envelope_operations():
    bundle = make()
    envelope, _ = run(bundle)
    assert envelope.operations.requests == bundle[0].requests_made > 5


def test_entity_without_any_website_candidate_is_not_available_citing_the_registry():
    body = (FIXTURES / f"entity_{ORG}.json").read_text(encoding="utf-8").replace("post@sandneselektriske.no", "kari@gmail.com")
    bundle = make()
    client = bundle[0]
    original = client._client._transport.handler

    def patched(request):
        if str(request.url) == BRREG_ENTITY.format(org=ORG):
            return httpx.Response(200, content=body.encode("utf-8"), headers={"content-type": "application/json"})
        return original(request)

    client._client._transport.handler = patched
    envelope, _ = run(bundle)
    site = by_field(envelope)["official_website"]
    assert site.availability == "not_available"
    assert site.evidence_ids == ["ev-registry_live"]


def test_a_crash_in_website_research_never_discards_the_official_data(monkeypatch):
    import signalpost.pipeline as pipeline

    def boom(*args, **kwargs):
        raise AssertionError("parser exploded")

    monkeypatch.setattr(pipeline, "research_website", boom)
    envelope, state = run(make())
    claims = by_field(envelope)
    assert claims["legal_identity"].availability == "available"
    assert claims["leadership"].availability == "available"
    for field in ("official_website", "brand_name", "company_profiles", "contact", "hiring"):
        assert claims[field].availability == "failed", field
    assert envelope.errors[-1]["module"] == "website"
    assert "parser exploded" in envelope.errors[-1]["message"]
    assert envelope.run.terminal_status == "completed"
    assert any(c["family"] == "roles" for c in state.values())


def test_quarantined_profiles_are_explained_in_the_claim_note_but_never_published():
    home = HOME.replace('<a href="https://www.facebook.com/sandneselektriske">FB</a>',
                        '<a href="https://www.facebook.com/somewebagency">Laget av</a>')
    envelope, state = run(make({f"{HOST}/": (200, home)}))
    claim = by_field(envelope)["company_profiles"]
    assert claim.availability == "not_available" and claim.value is None
    assert "1 profile link" in claim.note and "could not be tied" in claim.note
    assert "somewebagency" not in envelope.model_dump_json()
    assert not any(c["family"] == "socials" for c in state.values())
