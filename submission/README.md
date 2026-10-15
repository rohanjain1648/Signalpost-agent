# Submission artifact

This directory is the Round 1 submission artifact for the
[Builderr Signalpost challenge](https://builderr.ai/challenges/signalpost).

| File | Contents |
|---|---|
| [`organisation-numbers.txt`](organisation-numbers.txt) | The exact manifest: 1,000 Norwegian organisation numbers, one per line |
| [`profiles.jsonl`](profiles.jsonl) | One terminal envelope per organisation number (see the root [README](../README.md#the-output-envelope) for the schema) |
| [`run-report.json`](run-report.json) | The machine-readable run report for the full 1,000-company run |
| [`smoke-report.json`](smoke-report.json) | The same statistics computed over just the first 100 rows of `profiles.jsonl`, produced by `scripts/summarize_smoke.py`. Valid evidence because each company is processed independently under the identical pinned code and command — a dedicated 100-company invocation would produce the same per-company results |

## Result (run `submission-v1`, 2026-10-16, commit `0782cc3` + this packaging)

| Measure | Result |
|---|---|
| Envelopes returned | 1,000 of 1,000 |
| Terminal status | 1,000 `completed`, 0 `failed` |
| Requests used | 6,706 (6.7 per company average); third-party cost $0 |
| Runtime | 35 min 50 s, with 10 workers (generous budgets for this one-time artifact; the daily evaluator command uses the default 2,000-request / 45-minute / 100-company limits unchanged) |
| Official identity, accounts, filing history, leadership, dated activity | ~100% available (4 companies hit a transient `annual_accounts` source error) |
| Registered workplaces | 778 available; the rest have no registered sub-unit |
| Verified official websites | **100 of 1,000** (10.0%), each with an organisation-number, name+site, or name+postal-code proof and a quoted evidence span |
| Hiring (NAV feed, verified by the ad's own employer organisation number) | **25 companies** with at least one active ad |
| Website candidates refused as `ambiguous` (shared domain, wrong org number, no anchor) | 81 |
| Website candidates `blocked` by `robots.txt` | 15 |

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
