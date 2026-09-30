"""OpenCorporates company-registry data through the official API (https://api.opencorporates.com).

OpenCorporates' terms forbid scraping the website, so everything here goes through the REST API
with your own token (OPENCORPORATES_API_TOKEN). Free share-alike keys have small quotas (the docs
list 50 calls/day, 200/month by default); commercial use needs a paid key. A 401/403 (403 is also
what the API returns when you are rate limited) stops the current operation instead of retrying.

Data is under the Open Database Licence: keep the "from OpenCorporates" link (opencorporates_url)
next to anything you show or export.
"""
from __future__ import annotations
import asyncio, os, re, time
from difflib import SequenceMatcher
from urllib.parse import quote

import httpx
from .net import shared_ssl

API = "https://api.opencorporates.com/v0.4"
ID = "opencorporates"
LABEL = "OpenCorporates"
ATTRIBUTION = "from OpenCorporates"
needs_key = True


class OpenCorporatesError(RuntimeError):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message); self.status = status


def token() -> str:
    return os.getenv("OPENCORPORATES_API_TOKEN", "").strip()


def configured() -> bool:
    return bool(token())


# ---------------------------------------------------------------- HTTP
_lock = asyncio.Lock()
_last_call = 0.0
_cache: dict[tuple, tuple[float, dict]] = {}
CACHE_TTL = 6 * 3600


def _min_interval() -> float:
    try: return max(0.0, float(os.getenv("OPENCORPORATES_MIN_INTERVAL", "1.0")))
    except ValueError: return 1.0


async def _get(path: str, params: dict | None = None, transport: httpx.AsyncBaseTransport | None = None) -> dict:
    """One API call: token in the query string (as the API requires), cached, spaced out, no retries."""
    tok = token()
    if not tok: raise OpenCorporatesError("OPENCORPORATES_API_TOKEN is not configured", 401)
    params = {k: v for k, v in (params or {}).items() if v not in (None, "")}
    key = (path, tuple(sorted((k, str(v)) for k, v in params.items())))
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < CACHE_TTL: return hit[1]
    global _last_call
    async with _lock:  # one request at a time, at least OPENCORPORATES_MIN_INTERVAL apart
        wait = _last_call + _min_interval() - time.monotonic()
        if wait > 0: await asyncio.sleep(wait)
        try:
            async with httpx.AsyncClient(verify=shared_ssl(), timeout=30, transport=transport) as c:
                r = await c.get(f"{API}{path}", params={**params, "api_token": tok}, headers={"Accept": "application/json", "User-Agent": "ScraplingStudio/1.2"})
        finally:
            _last_call = time.monotonic()
    if r.status_code == 200:
        data = r.json(); _cache[key] = (time.time(), data); return data
    try: detail = (r.json().get("error") or {}).get("message") or r.text[:200]
    except Exception: detail = r.text[:200]
    msg = {401: "OpenCorporates rejected the API token (401). Check OPENCORPORATES_API_TOKEN.",
           403: "OpenCorporates refused the request (403): usually the daily/monthly quota of your API plan is used up.",
           404: "Not found on OpenCorporates (404).",
           503: "OpenCorporates is unavailable or the record is temporarily redacted (503)."}.get(r.status_code, f"OpenCorporates error {r.status_code}")
    raise OpenCorporatesError(f"{msg} {detail}".strip() if r.status_code not in (401, 404) else msg, r.status_code)


def clear_cache():
    _cache.clear()


# ---------------------------------------------------------------- normalisers
def _address(c: dict) -> str | None:
    if c.get("registered_address_in_full"): return c["registered_address_in_full"]
    a = c.get("registered_address") or {}
    parts = [a.get(k) for k in ("street_address", "locality", "region", "postal_code", "country")]
    return ", ".join(p for p in parts if p) or None


def company_row(c: dict) -> dict:
    """Flat company record (search result or full company)."""
    codes = [ic.get("industry_code", ic) for ic in c.get("industry_codes") or []]
    return {
        "name": c.get("name"), "company_number": c.get("company_number"), "jurisdiction_code": c.get("jurisdiction_code"),
        "company_type": c.get("company_type"), "current_status": c.get("current_status"), "inactive": c.get("inactive"),
        "incorporation_date": c.get("incorporation_date"), "dissolution_date": c.get("dissolution_date"),
        "registered_address": _address(c), "branch_status": c.get("branch_status"),
        "industry_codes": [f"{x.get('code')} {x.get('description') or ''}".strip() for x in codes if isinstance(x, dict)],
        "previous_names": [p.get("company_name") for p in c.get("previous_names") or [] if p.get("company_name")],
        "registry_url": c.get("registry_url"), "opencorporates_url": c.get("opencorporates_url"),
        "source_publisher": (c.get("source") or {}).get("publisher"), "retrieved_at": c.get("retrieved_at"),
    }


def display_name(name: str) -> str:
    """Registers often file names as "SMITH, JANE" or "JANE SMITH"; show them as "Jane Smith"."""
    name = (name or "").strip()
    if "," in name:
        last, _, first = name.partition(","); name = f"{first.strip()} {last.strip()}"
    return " ".join(w.capitalize() if w.isupper() or w.islower() else w for w in name.split())


def officer_row(o: dict) -> dict:
    o = o.get("officer", o)
    comp = o.get("company") or {}
    return {"name": display_name(o.get("name")), "name_as_filed": (o.get("name") or "").strip(), "position": o.get("position"), "start_date": o.get("start_date"), "end_date": o.get("end_date"),
            "current": not o.get("end_date") and not o.get("inactive"), "nationality": o.get("nationality"), "occupation": o.get("occupation"),
            "company_name": comp.get("name"), "company_number": comp.get("company_number"), "jurisdiction_code": comp.get("jurisdiction_code"),
            "opencorporates_url": o.get("opencorporates_url")}


# ---------------------------------------------------------------- API calls
SEARCH_FILTERS = ("jurisdiction_code", "country_code", "current_status", "company_type", "industry_codes", "registered_address", "incorporation_date", "inactive", "branch", "nonprofit", "order")


async def search_companies(q: str = "", page: int = 1, per_page: int = 30, transport=None, **filters) -> dict:
    # Common registry filters (see registries.py) mapped to OpenCorporates' own parameters.
    if filters.get("incorporated_from") or filters.get("incorporated_to"):
        filters.setdefault("incorporation_date", f"{filters.get('incorporated_from') or ''}:{filters.get('incorporated_to') or ''}")
    if filters.get("exclude_inactive"): filters.setdefault("inactive", False)
    params = {"q": q.strip() or None, "page": page, "per_page": max(1, min(100, per_page))}
    for k in SEARCH_FILTERS:
        v = filters.get(k)
        if isinstance(v, bool): v = str(v).lower()
        if v not in (None, ""): params[k] = v
    d = (await _get("/companies/search", params, transport)).get("results", {})
    return {"companies": [company_row(x.get("company", x)) for x in d.get("companies", [])], "page": d.get("page", page),
            "per_page": d.get("per_page", per_page), "total_count": d.get("total_count", 0), "total_pages": d.get("total_pages", 0)}


async def get_company(jurisdiction_code: str, company_number: str, transport=None) -> dict:
    if not re.fullmatch(r"[a-z]{2}(_[a-z0-9]{1,3})?", jurisdiction_code or ""): raise OpenCorporatesError("Invalid jurisdiction code", 400)
    c = (await _get(f"/companies/{jurisdiction_code}/{quote(str(company_number), safe='')}", None, transport)).get("results", {}).get("company", {})
    row = company_row(c)
    row["officers"] = [officer_row(o) for o in c.get("officers") or []]
    ctrl = c.get("ultimate_controlling_company") or c.get("controlling_entity") or {}
    row["controlling_company"] = {k: ctrl.get(k) for k in ("name", "jurisdiction_code", "company_number", "opencorporates_url")} if ctrl else None
    return row


async def search_officers(q: str, page: int = 1, per_page: int = 30, transport=None, **filters) -> dict:
    params = {"q": q, "page": page, "per_page": max(1, min(100, per_page)), **{k: filters.get(k) for k in ("jurisdiction_code", "position", "inactive") if filters.get(k) not in (None, "")}}
    d = (await _get("/officers/search", params, transport)).get("results", {})
    return {"officers": [officer_row(x) for x in d.get("officers", [])], "page": d.get("page", page), "total_count": d.get("total_count", 0), "total_pages": d.get("total_pages", 0)}


async def account_status(transport=None) -> dict:
    return (await _get("/account_status", None, transport)).get("results", {}).get("account_status", {})


# ---------------------------------------------------------------- matching a crawled company to its registry record
LEGAL_SUFFIX = re.compile(r"\b(private|pvt|limited|ltd|llc|l\.l\.c|inc|incorporated|corp|corporation|co|company|plc|gmbh|ag|sa|s\.a|bv|b\.v|nv|pty|pte|llp|lp|smc|fze|fzco|fz-llc)\b\.?", re.I)


def _norm(name: str) -> str:
    name = re.sub(r"[^\w\s]", " ", LEGAL_SUFFIX.sub(" ", (name or "").lower()))
    return re.sub(r"\s+", " ", name).strip()


def name_similarity(a: str, b: str) -> float:
    na, nb = _norm(a), _norm(b)
    if not na or not nb: return 0.0
    if na == nb: return 1.0
    return round(SequenceMatcher(None, na, nb).ratio(), 3)


async def match_company(name: str, country_code: str | None = None, domain: str | None = None, transport=None) -> dict | None:
    """Best registry match for a crawled company, with its officers.
    Quota-aware: at most 3 searches (name in the site's country, name anywhere, domain label) and 1 detail call."""
    queries = []
    if name and len(_norm(name)) >= 2:
        if country_code: queries.append((name, {"country_code": country_code.lower()}))
        queries.append((name, {}))
    label = (domain or "").split(".")[0]
    if label and len(_norm(label)) >= 3 and _norm(label) != _norm(name or ""): queries.append((label, {"country_code": country_code.lower()} if country_code else {}))
    best, best_score = None, 0.0
    for q, extra in queries:
        res = await search_companies(q, per_page=10, order="score", transport=transport, **extra)
        for c in res["companies"]:
            s = name_similarity(q, c["name"] or "")
            if c.get("inactive") or re.search(r"dissolved|struck|removed|liquidat|inactive", c.get("current_status") or "", re.I): s -= 0.15
            if country_code and (c.get("jurisdiction_code") or "").startswith(country_code.lower()): s += 0.05
            if s > best_score: best, best_score = c, s
        if best_score >= 0.85: break
    if not best or best_score < 0.6: return None
    detail = await get_company(best["jurisdiction_code"], best["company_number"], transport=transport)
    detail["match_confidence"] = round(min(1.0, best_score), 2)
    detail["matched_on"] = name or label
    detail["attribution"] = ATTRIBUTION
    return detail
