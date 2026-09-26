"""Crawl NAV's public job feed (Stillingsfeed) from a start date and keep the latest state per ad.

    uv run python scripts/crawl_nav_feed.py --since 2026-03-26 --output out/nav-state.json.gz

The feed is a change log: every modification of every ad is an item, oldest first. Passing
`If-Modified-Since` starts near the present (the API documents that active ads never exceed
about six months). Only the latest state per ad uuid is kept: status, employer name, title and
municipality. The organisation number is NOT in the feed listing; it is read from each ad's
detail record at run time, which is what makes a match exact (see signalpost/nav.py).

Resumable: progress is checkpointed every 10 pages; rerun the same command to continue.
Public token: https://pam-stilling-feed.nav.no/api/publicToken (documented open access).
"""
from __future__ import annotations

import argparse
import gzip
import json
import sys
import time
import urllib.request
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path

BASE = "https://pam-stilling-feed.nav.no"
AGENT = "signalpost-agent/0.1 (company research; bulk feed consumer)"


def public_token() -> str:
    raw = urllib.request.urlopen(urllib.request.Request(BASE + "/api/publicToken", headers={"User-Agent": AGENT}),
                                 timeout=30).read().decode()
    return next(line.strip() for line in raw.splitlines() if line.startswith("eyJ"))


def fetch(url: str, token: str, since: str | None) -> dict:
    headers = {"Authorization": "Bearer " + token, "User-Agent": AGENT}
    if since:
        headers["If-Modified-Since"] = since
    for attempt in range(4):
        try:
            with urllib.request.urlopen(urllib.request.Request(BASE + url if url.startswith("/") else url,
                                                               headers=headers), timeout=90) as response:
                return json.load(response)
        except Exception as exc:  # noqa: BLE001 - retry transient network errors
            if attempt == 3:
                raise
            print(f"retry after {type(exc).__name__}", file=sys.stderr)
            time.sleep(5 * (attempt + 1))
    raise RuntimeError("unreachable")


def save(path: Path, state: dict) -> None:
    tmp = path.with_suffix(".tmp")
    with gzip.open(tmp, "wt", encoding="utf-8") as handle:
        json.dump(state, handle, ensure_ascii=False, separators=(",", ":"))
    tmp.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--since", required=True, help="YYYY-MM-DD (UTC) to start from")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)

    since_dt = datetime.strptime(args.since, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    state = {"since": args.since, "ads": {}, "next_url": None, "pages": 0, "started_at": None, "complete": False}
    if out.exists():
        with gzip.open(out, "rt", encoding="utf-8") as handle:
            state = json.load(handle)
        print(f"resuming at page {state['pages']} with {len(state['ads'])} ads")
    if not state["started_at"]:
        state["started_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    token = public_token()
    started = time.monotonic()
    url = state["next_url"] or "/api/v1/feed"
    since_header = None if state["next_url"] else format_datetime(since_dt, usegmt=True)
    while url:
        page = fetch(url, token, since_header)
        since_header = None
        state["pages"] += 1
        for item in page["items"]:
            entry = item["_feed_entry"]
            state["ads"][entry["uuid"]] = [entry["status"], entry.get("businessName"), entry.get("title"),
                                           entry.get("municipal"), entry.get("sistEndret")]
        url = page.get("next_url")
        state["next_url"] = url
        if state["pages"] % 10 == 0 or not url:
            state["complete"] = not url
            save(out, state)
            active = sum(1 for a in state["ads"].values() if a[0] == "ACTIVE")
            last = page["items"][-1]["date_modified"] if page["items"] else "-"
            print(f"page {state['pages']} ads {len(state['ads'])} active {active} last {last} "
                  f"elapsed {time.monotonic() - started:.0f}s", flush=True)
    state["complete"] = True
    save(out, state)
    print("done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
