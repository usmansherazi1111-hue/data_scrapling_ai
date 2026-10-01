"""Relevance of listed companies, category chips, and the fill-the-gaps sources (OpenStreetMap by name, alternate address, official register)."""
import asyncio, importlib, io, pathlib, time
import httpx, pytest, respx
from fastapi.testclient import TestClient
from openpyxl import load_workbook

from scraper import overture, gapfill, db as db_mod

SAMPLE = str(pathlib.Path(__file__).parent / "fixtures" / "places_sample.parquet")


def rec(primary, hierarchy, name="X Ltd", alternates=None):
    return {"names": {"primary": name}, "taxonomy": {"primary": primary, "hierarchy": hierarchy, "alternates": alternates}}


def test_relevance_tiers_rank_own_category_over_parent_and_name_only():
    p = overture.parse_industry("textile")
    assert overture.relevance(rec("textile_manufacturer", ["manufacturing", "textile_manufacturer"]), p)[0] == 3
    assert overture.relevance(rec("apparel_manufacturer", ["manufacturing", "apparel_manufacturer"]), p)[0] == 2   # related category
    assert overture.relevance(rec("clothing_store", ["shopping", "clothing_store"], name="Gul Textile Mart"), p) == (0, "name only")
    m = overture.parse_industry("medicine")
    # a broad top-level "health_and_medical" is not the place's own category
    assert overture.relevance(rec("physical_therapy", ["health_and_medical", "physical_therapy"]), m)[0] == 0
    assert overture.relevance(rec("naturopathic_medicine", ["health_and_medical", "naturopathic_medicine"]), m)[0] == 3
    ph = overture.parse_industry("pharmaceutical")
    assert overture.relevance(rec("pharmaceutical_company", ["x", "pharmaceutical_company"]), ph)[0] == 3
    assert overture.relevance(rec("pharmacy", ["shopping", "pharmacy"]), ph)[0] == 2        # same stem only: listed after the real ones
    assert overture.relevance(rec("software_development", ["b2b", "software_development"]), overture.parse_industry("software development"))[0] == 3


def test_scan_ranks_by_relevance_and_reports_why_and_category_chips():
    r = overture.scan("textile", "pk", dataset_path=SAMPLE, release="test", limit=1000)
    cs = r["companies"]
    assert r["relevant"] >= 1 and r["facets"] and r["facets"][0]["category"] == "textile manufacturer"
    assert all("match" in c for c in cs)
    why = [c["match"] for c in cs]
    assert why == sorted(why, key=lambda w: ["category", "parent category", "related category", "related parent category", "name only"].index(w))   # best first
    only = overture.scan("textile", "pk", category="textile manufacturer", dataset_path=SAMPLE, release="test", limit=1000)
    assert only["companies"] and {c["category"] for c in only["companies"]} == {"textile manufacturer"}


def test_sibling_listing_fills_missing_contact_and_records_source():
    rows = [{"name": "Alpha Textiles Ltd", "website": "https://alpha.pk", "phone": None, "email": "a@alpha.pk"},
            {"name": "Alpha Textiles", "website": None, "phone": "+9212345", "email": None}]
    overture.fill_from_siblings(rows)
    assert rows[1]["website"] == "https://alpha.pk" and rows[1]["email"] == "a@alpha.pk" and rows[1]["filled_from"]["website"] == "sibling listing"
    assert rows[0]["phone"] == "+9212345"


NOM = [{"name": "VentureDive", "extratags": {"website": "https://venturedive.com/", "phone": "+92 42 111"}},
       {"name": "Venture Dive Cafe", "extratags": {"website": "https://cafe.example/"}}]


@pytest.mark.asyncio
async def test_openstreetmap_lookup_needs_an_exact_company_name(monkeypatch):
    monkeypatch.setenv("GAPFILL_NOMINATIM", "1")
    with respx.mock(assert_all_mocked=True) as m:
        route = m.get(gapfill.NOMINATIM).mock(return_value=httpx.Response(200, json=NOM))
        c = {"name": "VentureDive Pvt Ltd", "website": None, "phone": None, "email": None}
        await gapfill.discover_listing(c, "pk")
        assert c["website"] == "https://venturedive.com/" and c["phone"] == "+92 42 111" and c["filled_from"]["website"] == "OpenStreetMap"
        assert route.calls[0].request.headers["user-agent"] and "countrycodes=pk" in str(route.calls[0].request.url)
        other = {"name": "Unrelated Mills", "website": None}
        await gapfill.discover_listing(other, "pk")
        assert not other.get("website")                      # the cafe with a similar name must not be taken
        await gapfill.discover_listing({"name": "VentureDive", "website": None}, "pk")
        assert route.call_count == 2                         # second VentureDive lookup came from the cache


@pytest.mark.asyncio
async def test_lookup_failure_is_harmless(monkeypatch):
    monkeypatch.setenv("GAPFILL_NOMINATIM", "1")
    with respx.mock() as m:
        m.get(gapfill.NOMINATIM).mock(side_effect=httpx.ConnectError("blocked"))
        c = {"name": "Some Company", "website": None}
        await gapfill.discover_listing(c, "pk")
        assert not c.get("website")


def test_alt_urls_try_www_and_https_without_repeating_the_original():
    assert gapfill.alt_urls("http://www.acme.pk") == ["https://www.acme.pk", "https://acme.pk", "http://acme.pk"]
    assert "http://www.acme.pk" not in gapfill.alt_urls("http://www.acme.pk")
    assert gapfill.alt_urls("") == []


def test_merge_best_prefers_website_then_listing_and_lists_what_is_missing():
    c = {"name": "A", "website": "https://a.pk", "phone": "+9211", "email": "listing@a.pk", "socials": ["https://www.facebook.com/a"],
         "profile": {"emails": [{"email": "info@a.pk", "on_company_domain": True}], "phones": [], "people": [], "social": {}}}
    gapfill.merge_best(c)
    assert c["best"]["email"] == "info@a.pk" and c["best_source"]["email"] == "website"
    assert c["best"]["phone"] == "+9211" and c["best_source"]["phone"] == "listing"
    assert c["best"]["social_facebook"] == "https://www.facebook.com/a" and c["missing"] == ["contact_person"]
    d = {"name": "B"}; gapfill.merge_best(d)
    assert d["missing"] == ["website", "email", "phone", "contact_person"]


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "t.db")); monkeypatch.setenv("APP_PASSWORD", "")
    import main; importlib.reload(main)
    orig = overture.scan
    monkeypatch.setattr(overture, "scan", lambda *a, **k: orig(*a, dataset_path=SAMPLE, release="test", **{x: y for x, y in k.items() if x != "release"}))
    with TestClient(main.app) as c:
        yield c


def wait(c, rid):
    for _ in range(300):
        r = c.get(f"/api/companies/{rid}").json()
        if r["status"] != "running" and r["crawl"]["status"] != "running": return r
        time.sleep(0.05)
    raise AssertionError("timed out")


def test_pipeline_fills_gaps_tries_alternate_address_and_exports_sources(client, monkeypatch):
    import main
    tried = []

    class Res:
        def __init__(self, url, ok): self.url, self.ok = url, ok
        def jsonable(self):
            if not self.ok: return {"status": "completed", "stats": {}, "pages": [{"url": self.url, "status": 403}], "enrichment": None}
            return {"status": "completed", "stats": {}, "pages": [{"url": self.url, "status": 200}], "enrichment": {"status": "done", "lead_score": {"score": 60, "grade": "B"},
                    "contacts": {"emails": [{"email": "hello@found.pk", "on_company_domain": True}], "phones": []}, "people": [], "social": {}}}

    async def run(job_id, url, cfg):
        tried.append(url); return Res(url, url.startswith("https://"))   # plain http is refused, https works

    async def public(url): return True
    async def resolves(url): return True
    async def no_registry(c, cc): c["registry"] = {"name": "Registered Name Ltd", "lei": "LEI123", "current_status": "ACTIVE", "registered_address": "1 Mall Road", "source": "gleif"}
    monkeypatch.setattr(main.ENGINE, "run", run); monkeypatch.setattr(main.security, "is_public_url", public)
    monkeypatch.setattr(main, "domain_resolves", resolves); monkeypatch.setattr(main.gapfill, "registry_fill", no_registry)
    rid = client.post("/api/companies/search", json={"industry": "textile", "country": "pk", "limit": 5}).json()["id"]
    r = wait(client, rid)
    assert r["facets"] and len(r["companies"]) == 5 and r["crawl"]["done"] == 5
    with_site = [c for c in r["companies"] if c.get("website")]
    assert with_site and all(c["crawl_status"] == "completed" for c in with_site)
    assert any(u.startswith("https://") for u in tried)          # the https alternate was tried after http failed
    c0 = with_site[0]
    assert c0["best"]["email"] == "hello@found.pk" and c0["best_source"]["email"] == "website" and c0["registry"]["lei"] == "LEI123"
    wb = load_workbook(io.BytesIO(client.get(f"/api/companies/{rid}/export/xlsx").content))
    header = [c.value for c in wb["Summary"][1]]
    assert {"match", "email_source", "legal_name", "lei", "missing"} <= set(header)
    assert client.get(f"/api/companies/{rid}/export/csv").status_code == 200


def test_category_filter_through_the_api_and_stale_runs_are_closed(client, tmp_path):
    rid = client.post("/api/companies/search", json={"industry": "textile", "country": "pk", "limit": 5, "crawl": False, "category": "textile manufacturer"}).json()["id"]
    r = wait(client, rid)
    assert r["companies"] and {c["category"] for c in r["companies"]} == {"textile manufacturer"}
    d = db_mod.HistoryDB(str(tmp_path / "s.db"))
    d.save_registry({"id": "stale1", "created_at": "2026-01-01", "kind": "companies", "label": "x", "status": "running", "companies": [{"name": "A"}], "crawl": {"status": "running"}})
    assert d.fail_stale() == 1
    got = d.get_registry("stale1")
    assert got["status"] == "completed" and got["crawl"]["status"] == "interrupted" and "interrupted" in got["progress"]


def test_old_release_cache_is_pruned(tmp_path):
    old = tmp_path / "2026-08-19.0"; old.mkdir(); (old / "PK.parquet").write_bytes(b"x")
    new = tmp_path / "2026-09-23.0"; new.mkdir()
    overture.prune_old_releases(new / "PK.parquet")
    assert not old.exists() and new.exists()


@pytest.mark.asyncio
async def test_register_lookup_is_off_unless_enabled(monkeypatch):
    from scraper import registries
    calls = []
    async def fake_match(name, cc, *a, **k): calls.append(name); return {"name": "Reg Ltd", "lei": "L1", "source": "gleif"}, {}
    monkeypatch.setattr(registries, "match", fake_match)
    c = {"name": "Acme Ltd"}
    await gapfill.registry_fill(c, "pk")
    assert not calls and "registry" not in c
    monkeypatch.setenv("GAPFILL_REGISTRY", "1")
    await gapfill.registry_fill(c, "pk")
    assert calls == ["Acme Ltd"] and c["registry"]["lei"] == "L1"
