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


def test_exact_results_carry_a_quotable_evidence_span():
    p1 = assess_site(ENTITY, page(text="Kontakt oss. Org.nr 810 034 882 MVA. Velkommen"), "email_domain", {})
    assert "810 034 882" in p1["span"]

    p2 = assess_site(ENTITY, page(title="Sandnes Elektriske - elektriker"), "registry_website", {"sandneselektriske.no": 1})
    assert "Sandnes Elektriske" in p2["span"]

    p4 = assess_site(
        ENTITY,
        page(title="Sandnes Elektriske", text="Strandgata 21, 4307 Sandnes", final_url="https://other.no/"),
        "search", {"other.no": 1},
    )
    assert "4307" in p4["span"]


def test_non_exact_results_have_no_span():
    result = assess_site(ENTITY, page(title="Sandnes Bakeri"), "registry_website", {})
    assert result["span"] is None


def test_org_number_in_body_text_of_a_shared_domain_is_related_not_exact():
    # e.g. a manager or accountant whose site lists client org numbers
    result = assess_site(
        ENTITY, page(text="Våre kunder: Org.nr 810 034 882", final_url="https://kbbl.bu.no/"),
        "email_domain", {"kbbl.bu.no": 40},
    )
    assert result["status"] == "related"
    assert result["publishable"] is False


def test_org_number_declared_in_structured_identity_on_a_shared_domain_is_exact():
    result = assess_site(
        ENTITY,
        {**page(final_url="https://kbbl.bu.no/"), "identity_text": "Sandnes Elektriske AS NO 810 034 882 MVA"},
        "email_domain", {"kbbl.bu.no": 40},
    )
    assert result["status"] == "exact"
    assert result["tier"] == "P1"


# ---- unchecked domains (no multiplicity table available) -----------------------


def test_unchecked_source_cannot_reach_p2_p3_or_p4():
    for source in ("unchecked_registry_website", "unchecked_email_domain"):
        result = assess_site(
            ENTITY,
            page(title="Sandnes Elektriske", text="Strandgata 21, 4307 Sandnes"),
            source,
            {},
        )
        assert result["status"] == "ambiguous", source


def test_unchecked_source_ignores_org_number_in_body_text():
    result = assess_site(ENTITY, page(text="Org.nr 810 034 882"), "unchecked_email_domain", {})
    assert result["status"] != "exact"


def test_unchecked_source_still_accepts_the_sites_own_structured_declaration():
    result = assess_site(
        ENTITY, {**page(), "identity_text": "NO 810 034 882 MVA"}, "unchecked_registry_website", {}
    )
    assert result["status"] == "exact" and result["tier"] == "P1"


# ---- platform hosts are profiles, never company sites --------------------------


def test_social_and_platform_hosts_are_never_website_candidates():
    for site in ("https://www.facebook.com/sandneselektriske", "instagram.com/x", "linkedin.com/company/x",
                 "www.youtube.com/@x", "x.com/x", "linktr.ee/x"):
        assert candidate_domains({**ENTITY, "website": site, "email": None}) == [], site


def test_a_platform_host_does_not_hide_a_real_email_domain_candidate():
    entity = {**ENTITY, "website": "https://www.facebook.com/x", "email": "post@sandneselektriske.no"}
    assert candidate_domains(entity) == [("sandneselektriske.no", "email_domain")]
