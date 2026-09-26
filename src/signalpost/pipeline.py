"""Per-company pipeline: official BRREG data -> claims + evidence + ledger changes.

Website/identity fan-out, hiring and activity families plug in after this
official base; every family they add follows the same shape: claims that
point at evidence, availability that is never silently zero, and ledger
observations so refreshes report only real changes.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .fetch import fetch_official_modules
from .ledger import apply_observations
from .models import Claim, Envelope, Evidence
from .net import CountingClient
from .official import BRREG_ACCOUNT_PDF
from .robots import RobotsCache
from .site_claims import build_site_output, entity_unavailable_claims, failed_site_output
from .web import research_website

EXTRACTOR = "brreg-json/1"
OFFICIAL_MODULES = {"registry_live", "financials", "financial_history", "roles", "locations"}
FIELD = {
    "registry_live": "legal_identity",
    "financials": "annual_accounts",
    "financial_history": "financial_history",
    "roles": "leadership",
    "locations": "registered_workplaces",
    "group": "corporate_group",
}
LEDGER_FAMILY = {
    "financials": "financials",
    "financial_history": "financials",
    "roles": "roles",
    "locations": "locations",
}
FINANCIAL_METRICS = ("revenue", "operating_result", "profit_before_tax", "annual_result", "assets", "equity", "debt")


@dataclass
class WebResearch:
    """Website-research settings. `multiplicity=None` means no domain table: domains are unchecked."""
    robots: RobotsCache
    multiplicity: dict[str, int] | None
    max_pages: int = 4


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _availability(record: dict[str, Any], has_data: bool) -> str:
    status = record["status"]
    if status == "available":
        return "available" if has_data else "not_available"
    return "not_available" if status == "not_found" else "failed"


def _has_data(module: str, value: Any) -> bool:
    if value is None:
        return False
    if module == "registry_live":
        return True
    if module == "financials":
        return bool(value["records"])
    if module == "financial_history":
        return bool(value["years"])
    if module == "roles":
        return bool(value["roles"])
    if module == "locations":
        return bool(value["locations"])
    return bool(value)


def _present_value(module: str, org: str, value: Any) -> Any:
    if value is None:
        return None
    if module == "roles":
        return {"roles": [r for r in value["roles"] if not r["inactive"]]}
    if module == "financial_history":
        return {
            "years": value["years"],
            "pdfs": [{"year": y, "url": BRREG_ACCOUNT_PDF.format(org=org, year=y)} for y in reversed(value["years"])],
        }
    return value


def _as_of(module: str, value: Any) -> str | None:
    if module != "financials" or not value or not value["records"]:
        return None
    ends = [(r.get("period") or {}).get("tilDato") for r in value["records"]]
    return max((e for e in ends if e), default=None)


def _observations(org: str, modules: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    obs: list[dict[str, Any]] = []

    def evidence_id(module: str) -> str:
        return f"ev-{module}"

    roles = modules.get("roles", {}).get("value")
    for role in (roles or {}).get("roles", []):
        if role["inactive"]:
            continue
        obs.append({
            "family": "roles",
            "key": f"{role['role_code']}|{role['name']}|{role['organisation_number'] or ''}",
            "value": {"name": role["name"], "role": role["role"], "role_code": role["role_code"], "group": role["group"]},
            "evidence_id": evidence_id("roles"),
        })
    for loc in (modules.get("locations", {}).get("value") or {}).get("locations", []):
        code = (loc["industry"] or {}).get("kode") if isinstance(loc["industry"], dict) else None
        obs.append({
            "family": "locations",
            "key": str(loc["organisation_number"]),
            "value": {"name": loc["name"], "address": loc["address"], "industry_code": code, "employees": loc["employees"]},
            "evidence_id": evidence_id("locations"),
        })
    for record in (modules.get("financials", {}).get("value") or {}).get("records", []):
        end = (record.get("period") or {}).get("tilDato")
        for metric in FINANCIAL_METRICS:
            if end and record.get(metric) is not None:
                obs.append({
                    "family": "financials",
                    "key": f"{end}:{metric}",
                    "value": {"amount": record[metric], "currency": record.get("currency")},
                    "evidence_id": evidence_id("financials"),
                })
    for year in (modules.get("financial_history", {}).get("value") or {}).get("years", []):
        obs.append({
            "family": "financials",
            "key": f"filing:{year}",
            "value": {"year": year, "pdf": BRREG_ACCOUNT_PDF.format(org=org, year=year)},
            "evidence_id": evidence_id("financial_history"),
        })
    return obs


def process_company(
    org: str,
    client: CountingClient,
    *,
    run_id: str,
    run_ts: str,
    prev_state: dict[str, dict[str, Any]],
    web: WebResearch | None = None,
) -> tuple[Envelope, dict[str, dict[str, Any]]]:
    started = time.monotonic()
    requests_before = client.requests_made

    modules = fetch_official_modules(org, set(OFFICIAL_MODULES), client)
    entity = modules["registry_live"]
    if entity["status"] == "available" and (entity["value"] or {}).get("in_group"):
        modules.update(fetch_official_modules(org, {"group"}, client))

    evidence: list[Evidence] = []
    for module, record in modules.items():
        ev = record["evidence"]
        evidence.append(Evidence(
            id=f"ev-{module}", source_url=ev["source_url"], final_url=ev["final_url"],
            source_class=ev["source_class"], retrieved_at=ev["retrieved_at"],
            content_sha256=ev["content_sha256"], extractor=EXTRACTOR,
        ))

    claims: list[Claim] = []
    errors: list[dict[str, Any]] = []
    for module, record in modules.items():
        value = _present_value(module, org, record["value"])
        claims.append(Claim(
            field=FIELD[module], value=value,
            availability=_availability(record, _has_data(module, value)),
            evidence_ids=[f"ev-{module}"], as_of=_as_of(module, value),
        ))
        if record["status"] == "source_error":
            errors.append({"module": module, "type": "source_error", "message": record["evidence"]["note"]})
    if "group" not in modules:
        claims.append(Claim(
            field=FIELD["group"], value=None,
            availability="not_applicable" if entity["status"] == "available" else "failed",
            evidence_ids=["ev-registry_live"],
        ))

    checked = {
        LEDGER_FAMILY[m] for m, r in modules.items()
        if m in LEDGER_FAMILY and r["status"] in {"available", "not_found"}
    } - {"financials"}  # the accounts endpoint only returns recent periods: older ones are never "withdrawn"
    failed = {LEDGER_FAMILY[m] for m, r in modules.items() if m in LEDGER_FAMILY and r["status"] == "source_error"}
    observations = _observations(org, modules)
    if web is not None:
        if entity["status"] == "available":
            try:
                site = build_site_output(research_website(
                    entity["value"], client, web.robots, web.multiplicity, max_pages=web.max_pages))
            except Exception as exc:  # noqa: BLE001 - a website crash must never discard the official data
                message = f"{type(exc).__name__}: {exc}"
                errors.append({"module": "website", "type": "exception", "message": message})
                site = failed_site_output(f"website research crashed ({message})")
            claims.extend(site.claims)
            evidence.extend(site.evidence)
            observations.extend(site.observations)
            checked |= site.checked
            failed |= site.failed
        else:
            claims.extend(entity_unavailable_claims(entity["status"]))
    state, changes = apply_observations(
        org, prev_state, observations,
        run_ts=run_ts, checked_families=checked, failed_families=failed,
    )

    envelope = Envelope(
        organisation_number=org,
        run={
            "run_id": run_id, "started_at": run_ts, "completed_at": _utc_now(),
            "terminal_status": "failed" if entity["status"] == "source_error" else "completed",
        },
        claims=claims, evidence=evidence, changes=changes, errors=errors,
        operations={
            "requests": client.requests_made - requests_before,
            "runtime_ms": int((time.monotonic() - started) * 1000),
            "third_party_cost_usd": 0.0,
        },
    )
    return envelope, state
