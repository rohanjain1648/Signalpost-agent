"""Pure normalizers for Brønnøysundregistrene (BRREG) API responses.

These functions take the raw JSON body from each official endpoint and
return a plain, stable-shaped dict. They never make network calls and
never invent a value: a missing field stays `None`, an empty list stays
`[]`. Network fetching and evidence-wrapping live in `signalpost.fetch`.
"""
from __future__ import annotations

from typing import Any

BRREG_ENTITY = "https://data.brreg.no/enhetsregisteret/api/enheter/{org}"
BRREG_ROLES = BRREG_ENTITY + "/roller"
BRREG_GROUP = "https://data.brreg.no/enhetsregisteret/api/konsernstruktur/{org}"
BRREG_SUBUNITS = "https://data.brreg.no/enhetsregisteret/api/underenheter?overordnetEnhet={org}&size=1000"
BRREG_ACCOUNTS = "https://data.brreg.no/regnskapsregisteret/regnskap/{org}"
BRREG_ACCOUNT_YEARS = "https://data.brreg.no/regnskapsregisteret/regnskap/aarsregnskap/kopi/{org}/aar"
BRREG_ACCOUNT_PDF = "https://data.brreg.no/regnskapsregisteret/regnskap/aarsregnskap/kopi/{org}/{year}"


def _get(value: Any, *path: str) -> Any:
    for key in path:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def _display_name(entity_or_person: dict[str, Any] | None) -> str | None:
    """A role holder is either a person (fornavn/etternavn) or an entity (navn, sometimes a list)."""
    if not isinstance(entity_or_person, dict):
        return None
    navn = entity_or_person.get("navn")
    if isinstance(navn, list):
        return " ".join(part for part in navn if part) or None
    if isinstance(navn, str):
        return navn or None
    name = entity_or_person.get("navn") if isinstance(entity_or_person.get("navn"), dict) else None
    if name:
        parts = [name.get("fornavn"), name.get("mellomnavn"), name.get("etternavn")]
        return " ".join(part for part in parts if part) or None
    return None


def _capital(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    return {"amount": value.get("belop"), "currency": value.get("valuta"), "type": value.get("type"),
            "registered_date": value.get("innfortDato")}


def normalize_entity(body: Any) -> dict[str, Any]:
    body = body if isinstance(body, dict) else {}
    return {
        "organisation_number": body.get("organisasjonsnummer"),
        "name": body.get("navn"),
        "legal_form": _get(body, "organisasjonsform", "kode"),
        "employees": body.get("antallAnsatte"),
        "email": body.get("epostadresse"),
        "phone": body.get("telefon"),
        "bankrupt": body.get("konkurs"),
        "liquidating": body.get("underAvvikling"),
        "in_group": body.get("erIKonsern"),
        "website": body.get("hjemmeside"),
        "industry_code": _get(body, "naeringskode1", "kode"),
        "industry_label": _get(body, "naeringskode1", "beskrivelse"),
        "purpose": body.get("vedtektsfestetFormaal"),
        "municipality": _get(body, "forretningsadresse", "kommune"),
        "postal_code": _get(body, "forretningsadresse", "postnummer"),
        "business_address": body.get("forretningsadresse"),
        "postal_address": body.get("postadresse"),
        "historical_names": body.get("historiskeNavn") or [],
        "latest_submitted_accounts": body.get("sisteInnsendteAarsregnskap"),
        "articles_date": body.get("vedtektsdato"),
        "capital": _capital(body.get("kapital")),
        "employees_registered_date": body.get("registreringsdatoAntallAnsatteEnhetsregisteret"),
        "founded_date": body.get("stiftelsesdato"),
    }


def normalize_financials(body: Any) -> dict[str, Any]:
    records = body if isinstance(body, list) else []
    normalized = []
    for item in records[:3]:
        normalized.append(
            {
                "record_id": item.get("id"),
                "account_type": item.get("regnskapstype"),
                "period": item.get("regnskapsperiode"),
                "currency": item.get("valuta"),
                "revenue": _get(item, "resultatregnskapResultat", "driftsresultat", "driftsinntekter", "sumDriftsinntekter"),
                "operating_result": _get(item, "resultatregnskapResultat", "driftsresultat", "driftsresultat"),
                "profit_before_tax": _get(item, "resultatregnskapResultat", "ordinaertResultatFoerSkattekostnad"),
                "annual_result": _get(item, "resultatregnskapResultat", "aarsresultat"),
                "assets": _get(item, "eiendeler", "sumEiendeler"),
                "equity": _get(item, "egenkapitalGjeld", "egenkapital", "sumEgenkapital"),
                "debt": _get(item, "egenkapitalGjeld", "gjeldOversikt", "sumGjeld"),
            }
        )
    return {"records": normalized}


def normalize_financial_years(body: Any) -> list[str]:
    if not isinstance(body, list):
        return []
    return sorted({str(year) for year in body if str(year).isdigit()})


def normalize_roles(body: Any) -> list[dict[str, Any]]:
    roles: list[dict[str, Any]] = []
    for group in body.get("rollegrupper", []) if isinstance(body, dict) else []:
        changed = group.get("sistEndret")
        for item in group.get("roller", []):
            holder = item.get("person") or item.get("enhet") or {}
            roles.append(
                {
                    "name": _display_name(holder),
                    "organisation_number": holder.get("organisasjonsnummer"),
                    "role_code": _get(item, "type", "kode"),
                    "role": _get(item, "type", "beskrivelse"),
                    "group_code": _get(group, "type", "kode"),
                    "group": _get(group, "type", "beskrivelse"),
                    "last_changed": changed,
                    "inactive": bool(item.get("avregistrert")),
                }
            )
    return roles


def normalize_locations(body: Any) -> list[dict[str, Any]]:
    rows = _get(body, "_embedded", "underenheter") or []
    return [
        {
            "organisation_number": item.get("organisasjonsnummer"),
            "name": item.get("navn"),
            "address": item.get("beliggenhetsadresse") or item.get("postadresse"),
            "industry": item.get("naeringskode1"),
            "employees": item.get("antallAnsatte"),
        }
        for item in rows
    ]
