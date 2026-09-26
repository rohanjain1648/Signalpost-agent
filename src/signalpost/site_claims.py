"""Turn a `research_website` result into claims, evidence and ledger observations.

Every claim cites the page it came from. If the site was not verified as this exact
entity, every website-derived claim carries the same non-available state and a note
saying why, but no value: nothing about an unverified site is published as a fact.
"""
from __future__ import annotations

import urllib.parse
from dataclasses import dataclass, field
from typing import Any

from .models import Claim, Evidence

EXTRACTOR = "site-extract/1"
WEB_FIELDS = ("official_website", "brand_name", "company_profiles", "contact", "hiring")
WEB_FAMILIES = {"website", "socials", "contact", "jobs"}
CONFIDENCE = {"P1": 0.99, "P2": 0.95, "P3": 0.95, "P4": 0.9}
REGISTRY_EVIDENCE = "ev-registry_live"


@dataclass
class SiteOutput:
    claims: list[Claim] = field(default_factory=list)
    evidence: list[Evidence] = field(default_factory=list)
    observations: list[dict[str, Any]] = field(default_factory=list)
    checked: set[str] = field(default_factory=set)
    failed: set[str] = field(default_factory=set)


def entity_unavailable_claims(entity_status: str) -> list[Claim]:
    availability = "not_applicable" if entity_status == "not_found" else "failed"
    return [Claim(field=name, availability=availability, evidence_ids=[REGISTRY_EVIDENCE],
                  note="registry entity could not be read") for name in WEB_FIELDS]


def failed_site_output(note: str) -> SiteOutput:
    """Website research crashed: report every website family as failed and leave the ledger stale."""
    out = SiteOutput(failed=set(WEB_FAMILIES))
    out.claims = [Claim(field=name, availability="failed", evidence_ids=[REGISTRY_EVIDENCE], note=note)
                  for name in WEB_FIELDS]
    return out


def _domain(url: str) -> str:
    host = (urllib.parse.urlparse(url).hostname or "").lower()
    return host.removeprefix("www.")


def _rejected(site: dict[str, Any]) -> SiteOutput:
    status = site["status"]
    reasons = list(dict.fromkeys(c["reason"] for c in site["candidates"] if c.get("reason")))
    note = "; ".join(reasons) or "registry lists no website and no company-domain email address"
    out = SiteOutput()
    fetched = next((c for c in site["candidates"] if c.get("retrieved_at")), None)
    if fetched:
        out.evidence.append(Evidence(
            id="ev-site-candidate", source_url=fetched["url"], final_url=fetched.get("final_url"),
            source_class="unverified_candidate", retrieved_at=fetched["retrieved_at"],
            content_sha256=fetched.get("content_sha256"), extractor=EXTRACTOR,
        ))
    cited = ["ev-site-candidate"] if fetched else [REGISTRY_EVIDENCE]
    out.claims = [Claim(field=name, availability=status, evidence_ids=list(cited), note=note) for name in WEB_FIELDS]
    if status == "not_available":
        out.checked = set(WEB_FAMILIES)
    else:  # ambiguous, blocked or failed: we do not know, so never close or change anything
        out.failed = set(WEB_FAMILIES)
    return out


def build_site_output(site: dict[str, Any]) -> SiteOutput:
    if site["status"] != "available":
        return _rejected(site)

    out = SiteOutput()
    home, proof = site["homepage"], site["proof"]
    home_url = home["final_url"] or home["url"]
    out.evidence.append(Evidence(
        id="ev-site-home", source_url=home["url"], final_url=home["final_url"], source_class="company_owned",
        retrieved_at=home["retrieved_at"], content_sha256=home["content_sha256"],
        claim_span=proof["span"], extractor=EXTRACTOR,
    ))
    page_ids = {home_url: "ev-site-home", home["url"]: "ev-site-home"}
    for page in site["pages"]:
        evidence_id = f"ev-site-{page['kind']}"
        out.evidence.append(Evidence(
            id=evidence_id, source_url=page["url"], final_url=page["final_url"], source_class="company_owned",
            retrieved_at=page["retrieved_at"], content_sha256=page["content_sha256"], extractor=EXTRACTOR,
        ))
        page_ids[page["final_url"] or page["url"]] = evidence_id
        page_ids[page["url"]] = evidence_id

    def cite(*urls: str) -> list[str]:
        return list(dict.fromkeys(page_ids.get(u, "ev-site-home") for u in urls)) or ["ev-site-home"]

    confidence = CONFIDENCE.get(proof["tier"], 0.9)
    out.claims.append(Claim(
        field="official_website", availability="available", confidence=confidence, evidence_ids=["ev-site-home"],
        value={"url": home_url, "domain": _domain(home_url), "proof_tier": proof["tier"], "proof": proof["reasons"]},
    ))

    brand = home.get("site_name") or next(
        (o["name"] for o in site["structured_organizations"] if o.get("name")), None)
    out.claims.append(Claim(
        field="brand_name", value=brand, availability="available" if brand else "not_available",
        confidence=confidence if brand else None, evidence_ids=["ev-site-home"],
        note=None if brand else "the verified site declares no brand name",
    ))

    socials = [{"platform": s["platform"], "url": s["url"]} for s in site["socials"]]
    out.claims.append(Claim(
        field="company_profiles", value=socials or None, availability="available" if socials else "not_available",
        confidence=confidence if socials else None,
        evidence_ids=cite(*[s["page_url"] for s in site["socials"]]) if socials else ["ev-site-home"],
        note=None if socials else "the verified site links to no company-owned social profiles",
    ))

    emails, phones = site["emails"], site["phones"]
    has_contact = bool(emails or phones)
    out.claims.append(Claim(
        field="contact", availability="available" if has_contact else "not_available",
        value={"emails": emails, "phones": phones} if has_contact else None,
        confidence=confidence if has_contact else None,
        evidence_ids=cite(*site["contact_sources"].values()) if has_contact else ["ev-site-home"],
        note=None if has_contact else "no contact details found on the verified site's pages",
    ))

    jobs, careers = site["jobs"], site["careers_links"]
    if jobs or (careers and site["careers_checked"]):
        out.claims.append(Claim(
            field="hiring", availability="available", confidence=confidence,
            value={"jobs": jobs, "careers_pages": careers},
            evidence_ids=cite(*[j["page_url"] for j in jobs]) if jobs else cite(*careers),
            note=None if jobs else "a careers page exists but publishes no structured job postings",
        ))
    elif careers:
        out.claims.append(Claim(field="hiring", availability="failed", evidence_ids=["ev-site-home"],
                                note="a careers page is linked but could not be read"))
    else:
        out.claims.append(Claim(field="hiring", availability="not_available", evidence_ids=["ev-site-home"],
                                note="no careers page or job postings found on the verified site"))

    out.observations.append({"family": "website", "key": "official_website", "evidence_id": "ev-site-home",
                             "value": {"url": home_url, "domain": _domain(home_url)}})
    for social in site["socials"]:
        out.observations.append({
            "family": "socials", "key": f"{social['platform']}|{social['url']}",
            "value": {"platform": social["platform"], "url": social["url"]},
            "evidence_id": cite(social["page_url"])[0],
        })
    for key, page_url in site["contact_sources"].items():
        kind, value = key.split(":", 1)
        out.observations.append({"family": "contact", "key": key, "value": {"type": kind, "value": value},
                                 "evidence_id": cite(page_url)[0]})
    for job in jobs:
        out.observations.append({
            "family": "jobs", "key": job["url"] or f"{job['title']}|{job['location']}|{job['date_posted']}",
            "value": {k: job[k] for k in ("title", "date_posted", "valid_through", "employment_type", "location", "url")},
            "evidence_id": cite(job["page_url"])[0],
        })

    out.checked = {"website", "socials", "contact"}
    if site["careers_checked"]:
        out.checked.add("jobs")
    elif careers:
        out.failed.add("jobs")
    return out
