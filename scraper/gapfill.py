"""Fill the gaps: when the main source or the website crawl leaves a field empty, ask the next free source.

Order for one company (each step only runs when something is still missing, and every filled value records where it came from):
  1. the listing itself            Overture's own website / phone / email / social links
  2. sibling listings              other Overture rows with the same company name (done in overture.py)
  3. OpenStreetMap by name         Nominatim search "<company name>" in the country; website, phone and email tags of an exactly named place
  4. the website, alternate form   www / non-www and https variants of an address that did not open
  5. the official LEI register     GLEIF: legal name, LEI, status and registered address (Companies House / OpenCorporates when keyed)
Nominatim's rules are followed: at most one request per second, a real User-Agent, results cached.
"""
from __future__ import annotations
import asyncio, os, re, time
from difflib import SequenceMatcher
from urllib.parse import urlparse

import httpx

from .net import shared_ssl

NOMINATIM = "https://nominatim.openstreetmap.org/search"
_LEGAL = re.compile(r"(?i)\b(limited|ltd|\(?pvt\)?|private|inc|corporation|corp|co|company|group|plc|llc|the)\b\.?")
_cache: dict = {}
_lock: asyncio.Lock | None = None
_last = 0.0


def norm_name(name: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", _LEGAL.sub(" ", (name or "").lower()))


def same_company(a: str | None, b: str | None) -> bool:
    x, y = norm_name(a), norm_name(b)
    if not x or not y or min(len(x), len(y)) < 4: return False
    return x == y or SequenceMatcher(None, x, y).ratio() >= 0.9


def _tag(tags: dict, *keys):
    for k in keys:
        v = (tags or {}).get(k)
        if v: return str(v).split(";")[0].strip()
    return None


async def nominatim_lookup(name: str, cc: str, city: str | None = None, transport=None) -> dict:
    """Website / phone / email tags of OpenStreetMap places that carry exactly this company name."""
    global _lock, _last
    if os.getenv("GAPFILL_NOMINATIM", "1") == "0" or not name: return {}
    key = (norm_name(name), cc.lower())
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < 6 * 3600: return hit[1]
    from .registries import user_agent
    if _lock is None: _lock = asyncio.Lock()
    async with _lock:   # Nominatim policy: one request per second
        wait = _last + float(os.getenv("NOMINATIM_MIN_INTERVAL", "1.1")) - time.monotonic()
        if wait > 0: await asyncio.sleep(wait)
        try:
            async with httpx.AsyncClient(verify=shared_ssl(), timeout=20, transport=transport, headers={"User-Agent": user_agent()}) as c:
                r = await c.get(NOMINATIM, params={"q": name, "countrycodes": cc.lower(), "format": "jsonv2", "limit": 10, "extratags": 1})
            data = r.json() if r.status_code == 200 else []
        except Exception:
            data = []
        _last = time.monotonic()
    out: dict = {}
    for p in data if isinstance(data, list) else []:
        if not same_company(p.get("name"), name): continue
        t = p.get("extratags") or {}
        for field, keys in (("website", ("website", "contact:website", "url")), ("phone", ("phone", "contact:phone", "contact:mobile", "mobile")), ("email", ("email", "contact:email"))):
            v = _tag(t, *keys)
            if v and field not in out: out[field] = v
    _cache[key] = (time.time(), out)
    return out


def set_field(c: dict, field: str, value, source: str) -> None:
    if value and not c.get(field):
        c[field] = value; c.setdefault("filled_from", {})[field] = source


async def discover_listing(c: dict, cc: str) -> None:
    """A company with a missing website, phone or email: look it up by name in OpenStreetMap."""
    if c.get("website") and c.get("phone") and c.get("email"): return
    got = await nominatim_lookup(c.get("name") or "", cc, c.get("city"))
    for f, v in got.items(): set_field(c, f, v, "OpenStreetMap")


def alt_urls(url: str) -> list[str]:
    """Other spellings of an address that did not open: https on the same host first, then with / without www."""
    u = urlparse(url); host = u.hostname or ""
    if not host: return []
    other = host[4:] if host.startswith("www.") else "www." + host
    cands = [f"https://{host}", f"https://{other}", f"{u.scheme}://{other}"]
    out = []
    for cand in cands:
        if cand.rstrip("/") != f"{u.scheme}://{host}".rstrip("/") and cand not in out: out.append(cand)
    return out[:3]


async def registry_fill(c: dict, cc: str) -> None:
    """Official record by company name: legal name, LEI / company number, status, registered address."""
    from . import registries
    try:
        m, _errors = await registries.match(c.get("name") or "", cc.lower())
    except Exception:
        return
    if not m: return
    c["registry"] = {k: m.get(k) for k in ("name", "lei", "company_number", "current_status", "registered_address", "company_type", "incorporation_date", "record_url", "source", "match_confidence")}
    set_field(c, "registered_address", m.get("registered_address"), m.get("source") or "registry")


def merge_best(c: dict) -> None:
    """One best email / phone / website / social per company, each with where it came from, and what is still missing."""
    p = c.get("profile") or {}
    filled = c.get("filled_from") or {}
    best, src = {}, {}
    emails = p.get("emails") or []
    pick = next((e for e in emails if e.get("on_company_domain")), emails[0] if emails else None)
    if pick and pick.get("email"): best["email"], src["email"] = pick["email"], "website"
    elif c.get("email"): best["email"], src["email"] = c["email"], filled.get("email", "listing")
    phones = [x for x in (p.get("phones") or []) if x.get("valid")] or (p.get("phones") or [])
    if phones: best["phone"], src["phone"] = phones[0].get("e164") or phones[0].get("raw"), "website"
    elif c.get("phone"): best["phone"], src["phone"] = c["phone"], filled.get("phone", "listing")
    if c.get("website"): best["website"], src["website"] = c["website"], filled.get("website", "listing")
    soc = p.get("social") or {}
    listing_soc = {k: u for u in (c.get("socials") or []) for k in ("linkedin", "facebook", "instagram", "twitter", "youtube", "x") if k + ".com" in u}
    for k in set(soc) | set(listing_soc):
        if soc.get(k): best[f"social_{k}"], src[f"social_{k}"] = soc[k], "website"
        elif listing_soc.get(k): best[f"social_{k}"], src[f"social_{k}"] = listing_soc[k], "listing"
    if (p.get("people") or []): best["contact_person"], src["contact_person"] = p["people"][0].get("name"), "website"
    c["best"], c["best_source"] = best, src
    c["missing"] = [f for f in ("website", "email", "phone", "contact_person") if not best.get(f)]
