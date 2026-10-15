"""Select a reproducible batch of organisation numbers from the public universe file.

    curl -L https://builderr.ai/signalpost-company-universe-2025.jsonl.gz -o .cache/universe.jsonl.gz
    uv run python scripts/select_submission_batch.py \
        --universe .cache/universe.jsonl.gz --count 1000 --seed 20261016 \
        --output submission/organisation-numbers.txt

A fixed `--seed` makes the selection reproducible: the same seed and universe file always
produce the same manifest, in the same order. The output is one organisation number per line,
suitable as `signalpost --input`.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import random
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--universe", required=True)
    parser.add_argument("--count", type=int, default=1000)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    universe_path = Path(args.universe)
    content_sha256 = hashlib.sha256(universe_path.read_bytes()).hexdigest()
    with gzip.open(universe_path, "rt", encoding="utf-8") as handle:
        orgs = [json.loads(line)["organisation_number"] for line in handle if line.strip()]

    rng = random.Random(args.seed)
    selected = sorted(rng.sample(orgs, args.count))  # sorted: stable, readable manifest

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(selected) + "\n", encoding="utf-8")

    print(f"selected {len(selected)} of {len(orgs)} organisation numbers (seed={args.seed}) -> {output}")
    print(f"universe content sha256: {content_sha256}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
