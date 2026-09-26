"""Combine job postings from the company's own site and the NAV feed into one `hiring` claim.

NAV ads are attributed by the ad's own employer organisation number, so they are the stronger
source; when the same posting appears in both, one entry is kept (NAV's) and the company-site
URL is recorded as `also_listed_at`. The two sources keep separate ledger families so that
one source being unreachable can never close the other's jobs.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .models import Claim, Evidence
from .nav import SOURCE_CLASS

EXTRACTOR = "nav-feed/1"
REGISTRY_EVIDENCE = "ev-registry_live"


@dataclass
class HiringOutput:
    claim: Claim | None
    evidence: list[Evidence] = field(default_factory=list)
    observations: list[dict[str, Any]] = field(default_factory=list)
    checked: set[str] = field(default_factory=set)
    failed: set[str] = field(default_factory=set)


def _norm(value: Any) -> str:
    return " ".join(str(value or "").casefold().split())


def _evidence_id(uuid: str) -> str:
    return f"ev-nav-{uuid[:8]}"


def _join(*notes: str | None) -> str | None:
    text = "; ".join(n for n in notes if n)
    return text or None


def merge_hiring(site_claim: Claim | None, nav: dict[str, Any] | None) -> HiringOutput:
    if nav is None:
        return HiringOutput(claim=site_claim)

    out = HiringOutput(claim=None)
    if nav["status"] == "failed" or nav["incomplete"]:
        out.failed.add("nav_jobs")
    else:
        out.checked.add("nav_jobs")

    site_ok = site_claim is not None and site_claim.availability == "available" and isinstance(site_claim.value, dict)
    site_jobs = [dict(j, source="company_site") for j in (site_claim.value.get("jobs") or [])] if site_ok else []
    careers = list(site_claim.value.get("careers_pages") or []) if site_ok else []

    jobs: list[dict[str, Any]] = []
    for nav_job in nav["jobs"]:
        key = (_norm(nav_job["title"]), _norm(nav_job["location"]))
        twin = next((s for s in site_jobs if (_norm(s.get("title")), _norm(s.get("location"))) == key), None)
        merged = dict(nav_job)
        if twin is not None:
            merged["also_listed_at"] = twin.get("url") or twin.get("page_url")
            site_jobs.remove(twin)
        jobs.append(merged)
        uuid = nav_job["id"]
        raw = nav["evidence"][uuid]
        out.evidence.append(Evidence(
            id=_evidence_id(uuid), source_url=raw["source_url"], final_url=raw["final_url"],
            source_class=SOURCE_CLASS, retrieved_at=raw["retrieved_at"], content_sha256=raw["content_sha256"],
            claim_span=f'employer.orgnr {nav_job["employer_orgnr"]}: "{nav_job["title"]}"', extractor=EXTRACTOR,
        ))
        out.observations.append({
            "family": "nav_jobs", "key": uuid, "evidence_id": _evidence_id(uuid),
            "value": {k: nav_job[k] for k in ("title", "published", "expires", "application_due", "location", "url")},
        })
    jobs.extend(site_jobs)

    if jobs:
        ids = [_evidence_id(j["id"]) for j in jobs if j["source"] == "nav_feed"]
        if site_ok and (site_jobs or any("also_listed_at" in j for j in jobs)):
            ids.extend(i for i in site_claim.evidence_ids if i not in ids)
        sources = sorted({j["source"] for j in jobs})
        out.claim = Claim(
            field="hiring", availability="available", value={"jobs": jobs, "careers_pages": careers, "sources": sources},
            confidence=0.99 if "nav_feed" in sources else site_claim.confidence, evidence_ids=ids,
            note=_join(nav["note"] if nav["incomplete"] else None),
        )
        return out

    # No jobs anywhere. Keep a site verdict that says more than "nothing" (unknown, unreadable, careers page).
    nav_note = nav["note"]
    if site_claim is not None and site_claim.availability != "not_available":
        out.claim = site_claim.model_copy(update={"note": _join(site_claim.note, nav_note)})
    elif nav["status"] == "failed":
        out.claim = Claim(field="hiring", availability="failed", evidence_ids=[REGISTRY_EVIDENCE],
                          note=_join(nav_note, site_claim.note if site_claim else None))
    else:
        out.claim = Claim(field="hiring", availability="not_available",
                          evidence_ids=site_claim.evidence_ids if site_claim else [REGISTRY_EVIDENCE],
                          note=_join(nav_note, site_claim.note if site_claim else None))
    return out
