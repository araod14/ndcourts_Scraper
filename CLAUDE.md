# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Setup

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
playwright install chromium
```

Optional stealth enhancements (any combination):
```bash
pip install camoufox            # Firefox-based stealth browser (strongly recommended)
pip install rebrowser-playwright # Chromium with CDP-leak patches
pip install playwright-stealth  # Extra fingerprint overrides for Chromium
```

## Running the scraper

```bash
source venv/bin/activate
python scraper.py          # Runs yesterday's Misdemeanor + Felony searches and emails results
bash run_scraper.sh        # Wrapper script (activates venv, runs scraper.py)
```

Configure via `.env` (see below). Use `HEADLESS=false` to watch the browser while debugging.

## Architecture

Single-file scraper (`scraper.py`) targeting `https://publicsearch.ndcourts.gov/Search.aspx?ID=100`.

**Flow (search_by_date — primary daily mode):**
1. `NDCourtsScraper.search_by_date()` launches a stealth browser context via `_build_context()`
2. Navigates home → selects "State of North Dakota" → clicks "Criminal\Traffic" link
3. Resolves Cloudflare Managed Challenge (Turnstile) if detected via `_solve_cloudflare_challenge()`
4. `_fill_date_field_search()` clicks `#DateFiled` radio (triggers ASP.NET postback), fills date fields via JS, then downloads/solves the fresh CAPTCHA image **after all postbacks** to avoid stale token rejection
5. Sends CAPTCHA image to configured provider; polls until resolved; writes text into `#CodeTextBox`
6. After submit, `_collect_all_pages()` walks the ASP.NET GridView pager collecting all result pages
7. Each result row triggers `_fetch_detail()` to pull address/attorney/charges from `CaseDetail.aspx`
8. `main()` wraps each `search_by_date()` call in an outer retry loop (up to 3 full re-runs if 0 results); emails both CSVs via `send_email_with_csvs()`

**Flow (search — by-name mode):**
Same steps 1–3, then `_fill_and_submit()` fills the Defendant search form (CAPTCHA solved first, before any form interaction).

**Key classes and functions:**
- `SearchParams` — dataclass for Defendant search (last/first/middle name, DOB, date range, case status, case types, soundex)
- `DateFieldSearchParams` — dataclass for Date Field search (date_after, date_before, case_types, case_status); no name required
- `CaptchaSolverBase` — ABC defining the interface: `solve(image_bytes)`, `solve_turnstile(sitekey, pageurl)`, `report_bad()`
- `TwoCaptchaClient(api_key, provider)` — implements 2captcha and SolveCaptcha (same API, different base URLs)
- `CapSolverClient(api_key)` — implements CapSolver's native API (`api.capsolver.com`)
- `create_captcha_solver(provider, api_key)` — factory function; returns the right solver instance
- `_preprocess_captcha(image_bytes)` — scales image 3×, converts to grayscale, applies Gaussian blur, binarizes at threshold 180 to improve OCR accuracy on LanAP's checkerboard and orange-text styles
- `_LocalProxyServer` — local HTTP proxy (127.0.0.1) that pre-embeds upstream credentials; started automatically by `_build_context()` when a proxy is configured
- `NDCourtsScraper` — main orchestrator; accepts `provider`, `api_key`, optional `proxy` and `solver`
  - `search(params, max_retries)` — Defendant name search
  - `search_by_date(params, max_retries)` — Date Field search with full pagination
  - `_collect_all_pages(page)` — walks ASP.NET GridView pager (">", ">>", "Next" links)
  - `_fetch_detail(page, url)` / `_parse_detail_html(html)` — enriches each row with CaseDetail data
  - `_attach_console_listener(page)` — forwards browser console messages to the logger
- `send_email_with_csvs(files)` — emails multiple CSVs as attachments via Gmail SMTP
- `save_to_csv(results, filepath)` — writes results list to CSV

**Supplementary script:**
- `enrich_csv.py` — standalone tool to retroactively enrich an existing CSV with City/State/Zip/Attorney/Charges by fetching `CaseDetail.aspx` pages. Establishes a real browser session (stealth + Cloudflare), then fetches detail pages concurrently (semaphore-limited, default 8). Configure `CSV_FILE` and `SEARCH_HTML` at the top of the file.

**Choosing a CAPTCHA provider:**
```python
# 2captcha (default)
NDCourtsScraper(api_key="xxx", provider="2captcha")

# SolveCaptcha (2captcha-compatible API)
NDCourtsScraper(api_key="xxx", provider="solvecaptcha")

# CapSolver
NDCourtsScraper(api_key="CAP-xxx", provider="capsolver")

# Custom solver instance
NDCourtsScraper(api_key="", solver=my_custom_solver)
```

**Provider API differences:**

| Feature | 2captcha / SolveCaptcha | CapSolver |
|---------|------------------------|-----------|
| Image task type | `method=base64` (in.php) | `ImageToTextTask` |
| Turnstile task type | `TurnstileTaskProxyless` | `AntiTurnstileTaskProxyless` |
| Report bad | `res.php?action=reportbad` | `POST /feedbackTask {"invalid": true}` |

**Anti-detection (`_STEALTH_SCRIPT` + `_build_context`):**
- `_build_context()` tries **camoufox** first (Firefox-based, full fingerprint randomization, best Cloudflare bypass). Falls back to Chromium if not installed.
- Chromium fallback: tries **rebrowser-playwright** import (patches CDP leaks), then applies **playwright-stealth** if available, else falls back to the minimal `_STEALTH_SCRIPT` (injects `window.chrome` only)
- Canvas/WebGL/platform overrides intentionally omitted in the manual script — they create fingerprint inconsistencies that Cloudflare Turnstile detects
- Launch arg `--disable-blink-features=AutomationControlled`
- Randomized User-Agent (Chrome 134–136, Windows/Mac), viewport, `_human_type()` (per-character delays), `_human_click()` / `_human_click_element()` (Bezier-curve mouse movement), `_human_idle()`, `_random_scroll()`

**Proxy configuration (`.env`):**
```
PROXY_SERVER=http://p.webshare.io:80
PROXY_USERNAME=your_username
PROXY_PASSWORD=your_password
```
- Requires **residential proxies** — datacenter IPs are hard-blocked by Cloudflare on this site
- `publicsearch.ndcourts.gov` is a `.gov` domain; IProyal requires $500 spend to unlock `.gov` on residential plans
- Webshare Rotating Residential (`p.webshare.io:80`) confirmed working
- Chromium does not support the two-step proxy auth handshake (CONNECT → 407 → retry) used by some providers; `_LocalProxyServer` solves this by running a local proxy on `127.0.0.1` that pre-embeds the upstream credentials

**Email configuration (`.env`):**
```
GMAIL_USER=your_account@gmail.com
GMAIL_APP_PASSWORD=your_app_password   # Google App Password (not account password)
EMAIL_TO=recipient@example.com
```
Results are also always CC'd to `lawfirmping@gmail.com`.

## Target site details

- CAPTCHA vendor: **LanAP Captcha** (Tyler Technologies custom) — image-only, no audio fallback; two observed styles: orange text on white background, and black text on white/black checkerboard
- **Cloudflare Turnstile** sitekey: `0x4AAAAAAADnPIDROrmt1Wwj` (managed challenge on `Search.aspx?ID=100`)
- ASP.NET WebForms: `__VIEWSTATE`, `__EVENTVALIDATION`, `LBD_VCT_search_samplecaptcha` hidden fields are managed automatically by the browser session
- CAPTCHA image URL is session-specific (tokens `t` and `s` change per page load) — must be fetched with active browser cookies
- Switching to "Date Filed" radio triggers an UpdatePanel postback that **regenerates the CAPTCHA image** — always solve CAPTCHA after all postbacks, not before
- The "Criminal\Traffic" link executes `javascript:LaunchSearch(...)` — requires a real browser (no plain HTTP)
- Use `wait_for_url()` instead of `wait_for_load_state("networkidle")` — Turnstile keeps the network busy indefinitely
- Results are paginated via ASP.NET GridView pager links (">", ">>", "Next") — each click is a postback
