"""Enrichment phase: turns a finished crawl into a company profile with verified/derived data.

Works without any API keys (DNS, RDAP, tech fingerprints, text mining, phone parsing, email
pattern inference, GitHub public API). Optional providers: Hunter.io (HUNTER_API_KEY) and an
OpenAI-compatible LLM (AI_API_KEY). Every value carries its source so it can be audited.
"""
from __future__ import annotations
import asyncio, os, re, time
from collections import Counter
from urllib.parse import urlparse

import httpx

from . import ai
from .urltools import domain as domain_of

ROLE_LOCALS = {"info", "contact", "hello", "sales", "support", "admin", "office", "hr", "careers", "jobs", "marketing", "press", "media", "billing",
               "accounts", "enquiries", "inquiries", "team", "help", "service", "business", "partners", "privacy", "legal", "noreply", "no-reply", "webmaster",
               "acquisitions", "investors", "ir", "finance", "procurement", "recruitment", "talent", "events", "community", "security", "compliance", "dpo", "customercare", "orders"}
SPF_SENDERS = [("_spf.google.com", "Google Workspace"), ("protection.outlook.com", "Microsoft 365"), ("amazonses.com", "Amazon SES"), ("sendgrid.net", "SendGrid"),
               ("mailgun.org", "Mailgun"), ("sendinblue.com", "Brevo"), ("brevo.com", "Brevo"), ("mcsv.net", "Mailchimp"), ("mandrillapp.com", "Mailchimp Transactional"),
               ("hubspotemail.net", "HubSpot"), ("_spf.salesforce.com", "Salesforce"), ("zendesk.com", "Zendesk"), ("freshdesk.com", "Freshdesk"), ("zcsend.net", "Zoho Campaigns"),
               ("zoho.com", "Zoho"), ("pphosted.com", "Proofpoint"), ("mimecast.com", "Mimecast"), ("postmarkapp.com", "Postmark"), ("sparkpostmail.com", "SparkPost"),
               ("intercom.io", "Intercom"), ("jobdiva.com", "JobDiva"), ("greenhouse.io", "Greenhouse"), ("atlassian.net", "Atlassian")]
DISPOSABLE = {"mailinator.com", "10minutemail.com", "guerrillamail.com", "tempmail.com", "yopmail.com", "trashmail.com", "sharklasers.com"}
FREE_MAIL = {"gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "live.com", "aol.com", "icloud.com", "proton.me", "protonmail.com", "gmx.com", "yandex.com", "zoho.com"}
MX_PROVIDERS = [("google.com", "Google Workspace"), ("googlemail.com", "Google Workspace"), ("outlook.com", "Microsoft 365"), ("protection.outlook", "Microsoft 365"),
                ("zoho", "Zoho Mail"), ("pphosted.com", "Proofpoint"), ("mimecast", "Mimecast"), ("secureserver.net", "GoDaddy"), ("messagingengine.com", "Fastmail"),
                ("mailgun", "Mailgun"), ("sendgrid", "SendGrid"), ("amazonses", "Amazon SES"), ("barracudanetworks", "Barracuda"), ("titan.email", "Titan"), ("hostinger", "Hostinger")]
TLD_REGION = {"pk": "PK", "uk": "GB", "de": "DE", "fr": "FR", "in": "IN", "ae": "AE", "sa": "SA", "au": "AU", "ca": "CA", "nl": "NL", "es": "ES", "it": "IT",
              "se": "SE", "no": "NO", "dk": "DK", "fi": "FI", "pl": "PL", "br": "BR", "mx": "MX", "sg": "SG", "nz": "NZ", "ie": "IE", "ch": "CH", "at": "AT", "be": "BE", "qa": "QA", "tr": "TR"}

COUNTRY_HINTS = [(r"pakistan|lahore|karachi|islamabad|rawalpindi|punjab|sindh", "PK"), (r"india|mumbai|delhi|bangalore|bengaluru|hyderabad|pune", "IN"),
                 (r"united states|usa|\b(TX|CA|NY|FL|WA|IL|MA|NJ|GA|VA)\b \d{5}", "US"), (r"united kingdom|\bUK\b|london|manchester", "GB"),
                 (r"germany|berlin|munich|hamburg", "DE"), (r"\bUAE\b|dubai|abu dhabi|emirates", "AE"), (r"saudi|\bKSA\b|riyadh|jeddah", "SA"),
                 (r"qatar|doha", "QA"), (r"canada|ontario|toronto|vancouver", "CA"), (r"australia|sydney|melbourne", "AU"), (r"singapore", "SG"),
                 (r"netherlands|amsterdam", "NL"), (r"france|paris", "FR"), (r"ireland|dublin", "IE"), (r"turkey|istanbul", "TR")]
FOUNDED_RE = re.compile(r"\b(?:founded|established|incorporated|started|launched|since|est\.?)(?:\s+[A-Z][\w&.-]*){0,3}?\s+(?:in\s+|back\s+in\s+)?((?:19[5-9]|20[0-4])\d)\b", re.I)
EMPLOYEES_RE = re.compile(r"\b(?:(?:workforce|team|family|headcount|strength)\s+of\s+(?:over\s+|more\s+than\s+|about\s+|around\s+|nearly\s+)?(\d{1,3}(?:,\d{3})+|\d{1,6})|"
                          r"(\d{1,3}(?:,\d{3})+|\d{1,6})\s*(\+|plus)?\s*(?:full[- ]time\s+|talented\s+|skilled\s+|dedicated\s+)?(employees|professionals|people|team members|staff|persons|workforce|engineers|experts|developers|specialists|consultants))\b", re.I)
STRONG_HEADCOUNT = {"employees", "professionals", "people", "team members", "staff", "persons", "workforce"}


def _val(field):
    v = field.get("value") if isinstance(field, dict) else field
    return None if v in (None, "", "Not Found", []) else v


# ---------------------------------------------------------------- network lookups
DOH = ["https://cloudflare-dns.com/dns-query", "https://dns.google/resolve"]


async def dns_query(name: str, rtype: str) -> list[str]:
    """DNS-over-HTTPS first (immune to broken local resolvers/VPN DNS), then the system resolver."""
    async with httpx.AsyncClient(timeout=6) as c:
        for url in DOH:
            try:
                r = await c.get(url, params={"name": name, "type": rtype}, headers={"Accept": "application/dns-json"})
                d = r.json()
                if d.get("Status") in (0, 3):  # NOERROR / NXDOMAIN are both authoritative answers
                    code = {"MX": 15, "TXT": 16, "A": 1}[rtype]
                    return [a["data"] for a in d.get("Answer", []) if a.get("type") == code]
            except Exception:
                continue
    try:
        import dns.asyncresolver
        res = dns.asyncresolver.Resolver(); res.lifetime = 8
        return [r.to_text() for r in await res.resolve(name, rtype)]
    except Exception:
        return []


async def dns_lookup(domain: str) -> dict:
    q = dns_query
    mx, txt, dmarc, a = await asyncio.gather(q(domain, "MX"), q(domain, "TXT"), q("_dmarc." + domain, "TXT"), q(domain, "A"))
    mx_hosts = [m.split()[-1].rstrip(".").lower() for m in mx]
    provider = next((name for key, name in MX_PROVIDERS for h in mx_hosts if key in h), "Self-hosted / other" if mx_hosts else None)
    spf = next((t.strip('"') for t in txt if "v=spf1" in t.lower()), None)
    dmarc_rec = next((t.replace('" "', "").strip('"') for t in dmarc if "v=dmarc1" in t.lower()), None)
    policy = re.search(r"\bp=(\w+)", dmarc_rec or "")
    verifications = sorted({m.group(1) for t in txt for m in [re.match(r'"?([a-z0-9-]+)-(?:site-)?verification', t, re.I)] if m})
    senders = list(dict.fromkeys(name for key, name in SPF_SENDERS if spf and key in spf.lower()))
    return {"mx": mx_hosts, "email_provider": provider, "email_senders": senders, "accepts_email": bool(mx_hosts), "spf": spf, "dmarc": dmarc_rec,
            "dmarc_policy": policy.group(1) if policy else None, "a_records": a, "txt_verifications": verifications}


async def rdap_lookup(domain: str) -> dict | None:
    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as c:
        r = await c.get(f"https://rdap.org/domain/{domain}", headers={"Accept": "application/rdap+json"})
        if r.status_code != 200: return None
        d = r.json()
    ev = {e.get("eventAction"): e.get("eventDate") for e in d.get("events", [])}
    registrar = None
    for ent in d.get("entities", []):
        if "registrar" in ent.get("roles", []):
            for item in (ent.get("vcardArray") or [None, []])[1]:
                if item[0] == "fn": registrar = item[3]
    created = ev.get("registration")
    age = None
    if created:
        try: age = round((time.time() - time.mktime(time.strptime(created[:10], "%Y-%m-%d"))) / 31557600, 1)
        except Exception: pass
    return {"registrar": registrar, "created": created, "expires": ev.get("expiration"), "updated": ev.get("last changed"), "age_years": age,
            "nameservers": [n.get("ldhName", "").lower() for n in d.get("nameservers", [])], "status": d.get("status", [])}


async def github_lookup(handle: str) -> dict | None:
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(f"https://api.github.com/users/{handle}", headers={"Accept": "application/vnd.github+json"})
        if r.status_code != 200: return None
        d = r.json()
    return {"login": d.get("login"), "name": d.get("name"), "public_repos": d.get("public_repos"), "followers": d.get("followers"),
            "created_at": d.get("created_at"), "blog": d.get("blog"), "location": d.get("location"), "url": d.get("html_url")}


async def hunter_lookup(domain: str) -> dict | None:
    key = os.getenv("HUNTER_API_KEY")
    if not key: return None
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.get("https://api.hunter.io/v2/domain-search", params={"domain": domain, "api_key": key, "limit": 25})
        r.raise_for_status(); d = r.json().get("data", {})
    return {"pattern": d.get("pattern"), "organization": d.get("organization"),
            "emails": [{"email": e.get("value"), "name": " ".join(x for x in (e.get("first_name"), e.get("last_name")) if x), "title": e.get("position"),
                        "confidence": e.get("confidence"), "linkedin": e.get("linkedin"), "verified": (e.get("verification") or {}).get("status")} for e in d.get("emails", [])]}


# ---------------------------------------------------------------- local derivations
def firmographics(pages: dict, description: str = "", seed: str = "") -> dict:
    founded = Counter(); employees = []; seen_ctx = set()
    sources = list(pages.items()) + ([(seed, {"text": description})] if description else [])
    for url, p in sources:
        t = p.get("text", "")
        for m in FOUNDED_RE.finditer(t):
            ctx = t[max(0, m.start() - 40):m.end()].lower()
            if ctx in seen_ctx: continue  # shared header/footer/testimonial text repeats on every page
            seen_ctx.add(ctx)
            # "founded in 2007" is much stronger evidence than "since 2013"
            founded[m.group(1)] += 3 if re.match(r"(founded|established|incorporated)", m.group(0), re.I) else 1
        for m in EMPLOYEES_RE.finditer(t):
            n = int((m.group(1) or m.group(2)).replace(",", ""))
            # "800 professionals" / "workforce of 800" describe the company; "150 experts" is usually a subset.
            strong = bool(m.group(1)) or (m.group(4) or "").lower() in STRONG_HEADCOUNT
            if 5 <= n <= 500000 and not (1950 <= n <= 2049): employees.append((strong, n, m.group(0), url))
    out = {"founded_year": None, "employees": None, "employee_range": None, "evidence": {}}
    if founded:
        yr, _ = founded.most_common(1)[0]; out["founded_year"] = int(yr); out["company_age_years"] = time.gmtime().tm_year - int(yr)
    if employees:
        strong, n, phrase, url = max(employees, key=lambda x: (x[0], x[1]))
        out["employees"] = n; out["evidence"]["employees"] = {"text": phrase, "url": url, "strength": "direct" if strong else "approximate (subset noun)"}
        out["employee_range"] = next(r for lim, r in [(10, "1-10"), (50, "11-50"), (200, "51-200"), (500, "201-500"), (1000, "501-1000"), (5000, "1001-5000"), (10000, "5001-10000"), (10**9, "10000+")] if n <= lim)
    return out


def classify_email(email: str, company_domain: str, mx_ok: dict) -> dict:
    local, _, dom = email.lower().partition("@")
    return {"email": email.lower(), "type": "role" if local.split(".")[0].split("+")[0] in ROLE_LOCALS else "personal",
            "on_company_domain": dom == company_domain or dom.endswith("." + company_domain), "free_provider": dom in FREE_MAIL,
            "disposable": dom in DISPOSABLE, "domain_accepts_mail": mx_ok.get(dom), "syntax_ok": bool(re.fullmatch(r"[^@\s]+@[^@\s]+\.[a-z]{2,}", email.lower()))}


def parse_phone(raw: str, region: str | None) -> dict:
    import phonenumbers
    from phonenumbers import geocoder, number_type, PhoneNumberType
    out = {"raw": raw, "e164": None, "valid": False, "country": None, "type": None}
    try:
        n = phonenumbers.parse(raw, region)
        out.update(valid=phonenumbers.is_valid_number(n), e164=phonenumbers.format_number(n, phonenumbers.PhoneNumberFormat.E164),
                   international=phonenumbers.format_number(n, phonenumbers.PhoneNumberFormat.INTERNATIONAL),
                   country=geocoder.region_code_for_number(n) or None, location=geocoder.description_for_number(n, "en") or None,
                   type={PhoneNumberType.MOBILE: "mobile", PhoneNumberType.FIXED_LINE: "landline", PhoneNumberType.FIXED_LINE_OR_MOBILE: "landline/mobile",
                         PhoneNumberType.TOLL_FREE: "toll-free", PhoneNumberType.VOIP: "voip"}.get(number_type(n), "other"))
    except Exception: pass
    return out


def _name_parts(name: str):
    parts = [re.sub(r"[^a-z]", "", p.lower()) for p in re.sub(r"^(dr|mr|ms|mrs)\.?\s+", "", name, flags=re.I).split()]
    parts = [p for p in parts if p]
    return (parts[0], parts[-1]) if len(parts) >= 2 else (None, None)


PATTERNS = {"{first}.{last}": lambda f, l: f"{f}.{l}", "{first}": lambda f, l: f, "{f}{last}": lambda f, l: f[0] + l, "{first}{last}": lambda f, l: f + l,
            "{first}_{last}": lambda f, l: f"{f}_{l}", "{f}.{last}": lambda f, l: f"{f[0]}.{l}", "{last}": lambda f, l: l, "{first}{l}": lambda f, l: f + l[0]}


def infer_pattern(people: list[dict], emails: list[str], company_domain: str) -> str | None:
    """Learn the company's address format from any published personal email that matches a known person."""
    locals_ = {e.split("@")[0].lower() for e in emails if e.lower().endswith("@" + company_domain)}
    hits = Counter()
    for p in people:
        f, l = _name_parts(p["name"])
        if not f: continue
        for pat, fn in PATTERNS.items():
            if fn(f, l) in locals_: hits[pat] += 1
    return hits.most_common(1)[0][0] if hits else None


def lead_score(profile: dict) -> dict:
    f = profile["company"]; c = profile["contacts"]
    checks = [
        ("Company name", 5, bool(f.get("company_name"))), ("Description", 5, bool(f.get("description"))),
        ("Business email", 10, any(e["on_company_domain"] for e in c["emails"])), ("Email domain receives mail", 5, bool(profile.get("dns", {}).get("accepts_email"))),
        ("Valid phone", 10, any(p["valid"] for p in c["phones"])), ("Office location", 10, bool(f.get("locations"))),
        ("Decision maker identified", 15, any(p.get("context") != "mentioned (possibly a client)" for p in profile["people"])), ("Decision maker email (found or pattern)", 10, any(p.get("email") or p.get("email_guess") for p in profile["people"])),
        ("Social profiles", 5, bool(profile["social"])), ("Tech stack", 5, bool(profile["tech_stack"])),
        ("Founded year", 5, bool(profile["firmographics"].get("founded_year"))), ("Employee count", 10, bool(profile["firmographics"].get("employees"))),
        ("Domain registration data", 5, bool(profile.get("domain"))),
    ]
    score = sum(w for _, w, ok in checks if ok)
    return {"score": score, "grade": "A" if score >= 80 else "B" if score >= 60 else "C" if score >= 40 else "D", "checks": [{"label": l, "weight": w, "ok": ok} for l, w, ok in checks]}


# ---------------------------------------------------------------- orchestrator
async def enrich_job(job: dict, progress=None, use_ai: bool = True) -> dict:
    t0 = time.perf_counter()
    def step(msg):
        if progress: progress(msg)
    seed = job["seed_url"]; dom = domain_of(seed); fields = job.get("fields", {}); pages = job.get("raw_pages", {})
    region = TLD_REGION.get(dom.rsplit(".", 1)[-1])
    errors = {}
    company = {k: _val(v) for k, v in fields.items()}

    step("DNS, RDAP and provider lookups")
    async def safe(name, coro):
        try: return await coro
        except Exception as e: errors[name] = f"{type(e).__name__}: {e}"[:300]; return None
    github_handle = None
    for s in company.get("social_links") or []:
        m = re.match(r"https?://(?:www\.)?github\.com/([A-Za-z0-9-]+)/?$", s)
        if m: github_handle = m.group(1); break
    dns_info, rdap, hunter, gh = await asyncio.gather(
        safe("dns", dns_lookup(dom)), safe("rdap", rdap_lookup(dom)), safe("hunter", hunter_lookup(dom)),
        safe("github", github_lookup(github_handle)) if github_handle else asyncio.sleep(0, None))

    step("Contacts and people")
    emails = []
    for p in pages.values(): emails.extend(p.get("emails", []))
    if company.get("business_email"): emails.append(company["business_email"])
    if hunter: emails.extend(e["email"] for e in hunter["emails"] if e.get("email"))
    emails = list(dict.fromkeys(e.lower() for e in emails if "@" in e and not re.search(r"\.(png|jpe?g|gif|svg|webp)$", e, re.I)))
    mx_ok = {dom: bool(dns_info and dns_info["accepts_email"])}
    for d in {e.split("@")[1] for e in emails} - set(mx_ok):
        r = await safe("dns:" + d, dns_lookup(d)); mx_ok[d] = bool(r and r["accepts_email"])
    email_rows = [classify_email(e, dom, mx_ok) for e in emails]
    email_rows.sort(key=lambda e: (not e["on_company_domain"], e["type"] != "role"))

    phones_raw = []
    for p in pages.values(): phones_raw.extend(p.get("phones", []))
    if company.get("phone"): phones_raw.insert(0, company["phone"])
    # Local-format numbers ("(042) 3749...") need a country: try the TLD, then countries named in the addresses.
    loc_text = " ".join(company.get("locations") or []) if isinstance(company.get("locations"), list) else str(company.get("locations") or "")
    regions = [region] + [code for rx, code in COUNTRY_HINTS if re.search(rx, loc_text, re.I)]
    regions = list(dict.fromkeys(r for r in regions if r)) or [None]
    phone_rows, seen = [], set()
    for ph in phones_raw:
        r = next((x for x in (parse_phone(ph, rg) for rg in regions) if x["valid"]), None) or parse_phone(ph, regions[0])
        key = r["e164"] or ph
        if key in seen: continue
        seen.add(key); phone_rows.append(r)
    phone_rows.sort(key=lambda p: not p["valid"])
    phone_rows = phone_rows[:15]

    people, seen = [], set()
    for url, p in pages.items():
        # People on testimonial/case-study/blog pages are usually clients or authors, not this company's staff.
        mention = bool(re.search(r"testimonial|case-stud|success-stor|/blog|/insight|/news|/press|review|/clients?\b|/work/", url, re.I))
        for name, title in p.get("people", []):
            if name.lower() in seen: continue
            seen.add(name.lower())
            people.append({"name": name, "title": title, "source_url": url, "source": "website",
                           "context": "mentioned (possibly a client)" if mention else "team" if p.get("category") in ("team", "about") or re.search(r"team|leader|about|management|people", url, re.I) else "website"})
    for e in (hunter or {}).get("emails", []):
        if e.get("name") and e["name"].lower() not in seen:
            seen.add(e["name"].lower()); people.append({"name": e["name"], "title": e.get("title"), "email": e["email"], "source": "hunter", "linkedin": e.get("linkedin")})
    rank = lambda t: 0 if re.search(r"\bceo\b|chief executive|founder|owner|president|managing director", t or "", re.I) else 1 if re.search(r"\bc[a-z]o\b|chief|vp|vice president|head|director", t or "", re.I) else 2
    ctx_rank = {"team": 0, "website": 1, None: 1, "mentioned (possibly a client)": 3}
    people.sort(key=lambda p: (ctx_rank.get(p.get("context"), 1), rank(p.get("title"))))
    pattern = (hunter or {}).get("pattern") or infer_pattern(people, emails, dom)  # Hunter uses the same "{first}.{last}" notation
    # Only guess addresses for people we are confident work here: a team/leadership page, or any
    # non-mention page when the site has no team page at all.
    has_team = any(p.get("context") == "team" for p in people)
    for p in people:
        f, l = _name_parts(p["name"])
        found = next((e for e in emails if f and e.endswith("@" + dom) and any(fn(f, l) == e.split("@")[0] for fn in PATTERNS.values())), None)
        guessable = p.get("context") == "team" or (p.get("context") == "website" and not has_team)
        if found and not p.get("email"): p["email"] = found; p["email_source"] = "website"
        elif not p.get("email") and f and mx_ok.get(dom) and guessable:
            if pattern and pattern in PATTERNS:
                p["email_guess"] = f"{PATTERNS[pattern](f, l)}@{dom}"; p["email_guess_confidence"] = "medium (pattern learned from published emails)"
            else:
                p["email_guess"] = f"{f}.{l}@{dom}"; p["email_guess_confidence"] = "low (most common pattern, unverified)"
            p["email_guess_alternatives"] = [f"{fn(f, l)}@{dom}" for fn in list(PATTERNS.values())[:4]]
    people = people[:40]

    social = {}
    for s in company.get("social_links") or []:
        host = (urlparse(s).hostname or "").lower().removeprefix("www.").removeprefix("pk.").removeprefix("uk.")
        net = {"linkedin.com": "linkedin", "facebook.com": "facebook", "instagram.com": "instagram", "twitter.com": "x", "x.com": "x", "youtube.com": "youtube", "github.com": "github"}.get(host)
        if net and net not in social: social[net] = {"url": s}
    if gh and "github" in social: social["github"].update(gh)

    step("Tech stack and firmographics")
    tech = {}
    for url, p in pages.items():
        for t in p.get("tech", []):
            row = tech.setdefault(t["name"], {"name": t["name"], "category": t["category"], "pages": 0, "evidence": t.get("evidence"), "source_url": url})
            row["pages"] += 1
    tech_rows = sorted(tech.values(), key=lambda t: (t["category"], t["name"]))
    firmo = firmographics(pages, str(company.get("description") or ""), seed)
    if company.get("locations"): firmo["headquarters"] = (company["locations"][0] if isinstance(company["locations"], list) else company["locations"])

    profile = {"domain_name": dom, "company": company, "dns": dns_info or {}, "domain": rdap, "firmographics": firmo, "tech_stack": tech_rows,
               "contacts": {"emails": email_rows, "phones": phone_rows}, "people": people, "email_pattern": pattern, "social": social,
               "providers": {"hunter": bool(hunter), "hunter_configured": bool(os.getenv("HUNTER_API_KEY")), "ai": use_ai and ai.configured(), "ai_configured": ai.configured()},
               "ai": None, "errors": errors}

    if use_ai and ai.configured():
        step("AI analysis")
        docs = [{"url": u, "markdown": (p.get("markdown") or p.get("text", ""))[:12000]} for u, p in list(pages.items())[:10]]
        r = await safe("ai", ai.chat_json(
            "You are a B2B research analyst. Use ONLY the supplied website evidence. Reply with a JSON object with keys: summary (2-3 sentences), industry, sub_industries (list), "
            "business_model (B2B/B2C/B2B2C/Marketplace/Other), target_customers (list), key_offerings (list), notable_clients (list), headquarters, company_size_estimate, "
            "value_proposition, competitors_mentioned (list). Use null or [] when the evidence does not say. Never invent people, emails or numbers.",
            {"company": company, "firmographics": firmo, "documents": docs}))
        profile["ai"] = r
    profile["lead_score"] = lead_score(profile)
    profile["elapsed_ms"] = int((time.perf_counter() - t0) * 1000)
    profile["enriched_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    step("done")
    return profile


def flatten(profile: dict | None) -> dict:
    """One row of enrichment columns for CSV/XLSX export."""
    if not profile or profile.get("status") == "running": return {}
    p = profile; f = p.get("firmographics", {}); d = p.get("domain") or {}; n = p.get("dns") or {}; a = p.get("ai") or {}
    ppl = p.get("people", [])
    top = ppl[0] if ppl else {}
    return {
        "lead_score": (p.get("lead_score") or {}).get("score"), "lead_grade": (p.get("lead_score") or {}).get("grade"),
        "founded_year": f.get("founded_year"), "employees": f.get("employees"), "employee_range": f.get("employee_range"), "headquarters": f.get("headquarters"),
        "domain_created": d.get("created"), "domain_age_years": d.get("age_years"), "registrar": d.get("registrar"),
        "email_provider": n.get("email_provider"), "mx_records": ", ".join(n.get("mx", [])), "spf": bool(n.get("spf")), "dmarc_policy": n.get("dmarc_policy"),
        "email_pattern": p.get("email_pattern"),
        "decision_maker": top.get("name"), "decision_maker_title": top.get("title"), "decision_maker_email": top.get("email") or top.get("email_guess"),
        "decision_maker_email_status": "found" if top.get("email") else (top.get("email_guess_confidence") or None) if top else None,
        "all_emails": ", ".join(e["email"] for e in p.get("contacts", {}).get("emails", [])),
        "phones_e164": ", ".join(x["e164"] for x in p.get("contacts", {}).get("phones", []) if x.get("valid")),
        "tech_stack": ", ".join(t["name"] for t in p.get("tech_stack", [])),
        **{f"social_{k}": v.get("url") for k, v in p.get("social", {}).items()},
        "ai_summary": a.get("summary"), "ai_industry": a.get("industry"), "ai_business_model": a.get("business_model"),
    }
