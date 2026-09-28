"""Lightweight tech-stack fingerprinting from response headers and HTML (Wappalyzer-style signatures)."""
from __future__ import annotations
import re

# (name, category, pattern searched in "headers + html")
SIGNATURES = [
    # CMS / site builders
    ("WordPress", "CMS", r"/wp-content/|/wp-includes/|<meta name=\"generator\" content=\"WordPress"),
    ("Drupal", "CMS", r"Drupal\.settings|/sites/default/files/|x-drupal-cache|x-generator: drupal"),
    ("Joomla", "CMS", r"/media/jui/|<meta name=\"generator\" content=\"Joomla"),
    ("Webflow", "CMS", r"webflow\.com|data-wf-page|data-wf-site"),
    ("Wix", "CMS", r"static\.wixstatic\.com|x-wix-request-id"),
    ("Squarespace", "CMS", r"static1\.squarespace\.com|squarespace-cdn"),
    ("Ghost", "CMS", r"<meta name=\"generator\" content=\"Ghost"),
    ("HubSpot CMS", "CMS", r"x-hs-hub-id|hs-sites\.com|\.hubspotusercontent"),
    ("Contentful", "CMS", r"ctfassets\.net"),
    ("Sanity", "CMS", r"cdn\.sanity\.io"),
    ("Strapi", "CMS", r"x-powered-by: strapi"),
    # E-commerce
    ("Shopify", "E-commerce", r"cdn\.shopify\.com|x-shopify-stage|Shopify\.theme"),
    ("WooCommerce", "E-commerce", r"woocommerce"),
    ("Magento", "E-commerce", r"Mage\.Cookies|/static/version\d+/frontend/|x-magento"),
    ("BigCommerce", "E-commerce", r"cdn\d*\.bigcommerce\.com"),
    ("Stripe", "Payments", r"js\.stripe\.com"),
    ("PayPal", "Payments", r"paypal\.com/sdk/js|paypalobjects\.com"),
    # JS frameworks
    ("Next.js", "JavaScript framework", r"/_next/static/|__NEXT_DATA__|x-powered-by: next\.js"),
    ("Nuxt", "JavaScript framework", r"/_nuxt/|__NUXT__"),
    ("React", "JavaScript framework", r"data-reactroot|react-dom(\.production)?(\.min)?\.js|__NEXT_DATA__|_reactListening"),
    ("Vue.js", "JavaScript framework", r"data-v-[0-9a-f]{8}|vue(\.runtime)?(\.global)?(\.prod)?(\.min)?\.js"),
    ("Angular", "JavaScript framework", r"ng-version=|ng-app="),
    ("Svelte", "JavaScript framework", r"class=\"[^\"]*svelte-[a-z0-9]+"),
    ("Gatsby", "JavaScript framework", r"___gatsby|/page-data/"),
    ("Astro", "JavaScript framework", r"astro-island|data-astro-cid"),
    ("jQuery", "JavaScript library", r"jquery(\.min)?(-\d[\d.]*)?\.js|jquery/\d"),
    ("Alpine.js", "JavaScript library", r"x-data=|alpinejs"),
    ("GSAP", "JavaScript library", r"gsap(\.min)?\.js|/gsap@"),
    # CSS
    ("Bootstrap", "UI framework", r"bootstrap(\.min)?\.(css|js)"),
    ("Tailwind CSS", "UI framework", r"tailwindcss|--tw-"),
    # Servers / hosting / CDN
    ("Cloudflare", "CDN", r"server: cloudflare|cf-ray:|/cdn-cgi/"),
    ("Akamai", "CDN", r"x-akamai|akamaihd\.net|akamaized\.net"),
    ("Fastly", "CDN", r"x-served-by: cache-|x-fastly|fastly-debug"),
    ("Amazon CloudFront", "CDN", r"x-amz-cf-id|cloudfront\.net"),
    ("Vercel", "Hosting", r"x-vercel-id|server: vercel"),
    ("Netlify", "Hosting", r"x-nf-request-id|server: netlify"),
    ("AWS", "Hosting", r"amazonaws\.com|x-amz-"),
    ("Google Cloud", "Hosting", r"server: gws|googleusercontent\.com|x-goog-"),
    ("WP Engine", "Hosting", r"x-powered-by: wp engine|wpengine"),
    ("Nginx", "Web server", r"server: nginx"),
    ("Apache", "Web server", r"server: apache"),
    ("Microsoft IIS", "Web server", r"server: microsoft-iis"),
    ("LiteSpeed", "Web server", r"server: litespeed"),
    ("PHP", "Language", r"x-powered-by: php|\.php[\"'?]"),
    ("ASP.NET", "Language", r"x-powered-by: asp\.net|__VIEWSTATE|x-aspnet-version"),
    ("Express", "Language", r"x-powered-by: express"),
    # Analytics / marketing
    ("Google Analytics", "Analytics", r"google-analytics\.com|gtag\(|googletagmanager\.com/gtag/js"),
    ("Google Tag Manager", "Tag manager", r"googletagmanager\.com/gtm\.js|GTM-[A-Z0-9]{4,}"),
    ("Meta Pixel", "Advertising", r"connect\.facebook\.net/[^\"']*/fbevents\.js|fbq\("),
    ("LinkedIn Insight", "Advertising", r"snap\.licdn\.com|_linkedin_partner_id"),
    ("Google Ads", "Advertising", r"googleadservices\.com|AW-\d{6,}"),
    ("Hotjar", "Analytics", r"static\.hotjar\.com|hjSiteSettings"),
    ("Microsoft Clarity", "Analytics", r"clarity\.ms"),
    ("Mixpanel", "Analytics", r"cdn\.mxpnl\.com|mixpanel\.init"),
    ("Segment", "Analytics", r"cdn\.segment\.com"),
    ("Amplitude", "Analytics", r"cdn\.amplitude\.com"),
    ("HubSpot", "Marketing automation", r"js\.hs-scripts\.com|js\.hsforms\.net|_hsq"),
    ("Marketo", "Marketing automation", r"munchkin\.marketo\.net|mktoForms"),
    ("Pardot", "Marketing automation", r"pi\.pardot\.com"),
    ("Mailchimp", "Email marketing", r"chimpstatic\.com|list-manage\.com"),
    ("Salesforce", "CRM", r"\.force\.com|\.my\.salesforce\.com|salesforce\.com/embeddedservice"),
    # Support / chat
    ("Intercom", "Live chat", r"widget\.intercom\.io|intercomSettings"),
    ("Drift", "Live chat", r"js\.driftt\.com"),
    ("Zendesk", "Customer support", r"static\.zdassets\.com|\.zendesk\.com"),
    ("Tawk.to", "Live chat", r"embed\.tawk\.to"),
    ("Crisp", "Live chat", r"client\.crisp\.chat"),
    ("LiveChat", "Live chat", r"cdn\.livechatinc\.com"),
    ("Freshchat", "Live chat", r"wchat\.freshchat\.com"),
    # Misc
    ("Google reCAPTCHA", "Security", r"google\.com/recaptcha|recaptcha/api\.js"),
    ("hCaptcha", "Security", r"hcaptcha\.com/1/api\.js"),
    ("Cookiebot", "Cookie compliance", r"consent\.cookiebot\.com"),
    ("OneTrust", "Cookie compliance", r"cdn\.cookielaw\.org|optanon"),
    ("Google Fonts", "Font", r"fonts\.googleapis\.com"),
    ("Font Awesome", "Font", r"font-awesome|fontawesome"),
    ("Typekit", "Font", r"use\.typekit\.net"),
    ("YouTube embed", "Video", r"youtube\.com/embed/"),
    ("Vimeo", "Video", r"player\.vimeo\.com"),
    ("Calendly", "Scheduling", r"assets\.calendly\.com|calendly\.com/"),
    ("Greenhouse", "Recruiting", r"boards\.greenhouse\.io|greenhouse\.io/embed"),
    ("Lever", "Recruiting", r"jobs\.lever\.co"),
    ("Workable", "Recruiting", r"apply\.workable\.com"),
]
_COMPILED = [(n, c, re.compile(p, re.I)) for n, c, p in SIGNATURES]


def detect(response, html: str) -> list[dict]:
    try: headers = "\n".join(f"{k}: {v}" for k, v in dict(response.headers or {}).items())
    except Exception: headers = ""
    hay = headers.lower() + "\n" + html[:600_000]
    out = []
    for name, cat, rx in _COMPILED:
        m = rx.search(hay)
        if m: out.append({"name": name, "category": cat, "evidence": m.group(0)[:80]})
    return out
