"""Evaluator entry point: one command, N org numbers in, exactly N envelopes out.

    uv run signalpost --input orgs.txt --output out/envelopes.jsonl --run-id 2026-10-01 \
        --state-dir state/

Writes `<output>` (one JSON envelope per line) and `<output>.report.json`
(runtime, requests, cost, terminal-status counts).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Sequence

from . import AGENT_VERSION, SCHEMA_VERSION
from .batch import read_organisation_numbers, run_batch
from .domains import load_table
from .models import Envelope
from .net import PRODUCTION_THROTTLES, CountingClient
from .pipeline import WebResearch, process_company
from .robots import RobotsCache
from .store import load_state, save_state

DEFAULT_MAX_REQUESTS = 1900  # evaluator hard cap is 2,000 including redirects and retries
DEFAULT_DOMAIN_TABLE = str(Path(__file__).resolve().parents[2] / "data" / "domain_multiplicity.json.gz")

ClientFactory = Callable[[int], CountingClient]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def main(argv: Sequence[str] | None = None, *, client_factory: ClientFactory | None = None) -> int:
    parser = argparse.ArgumentParser(prog="signalpost", description="Signalpost company research agent")
    parser.add_argument("--input", required=True, help="txt/json/jsonl list of organisation numbers")
    parser.add_argument("--output", required=True, help="envelope JSONL to write")
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--state-dir", default=None, help="persistent ledger state for refresh diffs")
    parser.add_argument("--max-requests", type=int, default=DEFAULT_MAX_REQUESTS)
    parser.add_argument("--domain-table", default=DEFAULT_DOMAIN_TABLE,
                        help="shared-domain table built from the BRREG bulk file (see scripts/build_domain_table.py)")
    parser.add_argument("--no-web", action="store_true", help="skip website research (official data only)")
    args = parser.parse_args(argv)

    started = time.monotonic()
    run_ts = _utc_now()
    run_id = args.run_id or run_ts
    orgs = read_organisation_numbers(args.input)
    client = client_factory(args.max_requests) if client_factory else CountingClient(max_requests=args.max_requests, throttles=PRODUCTION_THROTTLES)

    table = None if args.no_web else load_table(args.domain_table)
    if not args.no_web and table is None:
        print(f"warning: domain table {args.domain_table!r} not found; website candidates are treated as "
              "unchecked and only a site's own structured organisation-number declaration can verify one",
              file=sys.stderr)
    web = None if args.no_web else WebResearch(robots=RobotsCache(client), multiplicity=table)

    def worker(org: str) -> Envelope:
        prev = load_state(args.state_dir, org)
        envelope, state = process_company(org, client, run_id=run_id, run_ts=run_ts, prev_state=prev, web=web)
        save_state(args.state_dir, org, state)
        return envelope

    envelopes = run_batch(orgs, worker=worker, run_id=run_id)
    client.close()

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as handle:
        for envelope in envelopes:
            handle.write(envelope.model_dump_json() + "\n")

    availability = Counter(
        f"{claim.field}:{claim.availability}" for envelope in envelopes for claim in envelope.claims
    )
    report = {
        "agent_version": AGENT_VERSION,
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "started_at": run_ts,
        "completed_at": _utc_now(),
        "expected_count": len(orgs),
        "emitted_envelopes": len(envelopes),
        "terminal_status_counts": dict(Counter(e.run.terminal_status for e in envelopes)),
        "availability_counts": dict(sorted(availability.items())),
        "requests": client.requests_made,
        "max_requests": args.max_requests,
        "web_enabled": web is not None,
        "domain_table_loaded": table is not None,
        "domain_table_shared_domains": None if table is None else len(table),
        "runtime_ms": int((time.monotonic() - started) * 1000),
        "third_party_cost_usd": 0.0,
    }
    Path(str(output) + ".report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return 0 if len(envelopes) == len(orgs) else 1


if __name__ == "__main__":
    raise SystemExit(main())
