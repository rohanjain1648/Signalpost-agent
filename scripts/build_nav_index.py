"""Turn a crawl state (from scripts/crawl_nav_feed.py) into data/nav_jobs_index.json.gz.

    uv run python scripts/crawl_nav_feed.py --since 2026-03-26 --output out/nav-state.json.gz
    uv run python scripts/build_nav_index.py out/nav-state.json.gz data/nav_jobs_index.json.gz

The index maps a normalised employer name to the ids of ads that were ACTIVE when the crawl
ended. It only *nominates* ads: at run time each nominated ad is fetched live and published only
if its own employer organisation number matches the company (see signalpost/nav.py).
Declared as a cache in LIMITATIONS.md; rebuild shortly before the final submission freeze so that
the run-time catch-up stays short.
"""
from __future__ import annotations

import gzip
import json
import sys

from signalpost.nav import build_index, save_index


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__)
        return 2
    with gzip.open(argv[1], "rt", encoding="utf-8") as handle:
        state = json.load(handle)
    if not state.get("complete"):
        print("warning: crawl state is not marked complete; the index may miss recent ads", file=sys.stderr)
    index = build_index(state)
    save_index(index, argv[2])
    ads = sum(len(ids) for ids in index.by_name.values())
    print(f"{ads} active ads under {len(index.by_name)} employer names; built_at {index.built_at}; "
          f"since {index.since} -> {argv[2]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
