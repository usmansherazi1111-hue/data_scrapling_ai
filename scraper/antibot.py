"""Block/challenge detection, robots.txt and proxy helpers.

The app does not solve CAPTCHAs automatically. When a challenge is detected the page is
flagged, and the user can solve it themselves in a real browser window (see auth.py); the
resulting session cookies are then reused by the crawler.
"""
from __future__ import annotations
import asyncio, re
from urllib.parse import urlparse

# Interstitial pages: conclusive on their own.
DECISIVE = [
    ("cloudflare", re.compile(r"<title>\s*(just a moment|attention required)|cf-browser-verification|window\._cf_chl_opt", re.I)),
]
# Widgets/scripts that also appear on normal pages: only count on thin or blocked responses.
CHALLENGES = [
    ("turnstile", re.compile(r"cf-turnstile|challenges\.cloudflare\.com/turnstile", re.I)),
    ("recaptcha", re.compile(r"google\.com/recaptcha|g-recaptcha|recaptcha/api\.js", re.I)),
    ("hcaptcha", re.compile(r"hcaptcha\.com|h-captcha", re.I)),
    ("datadome", re.compile(r"captcha-delivery\.com|geo\.captcha-delivery", re.I)),
    ("perimeterx", re.compile(r"px-captcha|_pxCaptcha|perimeterx", re.I)),
    ("akamai", re.compile(r"sec-if-cpt-container|akamai-bm-telemetry|/_sec/cp_challenge", re.I)),
    ("imperva", re.compile(r"_Incapsula_Resource|incapsula incident", re.I)),
]
LOGIN_URL = re.compile(r"/(login|log-in|signin|sign-in|auth|sso|account/login)(/|\?|$)", re.I)
PASSWORD_INPUT = re.compile(r"<input[^>]+type=[\"']?password", re.I)


def page_html(response) -> str:
    try:
        return str(response.html_content)
    except Exception:
        try: return response.body.decode("utf-8", "replace")
        except Exception: return ""


def visible_text_len(response) -> int:
    try: return len(response.get_all_text(strip=True) or "")
    except Exception: return 0


def detect_challenge(response, requested_url: str) -> str:
    """Return a challenge type ('cloudflare', 'recaptcha', 'login', 'blocked', ...) or '' when the page looks normal.

    CAPTCHA widgets are common on contact forms, so they only count as a block when the page is
    otherwise near-empty or returned a blocking status code.
    """
    status = getattr(response, "status", None) or 0
    html = page_html(response)[:400_000]
    thin = visible_text_len(response) < 1500
    blocked_status = status in (401, 403, 429, 503)
    for name, rx in DECISIVE:
        if rx.search(html): return name
    if thin or blocked_status:
        for name, rx in CHALLENGES:
            if rx.search(html): return name
    final = str(getattr(response, "url", "") or requested_url)
    if PASSWORD_INPUT.search(html) and thin and (LOGIN_URL.search(final) and not LOGIN_URL.search(requested_url) or status == 401):
        return "login"
    if blocked_status and thin:
        return "blocked"
    return ""


class Robots:
    """robots.txt cache per host, using Protego (installed with Scrapling)."""
    AGENT = "ScraplingStudio"

    def __init__(self, fetch):
        self.fetch = fetch; self.cache = {}; self.lock = asyncio.Lock()

    async def allowed(self, url: str) -> bool:
        p = urlparse(url); root = f"{p.scheme}://{p.netloc}"
        async with self.lock:
            if root not in self.cache:
                parser = None
                try:
                    from protego import Protego
                    r = await self.fetch(root + "/robots.txt")
                    if getattr(r, "status", 0) == 200:
                        parser = Protego.parse(r.body.decode("utf-8", "replace"))
                except Exception:
                    parser = None
                self.cache[root] = parser
        parser = self.cache[root]
        return True if parser is None else parser.can_fetch(url, self.AGENT)

    def crawl_delay(self, url: str) -> float:
        p = urlparse(url); parser = self.cache.get(f"{p.scheme}://{p.netloc}")
        try: return float(parser.crawl_delay(self.AGENT) or 0) if parser else 0.0
        except Exception: return 0.0


def parse_proxies(raw) -> list[str]:
    if not raw: return []
    items = raw if isinstance(raw, list) else re.split(r"[\s,]+", str(raw))
    out = []
    for p in items:
        p = p.strip()
        if not p or p.startswith("#"): continue
        if not re.match(r"^(https?|socks5h?|socks4)://", p, re.I): p = "http://" + p
        out.append(p)
    return out
