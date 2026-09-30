import httpx, pytest, respx
from scraper import gleif, companieshouse as ch, registries
from registry_fixtures import lei_page, ONEADVANCED, ARBISOFT, ch_item, ch_profile, CH_OFFICERS

G = "https://api.gleif.org/api/v1"
C = "https://api.company-information.service.gov.uk"


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    for api in (gleif.API, ch.API):
        monkeypatch.setattr(api, "min_interval", 0); api.cache.clear()
    monkeypatch.delenv("COMPANIES_HOUSE_API_KEY", raising=False)
    monkeypatch.delenv("OPENCORPORATES_API_TOKEN", raising=False)


# ---------------------------------------------------------------- GLEIF (no key)
@respx.mock
async def test_gleif_search_filters_and_row():
    route = respx.get(f"{G}/lei-records").mock(return_value=httpx.Response(200, json=lei_page(ONEADVANCED, total=336, last=12)))
    res = await gleif.search_companies("software", page=2, per_page=30, country_code="gb", jurisdiction_code="us_de", exclude_inactive=True, industry_codes="ignored")
    q = route.calls.last.request.url.params
    assert q["filter[fulltext]"] == "software" and q["filter[entity.legalAddress.country]"] == "GB" and q["filter[entity.jurisdiction]"] == "US-DE"
    assert q["filter[entity.status]"] == "ACTIVE" and q["page[number]"] == "2" and "api_token" not in q
    c = res["companies"][0]
    assert (c["name"], c["company_number"], c["lei"], c["jurisdiction_code"], c["incorporation_date"]) == ("ONEADVANCED LIMITED", "03214465", "213800SJAISA1Z1RM538", "gb", "1996-06-20")
    assert c["registered_address"].endswith("Birmingham, B1 1RF, GB") and c["previous_names"] == ["ADVANCED BUSINESS SOFTWARE AND SOLUTIONS LIMITED"]
    assert c["record_url"] == "https://search.gleif.org/#/record/213800SJAISA1Z1RM538" and res["total_count"] == 336 and res["total_pages"] == 12


@respx.mock
async def test_gleif_match_uses_trade_names_and_widens_country():
    route = respx.get(f"{G}/lei-records").mock(side_effect=[httpx.Response(200, json=lei_page()), httpx.Response(200, json=lei_page(ARBISOFT))])
    m = await gleif.match_company("Arbisoft", "PK", "arbisoft.com")
    assert m["name"] == "Arbisoft Incorporated" and m["match_confidence"] >= 0.85 and m["officers"] == []
    assert "filter[entity.legalAddress.country]" not in route.calls.last.request.url.params  # retried worldwide


@respx.mock
async def test_gleif_rate_limit_is_reported_not_retried():
    route = respx.get(f"{G}/lei-records").mock(return_value=httpx.Response(429))
    with pytest.raises(registries.RegistryError) as e:
        await gleif.search_companies("acme")
    assert e.value.status == 429 and "60 requests" in str(e.value) and route.call_count == 1


# ---------------------------------------------------------------- Companies House (free key)
@respx.mock
async def test_companies_house_advanced_search_uses_basic_auth_and_filters(monkeypatch):
    monkeypatch.setenv("COMPANIES_HOUSE_API_KEY", "ch-key")
    route = respx.get(f"{C}/advanced-search/companies").mock(return_value=httpx.Response(200, json={"items": [ch_item("ACME SOFTWARE LTD", "07654321")], "hits": 45}))
    res = await ch.search_companies("software", page=2, per_page=20, industry_codes="62012, 62020", registered_address="London", incorporated_from="2015-01-01", exclude_inactive=True)
    req = route.calls.last.request; q = req.url.params
    assert req.headers["authorization"] == httpx.BasicAuth("ch-key", "")._auth_header
    assert (q["company_name_includes"], q["company_status"], q["sic_codes"], q["location"], q["incorporated_from"], q["size"], q["start_index"]) == ("software", "active", "62012,62020", "London", "2015-01-01", "20", "20")
    c = res["companies"][0]
    assert c["name"] == "ACME SOFTWARE LTD" and c["current_status"] == "Active" and c["industry_codes"] == ["62012"] and res["total_pages"] == 3
    assert c["record_url"].endswith("/company/07654321")


@respx.mock
async def test_companies_house_no_results_404_is_empty(monkeypatch):
    monkeypatch.setenv("COMPANIES_HOUSE_API_KEY", "k")
    respx.get(f"{C}/advanced-search/companies").mock(return_value=httpx.Response(404))
    assert (await ch.search_companies("zzz"))["companies"] == []


@respx.mock
async def test_companies_house_match_with_officers(monkeypatch):
    monkeypatch.setenv("COMPANIES_HOUSE_API_KEY", "k")
    respx.get(f"{C}/search/companies").mock(return_value=httpx.Response(200, json={"items": [ch_item("ACME SOFTWARE HOLDINGS LTD", "00000001"), ch_item("ACME SOFTWARE LTD", "07654321")]}))
    respx.get(f"{C}/company/07654321").mock(return_value=httpx.Response(200, json=ch_profile("ACME SOFTWARE LTD", "07654321")))
    respx.get(f"{C}/company/07654321/officers").mock(return_value=httpx.Response(200, json=CH_OFFICERS))
    m = await ch.match_company("Acme Software", "GB", "acmesoftware.co.uk")
    assert m["company_number"] == "07654321" and m["previous_names"] == ["ACME SOFT LTD"]
    assert [(o["name"], o["current"]) for o in m["officers"]] == [("Jane Smith", True), ("John Old", False)]
    assert await ch.match_company("Acme", "DE") is None  # UK register only


async def test_companies_house_needs_key():
    with respx.mock(assert_all_called=False) as m:
        with pytest.raises(registries.RegistryError) as e:
            await ch.search_companies("acme")
        assert e.value.status == 401 and not m.calls


# ---------------------------------------------------------------- source selection
def test_default_and_match_order(monkeypatch):
    assert registries.default_source() == "gleif" and registries.match_order("gb") == ["gleif"]
    monkeypatch.setenv("COMPANIES_HOUSE_API_KEY", "k")
    assert registries.default_source() == "companieshouse"
    assert registries.match_order("gb") == ["companieshouse", "gleif"] and registries.match_order("pk") == ["gleif"]


@respx.mock
async def test_match_falls_back_to_gleif_when_companies_house_fails(monkeypatch):
    monkeypatch.setenv("COMPANIES_HOUSE_API_KEY", "bad")
    respx.get(f"{C}/search/companies").mock(return_value=httpx.Response(401))
    respx.get(f"{G}/lei-records").mock(return_value=httpx.Response(200, json=lei_page(ONEADVANCED)))
    m, errors = await registries.match("OneAdvanced", "GB", "oneadvanced.com")
    assert m["source"] == "gleif" and m["attribution"].startswith("Source: GLEIF") and "401" in errors["companieshouse"]
