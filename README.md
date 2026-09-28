# Scrapling Studio

A local web app for crawling company websites, extracting structured data, and enriching it into a lead profile. Built on [Scrapling](https://github.com/D4Vinci/Scrapling) 0.4.x, FastAPI and Playwright.

**What it does**

- **Crawl** a site with fast HTTP, a real browser, or a stealth browser. Auto mode starts fast and escalates only when a page is blocked or needs JavaScript.
- **Extract** company name, description, CEO/founders, emails, phones, office addresses, services, industries, case studies, certifications and social links. Each value comes with its confidence and source page.
- **Enrich** with domain age and registrar, mail provider, SPF/DMARC, email deliverability, normalised phone numbers, decision makers with email patterns, founded year, headcount, tech stack and a 0–100 lead score.
- **Handle access**: detects CAPTCHAs, bot walls and login pages, and saves logged-in sessions as auth profiles. Also supports proxy rotation, request delays and robots.txt.
- **Export** to CSV, XLSX (one sheet each for company, people, emails, phones, tech stack and pages) and JSON. Crawl history is kept in SQLite.

---

## Requirements

| | |
|---|---|
| OS | Windows 10/11, macOS 12+, or Linux (Ubuntu 22.04+ tested by Playwright) |
| Python | **3.10, 3.11 or 3.12** ([python.org](https://www.python.org/downloads/)). Tested with 3.11. |
| Disk | About 1.5 GB (Python packages plus the Chromium browsers) |
| RAM | 2 GB free (browser modes launch Chromium) |
| Network | Internet access to the sites you crawl |

## Quick start

### Windows

1. Install Python 3.11 from python.org (tick **Add python.exe to PATH**).
2. Get the code:
   ```bash
   git clone <your-repo-url> scrapling-studio
   ```
   (or download the ZIP and extract it)
3. Double-click **`setup.bat`**. It creates `.venv`, installs the packages, downloads the browsers and creates `.env`. This takes 3–10 minutes.
4. Double-click **`run.bat`**. The app opens at <http://127.0.0.1:8000>.

### macOS / Linux

```bash
git clone <your-repo-url> scrapling-studio
cd scrapling-studio
chmod +x setup.sh && ./setup.sh
.venv/bin/python main.py
```

Then open <http://127.0.0.1:8000>.

On Linux, if the browser modes fail with missing libraries, install Chromium's system dependencies once:

```bash
sudo .venv/bin/python -m playwright install-deps chromium
```

### Manual setup (any OS)

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate     macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
scrapling install          # downloads Chromium for the browser modes
cp .env.example .env       # Windows: copy .env.example .env
python main.py
```

> **Start the app with `python main.py`, not `uvicorn main:app --reload`.** On Windows, reload mode switches to an event loop that cannot launch browsers, so the Dynamic, Stealth and Auto-fallback modes would fail. `main.py` sets the correct loop on every OS.

---

## Configuration (`.env`)

| Variable | Default | Purpose |
|---|---|---|
| `APP_PASSWORD` | *(empty)* | Password for the web UI. **Required before sharing the app.** If empty, no login is asked. |
| `HOST` / `PORT` | `127.0.0.1` / `8000` | Where the server listens. Use `0.0.0.0` to allow other devices on your network (set a password first). |
| `DB_PATH` | `./data/scrapling.db` | Crawl history database. Auth profiles are stored next to it in `data/profiles/`. |
| `AI_API_KEY` | *(empty)* | Optional. Any OpenAI-compatible key. Adds industry, summary, business model, target customers and offerings. |
| `AI_BASE_URL` / `AI_MODEL` | OpenAI / `gpt-4.1-mini` | Endpoint and model for the AI step. |
| `HUNTER_API_KEY` | *(empty)* | Optional. Hunter.io key for verified staff emails. |
| `PLAYWRIGHT_BROWSERS_PATH` | *(unset)* | Optional. Where browsers are installed. Set it **before** `scrapling install` if your system drive is short on space, e.g. `D:/scrapling/browsers`. |

Restart the app after editing `.env`.

---

## Using the app

1. Enter a company URL and click **Start crawl**. Auto mode and 20 pages at depth 2 are a good default.
2. Watch the **Crawl activity** list. The mode column shows whether each page used HTTP, a browser or the stealth browser.
3. When it finishes, the **Enriched profile** tab shows the lead score, company facts, domain and email setup, people, verified contacts, tech stack and social profiles. **Extracted fields** shows the raw values with confidence and source.
4. Export with **XLSX / CSV / JSON**, or reopen past crawls from **History**. **Re-run enrichment** refreshes an old crawl.

### Blocked sites, CAPTCHAs and logins

The app does **not** solve CAPTCHAs automatically. When a page is blocked, a banner appears:

1. Click **Open browser to solve**. A real Chromium window opens on the computer running the app.
2. Log in or complete the check yourself, then click **Finish & save**. The cookies are saved as an auth profile.
3. Click **Re-run with profile**, or pick the profile under **Access options** for any crawl.

You can also create a profile by importing cookies exported from your own browser (JSON, `cookies.txt` or `name=value; …`) in **Access & profiles**. Cookies are stored in plain text under `data/profiles/`, so keep that folder private.

---

## Sharing a public link (Windows)

1. Set `APP_PASSWORD` in `.env`.
2. Install Cloudflare's tunnel client:
   ```bash
   winget install Cloudflare.cloudflared
   ```
   Or save `cloudflared-windows-amd64.exe` from the [releases page](https://github.com/cloudflare/cloudflared/releases) as `bin\cloudflared.exe` inside the project.
3. Double-click **`share.bat`**. It starts the app, prints a public `https://….trycloudflare.com` link in green, and keeps it online while the window is open.

The link changes every time `share.bat` runs, and it only works while this computer is on. On a public link:
- People must sign in with `APP_PASSWORD`.
- Crawls of local and private-network addresses are refused.
- **Open browser** only works on the host computer; remote users import cookies instead.

On macOS/Linux, run `cloudflared tunnel --url http://127.0.0.1:8000` after starting the app.

---

## Project structure

```
main.py               FastAPI app: API routes, login, exports, entry point
scraper/
  engine.py           Crawl loop: fetch ladder, sessions, proxies, robots.txt, block detection
  discovery.py        Link discovery and page classification (about/services/team/…)
  extract.py          Field extraction (JSON-LD, meta, people, addresses, phones, certifications)
  enrichment.py       Enrichment: DNS/RDAP, contacts, people and email patterns, firmographics, lead score
  techdetect.py       Tech-stack fingerprints from headers and HTML
  antibot.py          CAPTCHA/bot-wall detection, robots.txt, proxy parsing
  auth.py             Auth profiles and the interactive login browser
  security.py         Password login, private-address guard, local-only actions
  ai.py               Optional OpenAI-compatible client
  db.py, models.py, urltools.py
static/               Web UI (index.html, app.js, app.css)
setup.bat / setup.sh  One-time setup
run.bat               Start the app (Windows)
share.bat / share.ps1 Start the app with a public link (Windows)
```

`data/`, `.env` and `.venv/` are created locally and are not committed.

---

## Troubleshooting

| Problem | Fix |
|---|---|
| Dynamic/Stealth mode fails with `NotImplementedError` | You started with `uvicorn --reload`. Use `python main.py`. |
| `Executable doesn't exist … chromium` | Browsers are not installed. Run `scrapling install` inside the venv. If you set `PLAYWRIGHT_BROWSERS_PATH`, it must also be in `.env`. |
| `No space left on device` during setup | Point `TMP`, `TEMP` and `PLAYWRIGHT_BROWSERS_PATH` at a drive with space, then re-run setup. |
| Every page shows `TURNSTILE`, `DATADOME` or `CLOUDFLARE` | The site blocks bots. Use **Open browser to solve** and re-run with the profile. |
| Pages skipped with "Disallowed by robots.txt" | The site asks crawlers not to fetch them. Untick **Respect robots.txt** only if you have permission. |
| Enrichment shows no mail provider / DNS data | DNS lookups use DNS-over-HTTPS (Cloudflare, Google). Check that the machine can reach them. |
| `share.bat` says it could not get a tunnel | Cloudflare's free tunnel service is occasionally slow. Wait a minute and run it again. |
| Port 8000 already in use | Set `PORT=8001` in `.env`, or stop the other process. |

## Responsible use

Crawl only sites you are allowed to access. Respect robots.txt and the sites' terms, and use polite delays on small sites. Email addresses marked **guess** are unverified patterns: verify them before use and comply with the anti-spam and data-protection laws that apply to you (GDPR, CAN-SPAM, etc.).
