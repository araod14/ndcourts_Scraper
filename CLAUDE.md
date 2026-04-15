# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Setup

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
playwright install chromium
```

## Running the scraper

```bash
source venv/bin/activate
python scraper.py
```

Set your API key at the top of `scraper.py` (`TWOCAPTCHA_API_KEY`) and choose a provider via the `provider` parameter. Use `headless=False` on `NDCourtsScraper` to watch the browser while debugging.

## Architecture

Single-file scraper (`scraper.py`) targeting `https://publicsearch.ndcourts.gov/Search.aspx?ID=100`.

**Flow:**
1. `NDCourtsScraper.search()` launches a stealth Chromium context via `_build_context()`
2. Navigates home → selects "State of North Dakota" → executes `LaunchSearch(...)` via JS
3. Resolves Cloudflare Managed Challenge (Turnstile) if detected via `_solve_cloudflare_challenge()`
4. Downloads the session-bound LanAP CAPTCHA image using `page.request.get()` to inherit browser cookies
5. Sends image to the configured captcha provider; polls until resolved
6. `_fill_and_submit()` types into the ASP.NET form with human-like delays and mouse movement
7. `_parse_results()` reads the results table; retries up to `max_retries` times on CAPTCHA rejection

**Key classes:**
- `SearchParams` — dataclass with all form fields (last/first/middle name, DOB, date range, case status, case types)
- `CaptchaSolverBase` — ABC defining the interface: `solve(image_bytes)`, `solve_turnstile(sitekey, pageurl)`, `report_bad()`
- `TwoCaptchaClient(api_key, provider)` — implements 2captcha and SolveCaptcha (same API, different base URLs)
- `CapSolverClient(api_key)` — implements CapSolver's native API (`api.capsolver.com`)
- `create_captcha_solver(provider, api_key)` — factory function; returns the right solver instance
- `_LocalProxyServer` — local HTTP proxy (127.0.0.1) that pre-embeds upstream credentials; started automatically by `_build_context()` when a proxy is configured
- `NDCourtsScraper` — main orchestrator; accepts `provider`, `api_key`, optional `proxy` and `solver`

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
- Init script injects only `window.chrome` — canvas/WebGL/platform overrides are intentionally omitted (they break Cloudflare Turnstile by creating fingerprint inconsistencies)
- Launch arg `--disable-blink-features=AutomationControlled`
- Randomized User-Agent, viewport, `_human_type()` (per-character delays), `_human_click()` (multi-step mouse movement)

**Proxy configuration (`.env`):**
```
PROXY_SERVER=http://p.webshare.io:80
PROXY_USERNAME=your_username
PROXY_PASSWORD=your_password
```
- Requires **residential proxies** — datacenter IPs are hard-blocked by Cloudflare on this site
- `publicsearch.ndcourts.gov` is a `.gov` domain; IProyal requires $500 spend to unlock `.gov` on residential plans
- Webshare Rotating Residential (`p.webshare.io:80`) confirmed working
- Chromium does not support the two-step proxy auth handshake (CONNECT → 407 → retry) used by some providers; `_LocalProxyServer` solves this by running a local proxy on `127.0.0.1` that pre-embeds the upstream credentials — Chromium connects to localhost without auth, and the local proxy forwards with auth to the upstream

## Target site details

- CAPTCHA vendor: **LanAP Captcha** (Tyler Technologies custom) — image-only, no audio fallback
- **Cloudflare Turnstile** sitekey: `0x4AAAAAAADnPIDROrmt1Wwj` (managed challenge on `Search.aspx?ID=100`)
- ASP.NET WebForms: `__VIEWSTATE`, `__EVENTVALIDATION`, `LBD_VCT_search_samplecaptcha` hidden fields are managed automatically by the browser session
- CAPTCHA image URL is session-specific (tokens `t` and `s` change per page load) — must be fetched with active browser cookies
- The "Criminal\Traffic" link executes `javascript:LaunchSearch(...)` — requires a real browser (no plain HTTP)
- Use `wait_for_url()` instead of `wait_for_load_state("networkidle")` — Turnstile keeps the network busy indefinitely
