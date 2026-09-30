"""Company-registry sources behind one interface.

  gleif           GLEIF LEI API (api.gleif.org): free, no key, worldwide, CC0 data. Covers entities
                  that hold an LEI (several million; mostly companies that trade, borrow or report).
  companieshouse  UK Companies House public API: free key, every UK company, with directors.
  opencorporates  OpenCorporates API: needs a token (paid for commercial use), 140+ registers.

Each source module exposes: ID, LABEL, ATTRIBUTION, needs_key, configured(), search_companies(),
get_company(jurisdiction, number) and match_company(name, country_code, domain), all returning
the same row shape (see opencorporates.company_row) plus "source" and "record_url".
"""
from __future__ import annotations
import asyncio, os, time

import httpx
from .net import shared_ssl


class RegistryError(RuntimeError):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message); self.status = status


class JsonApi:
    """Polite JSON client: at most `concurrency` requests at a time per source (1 unless the service allows more),
    starts spaced by min_interval, cached, never retried around limits."""
    def __init__(self, base: str, min_interval: float, errors: dict[int, str], ttl: int = 6 * 3600, timeout: float = 30, concurrency: int = 1):
        self.base = base; self.timeout = timeout; self.min_interval = min_interval; self.errors = errors; self.ttl = ttl; self.concurrency = concurrency
        self.cache: dict[tuple, tuple[float, dict]] = {}; self._sem = None; self._space = None; self._last = 0.0

    async def get(self, path: str, params: dict | None = None, auth=None, headers: dict | None = None, transport=None) -> dict:
        params = {k: v for k, v in (params or {}).items() if v not in (None, "")}
        key = (path, tuple(sorted((k, str(v)) for k, v in params.items())))
        hit = self.cache.get(key)
        if hit and time.time() - hit[0] < self.ttl: return hit[1]
        if self._sem is None: self._sem = asyncio.Semaphore(self.concurrency); self._space = asyncio.Lock()
        async with self._sem:
            async with self._space:
                wait = self._last + self.min_interval - time.monotonic()
                if wait > 0: await asyncio.sleep(wait)
                self._last = time.monotonic()
            try:
                async with httpx.AsyncClient(verify=shared_ssl(), timeout=self.timeout, transport=transport) as c:
                    r = await c.get(f"{self.base}{path}", params=params, auth=auth, headers={"Accept": "application/json", "User-Agent": user_agent(), **(headers or {})})
            finally:
                self._last = max(self._last, time.monotonic()) if self.concurrency == 1 else self._last
        if r.status_code == 200:
            data = r.json(); self.cache[key] = (time.time(), data); return data
        raise RegistryError(self.errors.get(r.status_code, f"HTTP {r.status_code} from {self.base}"), r.status_code)


def user_agent() -> str:
    """Wikimedia and OSM ask automated clients for a User-Agent with a way to contact the operator (URL or email)."""
    contact = os.getenv("APP_CONTACT", "").strip() or "https://github.com/usmansherazi1111-hue/data_scrapling_ai"
    return f"ScraplingStudio/1.6 ({contact}) python-httpx"


def country_of(jurisdiction: str | None) -> str:
    return (jurisdiction or "").split("_")[0].split("-")[0].lower()


# ---------------------------------------------------------------- source registry
def _modules():
    from . import gleif, companieshouse, opencorporates
    return {"gleif": gleif, "companieshouse": companieshouse, "opencorporates": opencorporates}


LABELS = {"gleif": "GLEIF (worldwide, free, no key)", "companieshouse": "Companies House (UK, free key)", "opencorporates": "OpenCorporates (token)"}


def get(source: str):
    m = _modules().get(source)
    if not m: raise RegistryError(f"Unknown registry source '{source}'", 400)
    return m


def available() -> list[dict]:
    return [{"id": k, "label": LABELS[k], "configured": m.configured()} for k, m in _modules().items()]


def default_source() -> str:
    for k in ("companieshouse", "opencorporates"):
        if get(k).configured(): return k
    return "gleif"


def tag(row: dict, source: str) -> dict:
    """Mark a row with its source and a link to the public record (kept for attribution)."""
    row.setdefault("source", source)
    m = get(row["source"])
    row["attribution"] = m.ATTRIBUTION
    row.setdefault("record_url", row.get("opencorporates_url") or row.get("registry_url"))
    return row


async def search(source: str, q: str = "", page: int = 1, per_page: int = 30, **filters) -> dict:
    res = await get(source).search_companies(q, page=page, per_page=per_page, **filters)
    res["companies"] = [tag(c, source) for c in res["companies"]]
    return res


async def company(source: str, jurisdiction: str, number: str) -> dict:
    return tag(await get(source).get_company(jurisdiction, number), source)


def match_order(country_code: str | None) -> list[str]:
    """Most detailed source first: Companies House for UK companies (officers, free), then OpenCorporates, then GLEIF."""
    cc = (country_code or "").lower()
    order = []
    if cc in ("gb", "uk", "") and get("companieshouse").configured(): order.append("companieshouse")
    if get("opencorporates").configured(): order.append("opencorporates")
    order.append("gleif")
    return order


async def match(name: str, country_code: str | None = None, domain: str | None = None, sources: list[str] | None = None) -> tuple[dict | None, dict]:
    """First confident match across sources. Returns (record or None, {source: error}).
    A failing source (bad key, quota, outage) is recorded and the next source is tried."""
    errors = {}
    for s in sources or match_order(country_code):
        try:
            m = await get(s).match_company(name, country_code, domain)
        except Exception as e:
            errors[s] = str(e)[:300]; continue
        if m: return tag(m, s), errors
    return None, errors
