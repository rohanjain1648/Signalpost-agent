import json
from pathlib import Path

import httpx

from signalpost.net import CountingClient
from signalpost.official import (
    BRREG_ACCOUNT_YEARS,
    BRREG_ACCOUNTS,
    BRREG_ENTITY,
    BRREG_ROLES,
    BRREG_SUBUNITS,
)
from signalpost.pipeline import process_company

FIXTURES = Path(__file__).parent / "fixtures" / "brreg"
ORG = "810034882"
T1 = "2026-10-01T00:00:00Z"
T2 = "2026-10-02T00:00:00Z"

URL_TO_FIXTURE = {
    BRREG_ENTITY.format(org=ORG): f"entity_{ORG}.json",
    BRREG_ROLES.format(org=ORG): f"roles_{ORG}.json",
    BRREG_SUBUNITS.format(org=ORG): f"subunits_{ORG}.json",
    BRREG_ACCOUNTS.format(org=ORG): f"accounts_{ORG}.json",
    BRREG_ACCOUNT_YEARS.format(org=ORG): f"years_{ORG}.json",
}


def make_client(overrides=None):
    overrides = overrides or {}

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url in overrides:
            return httpx.Response(overrides[url], text="upstream")
        if url in URL_TO_FIXTURE:
            body = (FIXTURES / URL_TO_FIXTURE[url]).read_text(encoding="utf-8")
            return httpx.Response(200, content=body.encode("utf-8"), headers={"content-type": "application/json"})
        return httpx.Response(404, text="unexpected url " + url)

    return CountingClient(transport=httpx.MockTransport(handler), max_requests=100, resolver=lambda h: ["8.8.8.8"])


def run(client, prev=None, ts=T1):
    return process_company(ORG, client, run_id="r1", run_ts=ts, prev_state=prev or {})


def claims_by_field(envelope):
    return {c.field: c for c in envelope.claims}


def test_official_families_become_available_claims_with_resolvable_evidence():
    envelope, _ = run(make_client())
    claims = claims_by_field(envelope)
    for field in ("legal_identity", "annual_accounts", "financial_history", "leadership", "registered_workplaces"):
        assert claims[field].availability == "available", field
    evidence_ids = {e.id for e in envelope.evidence}
    for claim in envelope.claims:
        assert claim.evidence_ids, claim.field
        assert set(claim.evidence_ids) <= evidence_ids


def test_annual_accounts_claim_carries_reporting_period_and_real_figures():
    envelope, _ = run(make_client())
    claim = claims_by_field(envelope)["annual_accounts"]
    assert claim.as_of == "2025-12-31"
    assert claim.value["records"][0]["revenue"] == 10021242.0


def test_group_claim_is_not_applicable_when_entity_is_not_in_a_group():
    envelope, _ = run(make_client())
    assert claims_by_field(envelope)["corporate_group"].availability == "not_applicable"


def test_no_person_birth_dates_are_published():
    envelope, _ = run(make_client())
    dumped = envelope.model_dump_json()
    assert "fodselsdato" not in dumped
    assert "1981-09-27" not in dumped


def test_only_official_endpoints_are_requested_and_counted():
    client = make_client()
    envelope, _ = run(client)
    assert client.requests_made == 5
    assert envelope.operations.requests == 5


def test_first_run_reports_new_changes_and_identical_rerun_reports_none():
    envelope1, state1 = run(make_client(), ts=T1)
    types = {c["type"] for c in envelope1.changes}
    assert {"new_role", "new_location", "new_filing"} <= types

    envelope2, state2 = run(make_client(), prev=state1, ts=T2)
    assert envelope2.changes == []
    assert set(state2) == set(state1)


def test_entity_not_found_is_not_available_not_failed():
    overrides = {url: 404 for url in URL_TO_FIXTURE}
    envelope, _ = run(make_client(overrides))
    assert claims_by_field(envelope)["legal_identity"].availability == "not_available"
    assert envelope.run.terminal_status == "completed"


def test_registry_outage_is_failed_and_reported_as_error():
    overrides = {url: 503 for url in URL_TO_FIXTURE}
    envelope, _ = run(make_client(overrides))
    assert claims_by_field(envelope)["legal_identity"].availability == "failed"
    assert envelope.run.terminal_status == "failed"
    assert envelope.errors


def test_failed_roles_refresh_keeps_last_supported_roles_and_reports_no_change():
    _, state1 = run(make_client(), ts=T1)
    roles_url = BRREG_ROLES.format(org=ORG)
    envelope2, state2 = run(make_client({roles_url: 503}), prev=state1, ts=T2)

    assert claims_by_field(envelope2)["leadership"].availability == "failed"
    assert not any(c["type"] == "ended_role" for c in envelope2.changes)
    role_claims = [c for c in state2.values() if c["family"] == "roles"]
    assert role_claims and all(c["status"] == "stale" for c in role_claims)
    assert all(c["value"] for c in role_claims)
