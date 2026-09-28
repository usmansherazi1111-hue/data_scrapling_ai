from __future__ import annotations
from urllib.parse import urljoin, urlparse, urlunparse, parse_qsl, urlencode
import re

TRACKING = {"utm_source","utm_medium","utm_campaign","utm_term","utm_content","gclid","fbclid","mc_cid","mc_eid"}
BAD_EXT = re.compile(r"\.(?:jpg|jpeg|png|gif|svg|webp|ico|mp4|mp3|wav|zip|rar|7z|pdf|docx?|xlsx?|pptx?|css|js|woff2?|ttf)(?:$|\?)", re.I)


def normalize_url(url: str) -> str:
    url = url.strip()
    if not url:
        return ""
    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url
    p = urlparse(url)
    query = [(k,v) for k,v in parse_qsl(p.query, keep_blank_values=True) if k.lower() not in TRACKING]
    path = p.path or "/"
    if path != "/":
        path = path.rstrip("/")
    return urlunparse((p.scheme.lower(), p.netloc.lower(), path, "", urlencode(query), ""))


def domain(url: str) -> str:
    host = urlparse(url).hostname or ""
    return host.lower().removeprefix("www.")


def same_domain(a: str, b: str) -> bool:
    return domain(a) == domain(b)


def canonical_link(base: str, href: str) -> str | None:
    if not href or href.startswith(("#", "mailto:", "tel:", "javascript:", "data:")):
        return None
    u = normalize_url(urljoin(base, href))
    if not u or BAD_EXT.search(urlparse(u).path):
        return None
    return u
