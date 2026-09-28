"""Protection for running the app on a public URL: password login, SSRF guard, local-only actions."""
from __future__ import annotations
import asyncio, hashlib, hmac, ipaddress, os, socket
from urllib.parse import urlparse

COOKIE = "ss_session"
LOGIN_PAGE = """<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Scrapling Studio · Sign in</title><link rel="stylesheet" href="/static/app.css"></head>
<body style="display:grid;place-items:center;min-height:100vh"><form method="post" action="/login" class="card" style="width:min(360px,92vw)">
<div class="brand"><span>◈</span> Scrapling Studio</div><p class="muted">Enter the password to continue.</p>
<label>Password<input type="password" name="password" autofocus required></label>{error}
<div class="btns"><button type="submit">Sign in</button></div></form></body></html>"""


def password() -> str:
    return os.getenv("APP_PASSWORD", "")


def session_token() -> str:
    # Derived from the password, so changing APP_PASSWORD signs everyone out.
    return hmac.new(password().encode(), b"scrapling-studio-session", hashlib.sha256).hexdigest()


def is_authenticated(cookie: str | None) -> bool:
    return not password() or bool(cookie) and hmac.compare_digest(cookie, session_token())


def check_password(given: str) -> bool:
    return bool(password()) and hmac.compare_digest(given.encode(), password().encode())


def is_remote(headers) -> bool:
    """True unless the request was addressed to this machine directly (http://127.0.0.1:8000 / localhost).
    Tunnels keep the public hostname in Host, and some add forwarding headers."""
    host = (headers.get("host") or "").rsplit(":", 1)[0].strip("[]").lower()
    return host not in ("127.0.0.1", "localhost", "::1") or any(h in headers for h in ("cf-connecting-ip", "x-forwarded-for", "x-real-ip", "x-forwarded-host"))


async def is_public_url(url: str) -> bool:
    """Block crawling of localhost/private networks (someone with the link must not reach your LAN)."""
    host = urlparse(url).hostname or ""
    if not host or host == "localhost" or host.endswith((".local", ".internal", ".localhost")): return False
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except Exception:
        return True  # unresolvable here; the fetch itself will fail
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
            return False
    return True
