"""Website research for one entity: candidate -> robots -> fetch -> identity gate -> fan-out.

Nothing about a site is reported unless the identity gate says `exact`. A site that is
shared, names another organisation, is parked, or cannot be verified yields an
`ambiguous` result with no socials, contacts, pages or jobs attached to it.
"""
from __future__ import annotations

import re
import urllib.parse
from typing import Any

from .extract import ATS_HOSTS, extract_page
from .identity import FREEMAIL_DOMAINS, assess_site, candidate_domains
from .net import CountingClient, FetchResult
from .robots import RobotsCache

FAN_OUT_KINDS = ("contact", "about", "careers", "news")
MAX_CANDIDATES = 2


def _site(host: str) -> str:
    return ".".join(host.split(".")[-2:])


def _host(url: str) -> str:
    return (urllib.parse.urlparse(url).hostname or "").lower()


def _candidate_urls(entity: dict[str, Any]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for domain, source in candidate_domains(entity):
        if domain in seen:
            continue
        seen.add(domain)
        if source == "registry_website":
            raw = str(entity.get("website") or "").strip()
            url = raw if re.match(r"^https?://", raw, re.I) else f"https://{raw}"
            parsed = urllib.parse.urlparse(url)
            url = urllib.parse.urlunparse((parsed.scheme, parsed.netloc, parsed.path or "/", "", "", ""))
        else:
            url = f"https://{domain}/"
        out.append({"domain": domain, "source": source, "url": url})
    return out[:MAX_CANDIDATES]


def _fetch_outcome(fetch: FetchResult) -> tuple[str, str] | None:
    """Map a failed/unusable fetch to (outcome, reason); None means a usable HTML page."""
    error = fetch.error or ""
    if error.startswith("blocked: robots"):
        return "blocked", "robots.txt disallows fetching this site"
    if "did not resolve" in error:
        return "not_available", "domain does not resolve"
    if error.startswith("blocked:"):
        return "not_available", "URL rejected by the safe-URL policy"
    if fetch.status == 0:
        return "failed", error or "network error"
    if fetch.status >= 500:
        return "failed", f"HTTP {fetch.status}"
    if fetch.status != 200:
        return "not_available", f"HTTP {fetch.status}"
    content_type = (fetch.content_type or "").lower()
    if content_type and "html" not in content_type and "xml" not in content_type:
        return "not_available", f"not an HTML page ({content_type})"
    return None


def _evidence(fetch: FetchResult) -> dict[str, Any]:
    return {
        "url": fetch.url, "final_url": fetch.final_url, "retrieved_at": fetch.retrieved_at,
        "content_sha256": fetch.content_sha256, "redirect_chain": fetch.redirect_chain,
    }


def _keep_email(address: str, site_host: str) -> bool:
    domain = address.rsplit("@", 1)[-1]
    return _site(domain) == _site(site_host) or domain in FREEMAIL_DOMAINS


def _empty(status: str, candidates: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "status": status, "candidates": candidates, "proof": None, "homepage": None, "pages": [],
        "socials": [], "emails": [], "phones": [], "jobs": [], "careers_links": [], "news_links": [],
        "structured_organizations": [], "contact_sources": {}, "careers_checked": False,
    }


def _fan_out(home: FetchResult, home_extract: dict[str, Any], client: CountingClient, robots: RobotsCache,
             max_pages: int, result: dict[str, Any]) -> None:
    home_host = _host(home.final_url or home.url)

    def merge(extraction: dict[str, Any], page_url: str) -> None:
        for social in extraction["socials"]:
            if not any(s["platform"] == social["platform"] and s["url"] == social["url"] for s in result["socials"]):
                result["socials"].append({**social, "page_url": page_url})
        for key, prefix in (("emails", "email"), ("phones", "phone")):
            for value in extraction[key]:
                if value not in result[key] and (key == "phones" or _keep_email(value, home_host)):
                    result[key].append(value)
                    result["contact_sources"][f"{prefix}:{value}"] = page_url
        for job in extraction["jobs"]:
            result["jobs"].append({**job, "page_url": page_url})
        result["structured_organizations"].extend(extraction["organizations"])
        for kind, key in (("careers", "careers_links"), ("news", "news_links")):
            for link in extraction["priority_links"][kind]:
                if link not in result[key]:
                    result[key].append(link)

    merge(home_extract, home.final_url or home.url)
    targets: list[tuple[str, str]] = []
    for kind in FAN_OUT_KINDS:
        for url in home_extract["priority_links"][kind]:
            if url.rstrip("/") != (home.final_url or "").rstrip("/"):
                targets.append((kind, url))
                break
    for kind, url in targets[:max_pages]:
        page = client.fetch_page(url, allow=robots.allowed)
        if _fetch_outcome(page) is not None:
            continue
        final_host = _host(page.final_url or url)
        ats = any(final_host == a or final_host.endswith("." + a) for a in ATS_HOSTS)
        if _site(final_host) != _site(home_host) and not ats:
            continue  # a redirect must never carry us off the verified site
        extraction = extract_page(page.text or "", page.final_url or url)
        result["pages"].append({**_evidence(page), "kind": kind, "title": extraction["title"]})
        if kind == "careers":
            result["careers_checked"] = True
        merge(extraction, page.final_url or url)


def research_website(
    entity: dict[str, Any],
    client: CountingClient,
    robots: RobotsCache,
    multiplicity: dict[str, int] | None,
    *,
    max_pages: int = 4,
) -> dict[str, Any]:
    """`multiplicity=None` means no table is available: domains are then treated as unchecked."""
    candidates: list[dict[str, Any]] = []
    for candidate in _candidate_urls(entity):
        if multiplicity is None:
            candidate = {**candidate, "source": "unchecked_" + candidate["source"]}
        fetch = client.fetch_page(candidate["url"], allow=robots.allowed)
        record: dict[str, Any] = {
            **candidate, "final_url": fetch.final_url, "retrieved_at": fetch.retrieved_at,
            "content_sha256": fetch.content_sha256,
        }
        failure = _fetch_outcome(fetch)
        if failure:
            record["outcome"], record["reason"] = failure
            candidates.append(record)
            continue
        extraction = extract_page(fetch.text or "", fetch.final_url or candidate["url"])
        page = {
            "final_url": fetch.final_url, "title": extraction["title"], "text": extraction["text"],
            "identity_text": extraction["identity_text"], "structured_names": extraction["structured_names"],
        }
        assessment = assess_site(entity, page, candidate["source"], multiplicity or {})
        record["outcome"], record["reason"] = assessment["status"], "; ".join(assessment["reasons"])
        candidates.append(record)
        if assessment["status"] == "exact":
            result = _empty("available", candidates)
            result["proof"] = assessment
            result["homepage"] = {
                **_evidence(fetch), "title": extraction["title"], "description": extraction["description"],
                "site_name": extraction["site_name"],
            }
            _fan_out(fetch, extraction, client, robots, max_pages, result)
            return result

    outcomes = {c["outcome"] for c in candidates}
    for status, matches in (("ambiguous", {"related", "ambiguous"}), ("blocked", {"blocked"}), ("failed", {"failed"})):
        if outcomes & matches:
            return _empty(status, candidates)
    return _empty("not_available", candidates)
