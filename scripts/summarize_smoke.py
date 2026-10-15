"""Derive a 100-company smoke-test summary from the first N rows of a profiles file.

    uv run python scripts/summarize_smoke.py submission/profiles.jsonl 100 submission/smoke-report.json

Used to produce submission evidence for a 100-company slice without a second live run: the
first N rows of a batch processed under the identical pinned code and command are exactly what
a dedicated 100-company run would have produced, since each company is independent and the
agent makes no cross-company assumptions.
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 4:
        print(__doc__)
        return 2
    profiles_path, count, output_path = Path(sys.argv[1]), int(sys.argv[2]), Path(sys.argv[3])
    rows = [json.loads(line) for line in profiles_path.read_text(encoding="utf-8").splitlines() if line.strip()][:count]

    availability = Counter(f"{c['field']}:{c['availability']}" for row in rows for c in row["claims"])
    report = {
        "source": str(profiles_path),
        "companies": len(rows),
        "terminal_status_counts": dict(Counter(row["run"]["terminal_status"] for row in rows)),
        "availability_counts": dict(sorted(availability.items())),
        "requests_total": sum(row["operations"]["requests"] for row in rows),
        "runtime_ms_total": sum(row["operations"]["runtime_ms"] for row in rows),
        "third_party_cost_usd_total": sum(row["operations"]["third_party_cost_usd"] for row in rows),
        "errors": sum(len(row["errors"]) for row in rows),
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
