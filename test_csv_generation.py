"""
Tests for CSV generation using local HTML files as mocks.

Run with:
    source venv/bin/activate
    pytest test_csv_generation.py -v
"""

import csv
import io
import re
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from scraper import NDCourtsScraper, save_to_csv

# ---------------------------------------------------------------------------
# Paths to mock HTML files
# ---------------------------------------------------------------------------
HERE = Path(__file__).parent
SEARCH_HTML = HERE / "Search.aspx.html"
DETAIL_HTML = HERE / "CaseDetail.html"

DETAIL_CASE_ID = "5704014"  # the CaseDetail.html corresponds to this CaseID

# ---------------------------------------------------------------------------
# Helpers that replicate _parse_results logic without Playwright
# ---------------------------------------------------------------------------

def parse_search_html(html: str, detail_html_by_case_id: dict[str, str]) -> list[dict]:
    """
    Parse Search.aspx.html the same way _parse_results does, but using
    BeautifulSoup instead of Playwright so no browser is needed.
    """
    soup = BeautifulSoup(html, "html.parser")

    # The results table is the first one that has CaseDetail links
    rows = soup.find_all("tr", lambda tag: True)
    data_rows = [
        r for r in soup.find_all("tr")
        if r.find("a", href=lambda h: h and "CaseDetail" in h)
    ]

    results = []
    for row in data_rows:
        cells = row.find_all("td")
        if len(cells) < 5:
            continue

        # Case number + detail URL
        a = cells[0].find("a")
        case_number = a.get_text(strip=True) if a else ""
        detail_url = a["href"] if a else ""

        # Extract CaseID from the URL to look up mock detail HTML
        m = re.search(r"CaseID=(\d+)", detail_url)
        case_id = m.group(1) if m else ""
        detail_html = detail_html_by_case_id.get(case_id, "")
        detail = NDCourtsScraper._parse_detail_html(detail_html) if detail_html else {
            "address": "", "city": "", "state": "", "zip_code": "",
            "attorney": "", "charges_list": [],
        }

        # Defendant name → first / last
        def_divs = [d.get_text(strip=True) for d in cells[2].find_all("div")]
        defendant_name = def_divs[0] if def_divs else cells[2].get_text(strip=True)
        last_name, first_name = NDCourtsScraper._split_name(defendant_name)

        # Filed date / location / judicial officer
        loc_divs = [d.get_text(strip=True) for d in cells[3].find_all("div")]
        filed_date = loc_divs[0] if len(loc_divs) > 0 else ""
        county     = loc_divs[1].lstrip("- ").strip() if len(loc_divs) > 1 else ""
        judge      = loc_divs[2] if len(loc_divs) > 2 else ""

        # Case type / status
        type_divs = [d.get_text(strip=True) for d in cells[4].find_all("div")]
        case_type = type_divs[0] if len(type_divs) > 0 else ""
        status    = type_divs[1] if len(type_divs) > 1 else ""

        # Charges: prefer detail page; fall back to search-table column
        if detail["charges_list"]:
            charges = NDCourtsScraper._format_charges(detail["charges_list"])
        else:
            raw_charges = [
                td.get_text(strip=True)
                for td in (cells[5].find_all("td") if len(cells) > 5 else [])
                if td.get_text(strip=True)
            ]
            charges = NDCourtsScraper._format_charges(raw_charges)

        results.append({
            "Case Number":      case_number,
            "First Name":       first_name,
            "Last Name":        last_name,
            "Filed Date":       filed_date,
            "Location":         county,
            "Judicial Officer": judge,
            "Case Type":        case_type,
            "Case Status":      status,
            "Address":          detail["address"],
            "City":             detail["city"],
            "State":            detail["state"],
            "Zip Code":         detail["zip_code"],
            "Attorney":         detail["attorney"],
            "Charges":          charges,
        })

    return results


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def search_html() -> str:
    return SEARCH_HTML.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def detail_html() -> str:
    return DETAIL_HTML.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def results(search_html, detail_html) -> list[dict]:
    """Parse the mock HTML files and return all result rows."""
    return parse_search_html(search_html, {DETAIL_CASE_ID: detail_html})


@pytest.fixture(scope="module")
def csv_text(results) -> str:
    """Generate CSV text from results."""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=results[0].keys())
    writer.writeheader()
    writer.writerows(results)
    return buf.getvalue()


@pytest.fixture(scope="module")
def csv_rows(csv_text) -> list[dict]:
    return list(csv.DictReader(io.StringIO(csv_text)))


@pytest.fixture(scope="module", autouse=True)
def write_output_csv(results) -> None:
    """Write the parsed results to results_mock.csv next to this file."""
    out = HERE / "results_mock.csv"
    save_to_csv(results, out)
    print(f"\n  → CSV generado: {out}  ({len(results)} filas)")


# ---------------------------------------------------------------------------
# Tests — column structure
# ---------------------------------------------------------------------------

EXPECTED_COLUMNS = [
    "Case Number", "First Name", "Last Name", "Filed Date", "Location",
    "Judicial Officer", "Case Type", "Case Status", "Address", "City",
    "State", "Zip Code", "Attorney", "Charges",
]


def test_csv_has_all_columns(csv_rows):
    assert csv_rows, "CSV must not be empty"
    assert list(csv_rows[0].keys()) == EXPECTED_COLUMNS


def test_csv_row_count(results, csv_rows):
    """Every parsed result must appear as a row in the CSV."""
    assert len(csv_rows) == len(results)


def test_search_html_yields_200_rows(results):
    """Search.aspx.html has 200 result rows."""
    assert len(results) == 200


# ---------------------------------------------------------------------------
# Tests — known case (03-2025-CR-00130, CaseID=5704014, from CaseDetail.html)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def known_row(csv_rows) -> dict:
    for row in csv_rows:
        if row["Case Number"] == "03-2025-CR-00130":
            return row
    pytest.fail("Known case 03-2025-CR-00130 not found in CSV")


def test_known_case_number(known_row):
    assert known_row["Case Number"] == "03-2025-CR-00130"


def test_known_first_name(known_row):
    assert known_row["First Name"] == "Michael Lee"


def test_known_last_name(known_row):
    assert known_row["Last Name"] == "Jensen"


def test_known_filed_date(known_row):
    assert known_row["Filed Date"] == "11/12/2025"


def test_known_location(known_row):
    assert known_row["Location"] == "Benson County"


def test_known_judicial_officer(known_row):
    assert known_row["Judicial Officer"] == "Olson, Lonnie"


def test_known_case_type(known_row):
    assert known_row["Case Type"] == "Misdemeanor"


def test_known_case_status(known_row):
    assert known_row["Case Status"] == "Closed"


def test_known_address_empty(known_row):
    """This defendant only has city/state/zip — no street address."""
    assert known_row["Address"] == ""


def test_known_city(known_row):
    assert known_row["City"] == "Cameron"


def test_known_state(known_row):
    assert known_row["State"] == "WI"


def test_known_zip(known_row):
    assert known_row["Zip Code"] == "54822"


def test_known_attorney(known_row):
    assert known_row["Attorney"] == "Pro Se"


def test_known_charges_single(known_row):
    """Single charge: no commas, no 'and'."""
    assert known_row["Charges"] == "Hunting Waterfowl In Unharvested Crops Without Permission"


# ---------------------------------------------------------------------------
# Tests — rows without a detail-page mock (fallback to search-table charges)
# ---------------------------------------------------------------------------

def test_fallback_row_has_charges(csv_rows):
    """Rows without a detail mock must still have charges from the search table."""
    fallback_rows = [r for r in csv_rows if r["Case Number"] != "03-2025-CR-00130"]
    assert fallback_rows, "Need at least one fallback row"
    empty_charges = [r for r in fallback_rows if not r["Charges"]]
    assert not empty_charges, f"{len(empty_charges)} fallback rows have empty Charges"


def test_fallback_row_city_empty(csv_rows):
    """Rows without a detail mock must have empty address fields."""
    fallback_rows = [r for r in csv_rows if r["Case Number"] != "03-2025-CR-00130"]
    non_empty_city = [r for r in fallback_rows if r["City"]]
    assert not non_empty_city, "Fallback rows should have empty City"


# ---------------------------------------------------------------------------
# Tests — charge formatting rules
# ---------------------------------------------------------------------------

def test_format_charges_single():
    assert NDCourtsScraper._format_charges(["Charge A"]) == "Charge A"


def test_format_charges_two():
    assert NDCourtsScraper._format_charges(["Charge A", "Charge B"]) == "Charge A and Charge B"


def test_format_charges_three():
    result = NDCourtsScraper._format_charges(["Charge A", "Charge B", "Charge C"])
    assert result == "Charge A, Charge B, and Charge C"


def test_format_charges_empty():
    assert NDCourtsScraper._format_charges([]) == ""


# ---------------------------------------------------------------------------
# Tests — name splitting rules
# ---------------------------------------------------------------------------

def test_split_name_last_first_middle():
    last, first = NDCourtsScraper._split_name("Jensen, Michael Lee")
    assert last == "Jensen"
    assert first == "Michael Lee"


def test_split_name_no_comma():
    last, first = NDCourtsScraper._split_name("Michael Jensen")
    assert last == "Jensen"
    assert first == "Michael"


def test_split_name_empty():
    last, first = NDCourtsScraper._split_name("")
    assert last == ""
    assert first == ""


# ---------------------------------------------------------------------------
# Tests — address parsing
# ---------------------------------------------------------------------------

def test_parse_address_city_state_zip():
    street, city, state, zip_code = NDCourtsScraper._parse_address("Cameron, WI 54822")
    assert street == ""
    assert city == "Cameron"
    assert state == "WI"
    assert zip_code == "54822"


def test_parse_address_with_street():
    street, city, state, zip_code = NDCourtsScraper._parse_address("123 Main St\nFargo, ND 58102")
    assert street == "123 Main St"
    assert city == "Fargo"
    assert state == "ND"
    assert zip_code == "58102"


def test_parse_address_no_zip():
    street, city, state, zip_code = NDCourtsScraper._parse_address("Bismarck, ND")
    assert city == "Bismarck"
    assert state == "ND"
    assert zip_code == ""


def test_parse_address_empty():
    street, city, state, zip_code = NDCourtsScraper._parse_address("")
    assert street == city == state == zip_code == ""


# ---------------------------------------------------------------------------
# Integration: write real CSV file and read it back
# ---------------------------------------------------------------------------

def test_save_to_csv_roundtrip(results, tmp_path):
    out = tmp_path / "output.csv"
    save_to_csv(results, out)

    assert out.exists()
    rows = list(csv.DictReader(out.open(encoding="utf-8")))
    assert len(rows) == len(results)
    assert list(rows[0].keys()) == EXPECTED_COLUMNS

    # Spot-check known case survives file roundtrip
    known = next(r for r in rows if r["Case Number"] == "03-2025-CR-00130")
    assert known["Last Name"] == "Jensen"
    assert known["City"] == "Cameron"
