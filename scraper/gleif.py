"""GLEIF LEI records (https://api.gleif.org/api/v1): free, no registration, data under CC0.

GLEIF asks for at most 60 requests per minute per user; calls here are spaced 1.1 s apart.
Covers legal entities that hold an LEI, worldwide: legal name, registered and HQ address, company
number at the local register, legal form, status and creation date. It has no officers.
"""
from __future__ import annotations
import os, re

from .registries import JsonApi, RegistryError
from .opencorporates import name_similarity

ID = "gleif"
LABEL = "GLEIF"
ATTRIBUTION = "Source: GLEIF LEI data (CC0)"
needs_key = False
RECORD = "https://search.gleif.org/#/record/{}"

API = JsonApi("https://api.gleif.org/api/v1", float(os.getenv("GLEIF_MIN_INTERVAL", "1.1")),
              {400: "GLEIF rejected the search (check the country/jurisdiction codes).", 429: "GLEIF rate limit reached (60 requests/minute). Wait a minute and try again.",
               404: "Not found in the GLEIF LEI index.", 503: "GLEIF API is unavailable right now."})


def configured() -> bool:
    return True


def _addr(a: dict | None) -> str | None:
    if not a: return None
    parts = [*(a.get("addressLines") or []), a.get("city"), a.get("postalCode"), a.get("country")]
    return ", ".join(p for p in parts if p) or None


def _jurisdiction(j: str | None) -> str | None:
    return j.lower().replace("-", "_") if j else None        # "US-DE" -> "us_de", like OpenCorporates


def row(rec: dict) -> dict:
    a = rec.get("attributes", rec); e = a.get("entity", {}); reg = a.get("registration", {})
    status = e.get("status")
    return {
        "name": (e.get("legalName") or {}).get("name"), "company_number": e.get("registeredAs"), "lei": a.get("lei"),
        "jurisdiction_code": _jurisdiction(e.get("jurisdiction")), "company_type": (e.get("legalForm") or {}).get("other") or e.get("category"),
        "current_status": status.title() if status else None, "inactive": status == "INACTIVE",
        "incorporation_date": (e.get("creationDate") or "")[:10] or None, "dissolution_date": ((e.get("expiration") or {}).get("date") or "")[:10] or None,
        "registered_address": _addr(e.get("legalAddress")), "headquarters_address": _addr(e.get("headquartersAddress")),
        "industry_codes": [], "previous_names": [n["name"] for n in e.get("otherNames") or [] if n.get("type") == "PREVIOUS_LEGAL_NAME"],
        "other_names": [n["name"] for n in e.get("otherNames") or [] if n.get("type") != "PREVIOUS_LEGAL_NAME"],
        "registry_url": None, "record_url": RECORD.format(a.get("lei")), "source_publisher": "GLEIF",
        "lei_registration_status": reg.get("status"), "retrieved_at": reg.get("lastUpdateDate"),
        "opencorporates_id": a.get("ocid"),
    }


async def search_companies(q: str = "", page: int = 1, per_page: int = 30, transport=None, country_code: str = "", jurisdiction_code: str = "",
                           exclude_inactive: bool = False, **_ignored) -> dict:
    """Full-text search. Filters GLEIF supports: country, jurisdiction (e.g. us_de) and active status."""
    params = {"filter[fulltext]": q.strip() or None, "page[number]": page, "page[size]": max(1, min(200, per_page)),
              "filter[entity.legalAddress.country]": country_code.upper() or None,
              "filter[entity.jurisdiction]": jurisdiction_code.upper().replace("_", "-") or None,
              "filter[entity.status]": "ACTIVE" if exclude_inactive else None}
    d = await API.get("/lei-records", params, transport=transport)
    p = (d.get("meta") or {}).get("pagination") or {}
    return {"companies": [row(r) for r in d.get("data", [])], "page": p.get("currentPage", page), "per_page": p.get("perPage", per_page),
            "total_count": p.get("total", 0), "total_pages": p.get("lastPage", 0)}


async def get_company(jurisdiction_code: str, lei: str, transport=None) -> dict:
    if not re.fullmatch(r"[A-Z0-9]{20}", lei or ""): raise RegistryError("GLEIF records are looked up by their 20-character LEI", 400)
    d = await API.get(f"/lei-records/{lei}", transport=transport)
    r = row(d.get("data", {})); r["officers"] = []
    return r


async def match_company(name: str, country_code: str | None = None, domain: str | None = None, transport=None) -> dict | None:
    """Best LEI record for a crawled company: 1-2 searches, no detail call (search rows are complete)."""
    label = (domain or "").split(".")[0]
    queries = [q for q in dict.fromkeys([name, label]) if q and len(q.strip()) >= 3]
    best, best_score = None, 0.0
    for q in queries:
        res = await search_companies(q, per_page=10, country_code=country_code or "", transport=transport)
        if not res["companies"] and country_code:
            res = await search_companies(q, per_page=10, transport=transport)
        for c in res["companies"]:
            s = max([name_similarity(q, c["name"] or "")] + [name_similarity(q, n) for n in c["other_names"] + c["previous_names"]])
            if c["inactive"]: s -= 0.15
            if country_code and (c.get("jurisdiction_code") or "").startswith(country_code.lower()): s += 0.05
            if s > best_score: best, best_score = c, s
        if best_score >= 0.85: break
    if not best or best_score < 0.6: return None
    best["officers"] = []; best["match_confidence"] = round(min(1.0, best_score), 2); best["matched_on"] = name or label
    return best
