"""Identity-proof engine: decide whether a candidate site is *this* legal entity.

Proof tiers (see PLAN.md section 4):

  P1  this organisation number appears on the page                     -> exact
  P2  registry-listed website, unique domain, legal-name match         -> exact
  P3  registry email domain, unique domain, legal-name match           -> exact
  P4  full legal-name match plus the registered postal code on page    -> exact
  R   another org number is shown, or the domain is shared by >1 org   -> related
  A   anything else (parked page, partial name overlap, no anchor)     -> ambiguous

Only `exact` is publishable. A name match on its own never publishes: a
wrong-company publication blocks an official run, so when in doubt we abstain.
"""
from __future__ import annotations

import re
import unicodedata
import urllib.parse
from typing import Any, Iterable

LEGAL_AND_GENERIC = {
    "as", "asa", "ans", "da", "enk", "iks", "sa", "sam", "sti", "nuf",
    "ab", "b", "v", "limited", "ltd", "inc", "plc", "the", "og", "and",
}

FREEMAIL_DOMAINS = {
    "gmail.com", "hotmail.com", "hotmail.no", "outlook.com", "outlook.no", "yahoo.com",
    "yahoo.no", "live.com", "live.no", "icloud.com", "me.com", "msn.com", "online.no",
    "frisurf.no", "broadpark.no", "getmail.no", "lyse.net", "altibox.no", "c2i.net",
    "start.no", "hotmail.co.uk", "proton.me", "protonmail.com", "gmx.com", "tele2.no",
}

PARKED_MARKERS = (
    "domain is for sale", "domain for sale", "this domain may be for sale", "hugedomains",
    "parked at", "buy this domain", "domenet er til salgs", "her flytter snart en ny gjest",
)

_ORG_LABELLED = re.compile(
    r"(?:org(?:anisasjons)?\.?\s*(?:nr|nummer)\.?|orgnr|foretaksregisteret)\s*[:.]?\s*"
    r"(?:NO\s*)?(\d{3}\s?\d{3}\s?\d{3})(?!\d)",
    re.IGNORECASE,
)


def _fold(value: Any) -> str:
    text = str(value or "").translate(
        str.maketrans({"ø": "o", "Ø": "O", "å": "a", "Å": "A", "æ": "ae", "Æ": "AE"})
    )
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().casefold()


def name_tokens(name: Any) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9]+", _fold(name)) if t not in LEGAL_AND_GENERIC and len(t) > 1]


def _host(value: str | None) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if "://" not in raw:
        raw = "https://" + raw
    host = (urllib.parse.urlparse(raw).hostname or "").lower().rstrip(".")
    return host.removeprefix("www.")


def _email_domain(email: str | None) -> str:
    value = str(email or "").strip().lower()
    return value.split("@", 1)[1] if "@" in value else ""


def candidate_domains(entity: dict[str, Any]) -> list[tuple[str, str]]:
    """Domains worth fetching for this entity, each tagged with where it came from."""
    out: list[tuple[str, str]] = []
    site = _host(entity.get("website"))
    if site:
        out.append((site, "registry_website"))
    mail = _email_domain(entity.get("email"))
    if mail and mail not in FREEMAIL_DOMAINS:
        out.append((mail, "email_domain"))
    return out


def build_domain_multiplicity(rows: Iterable[dict[str, Any]]) -> dict[str, int]:
    """How many distinct orgs in the registry use each domain.

    A domain used by many orgs belongs to a manager, accountant, chain or parent,
    not to any single one of them.
    """
    owners: dict[str, set[str]] = {}
    for row in rows:
        org = str(row.get("organisation_number"))
        for domain in (_host(row.get("website")), _email_domain(row.get("email"))):
            if domain and domain not in FREEMAIL_DOMAINS:
                owners.setdefault(domain, set()).add(org)
    return {domain: len(orgs) for domain, orgs in owners.items()}


def _result(
    status: str, tier: str | None, reasons: list[str], matched: list[str], span: str | None = None
) -> dict[str, Any]:
    return {
        "status": status,
        "tier": tier,
        "publishable": status == "exact",
        "reasons": reasons,
        "matched_tokens": matched,
        "span": span,
        "method": "tiered_identity_v1",
    }


def _context(text: str, start: int, end: int, radius: int = 40) -> str:
    return " ".join(text[max(0, start - radius): end + radius].split())


def assess_site(
    entity: dict[str, Any], page: dict[str, Any], source: str, multiplicity: dict[str, int]
) -> dict[str, Any]:
    org = re.sub(r"\D", "", str(entity.get("organisation_number") or ""))
    title = str(page.get("title") or "")
    text = str(page.get("text") or "")
    structured = " ".join(str(n) for n in page.get("structured_names") or [])
    host = _host(page.get("final_url"))
    blob = f"{title} {structured} {text}"

    if any(marker in _fold(blob) for marker in PARKED_MARKERS):
        return _result("ambiguous", None, ["page is a parked or for-sale placeholder"], [])

    # P1: our exact org number, as a standalone 9-digit run (optionally spaced 3-3-3)
    for m in re.finditer(r"(?<!\d)(\d{3}\s?\d{3}\s?\d{3})(?!\d)", blob):
        if re.sub(r"\D", "", m.group(1)) == org:
            return _result("exact", "P1", ["organisation number appears on the page"], [],
                           _context(blob, m.start(), m.end()))

    # R: the page names a different organisation number
    others = {re.sub(r"\D", "", m.group(1)) for m in _ORG_LABELLED.finditer(blob)} - {org}
    if others:
        return _result(
            "related", None,
            [f"page shows a different organisation number: {sorted(others)[0]}"], [],
        )

    # R: a domain shared by several registry entities is not this entity's own site
    if multiplicity.get(host, 1) > 1:
        return _result(
            "related", None,
            [f"domain {host} is shared by {multiplicity[host]} registry entities"], [],
        )

    core = name_tokens(entity.get("name"))
    identity_tokens = set(name_tokens(f"{title} {structured}"))
    full_tokens = identity_tokens | set(name_tokens(text))
    host_compact = re.sub(r"[^a-z0-9]", "", _fold(host))
    core_compact = "".join(core)
    matched = [t for t in core if t in full_tokens]
    # A domain that spells the legal name only counts when the page does not contradict it:
    # a resold domain with another business's title must not be published as this entity.
    host_spells_name = len(core) >= 2 and core_compact in host_compact and not identity_tokens
    name_in_identity = bool(core) and (set(core) <= identity_tokens or host_spells_name)
    name_in_page = bool(core) and (set(core) <= full_tokens or host_spells_name)

    if source == "registry_website" and name_in_identity:
        return _result("exact", "P2", ["registry website, unique domain, legal name matches"], matched,
                       title or structured or host)
    if source == "email_domain" and name_in_identity:
        return _result("exact", "P3", ["registry email domain, unique domain, legal name matches"], matched,
                       title or structured or host)

    postal = str(entity.get("postal_code") or "")
    postal_hit = re.search(rf"(?<!\d){re.escape(postal)}(?!\d)", text) if postal else None
    if name_in_page and postal_hit:
        return _result("exact", "P4", ["full legal name and registered postal code both appear"], matched,
                       _context(text, postal_hit.start(), postal_hit.end()))

    return _result("ambiguous", None, ["no exact-entity anchor found"], matched)
