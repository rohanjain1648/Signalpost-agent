import json
from pathlib import Path

import pytest

from signalpost.official import (
    normalize_entity,
    normalize_financial_years,
    normalize_financials,
    normalize_locations,
    normalize_roles,
)

FIXTURES = Path(__file__).parent / "fixtures" / "brreg"


def load(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_normalize_entity_maps_key_registry_fields():
    body = load("entity_810034882.json")
    entity = normalize_entity(body)
    assert entity["organisation_number"] == "810034882"
    assert entity["name"] == "SANDNES ELEKTRISKE AS"
    assert entity["legal_form"] == "AS"
    assert entity["employees"] == 11
    assert entity["email"] == "post@sandneselektriske.no"
    assert entity["municipality"] == "SANDNES"
    assert entity["industry_code"] == "43.210"
    assert entity["postal_code"] == "4307"


def test_normalize_entity_handles_missing_optional_fields():
    entity = normalize_entity({"organisasjonsnummer": "810034882", "navn": "X AS"})
    assert entity["organisation_number"] == "810034882"
    assert entity["email"] is None
    assert entity["website"] is None


def test_normalize_financials_extracts_latest_period_figures():
    body = load("accounts_810034882.json")
    financials = normalize_financials(body)
    records = financials["records"]
    assert len(records) == 1
    record = records[0]
    assert record["period"]["fraDato"] == "2025-01-01"
    assert record["period"]["tilDato"] == "2025-12-31"
    assert record["revenue"] == 10021242.0
    assert record["annual_result"] == -78041.0
    assert record["currency"] == "NOK"


def test_normalize_financials_handles_empty_list():
    assert normalize_financials([]) == {"records": []}


def test_normalize_financial_years_sorts_and_filters_digits():
    years = normalize_financial_years(["2019", "2021", "2020", "not-a-year"])
    assert years == ["2019", "2020", "2021"]


def test_normalize_roles_extracts_named_person_roles():
    body = load("roles_810034882.json")
    roles = normalize_roles(body)
    dagl = next(r for r in roles if r["role_code"] == "DAGL")
    assert dagl["name"] == "Morten Aarrestad"
    assert dagl["role"] == "Daglig leder"


def test_normalize_roles_handles_entity_role_holder_with_list_name():
    body = load("roles_810034882.json")
    roles = normalize_roles(body)
    accountant = next(r for r in roles if r["role_code"] == "REGN")
    assert accountant["organisation_number"] == "926732072"
    assert accountant["name"] == "ACCOUNT GROUP AS"


def test_normalize_locations_extracts_subunit_address_and_industry():
    body = load("subunits_810034882.json")
    locations = normalize_locations(body)
    assert len(locations) == 3
    first = locations[0]
    assert first["organisation_number"] == "973150480"
    assert first["address"]["kommune"] == "SANDNES"
    assert first["industry"]["kode"] == "43.210"
