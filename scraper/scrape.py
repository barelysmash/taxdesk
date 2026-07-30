"""
taxdesk scraper

Pulls three data sources from the Texas Comptroller:
  1. Sales tax allocations to cities          (Socrata: vfba-b57j)
  2. Sales tax allocations to counties/MTAs    (Socrata: qsh8-tby8 / canonical equivalent)
  3. Mixed beverage gross receipts             (Socrata: naix-2893)

Plus parses the monthly news-release page for statewide rollups (no API equivalent).

Designed to run as a systemd timer once daily — incremental by default.
First run will backfill 3 years; subsequent runs only pull periods newer
than the max period already in SQLite.
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import sqlite3
import sys
import time
from contextlib import closing
from html.parser import HTMLParser
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable, Iterator
from urllib.parse import urlencode

import httpx

LOG = logging.getLogger("taxdesk.scraper")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DB_PATH = Path(os.environ.get("TAXDESK_DB", "/var/lib/taxdesk/taxdesk.db"))
SOCRATA_APP_TOKEN = os.environ.get("SOCRATA_APP_TOKEN", "")  # optional, raises rate limit

# Socrata resource IDs (verified against data.texas.gov, May 2026)
DS_CITY_ALLOC   = "vfba-b57j"   # Sales Tax Allocation, City
DS_COUNTY_ALLOC = "qsh8-tby8"   # Sales Tax Allocation, County/MTA/SPD
DS_MIXED_BEV    = "naix-2893"   # Mixed Beverage Gross Receipts

SOCRATA_BASE = "https://data.texas.gov/resource"
NEWS_INDEX   = "https://comptroller.texas.gov/about/media-center/news/"

PAGE_SIZE = 50_000   # Socrata hard ceiling is 50k per request
BACKFILL_YEARS = 3

# Cities we actively care about for the City Alloc dataset. Statewide totals
# stay in the news-release rollup table.
WATCH_CITIES_LIKE = ("AUSTIN",)

# County-side entities we want from the County/MTA dataset.
WATCH_COUNTY_ENTITIES_LIKE = (
    "TRAVIS",          # Travis County ESDs / MUDs / assistance districts
    "AUSTIN MTA",      # CapMetro
    "CAPITAL METRO",   # alternate naming
)

# Mixed beverage scope: pull all of Austin so we can join against the watchlist
# in SQL rather than guess permit numbers up front. ~2k rows/month, cheap.
MB_CITIES = ("AUSTIN",)


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _client() -> httpx.Client:
    headers = {"User-Agent": "taxdesk/0.1 (barelysmash)"}
    if SOCRATA_APP_TOKEN:
        headers["X-App-Token"] = SOCRATA_APP_TOKEN
    return httpx.Client(headers=headers, timeout=60.0)


def _soda(client: httpx.Client, resource: str, *, where: str = "",
          select: str = "", order: str = "", limit: int = PAGE_SIZE) -> Iterator[dict]:
    """Paginate a SoQL query and yield rows."""
    offset = 0
    while True:
        params: dict[str, str | int] = {"$limit": limit, "$offset": offset}
        if where:  params["$where"]  = where
        if select: params["$select"] = select
        if order:  params["$order"]  = order

        url = f"{SOCRATA_BASE}/{resource}.json?{urlencode(params)}"
        LOG.debug("GET %s", url)
        resp = client.get(url)
        resp.raise_for_status()
        rows = resp.json()
        if not rows:
            return
        yield from rows
        if len(rows) < limit:
            return
        offset += limit
        time.sleep(0.25)  # be polite


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def ensure_schema(conn: sqlite3.Connection) -> None:
    schema_sql = Path(__file__).parent.parent / "sql" / "schema.sql"
    with closing(conn.cursor()) as cur:
        cur.executescript(schema_sql.read_text())
    conn.commit()


def _latest_period(conn: sqlite3.Connection, table: str) -> tuple[int, int] | None:
    row = conn.execute(
        f"SELECT period_year, period_month FROM {table} "
        f"ORDER BY period_year DESC, period_month DESC LIMIT 1"
    ).fetchone()
    return row if row else None


def _latest_mb_date(conn: sqlite3.Connection) -> str | None:
    row = conn.execute(
        "SELECT MAX(obligation_end_date) FROM mixed_beverage"
    ).fetchone()
    return row[0] if row and row[0] else None


# ---------------------------------------------------------------------------
# Field normalization
#
# Socrata column names vary across these three datasets. We normalize them in
# one place so the rest of the code stays clean. If the Comptroller renames a
# column, only these maps change.
# ---------------------------------------------------------------------------

def _f(row: dict, *keys: str) -> float | None:
    for k in keys:
        if k in row and row[k] not in (None, ""):
            try: return float(row[k])
            except (TypeError, ValueError): pass
    return None


def _s(row: dict, *keys: str) -> str | None:
    for k in keys:
        if k in row and row[k] not in (None, ""):
            return str(row[k]).strip()
    return None


def _i(row: dict, *keys: str) -> int | None:
    v = _f(row, *keys)
    return int(v) if v is not None else None


# ---------------------------------------------------------------------------
# Source 1: City sales tax allocations
# ---------------------------------------------------------------------------

def ingest_city_alloc(conn: sqlite3.Connection, client: httpx.Client,
                      since_period: tuple[int, int] | None) -> int:
    where_parts = [
        " OR ".join(f"upper(city) LIKE '{c}%'" for c in WATCH_CITIES_LIKE)
    ]
    if since_period:
        yr, mo = since_period
        # Periods strictly newer than the latest we have.
        # Socrata column names: report_year / report_month.
        where_parts.append(
            f"(report_year > {yr} OR (report_year = {yr} AND report_month > {mo}))"
        )
    where = " AND ".join(f"({p})" for p in where_parts)

    n = 0
    sql = """
        INSERT OR REPLACE INTO sales_tax_city
            (city, period_year, period_month, net_payment, comparable_prior,
             pct_change, payment_ytd, payment_ytd_prior, pct_change_ytd)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """
    for row in _soda(client, DS_CITY_ALLOC, where=where,
                     order="report_year DESC, report_month DESC"):
        conn.execute(sql, (
            _s(row, "city"),
            _i(row, "report_year", "period_year"),
            _i(row, "report_month", "period_month"),
            _f(row, "net_payment_this_period", "net_payment"),
            _f(row, "comparable_payment_prior_year", "comparable_payment"),
            _f(row, "period_percent_change", "percent_change", "pct_change"),
            _f(row, "payments_to_date", "payment_ytd"),
            _f(row, "previous_payments_to_date", "prior_year_payment_ytd"),
            _f(row, "ytd_percent_change", "percent_change_to_date"),
        ))
        n += 1
    conn.commit()
    LOG.info("city alloc rows ingested: %d", n)
    return n


# ---------------------------------------------------------------------------
# Source 2: County / MTA / SPD allocations
# ---------------------------------------------------------------------------

def ingest_county_alloc(conn: sqlite3.Connection, client: httpx.Client,
                        since_period: tuple[int, int] | None) -> int:
    # The county/MTA/SPD dataset uses 'name' (not 'entity_name') and there's
    # no entity_type column — there's just 'type'. We also filter on
    # report_year/report_month, not period_year/period_month.
    name_clause = " OR ".join(
        f"upper(name) LIKE '%{p}%'" for p in WATCH_COUNTY_ENTITIES_LIKE
    )
    where_parts = [name_clause]
    if since_period:
        yr, mo = since_period
        where_parts.append(
            f"(report_year > {yr} OR (report_year = {yr} AND report_month > {mo}))"
        )
    where = " AND ".join(f"({p})" for p in where_parts)

    n = 0
    sql = """
        INSERT OR REPLACE INTO sales_tax_county
            (entity_name, entity_type, period_year, period_month, net_payment,
             comparable_prior, pct_change, payment_ytd, payment_ytd_prior,
             pct_change_ytd)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """
    for row in _soda(client, DS_COUNTY_ALLOC, where=where,
                     order="report_year DESC, report_month DESC"):
        conn.execute(sql, (
            _s(row, "name", "entity_name"),
            _s(row, "type", "entity_type"),
            _i(row, "report_year", "period_year"),
            _i(row, "report_month", "period_month"),
            _f(row, "net_payment_this_period", "net_payment"),
            _f(row, "comparable_payment_prior_year"),
            _f(row, "percent_change_prior_year", "percent_change", "pct_change"),
            _f(row, "payments_to_date", "payment_ytd"),
            _f(row, "previous_payments_to_date", "prior_year_payment_ytd"),
            _f(row, "percent_change_to_date", "ytd_percent_change"),
        ))
        n += 1
    conn.commit()
    LOG.info("county/MTA alloc rows ingested: %d", n)
    return n


# ---------------------------------------------------------------------------
# Source 3: Mixed beverage gross receipts
# ---------------------------------------------------------------------------

def ingest_mixed_beverage(conn: sqlite3.Connection, client: httpx.Client,
                          since_date: str | None) -> int:
    # Socrata's column is obligation_end_date_yyyymmdd. The values come back
    # as YYYYMMDD strings (no separators), e.g. "20260331". We compare and
    # store as 'YYYY-MM-DD' to keep SQL date math working in the API.
    city_clause = " OR ".join(f"upper(location_city) = '{c}'" for c in MB_CITIES)
    where_parts = [city_clause]
    if since_date:
        # Column is a datetime despite its name. SoQL wants ISO with quotes.
        since_iso = f"{since_date}T00:00:00.000"
        where_parts.append(
            f"obligation_end_date_yyyymmdd > '{since_iso}'"
        )
    else:
        cutoff = (date.today().replace(day=1)
                  - timedelta(days=365 * BACKFILL_YEARS))
        cutoff_iso = cutoff.strftime("%Y-%m-%dT00:00:00.000")
        where_parts.append(
            f"obligation_end_date_yyyymmdd >= '{cutoff_iso}'"
        )
    where = " AND ".join(f"({p})" for p in where_parts)

    n = 0
    sql = """
        INSERT OR REPLACE INTO mixed_beverage
            (taxpayer_number, taxpayer_name, location_number, location_name,
             location_address, location_city, location_county, location_zip,
             tabc_permit_number, obligation_end_date,
             liquor_receipts, wine_receipts, beer_receipts, total_receipts)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """
    for row in _soda(client, DS_MIXED_BEV, where=where,
                     order="obligation_end_date_yyyymmdd DESC"):
        liquor = _f(row, "liquor_receipts") or 0.0
        wine   = _f(row, "wine_receipts")   or 0.0
        beer   = _f(row, "beer_receipts")   or 0.0
        total  = _f(row, "total_receipts") or (liquor + wine + beer)

        # Normalize date to YYYY-MM-DD regardless of input format.
        end_raw = _s(row, "obligation_end_date_yyyymmdd",
                          "obligation_end_date") or ""
        if len(end_raw) == 8 and end_raw.isdigit():
            end_date = f"{end_raw[:4]}-{end_raw[4:6]}-{end_raw[6:8]}"
        else:
            end_date = end_raw[:10]   # ISO 8601 fallback

        conn.execute(sql, (
            _s(row, "taxpayer_number"),
            _s(row, "taxpayer_name"),
            _s(row, "location_number"),
            _s(row, "location_name"),
            _s(row, "location_address"),
            _s(row, "location_city"),
            _s(row, "location_county"),
            _s(row, "location_zip", "location_zip_code"),
            _s(row, "tabc_permit_number"),
            end_date,
            liquor, wine, beer, total,
        ))
        n += 1
    conn.commit()
    LOG.info("mixed beverage rows ingested: %d", n)
    return n


# ---------------------------------------------------------------------------
# Source 4: Statewide rollup from news releases
#
# The Comptroller publishes a monthly news release with the statewide split
# (cities / counties / transit / SPD) and a YoY pct. There's no API for this,
# so we scrape the release index, find the most recent allocation releases,
# and parse the table inside each.
# ---------------------------------------------------------------------------

# The index serves absolute URLs. An earlier version of this pattern required a
# root-relative href and therefore never matched anything.
RELEASE_URL_RE = re.compile(
    r'href="((?:https?://(?:www\.)?comptroller\.texas\.gov)?'
    r'/about/media-center/news/(\d{8})-[^"]*'
    r'distributes[^"]*sales-tax-revenue[^"]*)"',
    re.IGNORECASE,
)


class _TableParser(HTMLParser):
    """Collect every HTML table as a list of rows, each row a list of cell text.

    Row labels in these releases live in <th> inside <tbody>, not <td>, so both
    are treated as cells. A regex cannot do this reliably: the previous
    implementation matched markdown pipe-table syntax against raw HTML, which
    never matched a single byte, so this table was always empty.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[list[list[str]]] = []
        self._table: list[list[str]] | None = None
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag == "table" and self._table is None:
            self._table = []
        elif tag == "tr" and self._table is not None:
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []

    def handle_endtag(self, tag: str) -> None:
        if tag in ("td", "th") and self._cell is not None and self._row is not None:
            self._row.append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if self._row and self._table is not None:
                self._table.append(self._row)
            self._row = None
        elif tag == "table" and self._table is not None:
            self.tables.append(self._table)
            self._table = None

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)


# Recipient label as published -> our column key.
_ROW_KEYS = {
    "cities": "cities",
    "transit systems": "transit",
    "counties": "counties",
    "special purpose districts": "spd",
    "total": "total",
}


def _find_allocation_table(html: str) -> dict[str, list[str]] | None:
    """Return {lowercased label: [cells after the label]} for the allocation
    table, identified by containing both a Cities and a Total row."""
    parser = _TableParser()
    parser.feed(html)
    for table in parser.tables:
        rows = {r[0].strip().lower(): r[1:] for r in table if len(r) >= 2}
        if "total" in rows and "cities" in rows:
            return rows
    return None


def _pct(text: str) -> float | None:
    """Parse a change cell such as '\u21916.1%' or '\u21932.3%' into +6.1 / -2.3."""
    m = re.search(r"([\u2191\u2193]?)\s*([\d.]+)\s*%", text)
    if not m:
        return None
    return float(m.group(2)) * (-1 if m.group(1) == "\u2193" else 1)
DOLLAR_RE = re.compile(r"\$([\d.]+)\s*([BM])", re.IGNORECASE)
MONTH_RE  = re.compile(
    r"sales tax allocations for (\w+),\s*([\d.]+)\s*percent (more|less)",
    re.IGNORECASE,
)
MONTHS = {m: i for i, m in enumerate(
    ["january","february","march","april","may","june",
     "july","august","september","october","november","december"], start=1)}


def _to_dollars(text: str) -> float | None:
    m = DOLLAR_RE.search(text)
    if not m: return None
    val, suffix = float(m.group(1)), m.group(2).upper()
    return val * (1e9 if suffix == "B" else 1e6)


def _parse_release(client: httpx.Client, url: str) -> dict | None:
    resp = client.get(url)
    resp.raise_for_status()
    html = resp.text

    # The monthly news releases identify themselves by month+year in the
    # narrative paragraph, e.g. "sales tax allocations for May, 7.7 percent
    # more than in May 2025". The publish date in the URL is the distribution
    # month, not the reported sales month — we want the publish month.
    pub_match = re.search(r"news/(\d{4})(\d{2})(\d{2})-", url)
    if not pub_match: return None
    pub_year, pub_month = int(pub_match.group(1)), int(pub_match.group(2))

    yoy = None
    m = MONTH_RE.search(html)
    if m:
        yoy = float(m.group(2)) * (1 if m.group(3).lower() == "more" else -1)

    rows = _find_allocation_table(html)
    if rows is None:
        LOG.warning("no allocation table found in %s", url)
        return None

    totals: dict[str, float | None] = {
        "cities": None, "counties": None, "transit": None,
        "spd": None, "total": None,
    }
    ytd = None
    for label, cells in rows.items():
        key = _ROW_KEYS.get(label)
        if not key:
            continue
        totals[key] = _to_dollars(cells[0]) if cells else None
        if key == "total":
            # Columns are: allocation, change vs prior year, year-to-date change.
            if yoy is None and len(cells) > 1:
                yoy = _pct(cells[1])
            if len(cells) > 2:
                ytd = _pct(cells[2])

    if totals["total"] is None:
        LOG.warning("allocation table in %s had no parsable total", url)
        return None

    return {
        "period_year": pub_year,
        "period_month": pub_month,
        "total_allocations": totals["total"],
        "cities_total": totals["cities"],
        "counties_total": totals["counties"],
        "transit_total": totals["transit"],
        "spd_total": totals["spd"],
        "yoy_pct": yoy,
        "ytd_yoy_pct": ytd,
        "source_url": url,
    }


def ingest_statewide(conn: sqlite3.Connection, client: httpx.Client,
                     years_back: int = 1) -> int:
    """Scan the news index and parse any monthly allocation release we don't
    already have.

    The index has no ?page=N parameter — an earlier version passed one, which
    was silently ignored, so the same 'Latest' page was fetched repeatedly. It
    paginates by date range instead, and the default view only reaches back
    about six months, so earlier years need an explicit window.
    """
    seen: set[str] = set()
    have: set[tuple[int, int]] = {
        (r[0], r[1]) for r in conn.execute(
            "SELECT period_year, period_month FROM sales_tax_statewide"
        )
    }

    this_year = date.today().year
    index_urls = [NEWS_INDEX] + [
        f"{NEWS_INDEX}?fromDate={y}-01-01&toDate={y}-12-31"
        for y in range(this_year, this_year - years_back - 1, -1)
    ]

    n = 0
    for idx_url in index_urls:
        resp = client.get(idx_url)
        if resp.status_code != 200:
            LOG.warning("news index %s returned %d", idx_url, resp.status_code)
            continue
        hrefs = RELEASE_URL_RE.findall(resp.text)
        LOG.debug("%s -> %d candidate release links", idx_url, len(hrefs))
        for href, _stamp in hrefs:
            # The index serves absolute URLs, but tolerate relative ones.
            full = href if href.startswith("http") else f"https://comptroller.texas.gov{href}"
            if full in seen:
                continue
            seen.add(full)

            pub_match = re.search(r"/news/(\d{4})(\d{2})\d{2}-", full)
            if not pub_match:
                continue
            pub_year, pub_month = int(pub_match.group(1)), int(pub_match.group(2))
            if (pub_year, pub_month) in have:
                continue

            parsed = _parse_release(client, full)
            if not parsed:
                continue
            conn.execute("""
                INSERT OR REPLACE INTO sales_tax_statewide
                    (period_year, period_month, total_allocations, cities_total,
                     counties_total, transit_total, spd_total, yoy_pct,
                     ytd_yoy_pct, source_url)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                parsed["period_year"], parsed["period_month"],
                parsed["total_allocations"], parsed["cities_total"],
                parsed["counties_total"], parsed["transit_total"],
                parsed["spd_total"], parsed["yoy_pct"],
                parsed["ytd_yoy_pct"], parsed["source_url"],
            ))
            have.add((pub_year, pub_month))
            n += 1
            time.sleep(0.5)
    conn.commit()
    LOG.info("statewide releases ingested: %d", n)
    return n


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def run(full_backfill: bool = False) -> dict[str, int]:
    logging.basicConfig(
        level=os.environ.get("TAXDESK_LOG", "INFO"),
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    LOG.info("taxdesk scrape starting (full_backfill=%s, db=%s)",
             full_backfill, DB_PATH)

    conn = connect()
    ensure_schema(conn)

    if full_backfill:
        city_since = county_since = mb_since = None
    else:
        city_since   = _latest_period(conn, "sales_tax_city")
        county_since = _latest_period(conn, "sales_tax_county")
        mb_since     = _latest_mb_date(conn)

    counts: dict[str, int] = {}
    with _client() as client:
        counts["city"]      = ingest_city_alloc(conn, client, city_since)
        counts["county"]    = ingest_county_alloc(conn, client, county_since)
        counts["mb"]        = ingest_mixed_beverage(conn, client, mb_since)
        counts["statewide"] = ingest_statewide(conn, client)

    LOG.info("done: %s", counts)
    return counts


def main() -> int:
    p = argparse.ArgumentParser(description="taxdesk scraper")
    p.add_argument("--full-backfill", action="store_true",
                   help="Ignore high-water marks and re-pull everything")
    args = p.parse_args()
    try:
        run(full_backfill=args.full_backfill)
        return 0
    except Exception as e:
        LOG.exception("scrape failed: %s", e)
        return 1


if __name__ == "__main__":
    sys.exit(main())
