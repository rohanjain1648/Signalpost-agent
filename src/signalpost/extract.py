"""Deterministic page extraction: JSON-LD first, DOM second, visible text last.

`extract_page` never raises on bad input and never guesses: every field is
read from a concrete place in the document (a JSON-LD property, a meta tag,
an anchor, a mailto/tel link) so a claim can point back at where it came from.
"""
from __future__ import annotations

import json
import re
import urllib.parse
from typing import Any

import lxml.html
from lxml.etree import ParserError

MAX_TEXT_CHARS = 20_000
ORG_TYPES = {"Organization", "Corporation", "LocalBusiness", "NGO", "OnlineBusiness", "Store", "Restaurant"}
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp")

LINK_GROUPS: list[tuple[str, re.Pattern[str]]] = [
    ("contact", re.compile(r"kontakt|contact", re.I)),
    ("careers", re.compile(r"karriere|jobb|ledige|stilling|careers?|jobs?\b|rekrutt|vacanc|work[-_ ]with|bli[-_ ]med", re.I)),
    ("news", re.compile(r"nyheter|news|aktuelt|presse|press\b|blogg?\b|artikler", re.I)),
    ("about", re.compile(r"om[-_ ]?oss|about|hvem[-_ ]er[-_ ]vi|om[-_ ]selskapet", re.I)),
    ("locations", re.compile(r"lokasjon|avdeling|kontorer|locations?|butikker|finn[-_ ]oss|find[-_ ]us", re.I)),
    ("leadership", re.compile(r"ledelse|leadership|management|styret", re.I)),
]
ATS_HOSTS = (
    "easycruit.com", "webcruiter.com", "webcruiter.no", "jobylon.com", "teamtailor.com", "varbi.com",
    "recman.no", "hrmanager.no", "lever.co", "greenhouse.io", "workable.com", "smartrecruiters.com",
    "personio.de", "personio.com", "jobbnorge.no", "reachmee.com", "cvpartner.com",
)


def _empty() -> dict[str, Any]:
    return {
        "title": None, "description": None, "site_name": None, "text": "", "identity_text": "",
        "structured_names": [], "organizations": [], "jobs": [], "socials": [], "emails": [],
        "phones": [], "priority_links": {name: [] for name, _ in LINK_GROUPS},
    }


def _first(values: list[str]) -> str | None:
    for value in values:
        value = " ".join(str(value).split())
        if value:
            return value
    return None


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else [] if value is None else [value]


def _types(node: dict[str, Any]) -> set[str]:
    return {str(t) for t in _as_list(node.get("@type"))}


def _address(value: Any) -> dict[str, Any] | None:
    value = _as_list(value)[0] if _as_list(value) else None
    if isinstance(value, dict):
        return {
            "street": value.get("streetAddress"),
            "postal_code": value.get("postalCode"),
            "locality": value.get("addressLocality"),
            "country": value.get("addressCountry") if isinstance(value.get("addressCountry"), str) else None,
        }
    return {"text": value} if isinstance(value, str) else None


def _org(node: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": node.get("name"), "legal_name": node.get("legalName"),
        "vat_id": node.get("vatID") or node.get("taxID"), "telephone": node.get("telephone"),
        "email": node.get("email"), "url": node.get("url"), "address": _address(node.get("address")),
        "same_as": [str(u) for u in _as_list(node.get("sameAs")) if isinstance(u, str)],
    }


def _job(node: dict[str, Any]) -> dict[str, Any]:
    place = _as_list(node.get("jobLocation"))
    locality = None
    if place and isinstance(place[0], dict):
        address = place[0].get("address")
        locality = address.get("addressLocality") if isinstance(address, dict) else None
    hirer = node.get("hiringOrganization")
    return {
        "title": node.get("title"), "date_posted": node.get("datePosted"),
        "valid_through": node.get("validThrough"), "employment_type": node.get("employmentType"),
        "url": node.get("url"), "location": locality,
        "hiring_organization": hirer.get("name") if isinstance(hirer, dict) else None,
    }


def _walk(node: Any, orgs: list[dict[str, Any]], jobs: list[dict[str, Any]]) -> None:
    if isinstance(node, list):
        for child in node:
            _walk(child, orgs, jobs)
        return
    if not isinstance(node, dict):
        return
    if "@graph" in node:
        _walk(node["@graph"], orgs, jobs)
    kinds = _types(node)
    if "JobPosting" in kinds:
        jobs.append(_job(node))  # do not recurse: hiringOrganization is not this page's organisation
    elif kinds & ORG_TYPES or any(k.endswith("Business") for k in kinds):
        orgs.append(_org(node))  # do not recurse: parent/sub organisations are other entities
    else:
        for key, child in node.items():
            if key != "@graph" and isinstance(child, (dict, list)):
                _walk(child, orgs, jobs)


def _json_ld(doc: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    orgs: list[dict[str, Any]] = []
    jobs: list[dict[str, Any]] = []
    for script in doc.xpath('//script[@type="application/ld+json"]'):
        try:
            _walk(json.loads(script.text or ""), orgs, jobs)
        except ValueError:
            continue
    return orgs, jobs


def _site(host: str) -> str:
    return ".".join(host.split(".")[-2:])


def social_profile(url: str) -> tuple[str, str] | None:
    """Return (platform, canonical profile URL) for a genuine profile link, else None."""
    parsed = urllib.parse.urlparse(url)
    host = (parsed.hostname or "").lower()
    segments = [s for s in urllib.parse.unquote(parsed.path).split("/") if s]
    if not segments or parsed.scheme not in {"http", "https"}:
        return None
    first = segments[0]

    def on(domain: str) -> bool:
        return host == domain or host.endswith("." + domain)

    if on("facebook.com") or on("fb.com"):
        if first.lower() in {"sharer", "sharer.php", "share", "share.php", "plugins", "tr", "dialog", "login", "l.php", "events", "hashtag"}:
            return None
        keep = segments[:3] if first in {"pages", "people"} else segments[:1]
        return "facebook", "https://www.facebook.com/" + "/".join(keep)
    if on("instagram.com"):
        if first in {"p", "reel", "reels", "explore", "accounts", "stories", "tv"}:
            return None
        return "instagram", f"https://www.instagram.com/{first}"
    if on("x.com") or on("twitter.com"):
        if first in {"intent", "share", "home", "search", "i", "hashtag", "login"}:
            return None
        return "x", f"https://x.com/{first}"
    if on("youtube.com"):
        if first in {"channel", "c", "user"} and len(segments) >= 2:
            return "youtube", f"https://www.youtube.com/{first}/{segments[1]}"
        if first.startswith("@"):
            return "youtube", f"https://www.youtube.com/{first}"
        return None
    if on("linkedin.com"):
        if first in {"company", "school"} and len(segments) >= 2:
            return "linkedin", f"https://www.linkedin.com/{first}/{segments[1]}"
        return None
    if on("tiktok.com") and first.startswith("@"):
        return "tiktok", f"https://www.tiktok.com/{first}"
    return None


def _dedupe(values: list[Any]) -> list[Any]:
    seen: set[str] = set()
    out = []
    for value in values:
        key = json.dumps(value, sort_keys=True, default=str)
        if key not in seen:
            seen.add(key)
            out.append(value)
    return out


def _classify_links(links: list[tuple[str, str]], base_host: str) -> dict[str, list[str]]:
    groups: dict[str, list[str]] = {name: [] for name, _ in LINK_GROUPS}
    for url, text in links:
        parsed = urllib.parse.urlparse(url)
        host = (parsed.hostname or "").lower()
        if parsed.scheme not in {"http", "https"} or not host:
            continue
        same_site = _site(host) == _site(base_host)
        haystack = f"{urllib.parse.unquote(parsed.path)} {text}"
        for name, pattern in LINK_GROUPS:
            if not pattern.search(haystack):
                continue
            is_ats = name == "careers" and any(host == a or host.endswith("." + a) for a in ATS_HOSTS)
            if same_site or is_ats:
                if url not in groups[name] and len(groups[name]) < 10:
                    groups[name].append(url)
            break
    return groups


def extract_page(html: str, base_url: str) -> dict[str, Any]:
    result = _empty()
    try:
        doc = lxml.html.fromstring(html or "")
    except (ParserError, ValueError):
        return result

    orgs, jobs = _json_ld(doc)
    result["organizations"], result["jobs"] = orgs, jobs
    result["title"] = _first(doc.xpath("//title//text()"))
    result["description"] = _first(doc.xpath('//meta[@name="description"]/@content'))
    result["site_name"] = _first(doc.xpath('//meta[@property="og:site_name"]/@content'))

    links: list[tuple[str, str]] = []
    emails: list[str] = []
    phones: list[str] = []
    socials: list[dict[str, str]] = []
    for anchor in doc.xpath("//a[@href]"):
        href = anchor.get("href", "").strip()
        if not href or href.startswith(("#", "javascript:")):
            continue
        if href.lower().startswith("mailto:"):
            address = urllib.parse.unquote(href[7:]).split("?", 1)[0].strip().lower()
            if EMAIL_RE.fullmatch(address):
                emails.append(address)
            continue
        if href.lower().startswith("tel:"):
            phones.append(re.sub(r"[^\d+]", "", href[4:]))
            continue
        absolute = urllib.parse.urljoin(base_url, href)
        links.append((absolute, " ".join(anchor.text_content().split())))
        profile = social_profile(absolute)
        if profile:
            socials.append({"platform": profile[0], "url": profile[1], "source": "link"})

    for org in orgs:
        for same_as in org["same_as"]:
            profile = social_profile(same_as)
            if profile:
                socials.append({"platform": profile[0], "url": profile[1], "source": "json_ld"})
        if org["email"]:
            emails.append(str(org["email"]).strip().lower())
        if org["telephone"]:
            phones.append(re.sub(r"[^\d+]", "", str(org["telephone"])))

    if doc.tag in {"script", "style", "noscript", "template"}:
        return result  # the whole document is non-visible markup
    for bad in doc.xpath("//script|//style|//noscript|//template|//comment()"):
        if bad.getparent() is not None:  # comments outside <html> have no parent: leave them, they are not text
            bad.drop_tree()
    text = " ".join(" ".join(doc.itertext()).split())[:MAX_TEXT_CHARS]
    for found in EMAIL_RE.findall(text):
        if not found.lower().endswith(IMAGE_SUFFIXES):
            emails.append(found.lower())

    names = [str(n) for org in orgs for n in (org["name"], org["legal_name"]) if n]
    identity_bits = [result["title"], result["site_name"], result["description"], *names,
                     *[str(o["vat_id"]) for o in orgs if o["vat_id"]]]
    unique_socials: dict[tuple[str, str], dict[str, str]] = {}
    for profile in socials:  # one entry per profile; the first source that mentioned it wins
        unique_socials.setdefault((profile["platform"], profile["url"]), profile)
    result.update(
        text=text,
        identity_text=" ".join(str(b) for b in identity_bits if b),
        structured_names=names,
        socials=list(unique_socials.values()),
        emails=_dedupe(emails),
        phones=_dedupe([p for p in phones if p]),
        priority_links=_classify_links(links, (urllib.parse.urlparse(base_url).hostname or "").lower()),
    )
    return result
