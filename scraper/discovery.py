from __future__ import annotations
from urllib.parse import urlparse
from .urltools import canonical_link, same_domain

KEYWORDS = {
    "about": ["about", "company", "who-we-are", "our-story", "overview"],
    "services": ["services", "solutions", "what-we-do", "capabilities"],
    "industries": ["industries", "industry", "expertise", "verticals"],
    "technology": ["technology", "technologies", "tech-stack", "stack", "platform"],
    "team": ["team", "leadership", "people", "management"],
    "contact": ["contact", "locations", "offices"],
    "case_studies": ["case-study", "case-studies", "portfolio", "work", "clients"],
    "careers": ["career", "careers", "jobs"],
}


def classify(url: str, anchor: str) -> tuple[str, float]:
    # Most specific signal first: last path segment, then the full path, then the anchor text.
    path = urlparse(url).path.lower()
    last = path.rstrip("/").rsplit("/", 1)[-1]
    # Long slugs are usually articles ("...-is-not-a-technology-decision"), not section pages.
    slug_ok = len(last.split("-")) <= 3
    for text, score in ((last if slug_ok else "", 0.95), (path if slug_ok else "", 0.90), (anchor.lower() if len(anchor) <= 40 else "", 0.82)):
        if not text:
            continue
        for category, words in KEYWORDS.items():
            if any(word in text for word in words):
                return category, score
    return "other", 0.20


def discover(response, seed: str, limit: int) -> list[tuple[str, str, float]]:
    found: dict[str, tuple[str, float]] = {}
    try:
        anchors = response.css("a")
        for a in anchors:
            href = a.attrib.get("href", "")
            u = canonical_link(seed, href)
            if not u or not same_domain(seed, u):
                continue
            if seed.startswith("https://") and u.startswith("http://"):
                u = "https://" + u[len("http://"):]  # same site; avoid crawling both schemes
            try:
                text = " ".join(a.css("::text").getall()).strip()
            except Exception:
                text = ""
            category, score = classify(u, text)
            old = found.get(u)
            if old is None or score > old[1]:
                found[u] = (category, score)
    except Exception:
        return []
    # Round-robin across categories so one section (e.g. /about/*) can't eat the whole page budget.
    buckets: dict[str, list[tuple[str, str, float]]] = {}
    for u, (c, s) in sorted(found.items(), key=lambda x: (-x[1][1], len(x[0]), x[0])):
        buckets.setdefault(c, []).append((u, c, s))
    order = [c for c in KEYWORDS if c in buckets] + (["other"] if "other" in buckets else [])
    ranked: list[tuple[str, str, float]] = []
    while len(ranked) < limit and any(buckets[c] for c in order):
        for c in order:
            if buckets[c]:
                ranked.append(buckets[c].pop(0))
    return ranked[:limit]
