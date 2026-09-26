from signalpost.identity import (
    assess_site,
    build_domain_multiplicity,
    candidate_domains,
    name_tokens,
)

ENTITY = {
    "organisation_number": "810034882",
    "name": "SANDNES ELEKTRISKE AS",
    "email": "post@sandneselektriske.no",
    "website": None,
    "postal_code": "4307",
    "municipality": "SANDNES",
}


def page(text="", title="", final_url="https://sandneselektriske.no/"):
    return {"final_url": final_url, "title": title, "text": text, "structured_names": []}


# ---- tokens ----------------------------------------------------------------


def test_name_tokens_drop_legal_suffix_and_fold_norwegian_letters():
    assert name_tokens("BJØRKÅS & SØNN AS") == ["bjorkas", "sonn"]


# ---- candidate domains -----------------------------------------------------


def test_candidate_domains_uses_registry_website_and_own_email_domain():
    entity = {**ENTITY, "website": "www.sandneselektriske.no"}
    cands = candidate_domains(entity)
    assert ("sandneselektriske.no", "registry_website") in cands
    assert ("sandneselektriske.no", "email_domain") in cands


def test_candidate_domains_skips_freemail_addresses():
    entity = {**ENTITY, "email": "kari@hotmail.com"}
    assert candidate_domains(entity) == []


# ---- domain multiplicity ---------------------------------------------------


def test_build_domain_multiplicity_counts_distinct_orgs_per_domain():
    rows = [
        {"organisation_number": "1", "email": "a@styrerommet.no"},
        {"organisation_number": "2", "email": "b@styrerommet.no"},
        {"organisation_number": "3", "website": "https://www.solo.no"},
    ]
    mult = build_domain_multiplicity(rows)
    assert mult["styrerommet.no"] == 2
    assert mult["solo.no"] == 1


# ---- assess_site: proof tiers ----------------------------------------------


def test_org_number_on_page_is_exact_p1():
    result = assess_site(ENTITY, page(text="Kontakt oss. Org.nr 810 034 882 MVA"), "email_domain", {})
    assert result["status"] == "exact"
    assert result["tier"] == "P1"


def test_org_number_embedded_in_longer_digit_run_is_not_a_match():
    result = assess_site(ENTITY, page(text="Ref 98100348829999"), "email_domain", {})
    assert result["tier"] != "P1"


def test_registry_website_with_matching_name_is_exact_p2():
    result = assess_site(
        ENTITY, page(title="Sandnes Elektriske - elektriker i Sandnes"), "registry_website", {"sandneselektriske.no": 1}
    )
    assert result["status"] == "exact"
    assert result["tier"] == "P2"


def test_unique_email_domain_with_matching_name_is_exact_p3():
    result = assess_site(
        ENTITY, page(title="Sandnes Elektriske"), "email_domain", {"sandneselektriske.no": 1}
    )
    assert result["status"] == "exact"
    assert result["tier"] == "P3"


def test_shared_email_domain_is_related_not_exact():
    # e.g. a housing-co-op manager's domain shared by many orgs
    entity = {**ENTITY, "name": "DYRGRAV BORETTSLAG", "email": "x@kbbl.bu.no"}
    result = assess_site(
        entity, page(title="KBBL Boligbyggelag", final_url="https://kbbl.bu.no/"), "email_domain", {"kbbl.bu.no": 40}
    )
    assert result["status"] == "related"
    assert result["publishable"] is False


def test_name_match_alone_on_unrelated_domain_is_not_exact():
    result = assess_site(
        ENTITY, page(title="Sandnes Elektriske", final_url="https://other.no/"), "search", {"other.no": 1}
    )
    assert result["status"] != "exact"


def test_name_plus_postal_code_is_exact_p4_even_for_search_candidates():
    result = assess_site(
        ENTITY,
        page(title="Sandnes Elektriske", text="Strandgata 21, 4307 Sandnes", final_url="https://other.no/"),
        "search",
        {"other.no": 1},
    )
    assert result["status"] == "exact"
    assert result["tier"] == "P4"


def test_different_org_number_on_page_marks_related_parent():
    result = assess_site(
        ENTITY,
        page(title="Sandnes Elektriske", text="Konsernet. Org.nr 923 609 016"),
        "registry_website",
        {"sandneselektriske.no": 1},
    )
    assert result["status"] == "related"
    assert result["publishable"] is False


def test_parked_page_is_ambiguous():
    result = assess_site(
        ENTITY, page(title="Sandnes Elektriske", text="This domain is for sale"), "registry_website", {"sandneselektriske.no": 1}
    )
    assert result["status"] == "ambiguous"


def test_partial_name_overlap_is_ambiguous():
    result = assess_site(
        ENTITY, page(title="Sandnes Bakeri"), "registry_website", {"sandneselektriske.no": 1}
    )
    assert result["status"] == "ambiguous"
    assert result["publishable"] is False


def test_domain_spelling_the_legal_name_counts_when_page_has_no_title():
    result = assess_site(ENTITY, page(title="", text=""), "registry_website", {"sandneselektriske.no": 1})
    assert result["status"] == "exact"
    assert result["tier"] == "P2"
