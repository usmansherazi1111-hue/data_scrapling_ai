"""UK Companies House public data API (https://developer.company-information.service.gov.uk).

Free: register, create an application and a REST API key, put it in COMPANIES_HOUSE_API_KEY.
Limit: 600 requests per 5 minutes per key (429 after that); calls here are spaced 0.6 s apart.
Data is Crown copyright, reusable under the Open Government Licence. Covers every UK company,
with status, SIC codes, incorporation date, registered office and officers (directors).
"""
from __future__ import annotations
import os, re

from .registries import JsonApi, RegistryError
from .opencorporates import name_similarity, display_name

ID = "companieshouse"
LABEL = "Companies House"
ATTRIBUTION = "Source: Companies House (Open Government Licence)"
needs_key = True
RECORD = "https://find-and-update.company-information.service.gov.uk/company/{}"

API = JsonApi("https://api.company-information.service.gov.uk", float(os.getenv("COMPANIES_HOUSE_MIN_INTERVAL", "0.6")),
              {400: "Companies House rejected the search (check the filters).",
               401: "Companies House rejected the API key (401). Check COMPANIES_HOUSE_API_KEY (use a REST key).",
               404: "Not found at Companies House.",
               429: "Companies House rate limit reached (600 requests per 5 minutes). Wait a few minutes and try again.",
               503: "Companies House API is unavailable right now."})


def key() -> str:
    return os.getenv("COMPANIES_HOUSE_API_KEY", "").strip()


def configured() -> bool:
    return bool(key())


async def _get(path, params=None, transport=None):
    if not configured(): raise RegistryError("COMPANIES_HOUSE_API_KEY is not configured", 401)
    return await API.get(path, params, auth=(key(), ""), transport=transport)  # key as the Basic-auth username


def _addr(a: dict | None) -> str | None:
    if not a: return None
    parts = [a.get(k) for k in ("care_of", "premises", "address_line_1", "address_line_2", "locality", "region", "postal_code", "country")]
    return ", ".join(p for p in parts if p) or None


def row(c: dict) -> dict:
    number = c.get("company_number")
    status = c.get("company_status")
    return {
        "name": c.get("company_name") or c.get("title"), "company_number": number, "jurisdiction_code": "gb",
        "company_type": c.get("company_type") or c.get("type"), "current_status": status.replace("-", " ").title() if status else None,
        "inactive": bool(status) and status not in ("active", "open"),
        "incorporation_date": c.get("date_of_creation"), "dissolution_date": c.get("date_of_cessation"),
        "registered_address": _addr(c.get("registered_office_address") or c.get("address")) or c.get("address_snippet"),
        "industry_codes": [str(x) for x in c.get("sic_codes") or []],
        "previous_names": [p.get("name") for p in c.get("previous_company_names") or [] if p.get("name")],
        "registry_url": RECORD.format(number), "record_url": RECORD.format(number), "source_publisher": "Companies House",
    }


def officer_row(o: dict, number: str) -> dict:
    link = ((o.get("links") or {}).get("officer") or {}).get("appointments")
    return {"name": display_name(o.get("name")), "name_as_filed": o.get("name"), "position": (o.get("officer_role") or "").replace("-", " "),
            "start_date": o.get("appointed_on"), "end_date": o.get("resigned_on"), "current": not o.get("resigned_on"),
            "nationality": o.get("nationality"), "occupation": o.get("occupation"), "company_number": number, "jurisdiction_code": "gb",
            "opencorporates_url": None, "record_url": f"https://find-and-update.company-information.service.gov.uk{link}" if link else RECORD.format(number) + "/officers"}


async def search_companies(q: str = "", page: int = 1, per_page: int = 30, transport=None, current_status: str = "", company_type: str = "", industry_codes: str = "",
                           registered_address: str = "", incorporated_from: str = "", incorporated_to: str = "", exclude_inactive: bool = False, **_ignored) -> dict:
    """Advanced search: name, status, SIC codes, location, company type and incorporation dates."""
    per_page = max(1, min(100, per_page))
    status = current_status.strip().lower().replace(" ", "-") or ("active" if exclude_inactive else "")
    params = {"company_name_includes": q.strip() or None, "company_status": status or None, "company_type": company_type.strip().lower() or None,
              "sic_codes": re.sub(r"\s+", "", industry_codes) or None, "location": registered_address.strip() or None,
              "incorporated_from": incorporated_from or None, "incorporated_to": incorporated_to or None,
              "size": per_page, "start_index": (page - 1) * per_page}
    try:
        d = await _get("/advanced-search/companies", params, transport)
    except RegistryError as e:
        if e.status == 404: d = {}  # "No companies found"
        else: raise
    total = d.get("hits", d.get("total_results", 0)) or 0
    return {"companies": [row(c) for c in d.get("items", [])], "page": page, "per_page": per_page, "total_count": total, "total_pages": -(-total // per_page)}


async def get_company(jurisdiction_code: str, number: str, transport=None) -> dict:
    if not re.fullmatch(r"[A-Z0-9]{8}", (number or "").upper()): raise RegistryError("Companies House numbers have 8 characters, e.g. 03214465", 400)
    number = number.upper()
    r = row(await _get(f"/company/{number}", None, transport))
    try:
        offs = await _get(f"/company/{number}/officers", {"items_per_page": 50}, transport)
        r["officers"] = [officer_row(o, number) for o in offs.get("items", [])]
    except RegistryError as e:
        if e.status != 404: raise
        r["officers"] = []
    return r


async def match_company(name: str, country_code: str | None = None, domain: str | None = None, transport=None) -> dict | None:
    """Best UK registry match: 1-2 relevance searches, then the profile + officers (2 calls)."""
    if country_code and country_code.lower() not in ("gb", "uk"): return None
    label = (domain or "").split(".")[0]
    best, best_score = None, 0.0
    for q in [q for q in dict.fromkeys([name, label]) if q and len(q.strip()) >= 3]:
        d = await _get("/search/companies", {"q": q, "items_per_page": 10}, transport)
        for c in (row(x) for x in d.get("items", [])):
            s = name_similarity(q, c["name"] or "") - (0.15 if c["inactive"] else 0)
            if s > best_score: best, best_score = c, s
        if best_score >= 0.85: break
    if not best or best_score < 0.6: return None
    detail = await get_company("gb", best["company_number"], transport=transport)
    detail["match_confidence"] = round(min(1.0, best_score), 2); detail["matched_on"] = name or label
    return detail
