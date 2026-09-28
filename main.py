from __future__ import annotations
import asyncio, csv, io, json, os, uuid
from fastapi import FastAPI, HTTPException, Request, Form
from fastapi.responses import FileResponse, StreamingResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from dotenv import load_dotenv

load_dotenv()

from scraper.db import HistoryDB
from scraper.engine import CrawlEngine
from scraper.urltools import normalize_url
from scraper.auth import ProfileStore, parse_cookies
from scraper.enrichment import flatten
from scraper import ai, security

app = FastAPI(title="Scrapling Studio", version="1.1.0")
app.mount("/static", StaticFiles(directory="static"), name="static")
DB_PATH = os.getenv("DB_PATH", "./data/scrapling.db")
DB = HistoryDB(DB_PATH); JOBS = {}; TASKS = set()
PROFILES = ProfileStore(os.path.join(os.path.dirname(DB_PATH) or ".", "profiles"))
ENGINE = CrawlEngine(DB, JOBS, PROFILES)


def background(coro):
    task = asyncio.create_task(coro)
    TASKS.add(task); task.add_done_callback(TASKS.discard)


class CrawlRequest(BaseModel):
    url: str
    mode: str = Field(default="auto", pattern="^(auto|http|dynamic|stealth)$")
    max_pages: int = Field(default=20, ge=1, le=500)
    depth: int = Field(default=2, ge=0, le=5)
    concurrency: int = Field(default=4, ge=1, le=16)
    browser_pages: int = Field(default=3, ge=1, le=4)
    adaptive: bool = True
    network_idle: bool = False
    disable_resources: bool = True
    capture_xhr: bool = False
    ai_enrichment: bool = True
    enrich: bool = True
    escalate: bool = True
    respect_robots: bool = True
    delay_ms: int = Field(default=0, ge=0, le=60000)
    proxies: str = ""
    profile: str | None = None
    timeout: int = Field(default=30000, ge=5000, le=120000)


@app.middleware("http")
async def require_login(request: Request, call_next):
    open_paths = ("/login", "/static/app.css")
    if request.url.path in open_paths or security.is_authenticated(request.cookies.get(security.COOKIE)):
        return await call_next(request)
    if request.url.path.startswith("/api/"): return JSONResponse({"detail": "Not signed in"}, status_code=401)
    return RedirectResponse("/login", status_code=303)


@app.get("/login")
async def login_page(): return HTMLResponse(security.LOGIN_PAGE.format(error=""))


@app.post("/login")
async def login(request: Request, password: str = Form("")):
    if not security.check_password(password):
        await asyncio.sleep(1)  # slow down guessing
        return HTMLResponse(security.LOGIN_PAGE.format(error='<p class="err small">Wrong password.</p>'), status_code=401)
    r = RedirectResponse("/", status_code=303)
    r.set_cookie(security.COOKIE, security.session_token(), httponly=True, samesite="lax", max_age=30 * 86400,
                 secure=request.headers.get("x-forwarded-proto") == "https" or request.url.scheme == "https")
    return r


@app.get("/logout")
async def logout():
    r = RedirectResponse("/login", status_code=303); r.delete_cookie(security.COOKIE); return r


@app.get("/")
async def index(): return FileResponse("static/index.html")


@app.get("/api/status")
async def status():
    return {"ai": ai.configured(), "ai_model": os.getenv("AI_MODEL", "gpt-4.1-mini") if ai.configured() else None, "hunter": bool(os.getenv("HUNTER_API_KEY"))}


@app.post("/api/jobs")
async def create_job(req: CrawlRequest):
    url = normalize_url(req.url)
    if not url or not url.startswith(("http://", "https://")): raise HTTPException(400, "Invalid URL")
    if not await security.is_public_url(url): raise HTTPException(400, "Private, local and internal addresses cannot be crawled")
    if req.profile and not PROFILES.get(req.profile): raise HTTPException(400, f"Auth profile '{req.profile}' not found")
    job_id = uuid.uuid4().hex[:12]
    background(ENGINE.run(job_id, url, req.model_dump()))
    return {"job_id": job_id}


def result_for(job_id):
    r = JOBS.get(job_id) or DB.get(job_id)
    if not r: raise HTTPException(404, "Job not found")
    return r.jsonable() if hasattr(r, "jsonable") else r


@app.get("/api/jobs/{job_id}")
async def job(job_id: str): return result_for(job_id)


@app.post("/api/jobs/{job_id}/enrich")
async def enrich(job_id: str, ai_enrichment: bool = True):
    r = result_for(job_id)
    if r["status"] == "running": raise HTTPException(409, "Crawl still running")
    if (r.get("enrichment") or {}).get("status") == "running": raise HTTPException(409, "Enrichment already running")
    background(ENGINE.enrich(job_id, use_ai=ai_enrichment))
    return {"ok": True}


@app.get("/api/history")
async def history(): return DB.list()


# ---------------------------------------------------------------- auth profiles
class ProfileRequest(BaseModel):
    name: str
    domain: str = ""
    cookies: str = ""
    headers: str = ""
    useragent: str = ""


@app.get("/api/profiles")
async def profiles(): return PROFILES.list()


@app.post("/api/profiles")
async def save_profile(req: ProfileRequest):
    try:
        cookies = parse_cookies(req.cookies, req.domain) if req.cookies.strip() else None
        headers = None
        if req.headers.strip():
            headers = {k.strip(): v.strip() for k, _, v in (l.partition(":") for l in req.headers.splitlines()) if k.strip() and v.strip()}
        PROFILES.save(req.name, cookies=cookies, headers=headers, useragent=req.useragent or None, domain=req.domain or None)
    except (ValueError, json.JSONDecodeError) as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


@app.delete("/api/profiles/{name}")
async def delete_profile(name: str):
    try:
        await PROFILES.close_browser(name); PROFILES.delete(name)
    except ValueError as e: raise HTTPException(400, str(e))
    return {"ok": True}


class BrowserRequest(BaseModel):
    url: str


@app.post("/api/profiles/{name}/browser")
async def open_browser(name: str, req: BrowserRequest, request: Request):
    if security.is_remote(request.headers):
        raise HTTPException(403, "'Open browser' only works on the computer running the app (the window opens there). Remote users can import cookies instead.")
    url = normalize_url(req.url)
    if not url.startswith(("http://", "https://")): raise HTTPException(400, "Invalid URL")
    try: await PROFILES.open_browser(name, url)
    except ValueError as e: raise HTTPException(400, str(e))
    except Exception as e: raise HTTPException(500, f"Could not open browser: {e}")
    return {"ok": True}


@app.post("/api/profiles/{name}/browser/finish")
async def finish_browser(name: str):
    await PROFILES.close_browser(name)
    return {"ok": True, "profile": next((p for p in PROFILES.list() if p["name"] == name), None)}


# ---------------------------------------------------------------- exports
def export_row(r):
    row = {k: (v.get("value") if isinstance(v, dict) else v) for k, v in r["fields"].items()}
    row = {k: (None if v == "Not Found" else v) for k, v in row.items()}
    row.update(flatten(r.get("enrichment")))
    return {k: (json.dumps(v, ensure_ascii=False) if isinstance(v, dict) else ", ".join(map(str, v)) if isinstance(v, list) else v) for k, v in row.items()}


@app.get("/api/jobs/{job_id}/export/{kind}")
async def export(job_id: str, kind: str):
    r = result_for(job_id); row = export_row(r); e = r.get("enrichment") or {}
    if kind == "json":
        return StreamingResponse(io.BytesIO(json.dumps(r, ensure_ascii=False, indent=2).encode()), media_type="application/json", headers={"Content-Disposition": f'attachment; filename="{job_id}.json"'})
    if kind == "csv":
        b = io.StringIO(); w = csv.DictWriter(b, fieldnames=row.keys()); w.writeheader(); w.writerow(row)
        return StreamingResponse(iter([b.getvalue().encode("utf-8-sig")]), media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="{job_id}.csv"'})
    if kind == "xlsx":
        from openpyxl import Workbook
        from openpyxl.styles import Font
        wb = Workbook(); ws = wb.active; ws.title = "Company"
        for k, v in row.items(): ws.append([k.replace("_", " "), v])
        sheets = {
            "People": (["name", "title", "email", "email_guess", "email_guess_confidence", "source", "source_url"], e.get("people", [])),
            "Emails": (["email", "type", "on_company_domain", "domain_accepts_mail", "free_provider", "disposable"], (e.get("contacts") or {}).get("emails", [])),
            "Phones": (["raw", "e164", "international", "valid", "country", "location", "type"], (e.get("contacts") or {}).get("phones", [])),
            "Tech stack": (["name", "category", "pages", "evidence", "source_url"], e.get("tech_stack", [])),
            "Pages": (["url", "category", "status", "mode", "elapsed_ms", "challenge", "error"], r.get("pages", [])),
        }
        for title, (cols, items) in sheets.items():
            s = wb.create_sheet(title); s.append(cols)
            for it in items: s.append([json.dumps(it.get(c), ensure_ascii=False) if isinstance(it.get(c), (list, dict)) else it.get(c) for c in cols])
        for s in wb.worksheets:
            for cell in s[1]: cell.font = Font(bold=True)
            for col in s.columns: s.column_dimensions[col[0].column_letter].width = min(60, max(12, max(len(str(c.value or "")) for c in col) + 2))
        out = io.BytesIO(); wb.save(out); out.seek(0)
        return StreamingResponse(out, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition": f'attachment; filename="{job_id}.xlsx"'})
    raise HTTPException(400, "Unsupported export")


if __name__ == "__main__":
    import sys, uvicorn
    if sys.platform == "win32": asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())  # Playwright needs subprocess support
    uvicorn.run(app, host=os.getenv("HOST", "127.0.0.1"), port=int(os.getenv("PORT", "8000")), loop="asyncio")
