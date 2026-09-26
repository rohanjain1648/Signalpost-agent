import pytest
from pydantic import ValidationError

from signalpost.models import AVAILABILITY_STATES, Claim, Envelope, Evidence


def test_claim_rejects_invalid_availability_state():
    with pytest.raises(ValidationError):
        Claim(
            field="official_website",
            value="https://example.no/",
            availability="found_it",  # not a real state
            confidence=0.9,
            evidence_ids=["ev-1"],
        )


def test_claim_accepts_each_contract_availability_state():
    for state in AVAILABILITY_STATES:
        claim = Claim(
            field="official_website",
            value=None,
            availability=state,
            confidence=None,
            evidence_ids=[],
        )
        assert claim.availability == state


def test_evidence_requires_source_url_and_retrieved_at():
    with pytest.raises(ValidationError):
        Evidence(id="ev-1", source_class="official_registry_live")


def test_evidence_round_trips_required_provenance_fields():
    ev = Evidence(
        id="ev-1",
        source_url="https://data.brreg.no/enhetsregisteret/api/enheter/810034882",
        source_class="official_registry_live",
        retrieved_at="2026-09-26T00:00:00Z",
        content_sha256="a" * 64,
        claim_span="organisasjonsnummer=810034882",
    )
    assert ev.source_url.startswith("https://")
    assert ev.retrieved_at == "2026-09-26T00:00:00Z"


def test_envelope_rejects_organisation_number_that_is_not_nine_digits():
    with pytest.raises(ValidationError):
        Envelope(
            organisation_number="12345",
            run={
                "run_id": "r1",
                "started_at": "2026-09-26T00:00:00Z",
                "completed_at": "2026-09-26T00:00:08Z",
                "terminal_status": "completed",
            },
        )


def test_envelope_serializes_the_contract_shape():
    envelope = Envelope(
        organisation_number="810034882",
        run={
            "run_id": "r1",
            "started_at": "2026-09-26T00:00:00Z",
            "completed_at": "2026-09-26T00:00:08Z",
            "terminal_status": "completed",
        },
        claims=[
            Claim(
                field="official_website",
                value="https://example.no/",
                availability="available",
                confidence=0.99,
                evidence_ids=["ev-1"],
            )
        ],
        evidence=[
            Evidence(
                id="ev-1",
                source_url="https://example.no/",
                source_class="company_owned",
                retrieved_at="2026-09-26T00:00:04Z",
                content_sha256="b" * 64,
                claim_span="Example AS",
            )
        ],
    )
    body = envelope.model_dump(mode="json")
    assert set(body) == {
        "organisation_number",
        "run",
        "claims",
        "evidence",
        "changes",
        "errors",
        "operations",
    }
    assert body["organisation_number"] == "810034882"
    assert body["claims"][0]["evidence_ids"] == ["ev-1"]
    assert body["changes"] == []
    assert body["errors"] == []
