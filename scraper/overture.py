"""Company lists from Overture Maps "places" (open data, no key, no account).

One rule for every industry: the words typed in the Industry box are matched against each place's category path
(for example "manufacturing_and_industrial > textile_manufacturer") and its name. Nothing is hard-coded per industry.
The data is read straight from Overture's public files, filtered by the country's bounding box, so a whole country is
scanned in about a minute. Places have website, phone, email and social links when the mapper provided them.

Licence: Overture places are CDLA Permissive 2.0. Attribution: (c) Overture Maps Foundation.
"""
from __future__ import annotations
import asyncio, json, math, os, re, time
from pathlib import Path

ID = "overture"
LABEL = "Overture Maps (open data)"
ATTRIBUTION = "(c) Overture Maps Foundation, CDLA Permissive 2.0. https://overturemaps.org"
BUCKET = "overturemaps-us-west-2"
FALLBACK_RELEASE = "2026-09-23.0"
COLUMNS = ["id", "names", "taxonomy", "websites", "phones", "emails", "socials", "addresses", "confidence", "operating_status", "bbox"]
COUNTRIES = json.loads((Path(__file__).parent / "countries.json").read_text(encoding="utf-8"))
ALIASES = {"uk": "GB", "great britain": "GB", "britain": "GB", "england": "GB", "usa": "US", "america": "US", "uae": "AE", "emirates": "AE",
           "korea": "KR", "south korea": "KR", "russia": "RU", "iran": "IR", "vietnam": "VN", "turkey": "TR", "ivory coast": "CI"}
NAME_TO_CODE = {v["name"].lower(): k for k, v in COUNTRIES.items()}


class OvertureError(Exception):
    pass


def country_code(text: str) -> str | None:
    """'pk', 'PK', 'Pakistan' or 'uk' -> ISO code, or None when unknown."""
    t = (text or "").strip().lower()
    if not t: return None
    if t in ALIASES: return ALIASES[t]
    if len(t) == 2 and t.upper() in COUNTRIES: return t.upper()
    return NAME_TO_CODE.get(t)


def country_name(code: str) -> str:
    return (COUNTRIES.get((code or "").upper()) or {}).get("name") or (code or "").upper()


def stem(word: str) -> str:
    """Prefix used for matching so 'medicine' finds 'medical', 'textiles' finds 'textile' (first 5 letters of longer words)."""
    w = re.sub(r"[^\w]", "", word.lower())
    return w[:5] if len(w) >= 6 else w.rstrip("s") if len(w) > 4 else w


STOP_WORDS = {"and", "the", "of", "for", "in", "&", "companies", "company", "industry", "industries", "business", "businesses"}


def industry_terms(text: str) -> list[str]:
    return [stem(w) for w in re.split(r"[\s,/;|]+", text or "") if w and w.lower() not in STOP_WORDS and len(w) > 1]


def full_words(text: str) -> list[str]:
    """The typed words in full (plural s removed), used to rank exact category matches above prefix matches."""
    out = [re.sub(r"[^\w]", "", w.lower()) for w in re.split(r"[\s,/;|]+", text or "") if w and w.lower() not in STOP_WORDS and len(w) > 1]
    return [w[:-1] if len(w) > 4 and w.endswith("s") else w for w in out if w]


# Places are often filed under a neighbouring category (a software house as "information_technology_company", a spinning mill as
# "manufacturer"); these extra words widen a typed industry word so those companies are not missed.
RELATED = {"softw": ["information_technology", "it_company"], "it": ["information_technology", "softw"],
           "texti": ["apparel_manufacturer", "spinning", "weaving", "denim", "hosiery", "knitwear", "garment"],
           "garme": ["apparel_manufacturer", "texti"], "appar": ["garment", "texti"],
           "pharm": ["drug", "biotechnology_company"], "medic": ["pharmaceutical"],
           "const": ["contractor", "builder", "real_estate_developer"], "logis": ["freight", "shipping", "courier", "cargo"]}


def alternatives(term: str) -> list[str]:
    return [term] + RELATED.get(term, [])


def parse_industry(text: str) -> list[dict]:
    """Typed words -> [{word, prefix, alts}]: the full word (plural removed), its 5-letter prefix and the widened alternatives."""
    out = []
    for w in re.split(r"[\s,/;|]+", text or ""):
        w = re.sub(r"[^\w]", "", w.lower())
        if not w or w in STOP_WORDS or len(w) < 2: continue
        full = w[:-1] if len(w) > 4 and w.endswith("s") else w
        pre = stem(w)
        out.append({"word": full, "prefix": pre, "alts": alternatives(pre)})
    return out


def _tokens(text: str) -> list[str]:
    return [t for t in re.split(r"[^a-z0-9]+", (text or "").lower()) if t]


def _exact(word: str, tokens: list[str]) -> bool:
    return any(t == word or t == word + "s" or (len(word) >= 5 and t.startswith(word)) for t in tokens)


def _related(p: dict, tokens: list[str]) -> bool:
    text = " ".join(tokens)
    return any(re.search(r"(^| )" + re.escape(a.replace("_", " ")), text) for a in p["alts"]) or _exact(p["word"], tokens)


def relevance(rec: dict, parsed: list[dict]) -> tuple[int, str]:
    """How well a place's category fits the typed industry, and why it was listed.
    3 = the typed word is the place's own category ("textile manufacturer")
    2 = it is a parent category, or the category only shares the word's stem ("pharmacy" for "pharmaceutical")
    1 = a related word appears in a parent category
    0 = only the company name (or the broad top-level category such as "health and medical") matches."""
    if not parsed: return 3, "name"
    tax = rec.get("taxonomy") or {}
    hier = list(tax.get("hierarchy") or [])
    leaf = _tokens(" ".join([tax.get("primary") or ""] + list(tax.get("alternates") or [])))
    mid = _tokens(" ".join(hier[1:-1])) if len(hier) > 2 else []
    leaf_mid = leaf + mid
    if all(_exact(p["word"], leaf) for p in parsed): return 3, "category"
    if all(_exact(p["word"], leaf_mid) for p in parsed): return 2, "parent category"
    if all(_related(p, leaf) for p in parsed): return 2, "related category"
    if all(_related(p, leaf_mid) for p in parsed): return 1, "related parent category"
    return 0, "name only"


LEGAL_RE = re.compile(r"(?i)\b(limited|ltd|\(?pvt\)?|private limited|inc|corporation|corp|group|plc|llc)\b\.?")
# Hosts shared by unrelated places; they say nothing about which company a place belongs to.
SHARED_HOSTS = ("facebook.com", "instagram.com", "linkedin.com", "twitter.com", "x.com", "youtube.com", "wa.me", "whatsapp.com", "linktr.ee", "google.com",
                "sites.google.com", "business.site", "blogspot.com", "wordpress.com", "wixsite.com", "tiktok.com", "daraz.pk", "olx.com.pk", "t.me")


def site_domain(url: str | None) -> str | None:
    """'https://www.Nishat.net/careers' -> 'nishat.net'; None for social pages and shared hosting."""
    m = re.match(r"(?i)^(?:https?://)?(?:www\d?\.)?([^/:?#]+)", (url or "").strip())
    host = (m.group(1).lower().rstrip(".") if m else "")
    if not host or "." not in host or any(host == h or host.endswith("." + h) for h in SHARED_HOSTS): return None
    return host


def configured() -> bool:
    return True


def _fs():
    import pyarrow.fs as pafs
    return pafs.S3FileSystem(anonymous=True, region="us-west-2", request_timeout=60, connect_timeout=20)


_RELEASE_MEMO: dict = {}


def latest_release(fs=None) -> str:
    env = os.getenv("OVERTURE_RELEASE")
    if env: return env
    if _RELEASE_MEMO and time.time() - _RELEASE_MEMO["at"] < 6 * 3600: return _RELEASE_MEMO["release"]
    rel = _list_latest_release(fs)
    if rel != FALLBACK_RELEASE: _RELEASE_MEMO.update(release=rel, at=time.time())
    return rel


def _list_latest_release(fs=None) -> str:
    try:
        import pyarrow.fs as pafs
        infos = (fs or _fs()).get_file_info(pafs.FileSelector(f"{BUCKET}/release/", recursive=False))
        rel = sorted(i.base_name for i in infos if i.type == pafs.FileType.Directory and re.match(r"\d{4}-\d{2}-\d{2}", i.base_name))
        if rel: return rel[-1]
    except Exception:
        pass
    return FALLBACK_RELEASE


def _first(v):
    return v[0] if v else None


def _row(rec: dict, cc: str, score: float) -> dict:
    a = _first(rec.get("addresses")) or {}
    tax = rec.get("taxonomy") or {}
    parts = [a.get("freeform"), a.get("locality"), a.get("region"), a.get("postcode")]
    address = ", ".join(p.strip() for p in parts if p and p.strip())
    return {
        "name": (rec.get("names") or {}).get("primary"), "category": (tax.get("primary") or "").replace("_", " ") or None,
        "category_path": " > ".join(tax.get("hierarchy") or []) or None,
        "website": _first(rec.get("websites")), "phone": _first(rec.get("phones")), "email": _first(rec.get("emails")),
        "socials": rec.get("socials") or [], "city": a.get("locality"), "registered_address": address or None, "country_code": cc,
        "confidence": round(float(rec["confidence"]), 2) if rec.get("confidence") is not None else None,
        "source": "overture", "source_id": rec.get("id"), "_score": score,
    }


def cache_dir() -> Path:
    return Path(os.getenv("OVERTURE_CACHE_DIR", "./data/overture"))


def prune_old_releases(dest: Path) -> None:
    """Keep the newest release's cache only; older releases are never searched again and each country file is tens of MB."""
    import shutil
    try:
        for d in dest.parent.parent.iterdir():
            if d.is_dir() and d.name < dest.parent.name: shutil.rmtree(d, ignore_errors=True)
    except OSError:
        pass


def build_country_cache(src_path: str, cc: str, dest: Path, fs=None, progress=None) -> Path:
    """Copy one country's places (only the columns we use) to a local parquet file, so later searches skip the download."""
    import pyarrow.compute as pc, pyarrow.dataset as ds, pyarrow.parquet as pq
    dataset = ds.dataset(src_path, filesystem=fs, format="parquet")
    xmin, ymin, xmax, ymax = COUNTRIES[cc]["bbox"]
    flt = (ds.field("bbox", "xmin") > xmin - 0.01) & (ds.field("bbox", "xmax") < xmax + 0.01) & (ds.field("bbox", "ymin") > ymin - 0.01) & (ds.field("bbox", "ymax") < ymax + 0.01)
    cols = [c for c in COLUMNS if c in dataset.schema.names]
    dest.parent.mkdir(parents=True, exist_ok=True); tmp = dest.with_suffix(".part")
    writer = None; scanned = kept = 0
    try:
        for batch in dataset.scanner(columns=cols, filter=flt, batch_size=131072).to_batches():
            scanned += batch.num_rows
            if "addresses" in cols:   # the bbox also covers border areas of neighbours; keep rows whose first address is in this country
                first = pc.list_slice(batch["addresses"], 0, 1)
                idx = pc.indices_nonzero(pc.equal(pc.list_value_length(first), 1).fill_null(False))
                ctry = pc.struct_field(pc.list_flatten(first), "country")
                batch = batch.take(pc.filter(idx, pc.equal(ctry, cc).fill_null(False)))
            if writer is None: writer = pq.ParquetWriter(tmp, batch.schema)
            if batch.num_rows: writer.write_batch(batch); kept += batch.num_rows
            if progress: progress(f"first search for {country_name(cc)}: downloading places once ({scanned:,} read, {kept:,} kept)")
        if writer is None: raise OvertureError("No data received from Overture")
        writer.close(); writer = None
        os.replace(tmp, dest); prune_old_releases(dest); return dest
    finally:
        if writer is not None: writer.close()
        if tmp.exists():
            try: tmp.unlink()
            except OSError: pass


def name_key(name: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", LEGAL_RE.sub(" ", (name or "").lower()))


def fill_from_siblings(rows: list[dict]) -> None:
    """Another listing of the same company name often carries the website, phone or email this one lacks (branches, duplicate pins)."""
    best: dict = {}
    for r in rows:
        k = name_key(r.get("name"))
        if len(k) < 4: continue
        b = best.setdefault(k, {})
        for f in ("website", "phone", "email"):
            if r.get(f) and f not in b: b[f] = r[f]
    for r in rows:
        b = best.get(name_key(r.get("name")))
        if not b: continue
        for f in ("website", "phone", "email"):
            if not r.get(f) and b.get(f):
                r[f] = b[f]; r.setdefault("filled_from", {})[f] = "sibling listing"


def scan(industry: str, country: str, name: str = "", limit: int = 100, category: str = "", release: str | None = None, budget: float | None = None, progress=None, dataset_path: str | None = None, fs=None) -> dict:
    """Blocking scan; run it in a thread. Returns {"companies", "matched", "scanned", "release", "partial"}."""
    import pyarrow as pa, pyarrow.compute as pc, pyarrow.dataset as ds
    cc = country_code(country)
    if not cc: raise OvertureError(f"Unknown country '{country}'. Use a country name or a 2-letter code like pk, gb or us.")
    terms = industry_terms(industry)
    parsed = parse_industry(industry)
    if not terms and not name.strip(): raise OvertureError("Enter an industry (or a company name).")
    budget = budget if budget is not None else float(os.getenv("OVERTURE_SCAN_SECONDS", "300"))
    if dataset_path is None:
        fs = fs or _fs(); release = release or latest_release(fs)
        dataset_path = f"{BUCKET}/release/{release}/theme=places/type=place/"
        if os.getenv("OVERTURE_CACHE", "1") != "0":
            cached = cache_dir() / release / f"{cc}.parquet"
            try:
                if not cached.exists(): build_country_cache(dataset_path, cc, cached, fs=fs, progress=progress)
                dataset_path, fs = str(cached), None
            except Exception:
                pass   # cache is only a speed-up; fall back to reading Overture directly
    dataset = ds.dataset(dataset_path, filesystem=fs, format="parquet")
    xmin, ymin, xmax, ymax = COUNTRIES[cc]["bbox"]
    flt = (ds.field("bbox", "xmin") > xmin - 0.01) & (ds.field("bbox", "xmax") < xmax + 0.01) & (ds.field("bbox", "ymin") > ymin - 0.01) & (ds.field("bbox", "ymax") < ymax + 0.01)
    cols = [c for c in COLUMNS if c in dataset.schema.names]
    scanner = dataset.scanner(columns=cols, filter=flt, batch_size=131072)
    start = time.time(); scanned = 0; hits = []; partial = None; qn = name.strip()
    pats = [r"(?i)(^|[^\p{L}\p{N}])(" + "|".join(re.escape(a) for a in alternatives(t)) + ")" for t in terms]
    for batch in scanner.to_batches():
        scanned += batch.num_rows
        nm = pc.struct_field(batch["names"], "primary")
        tx = pc.struct_field(batch["taxonomy"], "primary").fill_null("") if "taxonomy" in cols else pa.nulls(batch.num_rows, pa.string())
        hier = pc.binary_join(pc.struct_field(batch["taxonomy"], "hierarchy"), " ").fill_null("") if "taxonomy" in cols else tx
        mask = None
        for p in pats:
            m = pc.or_(pc.or_(pc.match_substring_regex(nm.fill_null(""), p), pc.match_substring_regex(tx, p)), pc.match_substring_regex(hier, p))
            mask = m if mask is None else pc.and_(mask, m)
        if qn:
            m = pc.match_substring(nm.fill_null(""), qn, ignore_case=True)
            mask = m if mask is None else pc.and_(mask, m)
        for rec in batch.filter(mask).to_pylist():
            a = _first(rec.get("addresses")) or {}
            if (a.get("country") or "").upper() != cc: continue
            if (rec.get("operating_status") or "open") in ("closed", "permanently_closed"): continue
            if not (rec.get("names") or {}).get("primary"): continue
            tier, why = relevance(rec, parsed)
            if category and ((rec.get("taxonomy") or {}).get("primary") or "").replace("_", " ").lower() != category.strip().lower(): continue
            score = (2 if rec.get("websites") else 0) + (1 if rec.get("emails") else 0) + (1 if rec.get("phones") else 0) + float(rec.get("confidence") or 0)
            row = _row(rec, cc, score); row["match"] = why; row["_tier"] = tier; row["_root"] = ((rec.get("taxonomy") or {}).get("hierarchy") or [""])[0]
            hits.append(row)
        if progress: progress(f"scanned {scanned:,} places, {len(hits):,} match so far")
        if time.time() - start > budget:
            partial = f"Stopped after {int(budget)} s ({scanned:,} places scanned); results may be incomplete."; break
    fill_from_siblings(hits)
    # One row per company: branches / units / outlets that share a website are one company, and how many there are is a size signal.
    locations = {}
    for r in hits:
        d = site_domain(r.get("website"))
        if d: locations[d] = locations.get(d, 0) + 1
    for r in hits:
        n = locations.get(site_domain(r.get("website")), 1)
        r["locations"] = n
        r["_score"] += min(3.0, math.log2(n)) + (1 if LEGAL_RE.search(r["name"] or "") else 0)
    # Name-only matches ("Steel City Events"): the sectors where most of them sit show what the word means in this country, so rank those first.
    roots = {}
    for r in hits:
        if r["_tier"] == 0: roots[r["_root"]] = roots.get(r["_root"], 0) + 1
    top = max(roots.values()) if roots else 1
    for r in hits:
        if r["_tier"] == 0: r["_score"] += 2.0 * roots[r["_root"]] / top
    hits.sort(key=lambda r: (-r["_tier"], -r["_score"]))
    seen, out = set(), []
    for r in hits:
        d = site_domain(r.get("website"))
        k = ("site", d) if d else (re.sub(r"\W", "", (r["name"] or "").lower()), (r.get("city") or "").lower())
        if k in seen: continue
        seen.add(k); out.append(r)
    matched = len(out)
    relevant = sum(1 for r in out if r["_tier"] >= 1)
    facets = {}
    for r in out:
        if (r["_tier"] >= 1 or relevant < 10) and r.get("category"): facets[r["category"]] = facets.get(r["category"], 0) + 1
    facets = [{"category": k, "count": v} for k, v in sorted(facets.items(), key=lambda kv: -kv[1])[:15]]
    for r in out: r.pop("_score", None); r.pop("_tier", None); r.pop("_root", None)
    return {"companies": out[:limit], "matched": matched, "relevant": relevant, "facets": facets, "scanned": scanned, "release": release, "partial": partial}


async def search(industry: str, country: str, name: str = "", limit: int = 100, progress=None, **kw) -> dict:
    loop = asyncio.get_running_loop()
    def cb(m):
        if progress: loop.call_soon_threadsafe(progress, m)
    return await loop.run_in_executor(None, lambda: scan(industry, country, name, limit, progress=cb, **kw))
