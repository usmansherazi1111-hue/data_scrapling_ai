import httpx, pytest, respx
from scraper import opencorporates as oc
from oc_fixtures import company, search, detail, OFFICERS

API = oc.API


@respx.mock
async def test_search_sends_token_and_normalises():
    route = respx.get(f"{API}/companies/search").mock(return_value=httpx.Response(200, json=search(company("ACME SOFTWARE LIMITED", "0123"))))
    res = await oc.search_companies("acme", jurisdiction_code="gb", inactive=False, per_page=500)
    q = route.calls.last.request.url.params
    assert q["api_token"] == "test-token" and q["q"] == "acme" and q["jurisdiction_code"] == "gb" and q["inactive"] == "false" and q["per_page"] == "100"
    c = res["companies"][0]
    assert c["name"] == "ACME SOFTWARE LIMITED" and c["registered_address"].startswith("1 HIGH STREET")
    assert c["industry_codes"] == ["62.01 Computer programming activities"] and c["previous_names"] == ["ACME SOFTWARE LIMITED OLD LTD"]
    assert c["opencorporates_url"] == "https://opencorporates.com/companies/gb/0123"


@respx.mock
async def test_results_are_cached_to_save_quota():
    route = respx.get(f"{API}/companies/search").mock(return_value=httpx.Response(200, json=search()))
    await oc.search_companies("acme"); await oc.search_companies("acme")
    assert route.call_count == 1


@pytest.mark.parametrize("status,text", [(401, "rejected the API token"), (403, "quota"), (503, "unavailable")])
@respx.mock
async def test_errors_are_explained_and_not_retried(status, text):
    route = respx.get(f"{API}/companies/search").mock(return_value=httpx.Response(status, json={"error": {"message": "nope"}}))
    with pytest.raises(oc.OpenCorporatesError) as e:
        await oc.search_companies("acme")
    assert text in str(e.value) and e.value.status == status and route.call_count == 1


async def test_no_token_means_no_request(monkeypatch):
    monkeypatch.delenv("OPENCORPORATES_API_TOKEN")
    with respx.mock(assert_all_called=False) as m:
        with pytest.raises(oc.OpenCorporatesError):
            await oc.search_companies("acme")
        assert not m.calls


@respx.mock
async def test_company_detail_with_officers_and_escaped_number():
    route = respx.get(f"{API}/companies/us_de/AB%2F12").mock(return_value=httpx.Response(200, json=detail(company("ACME INC", "AB/12", "us_de", officers=OFFICERS))))
    c = await oc.get_company("us_de", "AB/12")
    assert route.called and [o["current"] for o in c["officers"]] == [True, False]
    with pytest.raises(oc.OpenCorporatesError):
        await oc.get_company("../x", "1")


def test_name_similarity_ignores_legal_suffixes():
    assert oc.name_similarity("Arbisoft", "ARBISOFT (PRIVATE) LIMITED") == 1.0
    assert oc.name_similarity("Arbisoft", "Microsoft Corporation") < 0.6


@respx.mock
async def test_match_company_prefers_active_close_name_in_country():
    respx.get(f"{API}/companies/search").mock(return_value=httpx.Response(200, json=search(
        company("ACME SOFTWARE HOLDINGS LIMITED", "1"), company("ACME SOFTWARE LTD", "2", status="Dissolved", inactive=True), company("ACME SOFTWARE LIMITED", "3"))))
    respx.get(f"{API}/companies/gb/3").mock(return_value=httpx.Response(200, json=detail(company("ACME SOFTWARE LIMITED", "3", officers=OFFICERS))))
    m = await oc.match_company("Acme Software", "GB", "acmesoftware.co.uk")
    assert m["company_number"] == "3" and m["match_confidence"] >= 0.85 and m["attribution"] == "from OpenCorporates"


@respx.mock
async def test_match_company_rejects_weak_matches_without_detail_call():
    respx.get(f"{API}/companies/search").mock(return_value=httpx.Response(200, json=search(company("TOTALLY DIFFERENT GROUP PLC", "9"))))
    detail_route = respx.get(url__regex=rf"{API}/companies/gb/.*").mock(return_value=httpx.Response(200, json={}))
    assert await oc.match_company("Acme Software", "GB", "acme.co.uk") is None
    assert not detail_route.called
