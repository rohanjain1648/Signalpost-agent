from signalpost.extract import extract_page

BASE = "https://www.sandneselektriske.no/om-oss/"

HTML = """<!doctype html>
<html lang="nb"><head>
<meta charset="utf-8">
<title>Sandnes Elektriske – elektriker i Sandnes</title>
<meta name="description" content="Vi utfører elektriske installasjoner.">
<meta property="og:site_name" content="Sandnes Elektriske">
<script type="application/ld+json">
{"@context":"https://schema.org","@graph":[
 {"@type":"Organization","name":"Sandnes Elektriske","legalName":"Sandnes Elektriske AS",
  "vatID":"NO 810 034 882 MVA","telephone":"+47 51 62 00 00","email":"post@sandneselektriske.no",
  "url":"https://www.sandneselektriske.no/",
  "address":{"@type":"PostalAddress","streetAddress":"Strandgata 21","postalCode":"4307","addressLocality":"Sandnes"},
  "sameAs":["https://www.facebook.com/sandneselektriske","https://www.linkedin.com/company/sandnes-elektriske/"]},
 {"@type":"WebSite","name":"x"}]}
</script>
<script type="application/ld+json">
{"@type":"JobPosting","title":"Elektriker","datePosted":"2026-09-01","validThrough":"2026-10-15",
 "employmentType":"FULL_TIME","url":"https://www.sandneselektriske.no/karriere/elektriker",
 "hiringOrganization":{"@type":"Organization","name":"Sandnes Elektriske AS"},
 "jobLocation":{"@type":"Place","address":{"addressLocality":"Sandnes"}}}
</script>
<style>.x{color:red}</style>
</head><body>
<nav><a href="/">Hjem</a><a href="/om-oss/">Om oss</a><a href="/kontakt">Kontakt</a>
<a href="/karriere">Ledige stillinger</a><a href="/nyheter">Nyheter</a>
<a href="https://sandneselektriske.easycruit.com/">Jobb hos oss</a></nav>
<main><h1>Om oss</h1><p>Vi er  elektrikere.</p><script>var secret = 1;</script></main>
<footer>
<a href="mailto:kari@sandneselektriske.no?subject=Hei">Skriv til oss</a>
<a href="tel:+4751620000">Ring</a>
<a href="https://www.facebook.com/sandneselektriske/">Facebook</a>
<a href="https://www.facebook.com/sharer/sharer.php?u=https://x.no">Del</a>
<a href="https://twitter.com/intent/tweet?text=hei">Tweet</a>
<a href="https://www.instagram.com/sandneselektriske/?hl=nb">Instagram</a>
<a href="https://www.youtube.com/@sandneselektriske">YouTube</a>
<a href="https://www.linkedin.com/company/sandnes-elektriske">LinkedIn</a>
</footer></body></html>"""


def page():
    return extract_page(HTML, BASE)


def test_title_description_and_site_name():
    p = page()
    assert p["title"] == "Sandnes Elektriske – elektriker i Sandnes"
    assert p["description"] == "Vi utfører elektriske installasjoner."
    assert p["site_name"] == "Sandnes Elektriske"


def test_text_is_visible_text_only_with_collapsed_whitespace():
    text = page()["text"]
    assert "Vi er elektrikere." in text
    assert "secret" not in text
    assert "color:red" not in text


def test_json_ld_organization_is_found_inside_graph_with_identifiers():
    orgs = page()["organizations"]
    org = next(o for o in orgs if o["legal_name"] == "Sandnes Elektriske AS")
    assert org["name"] == "Sandnes Elektriske"
    assert org["vat_id"] == "NO 810 034 882 MVA"
    assert org["telephone"] == "+47 51 62 00 00"
    assert org["address"]["postal_code"] == "4307"
    assert "https://www.facebook.com/sandneselektriske" in org["same_as"]


def test_organization_ids_are_included_in_identity_text():
    # the org number lives in vatID, not visible text: identity checks must still see it
    assert "810 034 882" in page()["identity_text"]


def test_social_profiles_are_canonical_and_exclude_share_and_intent_links():
    socials = {(s["platform"], s["url"]) for s in page()["socials"]}
    assert ("facebook", "https://www.facebook.com/sandneselektriske") in socials
    assert ("instagram", "https://www.instagram.com/sandneselektriske") in socials
    assert ("youtube", "https://www.youtube.com/@sandneselektriske") in socials
    assert ("linkedin", "https://www.linkedin.com/company/sandnes-elektriske") in socials
    assert not any("sharer" in url or "intent" in url for _, url in socials)
    assert len([s for s in socials if s[0] == "facebook"]) == 1  # deduplicated across JSON-LD and links


def test_contact_emails_and_phones_from_links_and_json_ld():
    p = page()
    assert "kari@sandneselektriske.no" in p["emails"]
    assert "post@sandneselektriske.no" in p["emails"]
    assert "+4751620000" in p["phones"]


def test_priority_links_are_classified_and_resolved_to_absolute_urls():
    links = page()["priority_links"]
    assert "https://www.sandneselektriske.no/kontakt" in links["contact"]
    assert "https://www.sandneselektriske.no/om-oss/" in links["about"]
    assert "https://www.sandneselektriske.no/nyheter" in links["news"]
    assert "https://www.sandneselektriske.no/karriere" in links["careers"]
    assert "https://sandneselektriske.easycruit.com/" in links["careers"]  # external ATS is allowed


def test_job_postings_are_extracted_from_json_ld():
    jobs = page()["jobs"]
    assert len(jobs) == 1
    job = jobs[0]
    assert job["title"] == "Elektriker"
    assert job["date_posted"] == "2026-09-01"
    assert job["valid_through"] == "2026-10-15"
    assert job["url"] == "https://www.sandneselektriske.no/karriere/elektriker"
    assert job["location"] == "Sandnes"


def test_malformed_json_ld_and_empty_documents_do_not_raise():
    broken = '<html><script type="application/ld+json">{not json</script><body>hei</body></html>'
    p = extract_page(broken, BASE)
    assert p["organizations"] == [] and p["jobs"] == []
    empty = extract_page("", BASE)
    assert empty["title"] is None and empty["text"] == ""


def test_documents_whose_root_is_a_removable_element_do_not_raise():
    for html in ("<script>var a = 1;</script>", "<noscript>enable js</noscript>",
                 "<template><script>x</script><noscript>y</noscript></template>",
                 "<!-- only a comment -->", "<style>a{}</style>"):
        p = extract_page(html, BASE)
        assert p["text"] == "" or "var a" not in p["text"], html


def test_comments_outside_the_html_element_do_not_crash_extraction():
    # real-world trigger: top-level comments have no parent element, which made lxml's drop_tree assert
    for html in ("<!-- saved from url --><html><body>hi</body></html>",
                 "<!DOCTYPE html><!-- x --><html><body>hi</body></html>",
                 "<html><body>hi</body></html><!-- trailing -->"):
        assert extract_page(html, BASE)["text"] == "hi", html
