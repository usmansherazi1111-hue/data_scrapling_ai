"""Latency guards: local country cache for Overture, and per-page / per-site time caps in the crawl engine."""
import asyncio, pathlib, time
import pyarrow.parquet as pq

from scraper import overture, engine as engine_mod

SAMPLE = str(pathlib.Path(__file__).parent / "fixtures" / "places_sample.parquet")


def test_country_cache_keeps_only_that_country_and_gives_same_results(tmp_path):
    dest = overture.build_country_cache(SAMPLE, "PK", tmp_path / "PK.parquet")
    t = pq.read_table(dest)
    assert t.num_rows > 0 and not (tmp_path / "PK.part").exists()
    assert {a[0]["country"] for a in t.column("addresses").to_pylist() if a} == {"PK"}
    direct = overture.scan("textile", "pk", dataset_path=SAMPLE, release="test", limit=500)
    cached = overture.scan("textile", "pk", dataset_path=str(dest), release="test", limit=500)
    assert cached["matched"] == direct["matched"] and [c["name"] for c in cached["companies"]] == [c["name"] for c in direct["companies"]]


class _DB:
    def save(self, r): pass


class _Resp:
    status = 200; captured_xhr = []
    def get_all_text(self, **k): return "Welcome " * 100
    def css(self, q):
        class L(list):
            def get(self, d=""): return d
            def getall(self): return []
        return L()
    def markdown(self, **k): return ""


def test_slow_page_is_cut_off_and_site_budget_stops_crawl(monkeypatch):
    calls = []

    async def fake_get(url, **kw):
        calls.append(url)
        if url.endswith("/slow"): await asyncio.sleep(5)
        return _Resp()

    monkeypatch.setattr(engine_mod.AsyncFetcher, "get", staticmethod(fake_get))
    monkeypatch.setattr(engine_mod, "discover", lambda r, seed, n: [(seed.rstrip("/") + "/slow", "about", 0.9)])
    monkeypatch.setattr(engine_mod, "page_html", lambda r: "")
    monkeypatch.setattr(engine_mod, "extract_page", lambda r, u, c: {"title": "", "text": "x"})
    monkeypatch.setattr(engine_mod, "aggregate", lambda pages, seed: {})
    eng = engine_mod.CrawlEngine(_DB(), {}, {})
    cfg = {"max_pages": 5, "depth": 1, "mode": "http", "respect_robots": False, "enrich": False, "page_time_limit": 0.3, "time_budget": 10}
    t = time.perf_counter()
    res = asyncio.run(eng.run("j1", "https://example.com/", cfg))
    assert time.perf_counter() - t < 3
    assert res.status == "completed"
    assert any(p.error and "timed out" in p.error for p in res.pages)


def test_branches_sharing_a_website_become_one_company():
    assert overture.site_domain("https://www.Nishat.net/careers") == "nishat.net"
    assert overture.site_domain("http://facebook.com/somepage") is None and overture.site_domain("") is None
    r = overture.scan("textile", "pk", dataset_path=SAMPLE, release="test", limit=5000)
    doms = [overture.site_domain(c["website"]) for c in r["companies"] if overture.site_domain(c["website"])]
    assert len(doms) == len(set(doms))
    assert all(c["locations"] >= 1 for c in r["companies"])


def test_related_categories_widen_the_match():
    assert "information_technology" in overture.alternatives(overture.stem("software"))
    assert overture.alternatives("zzzqq") == ["zzzqq"]
