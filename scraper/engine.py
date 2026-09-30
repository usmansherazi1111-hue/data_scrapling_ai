from __future__ import annotations
import asyncio, random, time
from collections import deque
from scrapling.fetchers import AsyncFetcher, AsyncDynamicSession, AsyncStealthySession
from scrapling.engines.toolbelt.proxy_rotation import ProxyRotator
from .models import CrawlResult, PageRecord, now_iso
from .urltools import normalize_url, same_domain
from .discovery import discover
from .extract import extract_page, aggregate
from .antibot import detect_challenge, page_html, Robots, parse_proxies
from .auth import cookies_for
from . import techdetect
from .enrichment import enrich_job

# Fetch ladder: each rung is tried only when the previous one was blocked or returned an empty shell.
LADDERS = {"http": ["http"], "auto": ["http", "dynamic", "stealth"], "dynamic": ["dynamic", "stealth"], "stealth": ["stealth"]}
CHALLENGE_HELP = "Blocked by a {} challenge. Open the site in Access → 'Open browser', solve it yourself, then re-run the crawl with that auth profile."


class CrawlEngine:
    def __init__(self, db, jobs, profiles):
        self.db = db; self.jobs = jobs; self.profiles = profiles

    @staticmethod
    def _needs_browser(response):
        """Blocked responses or near-empty HTML usually mean a JS-rendered or protected page."""
        status = getattr(response, "status", None) or 0
        if status in (401, 403, 429, 503) or status >= 500: return True
        try: txt = response.get_all_text(strip=True) or ""
        except Exception: txt = ""
        try: scripts = len(response.css("script"))
        except Exception: scripts = 0
        return len(txt) < 250 or (len(txt) < 800 and scripts > 5)

    async def run(self, job_id, seed, cfg):
        seed = normalize_url(seed)
        result = CrawlResult(job_id=job_id, started_at=now_iso(), finished_at=None, status="running", seed_url=seed, config=dict(cfg))
        self.jobs[job_id] = result
        max_pages = int(cfg.get("max_pages", 20)); depth = int(cfg.get("depth", 1)); mode = cfg.get("mode", "auto")
        adaptive = bool(cfg.get("adaptive", True)); capture_xhr = cfg.get("capture_xhr", False); timeout_ms = int(cfg.get("timeout", 30000))
        ladder = LADDERS[mode] if cfg.get("escalate", True) else LADDERS[mode][:1]
        delay = max(0, int(cfg.get("delay_ms", 0))) / 1000
        proxies = parse_proxies(cfg.get("proxies"))
        http_rotator = ProxyRotator(proxies) if proxies else None
        profile = self.profiles.get(cfg.get("profile")) if cfg.get("profile") else None
        p_cookies = (profile or {}).get("cookies", []); p_ua = (profile or {}).get("useragent") or None
        p_headers = dict((profile or {}).get("headers") or {})
        if p_ua: p_headers.setdefault("User-Agent", p_ua)

        async def fetch_http(url):
            kw = dict(stealthy_headers=True, timeout=max(5, timeout_ms / 1000), retries=2, selector_config={"adaptive": adaptive})
            if p_headers: kw["headers"] = p_headers
            c = cookies_for(p_cookies, url)
            if c: kw["cookies"] = c
            if http_rotator: kw["proxy"] = http_rotator.get_proxy()
            return await AsyncFetcher.get(url, **kw)

        sessions = {}; session_lock = asyncio.Lock()
        async def get_session(kind):
            # Browser sessions are created lazily (Auto mode may never need one) and shared by all workers.
            async with session_lock:
                if kind not in sessions:
                    kw = dict(headless=True, network_idle=bool(cfg.get("network_idle", False)), timeout=timeout_ms, max_pages=min(4, int(cfg.get("browser_pages", 3))),
                              disable_resources=bool(cfg.get("disable_resources", True)), capture_xhr=r".*" if capture_xhr else None)
                    if p_cookies: kw["cookies"] = [{k: v for k, v in c.items() if k in ("name", "value", "domain", "path", "expires", "httpOnly", "secure", "sameSite")} for c in p_cookies]
                    if p_ua: kw["useragent"] = p_ua
                    if (profile or {}).get("headers"): kw["extra_headers"] = profile["headers"]
                    if proxies: kw["proxy_rotator"] = ProxyRotator(proxies)
                    cls = AsyncStealthySession if kind == "stealth" else AsyncDynamicSession
                    s = cls(**kw); await s.start(); sessions[kind] = s
                return sessions[kind]

        robots = Robots(fetch_http) if cfg.get("respect_robots", True) else None
        queue = deque([(seed, 0, "homepage", 1.0)]); queued = {seed}; visited = set(); page_payload = []; started = time.perf_counter()

        async def one(item):
            url, dep, cat, score = item; t = time.perf_counter()
            try:
                if robots and not await robots.allowed(url):
                    return item, PageRecord(url=url, status=None, title="", mode="-", elapsed_ms=0, discovered_score=score, category=cat, challenge="robots", error="Disallowed by robots.txt"), None, None, [], "robots.txt"
                wait = max(delay, robots.crawl_delay(url) if robots else 0)
                if wait: await asyncio.sleep(wait * random.uniform(0.7, 1.3))
                response = None; used = None; challenge = ""; last_error = None
                for rung in ladder:
                    try:
                        r = await fetch_http(url) if rung == "http" else await (await get_session(rung)).fetch(url, selector_config={"adaptive": adaptive})
                    except Exception as e:
                        last_error = e; continue
                    response, used = r, rung
                    challenge = detect_challenge(r, url)
                    if challenge: continue                           # blocked: try the next rung
                    if rung == "http" and mode == "auto" and self._needs_browser(r) and len(ladder) > 1: continue  # JS shell: render it
                    break
                if response is None: raise last_error or RuntimeError("fetch failed")
                used_label = used if mode == used else f"{mode}:{used}"
                elapsed = int((time.perf_counter() - t) * 1000)
                if challenge:
                    return item, PageRecord(url=url, status=getattr(response, "status", None), title="", mode=used_label, elapsed_ms=elapsed, discovered_score=score, category=cat,
                                            challenge=challenge, error=CHALLENGE_HELP.format(challenge)), None, None, [], challenge
                def parse():
                    html = page_html(response)
                    return extract_page(response, url, cat), discover(response, seed, max_pages * 2), techdetect.detect(response, html)
                # Parsing runs off the event loop so other sites keep downloading meanwhile.
                extracted, links, tech = await asyncio.to_thread(parse) if cfg.get("parse_in_thread") else parse()
                raw = {"url": url, "category": cat, "status":getattr(response, "status", None), "title": extracted.get("title", ""), "markdown": "", "text": extracted.get("text", ""),
                       "jsonld": extracted.get("jsonld", []), "emails": extracted.get("emails", []), "phones": extracted.get("phones", []), "headings": extracted.get("headings", []),
                       "people": extracted.get("people", []), "addresses": extracted.get("addresses", []), "tech": tech, "xhr": []}
                if cfg.get("markdown", True):   # markdownify is slow and company-list crawls never show it
                    try: raw["markdown"] = response.markdown(main_content_only=True)
                    except Exception: pass
                xhr = getattr(response, "captured_xhr", []) or []
                if capture_xhr:
                    for x in xhr[:30]:
                        try: raw["xhr"].append({"url": x.url, "status": x.status, "body": x.body.decode("utf-8", "replace")[:20000]})
                        except Exception: pass
                rec = PageRecord(url=url, status=getattr(response, "status", None), title=extracted.get("title", ""), mode=used_label, elapsed_ms=elapsed, discovered_score=score,
                                 category=cat, links=len(links), xhr_count=len(xhr), text_chars=len(extracted.get("text", "")), markdown_chars=len(raw["markdown"]))
                return item, rec, raw, extracted, links, None
            except Exception as e:
                return item, PageRecord(url=url, status=None, title="", mode=mode, elapsed_ms=int((time.perf_counter() - t) * 1000), discovered_score=score, category=cat, error=str(e)[:500]), None, None, [], str(e)

        page_cap = float(cfg.get("page_time_limit") or 0); budget = float(cfg.get("time_budget") or 0)

        async def one_capped(item):
            # Hard stop per page so one slow or protected site cannot hold a worker for minutes.
            if not page_cap: return await one(item)
            try: return await asyncio.wait_for(one(item), page_cap)
            except asyncio.TimeoutError:
                url, _, cat, score = item; msg = f"timed out after {page_cap:g} s"
                return item, PageRecord(url=url, status=None, title="", mode=mode, elapsed_ms=int(page_cap * 1000), discovered_score=score, category=cat, error=msg), None, None, [], msg

        try:
            while queue and len(visited) < max_pages and not (budget and time.perf_counter() - started > budget):
                batch = []
                while queue and len(batch) < int(cfg.get("concurrency", 4)) and len(visited) + len(batch) < max_pages:
                    item = queue.popleft()
                    if item[0] not in visited: batch.append(item)
                if not batch: continue
                for item, record, raw, extracted, links, error in await asyncio.gather(*(one_capped(x) for x in batch)):
                    visited.add(item[0]); result.pages.append(record)
                    if raw:
                        result.raw_pages[item[0]] = raw; page_payload.append({"url": item[0], "category": item[2], "extracted": extracted})
                        if item[1] < depth:
                            for u, c, s in links:
                                if u not in queued and same_domain(seed, u):
                                    queued.add(u); queue.append((u, item[1] + 1, c, s))
                    elif record.challenge:
                        result.stats.setdefault("challenges", []).append({"url": item[0], "type": record.challenge})
                    else:
                        result.stats.setdefault("errors", []).append({"url": item[0], "error": error})
                self.db.save(result)
            result.fields = aggregate(page_payload, seed)
            result.stats.update({"pages_crawled": len(result.pages), "pages_succeeded": sum(1 for p in result.pages if not p.error), "pages_failed": sum(1 for p in result.pages if p.error and not p.challenge),
                                 "pages_blocked": sum(1 for p in result.pages if p.challenge), "elapsed_ms": int((time.perf_counter() - started) * 1000), "queue_remaining": len(queue),
                                 "profile": cfg.get("profile"), "proxies": len(proxies)})
            if cfg.get("enrich", True) and page_payload:
                await self.enrich(job_id, result=result, use_ai=cfg.get("ai_enrichment", True), use_registry=cfg.get("registry_lookup", True))
            result.status = "completed"; result.finished_at = now_iso(); self.db.save(result); return result
        except Exception as e:
            result.status = "failed"; result.stats["error"] = str(e)[:1000]; result.finished_at = now_iso(); self.db.save(result); return result
        finally:
            for s in sessions.values():
                try: await s.close()
                except Exception: pass

    async def enrich(self, job_id, result=None, use_ai=True, use_registry=True):
        """Run (or re-run) enrichment for a live CrawlResult or a job stored in history."""
        target = result or self.jobs.get(job_id)
        data = target.jsonable() if hasattr(target, "jsonable") else (target or self.db.get(job_id))
        if not data: raise KeyError(job_id)
        state = {"status": "running", "step": "starting"}
        def set_state(v):
            if hasattr(target, "jsonable"): target.enrichment = v
            else: data["enrichment"] = v
        set_state(state)
        if not hasattr(target, "jsonable"): self.jobs[job_id] = data
        try:
            profile = await enrich_job(data, progress=lambda s: state.update(step=s), use_ai=use_ai, use_registry=use_registry)
            profile["status"] = "done"
        except Exception as e:
            profile = {"status": "failed", "error": f"{type(e).__name__}: {e}"[:500]}
        set_state(profile)
        if hasattr(target, "jsonable"): self.db.save(target)
        else: self.db.save(data); self.jobs.pop(job_id, None)
        return profile
