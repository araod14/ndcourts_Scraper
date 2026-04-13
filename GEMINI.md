# ND Courts Criminal/Traffic Scraper

A robust Python-based scraper for the North Dakota Public Search portal, specifically targeting the Criminal/Traffic search functionality. It utilizes Playwright for browser automation and integrates with multiple CAPTCHA solving services to bypass bot detection mechanisms.

## Project Overview

*   **Main Purpose:** Extracting criminal and traffic case information from `https://publicsearch.ndcourts.gov`.
*   **Technologies:**
    *   **Python:** Core language.
    *   **Playwright (Chromium):** Used for navigation, handling ASP.NET WebForms, and executing JavaScript-based searches.
    *   **httpx:** Used for session-aware HTTP requests (e.g., fetching CAPTCHA images with browser cookies).
    *   **Captcha Solvers:** Supports 2captcha, SolveCaptcha, and CapSolver for both image-based and Cloudflare Turnstile challenges.
*   **Architecture:**
    *   A single-file orchestrator (`scraper.py`) containing data models, solver abstractions, and the main scraper class.
    *   **Key Classes:**
        *   `NDCourtsScraper`: The main entry point for managing the browser lifecycle and search flow.
        *   `SearchParams`: Dataclass for configuring search filters (names, DOB, dates, case types).
        *   `CaptchaSolverBase`: Interface for CAPTCHA solving implementations.
        *   `TwoCaptchaClient`, `CapSolverClient`: Specific provider implementations.

## Setup and Installation

1.  **Create and activate a virtual environment:**
    ```bash
    python -m venv venv
    source venv/bin/activate  # On Windows use `venv\Scripts\activate`
    ```
2.  **Install dependencies:**
    ```bash
    pip install -r requirements.txt
    ```
3.  **Install Playwright Chromium browser:**
    ```bash
    playwright install chromium
    ```

## Running the Scraper

1.  **Configure API Keys:** Open `scraper.py` and set `TWOCAPTCHA_API_KEY` (or the equivalent for your provider) at the top of the file.
2.  **Execute the scraper:**
    ```bash
    python scraper.py
    ```

### Configuration Options
*   **Captcha Provider:** Set via the `provider` parameter in `NDCourtsScraper` (`"2captcha"`, `"solvecaptcha"`, or `"capsolver"`).
*   **Debugging:** Use `headless=False` when initializing `NDCourtsScraper` to observe the browser's actions.
*   **Screenshots:** Error screenshots are automatically saved to the `debug_screenshots/` directory.

## Development Conventions

*   **Anti-Detection:** The scraper uses a randomized pool of User-Agents and viewports. A minimalist stealth script is injected to prevent detection without breaking Cloudflare Turnstile.
*   **Human-like Interaction:** `_human_type()` and `_human_click()` methods simulate natural delays and mouse movements.
*   **Error Handling:** Retries are implemented for CAPTCHA failures and unexpected page states.
*   **Site Details:**
    *   **Target URL:** `https://publicsearch.ndcourts.gov/Search.aspx?ID=100`
    *   **Turnstile Sitekey:** `0x4AAAAAAADnPIDROrmt1Wwj`
    *   **CAPTCHA Vendor:** LanAP Captcha (image-only).

## Key Files
*   `scraper.py`: Contains the entire scraper logic, CAPTCHA handling, and data extraction.
*   `CLAUDE.md`: Provides deeper architectural insights and implementation details (used for AI context).
*   `requirements.txt`: Lists Python dependencies (`playwright`, `httpx`).
