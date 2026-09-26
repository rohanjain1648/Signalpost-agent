"""Fetch + normalize + evidence-wrap each BRREG official module for one org number.

This is where `signalpost.net` (counted, SSRF-guarded HTTP) meets
`signalpost.official` (pure normalizers). Every module produces the same
shape: `{"status", "value", "evidence"}`, where `status` is one of
`available` / `not_found` / `source_error` and `evidence` carries the
provenance fields required by OUTPUT_CONTRACT.md (source_url, retrieved_at,
content_sha256, ...).
"""
from __future__ import annotations

from typing import Any

from .net import CountingClient, FetchResult
from .official import (
    BRREG_ACCOUNT_YEARS,
    BRREG_ACCOUNTS,
    BRREG_ENTITY,
    BRREG_GROUP,
    BRREG_ROLES,
    BRREG_SUBUNITS,
    normalize_entity,
    normalize_financial_years,
    normalize_financials,
    normalize_locations,
    normalize_roles,
)

_NORMALIZERS = {
    "registry_live": normalize_entity,
    "financials": normalize_financials,
    "financial_history": lambda body: {"years": normalize_financial_years(body)},
    "roles": lambda body: {"roles": normalize_roles(body)},
    "locations": lambda body: {"locations": normalize_locations(body)},
    "group": lambda body: body,
}

_ENDPOINTS = {
    "registry_live": (BRREG_ENTITY, "official_registry_live"),
    "financials": (BRREG_ACCOUNTS, "official_annual_accounts"),
    "financial_history": (BRREG_ACCOUNT_YEARS, "official_annual_account_copies"),
    "roles": (BRREG_ROLES, "official_roles"),
    "locations": (BRREG_SUBUNITS, "official_subunits"),
    "group": (BRREG_GROUP, "official_group_structure"),
}


def _status_for(result: FetchResult) -> str:
    if result.status == 200:
        return "available"
    if result.status in {404, 410}:
        return "not_found"
    return "source_error"


def _evidence(module: str, source_class: str, result: FetchResult) -> dict[str, Any]:
    return {
        "source_url": result.url,
        "final_url": result.final_url,
        "source_class": source_class,
        "retrieved_at": result.retrieved_at,
        "content_sha256": result.content_sha256,
        "note": result.error,
    }


def fetch_official_modules(
    org: str, modules: set[str], client: CountingClient
) -> dict[str, dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    for module in modules:
        if module not in _ENDPOINTS:
            continue
        url_template, source_class = _ENDPOINTS[module]
        url = url_template.format(org=org)
        result = client.fetch_json(url)
        status = _status_for(result)
        value = _NORMALIZERS[module](result.body) if status == "available" else None
        records[module] = {
            "status": status,
            "value": value,
            "evidence": _evidence(module, source_class, result),
        }
    return records
