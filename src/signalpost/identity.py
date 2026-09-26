"""Identity-proof engine: decide whether a candidate site is *this* legal entity.

Proof tiers (see PLAN.md section 4):

  P1  this organisation number appears on the page                     -> exact
  P2  registry-listed website, unique domain, legal-name match         -> exact
  P3  registry email domain, unique domain, legal-name match           -> exact
  P4  full legal-name match plus the registered postal code on page    -> exact
  R   another org number is shown, or the domain is shared by >1 org   -> related
  P   parked, for-sale or unconfigured placeholder                     -> placeholder (no site exists)
  A   anything else (partial name overlap, no anchor)                  -> ambiguous

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

# Hosts that carry many organisations' pages: a registry "website" here is a profile, not a company site.
PLATFORM_DOMAINS = {
    "facebook.com", "fb.com", "instagram.com", "linkedin.com", "twitter.com", "x.com", "youtube.com",
    "youtu.be", "tiktok.com", "linktr.ee", "google.com", "goo.gl", "wixsite.com", "business.site",
    "sites.google.com", "wordpress.com", "blogspot.com", "issuu.com", "proff.no", "gulesider.no",
    "1881.no", "finn.no", "brreg.no", "mac.com",
}

PARKED_MARKERS = (
    "domain is for sale", "domain for sale", "this domain may be for sale", "hugedomains",
    "parked at", "buy this domain", "domenet er til salgs", "her flytter snart en ny gjest",
    # empty placeholders and unconfigured servers: the domain exists, but there is no company site
    "future home of", "coming soon", "under construction", "under utvikling", "kommer snart",
    "welcome to nginx", "default web site page", "apache2 ubuntu default page", "apache2 debian default page",
    "it works!", "index of /", "test page for the apache", "web server is working",
)

_HOSTNAME_TITLE = re.compile(r"(?:https?://)?(?:www\.)?[\w-]+(?:\.[\w-]+)*\.[a-z]{2,}/?", re.IGNORECASE)

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
    if site and not any(site == d or site.endswith("." + d) for d in PLATFORM_DOMAINS):
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


def _find_org_number(text: str, org: str) -> re.Match[str] | None:
    """First standalone 9-digit run (optionally spaced 3-3-3) equal to `org`."""
    for m in re.finditer(r"(?<!\d)(\d{3}\s?\d{3}\s?\d{3})(?!\d)", text):
        if re.sub(r"\D", "", m.group(1)) == org:
            return m
    return None


def _context(text: str, start: int, end: int, radius: int = 40) -> str:
    return " ".join(text[max(0, start - radius): end + radius].split())


def assess_site(
    entity: dict[str, Any], page: dict[str, Any], source: str, multiplicity: dict[str, int]
) -> dict[str, Any]:
    org = re.sub(r"\D", "", str(entity.get("organisation_number") or ""))
    title = str(page.get("title") or "")
    if _HOSTNAME_TITLE.fullmatch(title.strip()):
        title = ""  # a title that merely echoes the hostname says nothing about who owns the site
    text = str(page.get("text") or "")
    structured = " ".join(str(n) for n in page.get("structured_names") or [])
    host = _host(page.get("final_url"))
    blob = f"{title} {structured} {text}"

    if any(marker in _fold(blob) for marker in PARKED_MARKERS):
        return _result("placeholder", None, ["page is a parked, for-sale or unconfigured placeholder: no company site yet"], [])

    shared = multiplicity.get(host, 1) > 1
    unverified = source.startswith("unchecked_")  # no multiplicity table: uniqueness is unknown

    # P1: our exact org number. Declared in structured identity (JSON-LD vatID) it proves the
    # site is this entity even on a shared domain; in body text alone it only counts when the
    # domain is unique, because managers and accountants list their clients' numbers.
    declared = str(page.get("identity_text") or "")
    hit = _find_org_number(declared, org)
    if hit:
        return _result("exact", "P1", ["organisation number declared in the site's structured identity"], [],
                       _context(declared, hit.start(), hit.end()))
    hit = _find_org_number(blob, org)
    if hit and not shared and not unverified:
        return _result("exact", "P1", ["organisation number appears on the page"], [],
                       _context(blob, hit.start(), hit.end()))

    # R: the page names a different organisation number
    others = {re.sub(r"\D", "", m.group(1)) for m in _ORG_LABELLED.finditer(blob)} - {org}
    if others:
        return _result(
            "related", None,
            [f"page shows a different organisation number: {sorted(others)[0]}"], [],
        )

    # R: a domain shared by several registry entities is not this entity's own site
    if shared:
        return _result(
            "related", None,
            [f"domain {host} is shared by {multiplicity[host]} registry entities"], [],
        )

    if unverified:
        return _result("ambiguous", None, ["domain uniqueness could not be checked (no multiplicity table)"], [])

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


# ---- social profile relevance ---------------------------------------------------

GENERIC_NAME_WORDS = {
    "eiendom", "eiendommer", "holding", "invest", "investering", "transport", "service", "services", "bygg",
    "norge", "norway", "group", "gruppen", "consulting", "consult", "utvikling", "drift", "forvaltning",
    "entreprenor", "borettslag", "sameie", "regnskap", "design", "teknikk", "elektro", "elektriske",
    "rorlegger", "maler", "maleren", "advokatfirma", "advokat", "eiendomsmegler", "restaurant", "kafe",
    "butikk", "industri", "produksjon", "solutions", "systems", "partner", "partners", "management",
}


def _compact(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", _fold(value))


def _social_handle(url: str) -> str:
    segments = [s for s in urllib.parse.unquote(urllib.parse.urlparse(url).path).split("/") if s]
    if not segments:
        return ""
    first = segments[0].lower()
    if first in {"company", "school", "channel", "c", "user"} and len(segments) >= 2:
        return segments[1]
    if first in {"pages", "people"} and len(segments) >= 2:
        return segments[1]
    return segments[0].lstrip("@")


def social_matches_entity(
    entity: dict[str, Any], brand: str | None, domain_label: str | None, profile: dict[str, str]
) -> bool:
    """Is this profile's handle tied to the entity's name, brand, domain or a historical name?

    A site's own JSON-LD `sameAs` declaration is accepted as-is. Otherwise a profile linked from a
    verified site is published only when its handle relates to this company: a link may just as
    well be a web agency's credit, a partner, or a sponsor.
    """
    if profile.get("source") == "json_ld":
        return True
    handle = _compact(_social_handle(profile["url"]))
    if len(handle) < 4 or handle.isdigit():
        return False

    names = [entity.get("name"), brand, *[h.get("navn") for h in entity.get("historical_names") or []]]
    candidates: set[str] = {_compact(domain_label)} if domain_label else set()
    for name in names:
        tokens = name_tokens(name)
        if tokens:
            candidates.add("".join(tokens))
        candidates.update(t for t in tokens if len(t) >= 6 and t not in GENERIC_NAME_WORDS)
    return any(len(c) >= 4 and (c in handle or (len(handle) >= 5 and handle in c)) for c in candidates)


# ---- name-derived domain guesses ------------------------------------------------

NO_SITE_FORMS = {"BRL", "ESEK", "BBL", "SAM", "STI", "SF", "FLI", "VPFO"}  # co-ops, condominiums, foundations
MAX_GUESSES = 3


def guess_domains(entity: dict[str, Any]) -> list[str]:
    """Plausible `.no` domains for an entity with no registry website or company-domain email.

    Guesses are only *candidates*: a guessed site is published only if it proves the exact
    entity with the organisation number (P1) or full name plus registered postal code (P4).
    """
    if str(entity.get("legal_form") or "").upper() in NO_SITE_FORMS:
        return []
    if str(entity.get("industry_code") or "")[:2] in {"64", "68"} and not entity.get("employees"):
        return []  # holding and property shells rarely have their own site
    name = entity.get("name")
    if re.search(r"\d", str(name or "")):
        return []  # numbered shells ("TOBB OVERVIK 2 UTBYGGING")
    tokens = name_tokens(name)
    if not tokens or len(tokens) > 3 or all(t in GENERIC_NAME_WORDS for t in tokens):
        return []
    labels = ["".join(tokens)]
    if len(tokens) > 1:
        labels.append("-".join(tokens))
    return [f"{label}.no" for label in dict.fromkeys(labels) if 5 <= len(label) <= 40][:MAX_GUESSES]
