from __future__ import annotations
import json, re
from urllib.parse import urlparse
from .models import FieldResult, Evidence

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(r"(?<![\w/.])(?:\+\d{1,3}[ .-]?)?(?:\(\d{1,4}\)[ .-]?)?\d{2,4}(?:[ .-]\d{2,4}){1,4}(?![\w/])|(?<![\w/.])\+\d{8,14}(?!\w)")


def phones_in(raw_text):
    out=[]
    for line in raw_text.splitlines():
        for m in PHONE_RE.findall(line):
            digits=re.sub(r"\D","",m)
            if not 8<=len(digits)<=15: continue
            if re.fullmatch(r"(19|20)\d{2}[ .-](19|20)?\d{2}", m.strip()): continue  # year ranges
            if not (m.strip().startswith(("+","(")) or len(digits)>=10): continue
            out.append(m.strip())
    return out
SOCIAL_HOSTS = {"linkedin.com","facebook.com","instagram.com","twitter.com","x.com","youtube.com","github.com"}
GENERIC = {"home","about","about us","services","contact","contact us","learn more","read more","menu","team","careers","our services","site footer","footer","header","navigation","main menu","frequently asked questions","faq","faqs","how can we help","get in touch","let's talk","subscribe","newsletter","privacy policy","cookie policy","terms of service","skip to main content"}


def clean(v):
    if v is None: return ""
    return re.sub(r"\s+", " ", str(v)).strip()


MOJIBAKE_RE = re.compile(r"[ÃÂâ][\x80-\xbf€‚ƒ„…†‡ˆ‰Š‹ŒŽ‘’“”•–—˜™š›œžŸ]")


def _cp1252_bytes(line):
    # cp1252 leaves 0x81/0x8d/0x8f/0x90/0x9d undefined; those arrive as raw C1 chars or lone surrogates.
    out=bytearray()
    for ch in line:
        o=ord(ch)
        if 0xDC80<=o<=0xDCFF: out.append(o-0xDC00)
        elif o<0x100 and not 0x80<=o<=0x9f or o in (0x81,0x8d,0x8f,0x90,0x9d): out.append(o)
        else: out+=ch.encode("cp1252")
    return bytes(out)


def fix_mojibake(s):
    """Undo UTF-8 text that was decoded as cp1252 ("â€œ" -> "“"); leaves normal text alone."""
    if not s or not MOJIBAKE_RE.search(s): return s
    out=[]
    for line in s.split("\n"):
        if MOJIBAKE_RE.search(line):
            try: line=_cp1252_bytes(line).decode("utf-8")
            except (UnicodeEncodeError, UnicodeDecodeError): pass
        out.append(line)
    return "\n".join(out)


def raw_text(response):
    try:
        return fix_mojibake(str(response.get_all_text(separator="\n", strip=True, valid_values=True)))
    except Exception:
        return ""


def text(response):
    return clean(raw_text(response))


def meta(response, key, attr="name"):
    try:
        return clean(response.css(f'meta[{attr}="{key}"]::attr(content)').get(""))
    except Exception:
        return ""


def jsonld(response):
    out=[]
    try:
        for raw in response.css('script[type="application/ld+json"]::text').getall():
            try:
                obj=json.loads(raw)
                if isinstance(obj,list): out.extend(obj)
                elif isinstance(obj,dict) and isinstance(obj.get("@graph"),list): out.extend(obj["@graph"])
                elif isinstance(obj,dict): out.append(obj)
            except Exception: pass
    except Exception: pass
    return out


def all_strings(obj):
    vals=[]
    if isinstance(obj,dict):
        for v in obj.values(): vals.extend(all_strings(v))
    elif isinstance(obj,list):
        for v in obj: vals.extend(all_strings(v))
    elif isinstance(obj,str): vals.append(clean(obj))
    return [v for v in vals if v]


def choose(existing: FieldResult, value, url, source, confidence):
    if value is None: return
    if isinstance(value,str): value=clean(value)
    if not value: return
    if isinstance(value,list): value=[clean(x) for x in value if clean(x)]
    if not value: return
    existing.evidence.append(Evidence(value,url,source,confidence))
    if existing.value == "Not Found" or confidence > existing.confidence:
        existing.value=value; existing.confidence=confidence; existing.source_url=url; existing.source_type=source


def dedupe(values):
    out=[]; seen=set()
    for v in values:
        v=clean(v)
        if v and v.lower() not in seen and v.lower() not in GENERIC:
            seen.add(v.lower()); out.append(v)
    return out


ROLE_RE = re.compile(r"\b(co[- ]?founder|founder|ceo|chief executive|president|managing director|owner|chairman)\b", re.I)
CTA_RE = re.compile(r"\s*\b(read more|learn more|view case study|view more|see more|explore|discover more|know more|visit page|view all|see all)\b\s*[→»>]*\s*$", re.I)
NAME_RE = re.compile(r"^(?:(?:Dr|Mr|Ms|Mrs)\.?\s+)?[A-Z][\w'’.-]+(?:\s+[A-Z][\w'’.-]+){1,3}$")
CERT_RE = re.compile(r"\b(ISO(?:/IEC)?\s?\d{4,5}(?::\d{4})?|SOC\s?[12](?:\s?Type\s?(?:II|I|2|1))?|CMMI(?:\s?(?:Level|ML)\s?\d)?|HIPAA(?:\s?Compliant)?|GDPR(?:\s?Compliant)?|PCI[\s-]?DSS|"
                     r"(?:AWS|Microsoft|Google Cloud|Salesforce|Shopify|HubSpot|Odoo|Oracle|SAP)\s(?:Advanced|Select|Premier|Gold|Silver|Certified|Registered|Consulting)?\s?(?:Tier\s)?(?:Consulting\s|Services\s|Solutions\s)?Partner)\b", re.I)


NOT_NAME_WORDS = {"download","company","profile","celebration","celebrations","birthday","message","activity","activities","news","events","event",
    "welcome","contact","about","read","view","team","board","group","limited","ltd","pvt","private","industries","mills","textile","textiles",
    "products","services","home","our","the","of","and","vision","mission","history","overview","corporate","annual","report","gallery","career","careers","happy","christmas","eid","holiday","us",
    "apply","now","click","here","learn","more","get","started","join","watch","listen","explore","book","shop","order","buy","sign","login",
    "register","subscribe","follow","share","latest","new","exclusive","podcast","interview","quote","request","free","trial","demo"}
NOT_TITLE_RE = re.compile(r"[\"“”«»]|\b(message|messages|activity|speech|desk|note|profile|if i were)\b|'s\b|’s\b", re.I)


def looks_like_name(s):
    return bool(NAME_RE.match(s)) and not any(w.lower().strip(".") in NOT_NAME_WORDS for w in s.split())


def people_in(raw_text):
    """Find (name, title) pairs where a role line sits right after/before a name-shaped line."""
    lines=[clean(l) for l in raw_text.splitlines() if clean(l)]
    out=[]
    for i,l in enumerate(lines):
        if len(l)>80 or not ROLE_RE.search(l) or NOT_TITLE_RE.search(l): continue
        m=re.match(r"^(.{3,40}?)\s*[,|–—-]\s*(.+)$", l)
        if m and looks_like_name(m.group(1)) and ROLE_RE.search(m.group(2)):
            out.append((m.group(1),m.group(2))); continue
        # Some sites split first/last name into separate elements ("Bilal" / "Mahmood" / "Managing Director").
        if i>=2 and re.fullmatch(r"[A-Z][\w'’.-]+",lines[i-1]) and re.fullmatch(r"[A-Z][\w'’.-]+",lines[i-2]) and looks_like_name(f"{lines[i-2]} {lines[i-1]}"):
            out.append((f"{lines[i-2]} {lines[i-1]}",l)); continue
        for j in (i-1,i+1):
            if 0<=j<len(lines) and len(lines[j])<=40 and looks_like_name(lines[j]) and not ROLE_RE.search(lines[j]):
                out.append((lines[j],l)); break
    # Titles often start with a bullet or a broken character ("� CEO."); keep the words only.
    return [(n, t.strip(" �•·–—-|:,.")) for n, t in out]


ADDRESS_HINT = re.compile(r"\b(floor|suite|road|rd|street|st|avenue|ave|block|tower|towers|building|plaza|centre|center|blvd|boulevard|drive|dr|lane|highway|sector|p\.?o\.? box)\b\.?", re.I)


def addresses_in(raw_text):
    """Fallback when the page has no <address> tag: comma-separated lines with a street hint and a number."""
    out=[]; merged=[]
    for l in raw_text.splitlines():
        l=clean(l)
        if merged and merged[-1].endswith((",","–","-")) and l: merged[-1]+=" "+l
        elif l: merged.append(l)
    for l in merged:
        if 15<=len(l)<=220 and l.count(",")>=2 and re.search(r"\d",l) and ADDRESS_HINT.search(l) and not EMAIL_RE.search(l):
            out.append(l)
    return out


def extract_page(response, url, category):
    rt=raw_text(response)
    result={"emails":[],"phones":[],"socials":[],"jsonld":jsonld(response),"headings":[],"text":clean(rt)}
    try: result["title"]=clean(response.css("title::text").get(""))
    except Exception: result["title"]=""
    try: result["headings"]=dedupe(response.css("h1::text,h2::text,h3::text").getall())
    except Exception: pass
    result["description"]=meta(response,"description") or meta(response,"og:description","property")
    try:
        result["site_name"]=meta(response,"og:site_name","property")
    except Exception: result["site_name"]=""
    result["emails"]=dedupe(EMAIL_RE.findall(result["text"]))
    try:
        for href in response.css('a[href^="mailto:"]::attr(href)').getall():
            e=href.split(":",1)[-1].split("?",1)[0]
            if e: result["emails"].append(e.lower())
    except Exception: pass
    result["emails"]=dedupe(result["emails"])
    result["phones"]=phones_in(rt)
    try:
        for href in response.css('a[href^="tel:"]::attr(href)').getall():
            result["phones"].insert(0,href.split(":",1)[-1].strip())
    except Exception: pass
    result["phones"]=dedupe(result["phones"])[:20]
    try:
        for href in response.css("a::attr(href)").getall():
            host=urlparse(href).hostname or ""
            if any(x in host.lower() for x in SOCIAL_HOSTS): result["socials"].append(href)
    except Exception: pass
    result["socials"]=dedupe(result["socials"])
    result["people"]=people_in(rt)
    result["certifications"]=dedupe(m.group(0) for m in CERT_RE.finditer(result["text"]))
    result["addresses"]=[]
    try:
        for el in response.css("address"):
            lines=[clean(x) for x in str(el.get_all_text(separator="\n",strip=True)).splitlines()]
            lines=[x for x in lines if x and not phones_in(x) and not EMAIL_RE.search(x) and not re.fullmatch(r"[\d\s()+./-]{7,}",x)]
            if lines: result["addresses"].append(", ".join(lines))
    except Exception: pass
    if not result["addresses"] and category in ("contact","about","homepage"):
        result["addresses"]=addresses_in(rt)[:10]
    result["addresses"]=dedupe(result["addresses"])
    result["links"]=[]
    try:
        from .urltools import canonical_link, same_domain
        for a in response.css("a"):
            u=canonical_link(url,a.attrib.get("href",""))
            if u and same_domain(url,u):
                # Card-style links: prefer the inner heading over the whole card text.
                t=""
                for h in a.css("h1,h2,h3,h4,h5,h6,strong"):
                    t=clean(h.get_all_text(strip=True))
                    if t: break
                if not t:
                    parts=[clean(x) for x in str(a.get_all_text(separator="\n",strip=True)).splitlines() if clean(x)]
                    t=(parts[0] if parts else "") or clean(a.attrib.get("title",""))
                t=CTA_RE.sub("",t).strip(" →»>-|")
                if t: result["links"].append((u,t))
    except Exception: pass
    result["logos"]=[]
    try:
        for alt in response.css('[class*="client" i] img::attr(alt), [class*="logo" i] img::attr(alt), [id*="client" i] img::attr(alt)').getall():
            alt=re.sub(r"(?i)\s*(logo|image|icon)\s*$","",clean(alt))
            if 2<len(alt)<50: result["logos"].append(alt)
    except Exception: pass
    result["logos"]=dedupe(result["logos"])
    return result


def child_link_names(pages, category):
    """Anchor texts of links nested under a section page's path, e.g. /industries/healthcare -> 'Healthcare'."""
    from urllib.parse import urlparse
    roots=[urlparse(p["url"]).path.rstrip("/") for p in pages if p["category"]==category]
    roots=[r for r in roots if r]
    names=[]; src=""
    for p in pages:
        for u,t in p["extracted"].get("links",[]):
            path=urlparse(u).path
            if any(path.startswith(r+"/") for r in roots) and 2<len(t)<60 and len(t.split())<=6:
                names.append(t); src=src or p["url"]
    return dedupe(names), src


def aggregate(pages, seed):
    fields={k:FieldResult() for k in ["company_name","description","founder_ceo","business_email","ceo_email","phone","website","locations","services","industries","technologies","clients","case_studies","certifications","social_links"]}
    emails=[]; phones=[]; socials=[]; headings=[]; descriptions=[]; locations=[]; certs=[]; logos=[]
    for p in pages:
        u=p["url"]; x=p["extracted"]; cat=p["category"]
        for e in x.get("emails",[]): emails.append((e,u,0.95))
        for ph in x.get("phones",[]): phones.append((ph,u,0.90))
        socials.extend(x.get("socials",[])); headings.extend(x.get("headings",[]))
        if x.get("description"): descriptions.append((x["description"],u,0.92))
        for obj in x.get("jsonld",[]):
            typ=str(obj.get("@type","")).lower() if isinstance(obj,dict) else ""
            if isinstance(obj,dict):
                if typ in {"organization","corporation","localbusiness","professionalservice"} or "organization" in typ:
                    choose(fields["company_name"],obj.get("name"),u,"jsonld",0.99)
                    choose(fields["website"],obj.get("url"),u,"jsonld",0.99)
                    choose(fields["business_email"],obj.get("email"),u,"jsonld",0.99)
                    choose(fields["phone"],obj.get("telephone"),u,"jsonld",0.99)
                    choose(fields["founder_ceo"],obj.get("founder"),u,"jsonld",0.96)
                    choose(fields["description"],obj.get("description"),u,"jsonld",0.96)
                    addr=obj.get("address")
                    if isinstance(addr,dict): choose(fields["locations"], ", ".join(str(addr.get(k,"")) for k in ["streetAddress","addressLocality","addressRegion","postalCode","addressCountry"] if addr.get(k)), u,"jsonld",0.94)
        if cat=="about":
            if x.get("headings"): choose(fields["company_name"],x["headings"][0],u,"heading",0.65)
        if cat in {"services","industries","technology","case_studies"}:
            vals=[h for h in x.get("headings",[]) if len(h)<80][:25]
            key={"services":"services","industries":"industries","technology":"technologies","case_studies":"case_studies"}[cat]
            choose(fields[key],vals,u,"heading",0.55)
        for name,title in x.get("people",[]):
            conf=0.9 if re.search(r"\bceo\b|chief executive",title,re.I) else 0.8 if re.search(r"founder",title,re.I) else 0.6
            if cat=="team": conf+=0.05
            choose(fields["founder_ceo"],f"{name} ({title})",u,"people",conf)
        for addr in x.get("addresses",[]): locations.append((addr,u))
        for c in x.get("certifications",[]): certs.append((c,u))
        for l in x.get("logos",[]): logos.append((l,u))
    if fields["company_name"].value=="Not Found":
        for p in pages:
            x=p["extracted"]; choose(fields["company_name"],x.get("site_name"),p["url"],"og:site_name",0.95); choose(fields["company_name"],x.get("title","").split("|")[0].split("-")[0],p["url"],"title",0.60)
    for key,cat in (("services","services"),("industries","industries"),("technologies","technology"),("case_studies","case_studies")):
        names,src=child_link_names(pages,cat)
        if len(names)>=2: choose(fields[key],names[:40],src,"section_links",0.8)
    if locations: choose(fields["locations"],dedupe(a for a,_ in locations),locations[0][1],"address",0.9)
    if certs: choose(fields["certifications"],dedupe(c for c,_ in certs),certs[0][1],"text",0.8)
    name=str(fields["company_name"].value).lower()
    logos=[(l,u) for l,u in logos if l.lower()!=name and name not in l.lower()]
    if len(logos)>=2: choose(fields["clients"],dedupe(l for l,_ in logos)[:40],logos[0][1],"logo_alt",0.6)
    if descriptions: choose(fields["description"],max(descriptions,key=lambda x: x[2])[0],max(descriptions,key=lambda x: x[2])[1],"meta",0.92)
    if emails:
        generic=[e for e in emails if re.match(r"(info|contact|hello|sales|business|inquir|enquir)",e[0],re.I)]
        best=(generic or emails)[0]
        choose(fields["business_email"],best[0],best[1],"email",best[2])
        ceo=fields["founder_ceo"].value
        if ceo!="Not Found":
            parts=[p.lower() for p in re.findall(r"[A-Za-z]{3,}",str(ceo).split("(")[0])]
            for e,u,_ in emails:
                local=e.split("@")[0].lower()
                if any(p in local for p in parts): choose(fields["ceo_email"],e,u,"email_match",0.7); break
    if phones: choose(fields["phone"],phones[0][0],phones[0][1],"phone",phones[0][2])
    choose(fields["website"],seed,seed,"seed_url",1.0)
    choose(fields["social_links"],dedupe(socials),seed,"links",0.75)
    return fields
