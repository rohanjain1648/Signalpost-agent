# Signalpost agent

An evidence-first research agent for Norwegian companies, built for the
[Builderr Signalpost challenge](https://builderr.ai/challenges/signalpost).

Give it a file of Norwegian organisation numbers. For each one it returns exactly one
JSON envelope containing a company profile in which **every fact carries its source, the
time it was retrieved, and (where it applies) the reporting period**. Anything it could
not find, could not verify, or was not allowed to fetch is reported with an explicit
state and a reason. Missing information is never turned into zero, and a site is never
attributed to a company on the strength of a similar name.

Run it again later with the same state directory and it reports only *real* changes,
keeping the earlier evidence.

> **Status: work in progress.** The official-data base and website research are built,
> tested and validated on live data. Coverage of websites, hiring and activity is still
> low. See [What works today](#what-works-today) and
> [Known limitations](#known-limitations) before relying on any number in this document.

---

## Contents

1. [Quick start](#quick-start)
2. [What works today](#what-works-today)
3. [The output envelope](#the-output-envelope)
4. [Availability states](#availability-states)
5. [Claims produced](#claims-produced)
6. [How a website is verified](#how-a-website-is-verified)
7. [Refresh and change detection](#refresh-and-change-detection)
8. [Budget, safety and source policy](#budget-safety-and-source-policy)
9. [Architecture](#architecture)
10. [Repository layout](#repository-layout)
11. [Development](#development)
12. [Validation results](#validation-results)
13. [Known limitations](#known-limitations)
14. [Roadmap](#roadmap)
15. [Declarations](#declarations)

---

## Quick start

Requires Python 3.12+ and [`uv`](https://docs.astral.sh/uv/). No API keys, no accounts,
no model, and no data download are needed to run it.

```bash
uv sync --frozen                     # installs the pinned dependencies from uv.lock

printf '810034882\n938702675\n' > orgs.txt

uv run signalpost \
  --input orgs.txt \
  --output out/envelopes.jsonl \
  --run-id 2026-10-01 \
  --state-dir state/
```

That is the single evaluator command. It writes:

| File | Contents |
|---|---|
| `out/envelopes.jsonl` | One JSON envelope per input organisation number, in input order |
| `out/envelopes.jsonl.report.json` | Machine-readable run report: counts, requests, runtime, cost |
| `state/<orgnr>.json` | Per-company ledger used for refresh (only if `--state-dir` is given) |

The exit code is `0` when the number of envelopes equals the number of inputs.

### Options

| Flag | Default | Meaning |
|---|---|---|
| `--input` | required | `.txt` (one per line), `.json` (list, or `{"organisation_numbers": [...]}`), or `.jsonl` (strings or `{"organisation_number": ...}` objects) |
| `--output` | required | Envelope JSONL to write |
| `--run-id` | current UTC time | Label stored in every envelope |
| `--state-dir` | none | Persist the ledger. Rerun with the same directory to get change detection |
| `--max-requests` | `1900` | Hard cap on outbound HTTP requests, redirects and retries included |
| `--domain-table` | `data/domain_multiplicity.json.gz` | Shared-domain table (see [below](#how-a-website-is-verified)). Missing table = safe fallback, with a warning |
| `--no-web` | off | Official registry data only; makes no request to any company website |
| `--no-discovery` | off | Do not guess name-derived domains for companies without a registry website or email domain |
| `--nav-index` | `data/nav_jobs_index.json.gz` | NAV job-feed name index (see [NAV jobs](#nav-job-ads)). Missing index = NAV disabled, with a warning |
| `--no-nav` | off | Do not query the NAV job feed |
| `--workers` | `6` | Companies processed concurrently. Output order always equals input order |
| `--soft-deadline` | `1500` | Seconds after which website, discovery and NAV work is skipped (reported as `failed: time budget`); official data is still collected |
| `--hard-deadline` | `2400` | Seconds after which no further company is *started*; each becomes a `failed` envelope, so the run still returns one envelope per input |

Input is validated strictly: every entry must be exactly nine digits and duplicates are
rejected, so a malformed batch fails loudly instead of silently dropping companies.

### Refreshing

```bash
# Day 1
uv run signalpost --input orgs.txt --output out/day1.jsonl --state-dir state/
# Later: same command, same state directory
uv run signalpost --input orgs.txt --output out/day2.jsonl --state-dir state/
```

Every envelope in `day2.jsonl` has a `changes` array. If nothing changed at the source it
is empty.

---

## What works today

| Area | State |
|---|---|
| Exactly one terminal envelope per input, including for companies that fail | Built, tested |
| Official Brønnøysund data: identity, latest accounts with reporting period, filing history with PDF links, leadership, registered workplaces, group structure | Built, validated live |
| Website discovery from the registry-listed website and from the registry email domain | Built, validated live |
| Exact-entity identity gate (tiered proof, shared-domain refusal, placeholder detection) | Built, tested, hand-audited on live output |
| Site fan-out: contact, about, careers and news pages; brand name; company-owned social profiles; contact details; structured job postings | Built, validated live |
| Refresh ledger: stable claim IDs, idempotent reruns, typed changes, `stale` vs `closed` | Built, validated live |
| robots.txt (RFC 9309) on every request hop; SSRF guard; request budget; per-endpoint throttle | Built, tested |
| Discovery of websites for companies with no registry website or email domain (name-derived domains, verified only by organisation number or name + postal code) | Built, validated live |
| Job ads from NAV's public feed, attributed by organisation number | Built, tested; index build in progress |
| Dated activity from registry records (articles, capital, employees, role changes) | Built, validated live |
| Concurrent processing with soft and hard time budgets | Built, validated live |
| Written company briefing; static viewer; hand-labelled gold set | **Not built** |
| Dated activity from news pages on company sites | **Not built** |

174 automated tests pass. Development was test-first: each behaviour was written as a
failing test before its implementation.

---

## The output envelope

One JSON object per line. Sample (shortened; values are real output from a live run):

```json
{
  "organisation_number": "982786223",
  "run": {
    "run_id": "web100b",
    "started_at": "2026-09-26T14:09:44Z",
    "completed_at": "2026-09-26T14:10:06Z",
    "terminal_status": "completed"
  },
  "claims": [
    {
      "field": "official_website",
      "value": {
        "url": "https://gudmestad-transport.no/",
        "domain": "gudmestad-transport.no",
        "proof_tier": "P1",
        "proof": ["organisation number appears on the page"]
      },
      "availability": "available",
      "confidence": 0.99,
      "evidence_ids": ["ev-site-home"],
      "as_of": null,
      "note": null
    }
  ],
  "evidence": [
    {
      "id": "ev-site-home",
      "source_url": "https://gudmestad-transport.no/",
      "final_url": "https://gudmestad-transport.no/",
      "source_class": "company_owned",
      "retrieved_at": "2026-09-26T14:10:05Z",
      "content_sha256": "b9cfea35…",
      "claim_span": "LES MER Gudmestad transport AS org.nr. 982 786 223 Folkvordveien 30 43…",
      "extractor": "site-extract/1"
    }
  ],
  "changes": [
    {
      "type": "new_role", "family": "roles", "materiality": "medium",
      "key": "DAGL|Eirik Gudmestad|", "old_value": null,
      "new_value": {"name": "Eirik Gudmestad", "role": "Daglig leder"},
      "old_evidence_id": null, "new_evidence_id": "ev-roles",
      "observed_at": "2026-09-26T14:09:44Z", "reopened": false
    }
  ],
  "errors": [],
  "operations": { "requests": 9, "runtime_ms": 3640, "third_party_cost_usd": 0.0 }
}
```

| Section | Meaning |
|---|---|
| `run.terminal_status` | `completed`, or `failed` if the registry itself could not be read or the company's processing crashed |
| `claims[]` | One per field. `availability` says whether there is a value; `evidence_ids` point into `evidence[]`; `as_of` is the reporting period end for accounts; `note` explains any non-available state |
| `evidence[]` | Provenance: requested and final URL, source class, retrieval time, SHA-256 of the response body, a quoted span for proof-of-identity, and the extractor version |
| `changes[]` | Typed differences from the previous run, with the evidence on both sides |
| `errors[]` | Source errors and any caught exception, with the module that raised it |
| `operations` | Requests made, runtime, and third-party cost for this company |

The run report (`<output>.report.json`) adds the total request count, availability counts
per field, terminal-status counts, whether website research was enabled, and whether the
shared-domain table was loaded.

---

## Availability states

Every claim carries exactly one of the six contract states.

| State | Used when |
|---|---|
| `available` | A value was found, is attributed to this exact company, and has evidence |
| `not_available` | It was looked for and is not there: a 404, a domain with no web server, a placeholder page, an empty result, or no candidate source exists |
| `blocked` | The source refused us: `robots.txt` disallows the URL, or it answers 5xx so that it is treated as disallowing (per RFC 9309) |
| `not_applicable` | The question does not apply: e.g. no group structure for a company that is not in a group |
| `ambiguous` | A candidate exists but could not be proven to be this company (shared domain, another organisation's number on the page, no anchor). **No value is published** |
| `failed` | The source or our code broke on this item (5xx, timeout, or a caught exception). The last supported value is kept as `stale` |

Absence is never converted to a zero. A registry 404 is "no record returned", not a value
of 0.

---

## Claims produced

| Claim field | Source | Notes |
|---|---|---|
| `legal_identity` | `data.brreg.no/enhetsregisteret/api/enheter/{orgnr}` | Name, legal form, employees, industry, address, registered email and website, historical names, purpose |
| `annual_accounts` | `data.brreg.no/regnskapsregisteret/regnskap/{orgnr}` | Revenue, operating result, profit before tax, annual result, assets, equity, debt. `as_of` is the period end. Only values the API returns; nulls stay null |
| `financial_history` | Regnskapsregisteret filing-year list | Years on file and official PDF-copy links |
| `leadership` | `.../enheter/{orgnr}/roller` | Active role holders. **Birth dates are never published** |
| `registered_workplaces` | `.../underenheter?overordnetEnhet={orgnr}` | Sub-units with address, industry and employees |
| `corporate_group` | `.../konsernstruktur/{orgnr}` | Fetched only when the registry says the company is in a group; otherwise `not_applicable` |
| `official_website` | Company site, after the identity gate | Includes the proof tier and a quoted span |
| `brand_name` | `og:site_name` or JSON-LD organisation name | Never guessed from a page title |
| `company_profiles` | Social links on the verified site | LinkedIn, Facebook, Instagram, X, YouTube, TikTok. Published only if the handle relates to the company |
| `contact` | `mailto:`/`tel:` links and JSON-LD on the verified site | E-mail addresses on the company's own domain (or a free-mail domain) and phone numbers |
| `hiring` | NAV job feed (by organisation number) and JSON-LD `JobPosting` markup on the verified site | Title, published/expires dates, location, canonical URL. The same posting in both sources is listed once. If a careers page exists but publishes no structured postings, the claim says so |
| `dated_activity` | The same registry records (no extra requests) | Dated official events, newest first: articles of association, share-capital registration, employee count registered, and changes to each role group (board, manager, auditor, accountant). `as_of` is the newest date |

Website-derived claims mirror the website's state: if the site is `ambiguous`, so are
brand, profiles and contact, with no values. `hiring` additionally draws on the NAV feed, which
is attributed by organisation number and so does not depend on the website being verified.

---

## How a website is verified

A brand, domain or social handle is only a *candidate* until it is tied to the exact legal
entity. The registry supplies two candidates per company: its listed website and the domain
of its registered email address (free-mail domains are ignored, and social/aggregator hosts
such as `facebook.com` or `proff.no` are never treated as a company site).

The page is fetched (honouring `robots.txt`), read, and judged by
[`identity.py`](src/signalpost/identity.py):

| Tier | Proof | Result |
|---|---|---|
| P1 | The organisation number appears on the page. On a domain used by more than one registry entity it must be declared in the site's own structured data (`vatID`/`taxID`) | `exact` |
| P2 | Registry-listed website, domain unique to this entity, and the legal-name tokens appear in the title or structured name | `exact` |
| P3 | Registry email domain, unique to this entity, and the legal-name tokens appear in the title or structured name | `exact` |
| P4 | Full legal name and the registered postal code both appear on the page (unique domain only) | `exact` |
| R | Another organisation's number is shown, or the domain is shared by several registry entities | `related`, not published |
| P | Parked, for-sale, "coming soon", or unconfigured-server placeholder | `placeholder` → `not_available` |
| A | Anything else | `ambiguous`, not published |

Only `exact` is published. Rules that came from auditing live output:

- **Shared-domain table.** [`data/domain_multiplicity.json.gz`](data/domain_multiplicity.json.gz)
  lists the 18,854 domains used by two or more registry entities (e.g. `styrerommet.no`: 3,691
  entities, `obos.no`: 996, `kbbl.bu.no`: 72). Housing-co-op managers, accountants, chains and
  parent companies live here. A domain missing from the table is unique.
- **No table, no guessing.** If the table is not loaded the agent still runs, but every
  candidate is *unchecked* and only the site's own structured organisation-number declaration
  can verify it. A warning is printed and the run report records `domain_table_loaded: false`.
- **A domain that spells the company name counts only if the page does not contradict it.**
  A resold domain with another business's title is not published.
- **A title that merely repeats the hostname is not evidence.**
- **Redirects are judged on the final host.** A registry site that redirects to a shared or
  different domain is refused on that final host.
- **Social profiles must relate to the company.** A profile linked from a verified site is
  published only if its handle relates to the legal name, brand, domain, or a *historical*
  registry name, or the site declares it in JSON-LD `sameAs`. Otherwise it is quarantined and
  the claim note says how many links were held back. This keeps a web agency's or a partner's
  credit link out of the profile while accepting, for example, a firm's old-name Facebook page.
- **Discovery.** For a company with no registry website and no company-domain email (about
  three quarters of the universe), up to three name-derived `.no` domains are guessed
  (`sandneselektriske.no`, `sandnes-elektriske.no`). Each is checked with a DNS lookup first, which is
  not an HTTP request, and fetched only if it resolves. A guessed site is verified **only** by tier
  P1 (organisation number on the page) or P4 (full name plus registered postal code), never by a
  matching name alone. Companies that cannot have a site are skipped (co-ops, condominiums,
  foundations, holding and property shells with no employees, numbered shells, names made only of
  generic words). A guess that cannot be verified is reported as plain `not_available`: nothing ties
  the domain to the company, so no claim is made about it. Guesses use a 6-second timeout. Discovery
  never probes a domain that was already tried, and never works around a `robots.txt` block.
- **Fan-out never leaves the verified site.** Pages found by fan-out are dropped if they
  redirect off-site (external applicant-tracking systems are allowed only for careers links).

Every published website claim cites the page it was read from, with the proof span quoted.

---

## Refresh and change detection

State is kept per company in `--state-dir/<orgnr>.json` and managed by
[`ledger.py`](src/signalpost/ledger.py).

- **Stable claim IDs:** `sha1(organisation | family | natural key)`, where the key is, for
  example, a job URL, a role code plus person, a sub-unit's organisation number, a
  period plus metric, or a platform plus profile URL.
- **Normalised comparison:** values are compared after whitespace normalisation, so
  reformatting never counts as a change.
- **Idempotent:** rerunning an unchanged snapshot produces zero changes and identical state.
  Verified live for the official-data families (a second run over 8 real companies reported 0 changes); for website families it is covered by simulated-site tests, not yet by a live rerun.
- **History is preserved:** each claim keeps every value it has held, with when it was seen.
- **`closed` vs `stale`:**
  - An item missing from a family that was *successfully checked* becomes `closed`
    (e.g. `closed_job`, `ended_role`) with its earlier value kept.
  - If the fetch *failed*, or the site is `ambiguous`/`blocked`, its claims become `stale`
    and **no change is emitted**. A failed refresh never erases or invalidates the last
    supported value.
  - A job is closed only if the careers page was actually read this run.
  - Older filing years are never "withdrawn": the accounts endpoint only returns recent periods.
- **Typed changes:** `new_job`, `closed_job`, `new_role`, `ended_role`, `new_location`,
  `closed_location`, `new_filing`, `changed_financial`, `new_website`, `website_changed`,
  `website_removed`, `new_social_profile`, `removed_social_profile`, and so on, each with a
  materiality level and the evidence on both sides. A closed item that reappears is reported
  as new again with `reopened: true`.

---

## Budget, safety and source policy

### Evaluator budget

| Limit | Value | How it is respected |
|---|---|---|
| Requests per batch | 2,000, redirects and retries included | Every hop is counted by one client with an atomic reservation (safe under concurrency); hard cap defaults to 1,900 |
| Wall clock | 45 minutes | Companies run concurrently; a soft deadline (25 min) skips website/NAV work and a hard deadline (40 min) stops starting companies, both reported explicitly |
| Third-party API spend | $10 | The agent uses no paid API: $0 |

### NAV job ads

The NAV public job feed (`pam-stilling-feed.nav.no`) lists ads by *employer name* only; the organisation
number is in each ad's detail record. So a name match only **nominates** an ad, and it is published only if
the ad's own `employer.orgnr` equals the company (or one of its registered sub-units) and its status is
`ACTIVE`. An ad from a namesake with a different number is rejected and never appears in the output.

- **Offline index** (declared cache): `scripts/crawl_nav_feed.py` crawls the last six months of the feed's
  change log (a resumable, roughly 30-minute job) and `scripts/build_nav_index.py` turns it into
  `data/nav_jobs_index.json.gz`, a map from normalised employer name to active ad ids.
- **At run start:** one request for the feed's documented public token, then a catch-up from the index's
  build time using `If-Modified-Since` (about three requests per day of index age). If the catch-up
  cannot finish, `hiring` is reported `failed` rather than trusting a stale list.
- **Per company:** at most eight nominated ads are fetched live (one request each).
- The bearer token is only ever sent to the feed's own host, never on a redirect to another host.

### Safety

- **Safe-URL policy.** Only public HTTP(S) URLs. Localhost, `.local`, and any host that
  resolves to a private, loopback, link-local, multicast or reserved address is refused,
  **checked again on every redirect hop**.
- **robots.txt (RFC 9309), checked before every hop:** 2xx is parsed and obeyed, 4xx means no
  restrictions, 5xx is treated as disallowing, and an unreachable host is not treated as a
  disallow (the page fetch then reports the real failure).
- **Rate limiting.** The registry's filing-year endpoint (about 30 requests per minute) is
  spaced at 2.1 seconds.
- **Bounded reads.** Pages are capped at 1.5 MB; redirects at five hops.
- **No secrets.** There are no credentials, keys or accounts anywhere in the code or config.
- **Privacy.** Person birth dates returned by the registry's roles endpoint are discarded.
- **A crash never costs data.** Website research runs inside its own guard: an exception there
  is reported as an error and `failed` website claims, while the company's official data is kept.
  A crash anywhere else becomes a terminal `failed` envelope. No company is ever dropped.

### Sources

| Source | Use | Terms |
|---|---|---|
| Brønnøysundregistrene Enhetsregisteret and Regnskapsregisteret APIs and bulk file | Identity, accounts, roles, sub-units, groups, dated activity, shared-domain table | Open data, NLOD 2.0 |
| NAV Stillingsfeed (`pam-stilling-feed.nav.no`) | Job ads, attributed by employer organisation number | Documented public token; NAV's terms of use apply (see [Declarations](#declarations)) |
| The company's own website (found via registry website or registry email domain) | Website, brand, profiles, contact, jobs | Fetched only when `robots.txt` permits |

Search engines, LinkedIn, Meta, Glassdoor, Indeed and other restricted platforms are **not**
used. A social profile is recorded only as a URL linked from the company's own verified site;
the platform itself is never fetched.

---

## Architecture

```
orgs.txt ──► batch.run_batch ─────────────────────────────────────────────►  envelopes.jsonl
              (one worker call per org; any exception becomes a failed envelope)
                     │
                     ▼
              pipeline.process_company
   ┌─────────────────┼──────────────────────────────────────┐
   ▼                 ▼                                        ▼
 fetch.py        web.py (optional)                        ledger.py
 official BRREG  candidate URLs                            observations ─► typed changes
 modules  ─►     robots ─► fetch ─► extract ─► identity    state per claim, stale/closed
 official.py     ─► fan-out ─► social relevance gate
 (normalizers)          │
                        ▼
                  site_claims.py ─► claims + evidence + observations
   ▲
   └── net.CountingClient: budget · safe-URL · manual redirects · throttle · per-hop robots hook
```

| Module | Responsibility |
|---|---|
| [`models.py`](src/signalpost/models.py) | Pydantic `Claim`, `Evidence`, `Envelope`; the six availability states; nine-digit organisation-number validation |
| [`net.py`](src/signalpost/net.py) | The only place HTTP happens. Atomic request budget, safe-URL checks, manual redirect following, size cap, charset decoding, per-endpoint throttle, per-hop permission hook, per-call timeout, DNS-only `resolves()`, credential headers never forwarded cross-host |
| [`robots.py`](src/signalpost/robots.py) | Per-origin robots cache using the counted client |
| [`official.py`](src/signalpost/official.py) | Pure normalisers for registry JSON. No network, never invents a value |
| [`fetch.py`](src/signalpost/fetch.py) | Fetch and normalise each registry module into `{status, value, evidence}` |
| [`extract.py`](src/signalpost/extract.py) | Deterministic page extraction: JSON-LD, meta tags, mailto/tel, social links, priority links, job postings. Never raises on malformed HTML |
| [`identity.py`](src/signalpost/identity.py) | Proof tiers, placeholder detection, shared-domain logic, social relevance, name-derived domain guesses |
| [`domains.py`](src/signalpost/domains.py) | Build, save and load the shared-domain table |
| [`web.py`](src/signalpost/web.py) | Candidate → robots → fetch → identity gate → fan-out, with `www.`/`http` fallbacks and name-derived discovery |
| [`nav.py`](src/signalpost/nav.py) | NAV job feed: name index, run-start catch-up, per-ad verification by employer organisation number |
| [`hiring.py`](src/signalpost/hiring.py) | Merge NAV and company-site postings into one `hiring` claim without duplicates |
| [`site_claims.py`](src/signalpost/site_claims.py) | Turn a website result into claims, evidence and ledger observations |
| [`ledger.py`](src/signalpost/ledger.py) | Claim IDs, idempotent upserts, typed changes |
| [`store.py`](src/signalpost/store.py) | Atomic per-company state files |
| [`pipeline.py`](src/signalpost/pipeline.py) | Per-company orchestration, dated-activity events, soft time budget |
| [`batch.py`](src/signalpost/batch.py) | Input parsing, concurrent processing in input order, the exactly-N-envelopes guarantee, and the hard deadline |
| [`cli.py`](src/signalpost/cli.py) | The evaluator command and run report |

Design rules, applied throughout:

1. The organisation number is the only stable key; everything else is a candidate until proven.
2. Deterministic evidence first. There is **no LLM in the loop**, so nothing can invent a value.
3. Every published claim points at evidence with a source URL, retrieval time and content hash.
4. Missing, blocked and ambiguous are explicit states, never blanks or zeros.
5. When unsure, abstain. A missed fact costs a little; a wrong-company fact costs the run.

---

## Repository layout

```
src/signalpost/      the agent (modules above)
tests/               174 tests; fixtures/brreg/ holds real recorded registry responses
data/                domain_multiplicity.json.gz (shared domains), nav_jobs_index.json.gz (NAV employer-name index)
scripts/             build_domain_table.py, crawl_nav_feed.py, build_nav_index.py: rebuild the two declared caches
PLAN.md              research findings, scoring analysis, build plan and status
pyproject.toml       dependencies (httpx, lxml, pydantic; pytest for development)
uv.lock              pinned dependency versions
```

---

## Development

```bash
uv sync                      # includes the dev group (pytest)
uv run pytest                # full suite; network is never used, HTTP is simulated
uv run pytest tests/test_identity.py -q
```

Tests use `httpx.MockTransport` and a recorded-fixture set from the real registry API, so
they are deterministic and offline. Live behaviour was checked separately (see below).

### Rebuilding the shared-domain table

```bash
curl -L https://data.brreg.no/enhetsregisteret/api/enheter/lastned/csv -o brreg-enheter.csv.gz
uv run python scripts/build_domain_table.py brreg-enheter.csv.gz data/domain_multiplicity.json.gz
```

The bulk file is about 155 MB and the build takes about 35 seconds. Rebuild it when the
registry snapshot the evaluation uses changes.

### Adding a source or field family

1. Write failing tests first (`tests/`), using recorded or simulated responses.
2. Extract with deterministic rules; keep the extractor total (it must never raise).
3. Produce claims that cite evidence, plus ledger observations with a stable key.
4. Decide which families count as *checked* and which as *failed* for refresh semantics.
5. Run a live batch and read real output by hand before trusting it. Every live audit so far
   found a bug the fixtures could not.

---

## Validation results

Measured on 100 random companies from the 411,160-company universe, against the live
registry and real company websites, on 2026-09-26. These are development measurements,
not evaluator scores.

| Measure | Result |
|---|---|
| Envelopes returned | 100 of 100 |
| Terminal status | 100 completed, 0 failed |
| Requests used | about 596 of 2,000 |
| Runtime | about 4.5 minutes of 45 |
| Third-party cost | $0 |
| Official identity, accounts, filing history, leadership | 99–100% available |
| Registered workplaces | 80% available; the rest have no registered sub-unit |
| Verified official websites | **3 of 100**, all checked by hand and correct |
| Candidates refused as `ambiguous` | 14 at the time of the run: shared domains, another organisation's number, no anchor, and 2 placeholder pages |
| Dead web servers | 5 reported `failed`; a later fix now reports these as `not_available`, so the two lists above would shift if rerun |
| Rerun on unchanged sources | 0 changes (8 live companies, official-data families only) |

The first live run found six defects, all fixed and covered by regression tests: an
extractor crash on comments outside `<html>`, a website crash discarding official data, an
unreachable host mislabelled as a robots block, an empty placeholder accepted as a verified
site, unrelated social links accepted, and dead domains reported as `failed`.

---

## Known limitations

Stated plainly, because a wrong claim is worse than a missing one:

- **Website coverage is low, about 3% on a random batch.** Only about 11% of companies have a
  registry website, about 30% have a company-domain email (from an 80-company sample), and the shared-domain rule
  correctly refuses the many that use a manager's or parent's domain. Companies with neither
  are not researched yet.
- **No job-ad feed and no dated activity yet.** `hiring` reads only structured job postings
  and careers pages on a verified site. There is no NAV feed, no news extraction and no
  registry change-event feed.
- **No JavaScript rendering.** Pages that need a browser to show any content yield little text.
  A page with a good static title still verifies; a bare shell does not.
- **Processing is sequential.** Fast enough for 100 companies, but not tuned for scale.
- **No request-budget planner.** Website research is bounded per company, but nothing yet
  reserves budget for the official data of later companies. At about 6 requests per company
  it has ample headroom today.
- **Accounts come from the registry's normalised API only.** Annual-account PDFs are linked,
  not parsed. The accounts endpoint returns recent periods, so long numeric history is not
  extracted.
- **Norwegian names only.** Identity matching folds `æøå`; it has not been tuned for foreign
  branches (NUF) or names in other scripts.
- **The User-Agent contains a placeholder contact URL.** Replace it with the project's
  repository URL before official submission (`USER_AGENT` in [`net.py`](src/signalpost/net.py)).
- **Nothing here is an evaluator score.** The pooled reference collection, field weights and
  scoring are Builderr's; this repository has only been measured against its own tests and
  hand-audited live output.

---

## Roadmap

In priority order:

1. **Candidate discovery** for companies with no registry website or email domain: name-derived
   domains, accepted only on P1 or P4 proof.
2. **NAV public job feed**, matched by exact employer organisation number.
3. **Dated public activity** from the registry's change-event feed, plus dated items on company
   news pages.
4. **Request-budget planner** so that website research can never starve later companies of
   official data.
5. **Company briefing**: a deterministic, source-cited summary of what the company does, who
   runs it, its latest numbers, hiring, what changed and what is unknown.
6. **Static viewer** for browsing and verifying profiles on desktop and mobile.
7. **Hand-labelled gold set** with a promotion gate: no change ships unless it adds no wrong-company
   publication and no drop in precision.
8. **Submission artifact**: 1,000 completed profiles with their organisation-number manifest.
9. Repository docs for the submission: `AGENT.md`, `CRAWLERS.md`, `IDENTITY_RESOLUTION.md`,
   `DATA_SCHEMA.md`, `REFRESH.md`, `EVAL.md`, `LIMITATIONS.md`.

The full analysis behind these choices, including the scoring rubric and the observation
that 85% of the universe reports no employees, is in [`PLAN.md`](PLAN.md).

---

## Declarations

- **Models and third-party APIs:** none. No LLM, no search API, no paid service.
- **Expected cost per 100-company batch:** $0.
- **Secrets:** none required or read from the environment.
- **Declared cache:** [`data/domain_multiplicity.json.gz`](data/domain_multiplicity.json.gz), built
  from the public Brønnøysund bulk file. It is used only to *refuse* shared domains. It never
  supplies a fact or a candidate, and every published fact is fetched live during the run.
- **Data licences:** Brønnøysundregistrene open data under NLOD 2.0. Company websites are read
  only where `robots.txt` allows, and only to extract facts the company itself publishes.
- **Personal data:** person names appear only as registry role holders (a public register);
  birth dates are discarded. Contact details are those a company publishes on its own site.
