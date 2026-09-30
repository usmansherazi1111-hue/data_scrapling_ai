"""End-to-end crawl of a local test site with the existing engine (HTTP mode), registry lookup mocked."""
import asyncio, threading, http.server, functools
import httpx, respx
from scraper.engine import CrawlEngine
from scraper.db import HistoryDB
from scraper.auth import ProfileStore
from scraper import enrichment, opencorporates as oc
from oc_fixtures import company, search, detail, OFFICERS

HOME = """<html><head><title>Acme Software</title><meta name="description" content="Acme Software builds custom business software for enterprises."></head>
<body><h1>Acme Software</h1><p>Founded in 2009, Acme Software is a team of 120 employees delivering web and mobile products for clients worldwide.
We design, build and support software for banks, retailers and logistics firms across Europe.</p>
<a href="/team">Team</a> <a href="/contact">Contact</a><p>Email info@acme.test</p></body></html>"""
TEAM = """<html><head><title>Our team | Acme Software</title></head><body><h1>Leadership team</h1>
<div><h3>Alice Brown</h3><p>Chief Executive Officer</p></div><div><h3>Bob Green</h3><p>CTO</p></div>
<p>Our leadership team has decades of experience building software products for global customers and partners.</p></body></html>"""
CONTACT = """<html><head><title>Contact | Acme Software</title></head><body><h1>Contact us</h1><p>Call +44 20 7946 0958 or write to sales@acme.test.
Office: 1 High Street, London SW1Y 4PD, United Kingdom. We reply to every message within one business day.</p></body></html>"""


class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = {"/": HOME, "/team": TEAM, "/contact": CONTACT}.get(self.path)
        self.send_response(200 if body else 404); self.send_header("Content-Type", "text/html"); self.end_headers()
        self.wfile.write((body or "not found").encode())
    def log_message(self, *a): pass


async def test_crawl_extract_enrich_with_registry(tmp_path, monkeypatch):
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H); threading.Thread(target=srv.serve_forever, daemon=True).start()
    async def none(*a, **k): return None
    async def dns(d): return {"mx": [], "accepts_email": False, "email_provider": None, "email_senders": [], "spf": None, "dmarc": None, "dmarc_policy": None, "a_records": [], "txt_verifications": []}
    monkeypatch.setattr(enrichment, "dns_lookup", dns); monkeypatch.setattr(enrichment, "rdap_lookup", none)
    db = HistoryDB(tmp_path / "t.db"); jobs = {}
    engine = CrawlEngine(db, jobs, ProfileStore(str(tmp_path / "profiles")))
    try:
        with respx.mock(assert_all_mocked=False) as m:
            m.get(f"{oc.API}/companies/search").mock(return_value=httpx.Response(200, json=search(company("ACME SOFTWARE LIMITED", "3"))))
            m.get(f"{oc.API}/companies/gb/3").mock(return_value=httpx.Response(200, json=detail(company("ACME SOFTWARE LIMITED", "3", officers=OFFICERS))))
            r = await engine.run("job1", f"http://127.0.0.1:{srv.server_port}/", {"mode": "http", "max_pages": 5, "depth": 1, "respect_robots": False, "ai_enrichment": False})
    finally:
        srv.shutdown()
    d = r.jsonable()
    assert d["status"] == "completed" and d["stats"]["pages_succeeded"] == 3
    assert "Acme" in str(d["fields"]["company_name"]["value"])
    e = d["enrichment"]
    assert e["status"] == "done" and e["registry"]["name"] == "ACME SOFTWARE LIMITED"
    assert {"Alice Brown", "Jane Smith"} <= {p["name"] for p in e["people"]}
    assert db.get("job1")["enrichment"]["registry"]["company_number"] == "3"
