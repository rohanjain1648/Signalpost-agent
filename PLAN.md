# Signalpost: research findings and build plan

## Build status (updated 2026-09-26)

Done, all test-first (80 tests): envelope schema, counted + SSRF-guarded HTTP client with per-endpoint throttle,
BRREG fetch/normalize (identity, accounts + period, filing history, leadership, workplaces, group), tiered
identity-proof engine (P1-P4 / related / ambiguous), idempotent refresh ledger (stale vs closed, typed changes),
exactly-N-envelopes batch runner, state store, and the single evaluator command `uv run signalpost --input ... --output ...`.

Live validation (real BRREG APIs): 100/100 envelopes, 509 requests of 2,000, 3m32s of 45 min, 0 failures.
Rerun on unchanged sources: 0 changes across 8 live companies. **Headroom left: ~1,490 requests, ~41 min.**

Not built yet: website fetch + extraction (robots, JSON-LD, socials, contact), candidate discovery wiring into the
pipeline, NAV job feed, BRREG change-event activity, briefing text, static site, gold set, docs set, 1,000-profile artifact.

Drafted 2026-09-26. Revisions close **Oct 18**, round closes **Oct 21**.

---

## 1. What the research found

### Sources analysed
- The challenge page and live leaderboard: builderr.ai/challenges/signalpost
- The brief, playbook, learning harness, source policy and evaluation contract (all pasted in)
- The starter kit (`signalpost-starter-kit.tar.gz`): 7.5k lines of Python, 6 docs and their fixtures
- The 100-profile product sample (builderr.ai/signalpost). Its page embeds Builderr's own reference profiles as JSON.
- The full universe file (411,160 rows; the SHA-256 matches the published value)
- Live probes against the BRREG entity API, the BRREG updates API and the NAV stilling-feed docs

### Competitive picture (board reviewed 26 Sep)
- 28 submissions, 9 assessed, **only 1 qualified**. Karthik scored 69.49. Next were 63.26, 60.69, 59.69, then 39–57.
- Scores come from a locked corpus of 700 companies (seven slices of 100), using saved first-pass sources.
- Reading: a disciplined entry that clears 65 is already in contention for the podium. Most entrants fall short on accuracy, refresh or contract hygiene more than on raw volume.

### The universe is sparse: this shapes everything
| Fact | Value |
|---|---|
| Registry website present | **10.9%** (44,855 of 411,160) |
| 0 or missing employees | **85%** |
| Top industries | 68 real estate (22%), 00 unknown (11%), 47 retail, 43 construction, 97 housing co-ops, 64 holding |
| Legal forms | AS 91.7%, BRL 2.4%, ESEK 2.1%, STI 1.3% |

Live sample of 80 random companies from the BRREG entity API:
- 11 have a registry website.
- **24 have an email on a non-freemail domain, and 16 of those have no registered website.**
- **27 of 80 (34%) have either a website or an own-domain email, 2.5× the website-only candidate pool.**
- There are traps in that email set. `bonord.no`, `styrerommet.no` and `kbbl.bu.no` belong to housing-co-op managers. `ferdighus.no` and `sveli.no` belong to a parent or related company. Using them without a check would publish facts under the wrong company.

### How the score works, and what that means for design
- **Coverage (35)**: for each external field family, 70% company recall and 30% claim recall, measured against a pool built from all entrants plus Builderr's crawlers. A company with no footprint does not count against you, because it is not in the denominator. So breadth wins: **find at least one verified fact per family for every company that has one.**
- **Accuracy (30)**, with one wrong-company publication blocking an official run. Precision gates come first. When unsure, return `ambiguous`.
- **Refresh (20)** is mostly engineering: stable claim keys, idempotent reruns, preserved history, no false changes. These points are cheap if done properly.
- **Synthesis (10) and UX (5)** can be solved with templates plus a static site.
- The final rank is the **mean over all daily batches while a version is active, and earlier batches are never replaced**. Do not submit a weak v1 just to get on the board.

### Rules that act as strategic levers
1. **"Cache hits are free"** and **"Cached public-universe material is allowed; external caches must be declared."** You can pre-resolve identity offline, then re-fetch the evidence live during the run.
2. **"A credential tied to your own account on another service" cannot be reproduced.** Paid search or Places keys may not work in the evaluator unless Builderr supplies them, so the core path must not depend on them. (Open question Q1.)
3. Builderr supplies the official registry snapshot, and it does not count toward spend.
4. Budget per batch: **2,000 requests (redirects and retries count)**, 45 minutes, $10. That is about 20 requests per company, but 85% of companies need only about 6 official calls, so the spare budget can go to the roughly 30% that have a footprint.

### What Builderr's own crawler publishes (from the sample JSON)
- Five coverage dimensions: `company`, `financials`, `people_places`, `website`, `footprint`.
- The footprint data comes from website social handles plus **experimental LinkedIn** data (profile, workforce, posts, jobs), captured with `rightsStatus: "experimental"`.
- Their kit also has connectors for Google Maps, YouTube, Google News RSS, Fagfolkguiden reviews, site news and PDF workforce. **Expect the pool to contain these families.**
- The sample of 100 is skewed (97 have a website), so it is not representative of the daily batches.

### High-value sources that are permitted and exact-match
| Source | What it gives | Why it matters |
|---|---|---|
| BRREG entity API | name, form, NACE 1–3, `aktivitet` (stated purpose), `historiskeNavn`, `kapital`, **`epostadresse`**, phone, `hjemmeside`, `erIKonsern` | Official identity, what the company does, and website candidates |
| BRREG roles, subunits, konsernstruktur | leadership, registered workplaces, group links | Leadership and workplaces section |
| Regnskapsregisteret + filing-year list + PDFs | latest accounts, year list, official PDF copies | Financials and their history |
| BRREG `oppdateringer/enheter` (checked: returns 200) | **dated official change events** | Dated public activity with an exact org number, at zero identity risk |
| **NAV pam-stilling-feed** (`employer.orgnr`, public token) | job ads with the employer's org number | **Hiring with exact identity.** Often keyed to the subunit's org number, so map it to the parent |
| Verified company site | about text, JSON-LD, socials, contact, locations, news/RSS/sitemap `lastmod`, careers pages and ATS boards | Website, profiles, hiring and activity |
| Google Places API (if a key is allowed) | rating, review count, place | Ratings family. Match on address, phone and domain |
| Brave Search API (if a key is allowed) | website candidates only | Discovery only, never evidence |

**Excluded or quarantined:** LinkedIn, Meta, Glassdoor and Indeed scraping; search-result pages; Proff and similar aggregators. A social URL can still be recorded when the verified company site links to it.

---

## 2. Strategy in one paragraph

Use **official data as the reliable base** for all 100 companies. Then run a **tiered identity-proof engine** that turns registry websites, **registry email domains**, a declared **offline discovery cache** and (optionally) search candidates into verified company sites. Each verified site is fanned out into socials, contacts, locations, careers and news. Jobs come from the **NAV feed keyed by org number**, and dated activity comes from **BRREG change events**. Everything is written to an **append-only claim ledger** so reruns are idempotent and changes are real. Every company gets exactly one envelope, whatever happens. The one-sentence rule: **every published fact must be provable back to the organisation number; anything else is `ambiguous`.**

---

## 3. Architecture

```
input (txt/json/jsonl of org numbers)
  │
  ▼
[Planner] budget and deadline controller: global request counter (hard cap 1,900), 40-min soft deadline, priority queue
  │
  ├─ Phase A  Official (all 100)        entity · roles · subunits · accounts · filing years · group (only if erIKonsern) · change events
  ├─ Phase B  Candidate generation      registry URL · email domain · offline cache · DNS name guess · (search API)
  ├─ Phase C  Identity gate             tiered proof → exact | related(group/parent) | ambiguous | rejected
  ├─ Phase D  Site fan-out (exact only) home + priority paths + sitemap/RSS → JSON-LD, socials, contact, locations, careers, news
  ├─ Phase E  Hiring                    NAV feed index (cached cursor + live delta) · site careers · ATS boards · JSON-LD JobPosting
  ├─ Phase F  Optional paid             Places ratings (NACE-targeted) · search for high-prior companies without a candidate
  │
  ▼
[Claim builder] typed claims (Pydantic) → evidence (url, final_url, redirects, status, retrieved_at, sha256, span/selector, extractor version)
  ▼
[Ledger/refresh] stable claim_id → upsert → first_seen/last_seen/status → typed changes vs previous state
  ▼
[Synthesis] deterministic briefing + optional Haiku polish, checked by a number/entity validator
  ▼
[Outputs] envelopes.jsonl (exactly N) · run-report.json · state/ snapshots · site/ (static, mobile)
```

**Stack:** Python 3.12, `uv` lockfile, `httpx` async with a counting transport (so redirects are counted), selectolax/lxml, `extruct`, `trafilatura`, `tldextract`, `rapidfuzz`, `phonenumbers`, `pypdf`, `pydantic` v2, `duckdb` for eval. No browser by default; Playwright only behind a JS-shell detector, and it can be dropped entirely if the budget is tight.

---

## 4. Identity-proof engine (the part that decides qualification)

| Tier | Proof | Result |
|---|---|---|
| P1 | This org number appears on the site (footer, contact, imprint, JSON-LD `vatID`/`taxID`, "Org.nr") | **exact** |
| P2 | Registry `hjemmeside` resolves, the site is not parked, and name tokens match on the homepage | **exact** |
| P3 | Registry email domain is **unique to this org across the bulk registry** and name tokens match on the site | **exact** |
| P4 | Legal name or registered alias matches, plus a registry address or phone match on the site | **exact** |
| R  | The site shows a **different** org number, or the domain is shared by more than one org in the registry | **related** (group, parent, manager, accountant). Label it and never merge its facts |
| A  | Anything else | **ambiguous**. Keep the candidate for debugging, publish nothing |

Deterministic traps to encode, each with a regression test:
- **Domain multiplicity table:** count orgs per email or web domain from the bulk snapshot, computed offline and shipped. A shared domain means a housing-co-op manager, accountant, chain or parent (e.g. `styrerommet.no`, `bonord.no`, `kbbl.bu.no`).
- Parked or for-sale pages, hosting placeholders, and redirects to a different registered domain.
- Franchises and chains (e.g. "REMA 1000 X AS"), business sports clubs (BIL), holding companies (`… HOLDING AS`, `… INVEST AS`) that point at the operating company's site.
- Namesakes in another municipality: require an address, phone or org-number anchor for P4.
- Social handles are publishable only when **linked from an exact site**; name similarity alone is never enough.

---

## 5. Field families: source, gate and state

| Envelope section | Claims | Source | Notes |
|---|---|---|---|
| 1 Identity and brand | legal name, form, NACE, purpose, historical names, capital, address, brand name/aliases | BRREG; site title / JSON-LD `name`/`alternateName` | A brand alias needs an exact site |
| 2 Annual accounts | revenue, operating result, pre-tax, net, assets, equity, debt, currency, **period**; filing years; PDF links | Regnskapsregisteret | **Never fabricate.** PDF prior-year comparatives are published only if the current-year numbers in the PDF match the API exactly |
| 3 Leadership and workplaces | CEO, chair, board, auditor, accountant; subunits with addresses and employees; site locations | BRREG roles/subunits; exact site | No birth dates stored |
| 4 Website and owned profiles | verified URL + proof tier; LinkedIn/FB/IG/X/YouTube/TikTok URLs linked from the site; email/phone | Exact site | `related` sites are shown as relationships |
| 5 Hiring and activity | job ads (title, location, published, expires, url); dated news/press; BRREG change events; ratings | NAV feed, careers/ATS, RSS/sitemap/JSON-LD, BRREG updates, Places | Each item is dated; closed jobs are tracked |
| 6 Evidence and states | per-claim evidence refs; per-family availability | all | `not_available` = checked and nothing found; `blocked` = robots/403/ToS; `not_applicable` = e.g. BRL hiring; `failed` = error |
| 7 Refresh | run ids, previous run, changes with both sides' evidence, stale/failed markers | ledger | |

---

## 6. Request budget per 100-company batch (target ≤ 1,600)

| Phase | Requests |
|---|---|
| Official: entity, roles, subunits, accounts, filing years (100 × 5) + group (~10) + change events (paged, ~5) | ~515 |
| Candidate checks: DNS is free (declare it); HEAD/GET of about 35 candidates | ~50 |
| Site fan-out: about 30 exact sites × 12 (robots, home, sitemap/RSS, about, contact, careers, news…) | ~360 |
| NAV feed delta from the cached cursor (~20 pages) + ad detail for batch matches (~30) | ~50 |
| Optional: Places (~30 targeted) + search (~40) | ~70 |
| Retries, redirects, headroom | ~550 |

The planner runs a hard stop at 1,900 and a soft deadline at 40 minutes. When either is hit, every open family is finalized with an honest state.

---

## 7. Refresh design (worth 20 points)

- `claim_id = sha1(org | family | natural_key)`, where the natural key is the job URL or ad id, the role code plus person, the subunit's org number, the fiscal year plus line item, the social platform plus URL, or the news URL.
- **Hash normalized extracted values, not raw HTML**, so CSRF tokens and timestamps do not cause false changes.
- The ledger stores `first_seen`, `last_seen`, `last_verified`, `status` (active/closed/stale) and a list of `value_history`.
- Typed changes include `new_filing`, `changed_financial`, `new_role`, `ended_role`, `new_location`, `closed_location`, `new_job`, `closed_job`, `website_changed`, `changed_description`, `new_activity`, `name_change` and `address_change`, each carrying a materiality level.
- A failed fetch keeps the last supported value, marks it `stale` with the error attached and emits **no** change.
- CLI: `--state-dir` (persistent) or `--previous envelopes.jsonl`. Rerunning the same snapshot in replay mode must give **zero changes and byte-identical claims**. Build this as a test from day 1, extending the kit's `run_refresh_replay.py`.

---

## 8. Synthesis (10) and UX (5)

- **Briefing template:** what it does → who leads → where → latest numbers with the year-on-year change and period → hiring → recent dated activity → what changed since last run → **unknowns** (explicit list). Every sentence carries claim refs.
- Optional **Claude Haiku 4.5** rewrite, using a key supplied by Builderr, for readability only. A post-validator rejects any number, name or date not present in the claims and falls back to the template.
- **Static site** (single-file HTML per profile plus an index): search, filter by availability, compare two companies side by side, a "verify" link on every claim that opens the source, a changes timeline, and mobile-first layout. Publish the 1,000-profile artifact on GitHub Pages.

---

## 9. Offline discovery cache (declared)

- Offline, from outside any evaluator budget, run discovery (registry URL, email domain, DNS guess and, if you hold keys personally, search) across the universe, prioritising `employees > 0`, filed revenue above a threshold, and own-domain emails (roughly 60–120k orgs).
- Ship `cache/discovery.parquet` with fields `org → candidate_domain, proof_tier, proof_span, verified_at, domain_multiplicity`, plus the NAV feed index (`orgnr → ad ids`, with the cursor).
- At run time, a cache hit supplies the **candidate only**. Evidence is always **re-fetched live** and re-gated. Companies not in the cache go through the live path, so the agent still works on unseen org numbers.
- Declare the cache in `CRAWLERS.md` / `LIMITATIONS.md`: its build date, method, and the fact that it is used for discovery only.

---

## 10. Evaluation loop

- **Gold set:** 200 companies hand-labelled, split dev 120 / val 80 with no host overlap. Oversample companies with a footprint and hard negatives (co-op managers, holding companies, franchises, namesakes).
- Labels cover the exact site (or none), socials, whether jobs exist, and 3 facts per family.
- `eval/run.py` reports wrong-company publications (**must be 0**), precision and recall per family, evidence validity, refresh false-change rate, requests, p50/p95 and cost.
- Promotion rule from the learning harness: zero new wrong-company publications, no drop in precision, a coverage gain of at least the declared minimum, and staying within budget.
- **Clean-room check before every submission:** fresh clone at the commit → `uv sync --frozen` → the single command on 100 random org numbers from the universe → assert 100 envelopes and the schema.

---

## 11. Timeline

| Dates | Deliverables |
|---|---|
| **Sep 26–28** | Repo skeleton, Pydantic envelope schema v1, async fetcher with request counting and SSRF guard, planner (budget and deadline), guaranteed 100-envelope writer, official modules (all BRREG endpoints including email, purpose, change events). Start labelling the gold set. |
| **Sep 29–Oct 2** | Candidate generation (registry URL, email domain, DNS guess), domain-multiplicity table, identity engine P1–P4/R/A with trap tests, site fan-out and extractors, ledger and refresh with a replay idempotency test. Start the offline cache build. |
| **Oct 3–6** | NAV feed index and hiring, careers/ATS/JSON-LD jobs, news/RSS/sitemap activity, BRREG change events, briefing template, static site. Produce the 1,000-profile artifact. Clean-room test. **Submit v1 (~Oct 6)** only if the gold set shows 0 wrong-company publications. |
| **Oct 7–12** | Optional connectors (Places, search) behind env vars; PDF history with cross-check; cache expansion; fix the biggest error buckets. **v2–v3.** |
| **Oct 13–18** | Hardening and budget tuning, docs set (AGENT, CRAWLERS, IDENTITY_RESOLUTION, DATA_SCHEMA, REFRESH, EVAL, LIMITATIONS). **Final v4/v5 by Oct 18.** |

---

## 12. Risks

| Risk | Mitigation |
|---|---|
| Wrong-company publication | Tiered proof, domain-multiplicity table, `related` label, hard-negative gold set, publication gate in code rather than in prompts |
| Keys not reproducible in the evaluator | Core path is key-free; paid connectors are optional, set by env var, and fall back to `blocked` with the reason `connector_not_configured` |
| Running over the request budget | Counting transport, planner hard cap, robots cache, priority by expected yield |
| NAV public token rotates | Fetch `/api/publicToken` at run start; if it is missing, jobs fall back to site/ATS and the state says so |
| Fabricated financials | Only API values are published; PDF values only with an exact cross-check; nulls stay null |
| Rate limit on filing years (~30/min) | Throttle at 2.1 s: 100 calls take about 3.5 min and run in parallel with other phases |
| BRREG outage | Classified as a shared-source failure (void batch); still emit `failed` envelopes |

---

## 13. Open questions (ask Builderr at submit@builderr.ai)
- **Q1.** Will they inject third-party keys (Brave Search, Google Places) through env vars, or must the run work with no keys at all?
- **Q2.** Confirm that a shipped **offline discovery cache** (candidate domains, with evidence always re-fetched live) is acceptable as a "declared external cache".
- **Q3.** How is previous state passed on a rerun: a `--previous` file from them, or a persistent state directory kept between batches?
- **Q4.** Do DNS lookups count toward the 2,000 outbound requests?
- **Q5.** Which field families and weights make up the 35 coverage points? (They are not published; the sample suggests company, financials, people_places, website and footprint.)
