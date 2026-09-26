"""NAV job feed (arbeidsplassen.no Stillingsfeed): exact-identity job ads.

The feed's listing carries only the employer's *name*; the organisation number is in each ad's
detail record. So the flow is:

  offline   crawl the last ~6 months of the change-log and keep, per ad, its latest status and
            employer name (scripts/crawl_nav_feed.py) -> a name -> active-ad-ids index (declared cache);
  run start `prepare()` fetches the public token and catches the index up from its build time;
  per firm  the index *nominates* ads whose employer name matches; each nominated ad is fetched live
            and published only if its own `employer.orgnr` equals this company (or one of its
            registered sub-units) and its status is ACTIVE. A namesake is rejected by number.

Source: https://navikt.github.io/pam-stilling-feed/ (public token, documented open access).
"""
from __future__ import annotations

import gzip
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path
from typing import Any

from .identity import name_tokens
from .net import CountingClient

FEED_BASE = "https://pam-stilling-feed.nav.no"
TOKEN_URL = FEED_BASE + "/api/publicToken"
FEED_URL = FEED_BASE + "/api/v1/feed"
ENTRY_URL = FEED_BASE + "/api/v1/feedentry/{uuid}"
SOURCE_CLASS = "job_feed_api"


def name_key(name: Any) -> str:
    """Case-, punctuation- and legal-suffix-insensitive key for matching employer names."""
    return " ".join(name_tokens(name))


@dataclass
class NavIndex:
    built_at: str
    since: str
    by_name: dict[str, list[str]] = field(default_factory=dict)


def build_index(state: dict[str, Any]) -> NavIndex:
    """Turn a crawl state (latest status per ad) into the name -> active-ad-ids index."""
    by_name: dict[str, list[str]] = {}
    for uuid, (status, business_name, *_rest) in state["ads"].items():
        key = name_key(business_name)
        if status == "ACTIVE" and key:
            by_name.setdefault(key, []).append(uuid)
    return NavIndex(built_at=state["started_at"], since=state["since"], by_name=by_name)


def save_index(index: NavIndex, path: str | Path) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        json.dump({"built_at": index.built_at, "since": index.since, "by_name": index.by_name},
                  handle, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def load_index(path: str | Path) -> NavIndex | None:
    source = Path(path)
    if not source.exists():
        return None
    with gzip.open(source, "rt", encoding="utf-8") as handle:
        body = json.load(handle)
    return NavIndex(built_at=body["built_at"], since=body["since"], by_name=body["by_name"])


def _http_date(iso: str) -> str:
    moment = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return format_datetime(moment.astimezone(timezone.utc), usegmt=True)


def _job(uuid: str, ad: dict[str, Any]) -> dict[str, Any]:
    places = ad.get("workLocations") or []
    first = places[0] if places and isinstance(places[0], dict) else {}
    employer = ad.get("employer") or {}
    return {
        "id": uuid, "title": ad.get("title"), "job_title": ad.get("jobtitle"),
        "published": ad.get("published"), "expires": ad.get("expires"),
        "application_due": ad.get("applicationDue"),
        "location": first.get("city") or first.get("municipal"), "postal_code": first.get("postalCode"),
        "url": ad.get("link") or f"https://arbeidsplassen.nav.no/stillinger/stilling/{uuid}",
        "employment": {"extent": ad.get("extent"), "engagement": ad.get("engagementtype")},
        "employer_name": employer.get("name"), "employer_orgnr": employer.get("orgnr"),
        "source": "nav_feed",
    }


class NavJobs:
    """Run-scoped NAV lookup. Call `prepare()` once per run, then `jobs_for()` per company."""

    def __init__(self, index: NavIndex, client: CountingClient, *, max_tail_pages: int = 120,
                 max_ads_per_company: int = 8) -> None:
        self.index = index
        self.client = client
        self.max_tail_pages = max_tail_pages
        self.max_ads_per_company = max_ads_per_company
        self.ready = False
        self.error: str | None = None
        self._token: str | None = None

    def _auth(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}", **(extra or {})}

    def prepare(self) -> dict[str, Any]:
        summary: dict[str, Any] = {"ready": False, "pages": 0, "truncated": False, "added": 0, "removed": 0,
                                   "up_to_date": False}
        token = self.client.fetch_page(TOKEN_URL, max_bytes=20_000)
        candidates = [line.strip() for line in (token.text or "").splitlines() if line.strip().startswith("eyJ")]
        if token.status != 200 or not candidates:
            self.error = f"public token unavailable ({token.error or 'no token in response'})"
            summary["error"] = self.error
            return summary
        self._token = candidates[0]

        by_name = self.index.by_name
        url: str | None = FEED_URL
        headers = self._auth({"If-Modified-Since": _http_date(self.index.built_at)})
        while url:
            if summary["pages"] >= self.max_tail_pages:
                summary["truncated"] = True
                self.error = "catch-up since the index was built exceeded the page cap"
                summary["error"] = self.error
                return summary
            page = self.client.fetch_json(url if url.startswith("http") else FEED_BASE + url, headers=headers)
            if page.status == 404 and summary["pages"] == 0:
                # The feed answers 404 when nothing changed since If-Modified-Since: the index is current.
                summary["up_to_date"] = True
                break
            if page.status != 200 or not isinstance(page.body, dict):
                self.error = f"feed catch-up failed ({page.error or page.status})"
                summary["error"] = self.error
                return summary
            summary["pages"] += 1
            for item in page.body.get("items", []):
                entry = item["_feed_entry"]
                key, uuid = name_key(entry.get("businessName")), entry["uuid"]
                if entry["status"] == "ACTIVE" and key:
                    if uuid not in by_name.setdefault(key, []):
                        by_name[key].append(uuid)
                        summary["added"] += 1
                else:
                    for ids in by_name.values():
                        if uuid in ids:
                            ids.remove(uuid)
                            summary["removed"] += 1
            url = page.body.get("next_url")
            headers = self._auth()
        self.ready = True
        summary["ready"] = True
        return summary

    def jobs_for(self, entity: dict[str, Any], org_numbers: set[str]) -> dict[str, Any]:
        result: dict[str, Any] = {"status": "not_available", "jobs": [], "rejected": 0, "incomplete": False,
                                  "evidence": {}, "note": None}
        if not self.ready:
            result.update(status="failed", note=self.error or "NAV feed not prepared")
            return result

        names = [entity.get("name"), *[h.get("navn") for h in entity.get("historical_names") or []]]
        uuids: list[str] = []
        for name in names:
            for uuid in self.index.by_name.get(name_key(name), []):
                if uuid not in uuids:
                    uuids.append(uuid)
        uuids = uuids[: self.max_ads_per_company]
        if not uuids:
            result["note"] = "no active ad in the NAV feed lists this company's name as employer"
            return result

        failures = 0
        for uuid in uuids:
            fetched = self.client.fetch_json(ENTRY_URL.format(uuid=uuid), headers=self._auth())
            if fetched.status != 200 or not isinstance(fetched.body, dict):
                failures += 1
                continue
            body = fetched.body
            ad = body.get("ad_content") or {}
            if body.get("status") != "ACTIVE":
                continue
            employer = (ad.get("employer") or {}).get("orgnr")
            if employer not in org_numbers:
                result["rejected"] += 1  # same name, different organisation number: not this company
                continue
            job = _job(uuid, ad)
            result["jobs"].append(job)
            result["evidence"][uuid] = {
                "source_url": fetched.url, "final_url": fetched.final_url, "retrieved_at": fetched.retrieved_at,
                "content_sha256": fetched.content_sha256,
            }
        result["jobs"].sort(key=lambda j: j["published"] or "", reverse=True)
        result["incomplete"] = failures > 0
        if result["jobs"]:
            result["status"] = "available"
            if failures:
                result["note"] = f"{failures} candidate ad(s) could not be checked; the list may be incomplete"
        elif failures:
            result.update(status="failed", note="the NAV feed could not be queried for this company's ads")
        else:
            result["note"] = "candidate ads were inactive or belonged to a different organisation number"
        return result
