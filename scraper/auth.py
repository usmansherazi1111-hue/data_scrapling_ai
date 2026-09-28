"""Auth profiles: saved cookies/headers/user-agent that the crawler sends with every request.

A profile is filled either by pasting cookies (exported from your own browser) or by opening a
visible browser window where you log in and/or pass a CAPTCHA yourself; its cookies are then
saved and reused by all fetch modes. Profiles are stored as plain JSON under data/profiles.
"""
from __future__ import annotations
import asyncio, json, re, time
from pathlib import Path
from urllib.parse import urlparse

NAME_RE = re.compile(r"^[\w.-]{1,60}$")


def _host(url_or_domain: str) -> str:
    s = url_or_domain.strip()
    if "://" in s: s = urlparse(s).hostname or ""
    return s.lower().lstrip(".")


def parse_cookies(raw: str, domain: str) -> list[dict]:
    """Accept a JSON array (browser extension export), a Netscape cookies.txt, or a 'a=1; b=2' header string."""
    raw = (raw or "").strip(); domain = _host(domain)
    if not raw: return []
    if raw.startswith("["):
        out = []
        for c in json.loads(raw):
            if not c.get("name"): continue
            d = {"name": str(c["name"]), "value": str(c.get("value", "")), "domain": c.get("domain") or domain, "path": c.get("path") or "/"}
            exp = c.get("expires", c.get("expirationDate"))
            if isinstance(exp, (int, float)) and exp > 0: d["expires"] = float(exp)
            if c.get("secure"): d["secure"] = True
            if c.get("httpOnly"): d["httpOnly"] = True
            out.append(d)
        return out
    if "\t" in raw:  # Netscape format
        out = []
        for line in raw.splitlines():
            if not line.strip() or (line.startswith("#") and not line.startswith("#HttpOnly_")): continue
            parts = line.split("\t")
            if len(parts) < 7: continue
            dom = parts[0].replace("#HttpOnly_", "")
            out.append({"name": parts[5], "value": parts[6].strip(), "domain": dom, "path": parts[2] or "/", "secure": parts[3].upper() == "TRUE"})
        return out
    if not domain: raise ValueError("A domain is required for 'name=value' cookie strings")
    raw = re.sub(r"^cookie:\s*", "", raw, flags=re.I)
    return [{"name": k.strip(), "value": v.strip(), "domain": domain, "path": "/"} for k, _, v in (p.partition("=") for p in raw.split(";")) if k.strip()]


def cookies_for(cookies: list[dict], url: str) -> dict:
    """name->value for cookies whose domain matches the URL host (used by the HTTP fetcher)."""
    host = _host(url)
    return {c["name"]: c["value"] for c in cookies if host == c.get("domain", "").lstrip(".") or host.endswith("." + c.get("domain", "").lstrip("."))}


class ProfileStore:
    def __init__(self, root: str | Path):
        self.root = Path(root); self.root.mkdir(parents=True, exist_ok=True)
        self.browsers: dict[str, dict] = {}  # name -> {"playwright","context","task","url"}

    def _path(self, name: str) -> Path:
        if not NAME_RE.match(name or ""): raise ValueError("Profile name may only contain letters, digits, '.', '_' and '-'")
        return self.root / f"{name}.json"

    def list(self) -> list[dict]:
        out = []
        for p in sorted(self.root.glob("*.json")):
            try: d = json.loads(p.read_text(encoding="utf-8"))
            except Exception: continue
            out.append({"name": d["name"], "domain": d.get("domain", ""), "cookies": len(d.get("cookies", [])), "cookie_domains": sorted({c.get("domain", "").lstrip(".") for c in d.get("cookies", [])})[:8],
                        "useragent": d.get("useragent", ""), "headers": list((d.get("headers") or {}).keys()), "updated": d.get("updated", 0), "browser_open": d["name"] in self.browsers})
        return out

    def get(self, name: str | None) -> dict | None:
        if not name: return None
        p = self._path(name)
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None

    def save(self, name: str, **fields) -> dict:
        d = self.get(name) or {"name": name, "cookies": [], "headers": {}, "useragent": "", "domain": ""}
        d.update({k: v for k, v in fields.items() if v is not None}); d["updated"] = time.time()
        self._path(name).write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
        return d

    def delete(self, name: str):
        p = self._path(name)
        if p.exists(): p.unlink()
        import shutil
        shutil.rmtree(self.root / f"{name}_browser", ignore_errors=True)  # saved browser session for this profile

    # ---- interactive browser -------------------------------------------------
    async def open_browser(self, name: str, url: str):
        """Open a visible Chromium window with a persistent profile. The user logs in / solves any
        challenge; cookies are saved every few seconds and when the window closes."""
        self._path(name)
        if name in self.browsers: raise ValueError("A browser window is already open for this profile")
        from playwright.async_api import async_playwright
        pw = await async_playwright().start()
        ctx = await pw.chromium.launch_persistent_context(str(self.root / f"{name}_browser"), headless=False, no_viewport=True, args=["--disable-blink-features=AutomationControlled"])
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        ua = await page.evaluate("navigator.userAgent")
        existing = (self.get(name) or {}).get("cookies", [])
        if existing:
            try: await ctx.add_cookies(existing)
            except Exception: pass
        state = {"playwright": pw, "context": ctx, "url": url, "closed": asyncio.Event()}
        self.browsers[name] = state
        ctx.on("close", lambda *_: state["closed"].set())
        try: await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        except Exception: pass

        async def keep_saving():
            try:
                while not state["closed"].is_set():
                    await self._snapshot(name, ctx, ua, url)
                    try: await asyncio.wait_for(state["closed"].wait(), 3)
                    except asyncio.TimeoutError: pass
            finally:
                self.browsers.pop(name, None)
                try: await pw.stop()
                except Exception: pass
        state["task"] = asyncio.create_task(keep_saving())

    async def _snapshot(self, name, ctx, ua, url):
        try: cookies = await ctx.cookies()
        except Exception: return
        keep = ("name", "value", "domain", "path", "expires", "httpOnly", "secure", "sameSite")
        self.save(name, cookies=[{k: c[k] for k in keep if k in c} for c in cookies], useragent=ua, domain=_host(url))

    async def close_browser(self, name: str):
        st = self.browsers.get(name)
        if not st: return
        ua = None
        try:
            page = st["context"].pages[0]
            ua = await page.evaluate("navigator.userAgent")
        except Exception: pass
        await self._snapshot(name, st["context"], ua, st["url"])
        try: await st["context"].close()
        except Exception: pass
        st["closed"].set()
        try: await asyncio.wait_for(st["task"], 10)
        except Exception: pass
