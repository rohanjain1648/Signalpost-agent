# Submission artifact

This directory is the Round 1 submission artifact for the
[Builderr Signalpost challenge](https://builderr.ai/challenges/signalpost).

| File | Contents |
|---|---|
| [`organisation-numbers.txt`](organisation-numbers.txt) | The exact manifest: 1,000 Norwegian organisation numbers, one per line |
| [`profiles.jsonl`](profiles.jsonl) | One terminal envelope per organisation number (see the root [README](../README.md#the-output-envelope) for the schema) |
| [`run-report.json`](run-report.json) | The machine-readable run report: counts, requests, runtime, cost |

## How it was produced

```bash
uv sync --frozen
curl -L https://builderr.ai/signalpost-company-universe-2025.jsonl.gz -o .cache/universe.jsonl.gz

uv run python scripts/select_submission_batch.py \
  --universe .cache/universe.jsonl.gz --count 1000 --seed 20261016 \
  --output submission/organisation-numbers.txt

uv run signalpost \
  --input submission/organisation-numbers.txt \
  --output submission/out/envelopes.jsonl \
  --run-id submission-v1 \
  --state-dir submission/state \
  --workers 10 --max-requests 20000 --soft-deadline 7200 --hard-deadline 7200
```

Selection is a uniform random sample of the public 411,160-company universe (seed `20261016`,
sorted for a readable manifest), using the `random.Random(seed).sample(...)` standard-library
sampler for reproducibility: the same seed and universe file always produce the same manifest.
The wider time and request budgets (vs. the evaluator's per-100-company daily limits) are only
for this one-time artifact; the daily evaluator command is unchanged and respects the 2,000
request / 45 minute / 100-company limits (see the root README's
[Budget, safety and source policy](../README.md#budget-safety-and-source-policy)).

## Verifying it

```bash
wc -l submission/organisation-numbers.txt submission/profiles.jsonl   # both 1000
python -c "import json; print(len({json.loads(l)['organisation_number'] for l in open('submission/profiles.jsonl', encoding='utf-8')}))"  # 1000 distinct
```

Every row in `profiles.jsonl` cites its own evidence (source URL, retrieval time, content
hash); see the root README for how to read and verify a claim.
