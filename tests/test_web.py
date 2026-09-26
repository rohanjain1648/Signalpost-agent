import socket

import httpx

from signalpost.net import CountingClient
from signalpost.robots import RobotsCache
from signalpost.web import research_website

ENTITY = {
    "organisation_number": "810034882",
    "name": "SANDNES ELEKTRISKE AS",
    "email": "post@sandneselektriske.no",
    "website": "www.sandneselektriske.no",
    "postal_code": "4307",
    "municipality": "SANDNES",
}
HOST = "https://www.sandneselektriske.no"

HOME = """<html><head><title>Sandnes Elektriske - elektriker i Sandnes</title>
<meta name="description" content="Elektriske installasjoner."></head><body>
<a href="/kontakt">Kontakt</a><a href="/om-oss">Om oss</a><a href="/karriere">Karriere</a>
<a href="/nyheter">Nyheter</a><a href="https://elsewhere.com/karriere">Jobb</a>
<a href="https://www.facebook.com/sandneselektriske">FB</a></body></html>"""
CONTACT = '<html><title>Kontakt</title><body><a href="mailto:post@sandneselektriske.no">Mail</a> Strandgata 21</body></html>'
ABOUT = "<html><title>Om oss</title><body>Vi er elektrikere.</body></html>"
CAREERS = """<html><title>Karriere</title><script type="application/ld+json">
{"@type":"JobPosting","title":"Elektriker","datePosted":"2026-09-01","url":"HOST/karriere/1"}</script></html>""".replace("HOST", HOST)
NEWS = "<html><title>Nyheter</title><body>Ny avtale signert</body></html>"


def site(overrides=None, robots="User-agent: *\nDisallow:\n", unresolvable=()):
    routes = {
        f"{HOST}/robots.txt": (200, robots),
        f"{HOST}/": (200, HOME),
        f"{HOST}/kontakt": (200, CONTACT),
        f"{HOST}/om-oss": (200, ABOUT),
        f"{HOST}/karriere": (200, CAREERS),
        f"{HOST}/nyheter": (200, NEWS),
    }
    routes.update(overrides or {})
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        seen.append(url)
        if url not in routes:
            return httpx.Response(404, text="nope")
        status, body = routes[url]
        if status == "connect_error":
            raise httpx.ConnectError("connection refused")
        if status == "timeout":
            raise httpx.ReadTimeout("timed out")
        if status in (301, 302):
            return httpx.Response(status, headers={"location": body})
        return httpx.Response(status, text=body, headers={"content-type": "text/html; charset=utf-8"})

    def resolver(host):
        if host in unresolvable:
            raise socket.gaierror("no such host")
        return ["8.8.8.8"]

    client = CountingClient(transport=httpx.MockTransport(handler), max_requests=100, resolver=resolver)
    return client, RobotsCache(client), seen


def run(entity=ENTITY, client_bundle=None, multiplicity=None, **kw):
    client, robots, seen = client_bundle or site()
    return research_website(entity, client, robots, multiplicity or {"sandneselektriske.no": 1}, **kw), client, seen


def test_registry_website_with_matching_name_is_verified_and_fanned_out():
    result, client, seen = run()
    assert result["status"] == "available"
    assert result["proof"]["tier"] == "P2"
    assert result["homepage"]["final_url"] == f"{HOST}/"
    assert {p["kind"] for p in result["pages"]} == {"contact", "about", "careers", "news"}
    assert result["jobs"][0]["title"] == "Elektriker"
    assert result["jobs"][0]["page_url"] == f"{HOST}/karriere"
    assert "post@sandneselektriske.no" in result["emails"]
    assert {"platform": "facebook", "url": "https://www.facebook.com/sandneselektriske"} == {
        k: v for k, v in result["socials"][0].items() if k in {"platform", "url"}
    }
    assert client.requests_made == len(seen) == 6  # robots + home + 4 pages


def test_fan_out_never_leaves_the_verified_site():
    _, _, seen = run()
    assert not any("elsewhere.com" in url for url in seen)


def test_max_pages_limits_the_fan_out():
    result, client, _ = run(max_pages=2)
    assert len(result["pages"]) == 2
    assert client.requests_made == 4


def test_no_candidate_domain_is_not_available_and_makes_no_requests():
    entity = {**ENTITY, "website": None, "email": "kari@gmail.com"}
    result, client, _ = run(entity, discover=False)  # discovery has its own tests below
    assert result["status"] == "not_available"
    assert client.requests_made == 0
    assert result["candidates"] == []


def test_robots_disallow_blocks_the_site_without_fetching_pages():
    bundle = site(robots="User-agent: *\nDisallow: /\n")
    result, client, seen = run(client_bundle=bundle)
    assert result["status"] == "blocked"
    assert seen == [f"{HOST}/robots.txt"]


def test_shared_email_domain_is_ambiguous_and_publishes_nothing():
    entity = {**ENTITY, "website": None, "name": "DYRGRAV BORETTSLAG", "email": "x@sandneselektriske.no"}
    bundle = site({"https://sandneselektriske.no/": (200, HOME)})
    result, _, _ = run(entity, client_bundle=bundle, multiplicity={"sandneselektriske.no": 40})
    assert result["status"] == "ambiguous"
    assert result["socials"] == [] and result["emails"] == [] and result["pages"] == []
    assert result["candidates"][0]["outcome"] == "related"


def test_missing_homepage_is_not_available_and_server_error_is_failed():
    result, _, _ = run(client_bundle=site({f"{HOST}/": (404, "")}))
    assert result["status"] == "not_available"
    result, _, _ = run(client_bundle=site({f"{HOST}/": (503, "")}))
    assert result["status"] == "failed"


def test_org_number_on_page_verifies_an_email_domain_candidate():
    entity = {**ENTITY, "website": None, "name": "HELT ANNET NAVN AS"}
    home = "<html><title>Firma</title><body>Org.nr 810 034 882 MVA</body></html>"
    bundle = site({
        "https://sandneselektriske.no/": (301, f"{HOST}/"),
        f"{HOST}/": (200, home),
    })
    result, _, _ = run(entity, client_bundle=bundle)
    assert result["status"] == "available"
    assert result["proof"]["tier"] == "P1"
    assert "810 034 882" in result["proof"]["span"]


def test_redirect_to_a_host_whose_robots_disallows_is_blocked():
    entity = {**ENTITY, "website": "sandneselektriske.no"}
    bundle = site({
        "https://sandneselektriske.no/": (301, "https://other-host.no/"),
        "https://other-host.no/robots.txt": (200, "User-agent: *\nDisallow: /\n"),
    })
    result, _, seen = run(entity, client_bundle=bundle)
    assert result["status"] == "blocked"
    assert "https://other-host.no/" not in seen


def test_site_that_redirects_to_another_domain_is_gated_on_the_final_host():
    bundle = site({
        "https://sandneselektriske.no/": (301, "https://parent-holding.no/"),
        "https://parent-holding.no/robots.txt": (200, "User-agent: *\nDisallow:\n"),
        "https://parent-holding.no/": (200, "<html><title>Sandnes Elektriske</title></html>"),
    })
    entity = {**ENTITY, "website": "sandneselektriske.no"}
    result, _, _ = run(entity, client_bundle=bundle, multiplicity={"sandneselektriske.no": 1, "parent-holding.no": 12})
    assert result["status"] == "ambiguous"  # the final host is shared by 12 entities


def test_internal_link_that_redirects_off_site_is_discarded():
    bundle = site({
        f"{HOST}/nyheter": (301, "https://elsewhere.com/nyheter"),
        "https://elsewhere.com/robots.txt": (200, "User-agent: *\nDisallow:\n"),
        "https://elsewhere.com/nyheter": (200, "<html><title>Someone else</title><a href='mailto:x@elsewhere.com'>m</a></html>"),
    })
    result, _, _ = run(client_bundle=bundle)
    assert "news" not in {p["kind"] for p in result["pages"]}
    assert "x@elsewhere.com" not in result["emails"]


def test_without_a_multiplicity_table_a_registry_site_is_not_verified_by_name_alone():
    client, robots, _ = site()
    result = research_website(ENTITY, client, robots, None)
    assert result["status"] == "ambiguous"
    assert result["candidates"][0]["source"] == "unchecked_registry_website"
    assert result["socials"] == [] and result["pages"] == []


def test_without_a_multiplicity_table_a_structured_org_number_still_verifies():
    home = ('<html><title>X</title><script type="application/ld+json">'
            '{"@type":"Organization","name":"X","vatID":"NO 810 034 882 MVA"}</script></html>')
    client, robots, _ = site({f"{HOST}/": (200, home)})
    result = research_website(ENTITY, client, robots, None)
    assert result["status"] == "available"
    assert result["proof"]["tier"] == "P1"


def test_each_contact_value_records_the_page_it_was_found_on():
    result, _, _ = run()
    assert result["contact_sources"]["email:post@sandneselektriske.no"] == f"{HOST}/kontakt"


def test_careers_checked_is_true_only_when_the_careers_page_was_actually_read():
    result, _, _ = run()
    assert result["careers_checked"] is True

    result, _, _ = run(client_bundle=site({f"{HOST}/karriere": (503, "")}))
    assert result["status"] == "available"
    assert result["careers_checked"] is False
    assert result["jobs"] == []


# ---- fallbacks: apex -> www, https -> http --------------------------------------


EMAIL_ONLY = {**ENTITY, "website": None}


def test_apex_domain_that_does_not_resolve_falls_back_to_www():
    bundle = site(unresolvable={"sandneselektriske.no"})
    result, _, seen = run(EMAIL_ONLY, client_bundle=bundle)
    assert result["status"] == "available"
    assert result["homepage"]["final_url"] == f"{HOST}/"
    assert "https://sandneselektriske.no/" not in seen  # never requested: DNS failed first


def test_apex_connection_error_falls_back_to_www():
    bundle = site({"https://sandneselektriske.no/robots.txt": ("connect_error", ""),
                   "https://sandneselektriske.no/": ("connect_error", "")})
    result, _, _ = run(EMAIL_ONLY, client_bundle=bundle)
    assert result["status"] == "available"


def test_https_failure_on_a_www_site_falls_back_to_http():
    entity = {**ENTITY, "website": "www.sandneselektriske.no"}
    bundle = site({
        f"{HOST}/robots.txt": ("connect_error", ""), f"{HOST}/": ("connect_error", ""),
        "http://www.sandneselektriske.no/robots.txt": (200, "User-agent: *\nDisallow:\n"),
        "http://www.sandneselektriske.no/": (200, HOME),
    })
    result, _, _ = run(entity, client_bundle=bundle)
    assert result["status"] == "available"
    assert result["homepage"]["final_url"] == "http://www.sandneselektriske.no/"


def test_http_error_status_does_not_trigger_a_fallback():
    bundle = site({"https://sandneselektriske.no/": (404, "")})
    result, _, seen = run(EMAIL_ONLY, client_bundle=bundle)
    assert result["status"] == "not_available"
    assert not any(url.startswith(HOST) for url in seen)


def test_nothing_reachable_is_failed_not_blocked():
    bundle = site({
        "https://sandneselektriske.no/robots.txt": ("connect_error", ""),
        "https://sandneselektriske.no/": ("connect_error", ""),
        f"{HOST}/robots.txt": ("connect_error", ""), f"{HOST}/": ("connect_error", ""),
    })
    result, _, _ = run(EMAIL_ONLY, client_bundle=bundle)
    assert result["status"] == "not_available"  # no web server answers: nothing to find, nothing broke
    assert "no web server" in result["candidates"][0]["reason"]


def test_social_links_that_do_not_relate_to_the_company_are_quarantined_not_published():
    home = HOME.replace('<a href="https://www.facebook.com/sandneselektriske">FB</a>',
                        '<a href="https://www.facebook.com/sandneselektriske">FB</a>'
                        '<a href="https://www.facebook.com/somewebagency">Laget av</a>')
    result, _, _ = run(client_bundle=site({f"{HOST}/": (200, home)}))
    assert [s["url"] for s in result["socials"]] == ["https://www.facebook.com/sandneselektriske"]
    assert [s["url"] for s in result["quarantined_socials"]] == ["https://www.facebook.com/somewebagency"]


def test_a_placeholder_page_means_no_company_site_exists_yet():
    home = "<html><title>liivat.no</title><body>This is the future home of: www.liivat.no</body></html>"
    entity = {**ENTITY, "name": "LIIVAT AS", "website": None, "email": "info@liivat.no"}
    bundle = site({"https://liivat.no/": (200, home)})
    result, _, _ = run(entity, client_bundle=bundle)
    assert result["status"] == "not_available"
    assert result["socials"] == [] and result["pages"] == []


def test_a_timeout_is_still_failed_because_it_may_be_transient():
    class TimeoutOnce(httpx.MockTransport):
        pass

    bundle = site({"https://sandneselektriske.no/robots.txt": ("timeout", ""),
                   "https://sandneselektriske.no/": ("timeout", ""),
                   f"{HOST}/robots.txt": ("timeout", ""), f"{HOST}/": ("timeout", "")})
    result, _, _ = run(EMAIL_ONLY, client_bundle=bundle)
    assert result["status"] == "failed"


# ---- discovery: name-derived domains ---------------------------------------------

NO_CANDIDATE = {**ENTITY, "website": None, "email": None}
GUESS = "https://sandneselektriske.no"


def guess_site(home, unresolvable=(), extra=None):
    routes = {f"{GUESS}/": (200, home)}
    routes.update(extra or {})
    return site(routes, unresolvable=set(unresolvable) | {"sandnes-elektriske.no"})


def test_a_guessed_domain_is_verified_when_the_page_shows_the_organisation_number():
    home = "<html><title>Firma</title><body>Org.nr 810 034 882 MVA. Vi er elektrikere.</body></html>"
    result, client_, seen = run(NO_CANDIDATE, client_bundle=guess_site(home))
    assert result["status"] == "available"
    assert result["proof"]["tier"] == "P1"
    assert result["candidates"][0]["source"] == "name_guess"


def test_a_guessed_domain_is_verified_by_full_name_plus_registered_postal_code():
    home = "<html><title>Sandnes Elektriske</title><body>Strandgata 21, 4307 Sandnes</body></html>"
    result, _, _ = run(NO_CANDIDATE, client_bundle=guess_site(home))
    assert result["status"] == "available"
    assert result["proof"]["tier"] == "P4"


def test_a_guessed_domain_is_never_verified_by_a_matching_name_alone():
    home = "<html><title>Sandnes Elektriske</title><body>Velkommen til vår elektrikerbedrift.</body></html>"
    result, _, _ = run(NO_CANDIDATE, client_bundle=guess_site(home))
    assert result["status"] == "ambiguous"
    assert result["socials"] == [] and result["pages"] == []


def test_guesses_that_do_not_resolve_cost_no_http_requests():
    bundle = site(unresolvable={"sandneselektriske.no", "sandnes-elektriske.no"})
    result, client_, seen = run(NO_CANDIDATE, client_bundle=bundle)
    assert result["status"] == "not_available"
    assert client_.requests_made == 0 and seen == []
    assert [g["resolved"] for g in result["guesses"]] == [False, False]


def test_discovery_can_be_switched_off():
    bundle = site()
    result, client_, _ = run(NO_CANDIDATE, client_bundle=bundle, discover=False)
    assert result["guesses"] == []
    assert client_.dns_lookups == 0


def test_guessing_is_skipped_when_a_registry_candidate_already_produced_a_page():
    result, _, seen = run(client_bundle=site())  # registry website verifies (P2)
    assert result["status"] == "available"
    assert result["guesses"] == []


def test_guessing_is_tried_when_the_registry_candidate_is_a_dead_domain():
    entity = {**ENTITY, "website": None, "email": "x@dead-mail-domain.no"}
    home = "<html><title>Firma</title><body>Org.nr 810 034 882</body></html>"
    bundle = site({f"{GUESS}/": (200, home)},
                  unresolvable={"dead-mail-domain.no", "www.dead-mail-domain.no", "sandnes-elektriske.no"})
    result, _, _ = run(entity, client_bundle=bundle)
    assert result["status"] == "available"
    assert [c["source"] for c in result["candidates"]] == ["email_domain", "name_guess"]


def test_a_guess_on_a_shared_domain_is_related_and_never_published():
    home = "<html><title>Sandnes Elektriske</title><body>Org.nr 810 034 882</body></html>"
    result, _, _ = run(NO_CANDIDATE, client_bundle=guess_site(home), multiplicity={"sandneselektriske.no": 9})
    assert result["status"] == "ambiguous"
    assert result["candidates"][0]["outcome"] == "related"


def test_discovery_never_probes_a_domain_that_was_already_tried_or_blocked():
    bundle = site(robots="User-agent: *\nDisallow: /\n")
    result, client_, seen = run(client_bundle=bundle)  # registry site is www.sandneselektriske.no
    assert result["status"] == "blocked"
    assert result["guesses"] == []  # a robots block is respected, not worked around
    assert not any(u.startswith("https://sandneselektriske.no") for u in seen)


def test_a_guess_equal_to_an_already_tried_registry_domain_is_skipped():
    entity = {**ENTITY, "website": None, "email": "post@sandneselektriske.no"}  # tried as email_domain
    bundle = site({"https://sandneselektriske.no/": (404, "")}, unresolvable={"sandnes-elektriske.no"})
    result, _, seen = run(entity, client_bundle=bundle)
    assert [g["domain"] for g in result["guesses"]] == ["sandnes-elektriske.no"]
    assert seen.count("https://sandneselektriske.no/") == 1
