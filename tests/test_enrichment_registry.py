import httpx, respx
from scraper import enrichment, opencorporates as oc
from oc_fixtures import company, search, detail, OFFICERS


def job():
    page = {"text": "We are Acme Software. Our team of 40 employees builds apps.", "emails": ["info@acme.co.uk"], "phones": [], "people": [["Alice Brown", "CEO"]],
            "category": "team", "tech": []}
    return {"seed_url": "https://acme.co.uk/", "fields": {"company_name": {"value": "Acme Software"}, "locations": {"value": ["London, UK"]}},
            "raw_pages": {"https://acme.co.uk/team": page}}


def offline(monkeypatch):
    async def dns(d): return {"mx": ["aspmx.l.google.com"], "accepts_email": True, "email_provider": "Google Workspace", "email_senders": [], "spf": None, "dmarc": None, "dmarc_policy": None, "a_records": [], "txt_verifications": []}
    async def none(*a, **k): return None
    monkeypatch.setattr(enrichment, "dns_lookup", dns); monkeypatch.setattr(enrichment, "rdap_lookup", none); monkeypatch.setattr(enrichment, "hunter_lookup", none)


@respx.mock
async def test_enrichment_adds_registry_record_officers_and_export_columns(monkeypatch):
    offline(monkeypatch)
    respx.get(f"{oc.API}/companies/search").mock(return_value=httpx.Response(200, json=search(company("ACME SOFTWARE LIMITED", "3"))))
    respx.get(f"{oc.API}/companies/gb/3").mock(return_value=httpx.Response(200, json=detail(company("ACME SOFTWARE LIMITED", "3", inc="2009-04-02", officers=OFFICERS))))
    p = await enrichment.enrich_job(job(), use_ai=False)
    assert p["registry"]["company_number"] == "3" and p["providers"]["opencorporates"] is True
    names = {x["name"]: x for x in p["people"]}
    assert "Alice Brown" in names and names["Jane Smith"]["source"] == "opencorporates" and "John Old" not in names  # resigned officer left out
    assert names["Jane Smith"]["email_guess"] == "jane.smith@acme.co.uk"
    assert p["firmographics"]["founded_year"] == 2009 and p["firmographics"]["incorporation_date"] == "2009-04-02"
    row = enrichment.flatten(p)
    assert row["registry_company_number"] == "3" and row["opencorporates_url"].endswith("/gb/3") and "Jane Smith (director)" in row["registry_officers"]


async def test_enrichment_unchanged_without_token(monkeypatch):
    offline(monkeypatch); monkeypatch.delenv("OPENCORPORATES_API_TOKEN")
    with respx.mock(assert_all_called=False) as m:
        p = await enrichment.enrich_job(job(), use_ai=False)
        assert not m.calls
    assert p["registry"] is None and [x["name"] for x in p["people"]] == ["Alice Brown"] and "registry_company_number" not in enrichment.flatten(p)
    assert p["lead_score"]["score"] > 0


@respx.mock
async def test_registry_switch_off_and_quota_error_do_not_break_enrichment(monkeypatch):
    offline(monkeypatch)
    route = respx.get(f"{oc.API}/companies/search").mock(return_value=httpx.Response(403, json={}))
    p = await enrichment.enrich_job(job(), use_ai=False, use_registry=False)
    assert p["registry"] is None and not route.called
    p = await enrichment.enrich_job(job(), use_ai=False)
    assert p["registry"] is None and "quota" in p["errors"]["opencorporates"]
