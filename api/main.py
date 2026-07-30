"""
taxdesk API — FastAPI backend.

All endpoints are read-only and return JSON shaped for the React dashboard.
The scraper owns writes; this process never modifies the database.
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

DB_PATH = Path(os.environ.get("TAXDESK_DB", "/var/lib/taxdesk/taxdesk.db"))
WEB_DIST = Path(os.environ.get("TAXDESK_WEB_DIST", "/opt/taxdesk/web/dist"))

app = FastAPI(title="taxdesk", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],          # tighten in deploy if exposing publicly
    allow_methods=["GET"],
    allow_headers=["*"],
)


@contextmanager
def db() -> Iterator[sqlite3.Connection]:
    if not DB_PATH.exists():
        raise HTTPException(503, f"database not found at {DB_PATH}")
    conn = sqlite3.connect(DB_PATH, detect_types=sqlite3.PARSE_DECLTYPES)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def rows(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> list[dict]:
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


# ---------------------------------------------------------------------------
# Meta
# ---------------------------------------------------------------------------

@app.get("/api/health")
def health() -> dict:
    with db() as conn:
        latest_city = conn.execute(
            "SELECT period_year, period_month, MAX(fetched_at) AS fetched_at "
            "FROM sales_tax_city"
        ).fetchone()
        latest_mb = conn.execute(
            "SELECT MAX(obligation_end_date) AS d, MAX(fetched_at) AS fetched_at "
            "FROM mixed_beverage"
        ).fetchone()
    return {
        "ok": True,
        "latest_city_period": (
            dict(latest_city) if latest_city and latest_city["period_year"] else None
        ),
        "latest_mb_obligation": dict(latest_mb) if latest_mb else None,
    }


# ---------------------------------------------------------------------------
# Sales tax — city
# ---------------------------------------------------------------------------

@app.get("/api/sales-tax/city")
def sales_tax_city(
    city: str = Query("AUSTIN"),
    years: int = Query(5, ge=1, le=20),
) -> dict:
    with db() as conn:
        data = rows(conn, """
            SELECT period_year, period_month, net_payment, comparable_prior,
                   pct_change, payment_ytd, payment_ytd_prior, pct_change_ytd
            FROM sales_tax_city
            WHERE upper(city) = upper(?)
              AND period_year >= (SELECT MAX(period_year) FROM sales_tax_city) - ?
            ORDER BY period_year, period_month
        """, (city, years - 1))
    return {"city": city.upper(), "series": data}


@app.get("/api/sales-tax/city/yoy")
def sales_tax_city_yoy(city: str = Query("AUSTIN")) -> dict:
    """Pivot months across years for a YoY heatmap."""
    with db() as conn:
        data = rows(conn, """
            SELECT period_year, period_month, net_payment, pct_change
            FROM sales_tax_city
            WHERE upper(city) = upper(?)
            ORDER BY period_year, period_month
        """, (city,))
    return {"city": city.upper(), "data": data}


# ---------------------------------------------------------------------------
# Sales tax — county / MTA
# ---------------------------------------------------------------------------

@app.get("/api/sales-tax/county")
def sales_tax_county(
    entity_like: str | None = Query(None),
    years: int = Query(5, ge=1, le=20),
) -> dict:
    where = ""
    params: list = []
    if entity_like:
        where = "WHERE upper(entity_name) LIKE upper(?)"
        params.append(f"%{entity_like}%")

    with db() as conn:
        entities = rows(conn, f"""
            SELECT DISTINCT entity_name, entity_type
            FROM sales_tax_county
            {where}
            ORDER BY entity_name
        """, tuple(params))

        series = rows(conn, f"""
            SELECT entity_name, entity_type, period_year, period_month,
                   net_payment, pct_change, payment_ytd, pct_change_ytd
            FROM sales_tax_county
            {where + (' AND ' if where else 'WHERE ')}
              period_year >= (SELECT MAX(period_year) FROM sales_tax_county) - ?
            ORDER BY entity_name, period_year, period_month
        """, tuple(params + [years - 1]))

    return {"entities": entities, "series": series}


# ---------------------------------------------------------------------------
# Sales tax — statewide
# ---------------------------------------------------------------------------

@app.get("/api/sales-tax/statewide")
def sales_tax_statewide(years: int = Query(5, ge=1, le=20)) -> dict:
    with db() as conn:
        data = rows(conn, """
            SELECT period_year, period_month, total_allocations, cities_total,
                   counties_total, transit_total, spd_total, yoy_pct
            FROM sales_tax_statewide
            WHERE period_year >= (SELECT MAX(period_year) FROM sales_tax_statewide) - ?
            ORDER BY period_year, period_month
        """, (years - 1,))
    return {"series": data}


# ---------------------------------------------------------------------------
# Mixed beverage
# ---------------------------------------------------------------------------

@app.get("/api/mb/watchlist")
def mb_watchlist() -> dict:
    """Time series of total mixed-beverage receipts for each watchlist venue.

    A venue can match multiple locations (chains, multiple addresses), so we
    sum within (venue, period). This is what the dashboard charts."""
    with db() as conn:
        venues = rows(conn, """
            SELECT slug, display_name, bucket, match_pattern
            FROM venue_watchlist
            ORDER BY bucket, display_name
        """)

        series: list[dict] = []
        for v in venues:
            matched = rows(conn, """
                SELECT obligation_end_date,
                       SUM(total_receipts)  AS total,
                       SUM(liquor_receipts) AS liquor,
                       SUM(wine_receipts)   AS wine,
                       SUM(beer_receipts)   AS beer,
                       COUNT(DISTINCT location_number) AS locations
                FROM mixed_beverage
                WHERE location_name LIKE ?
                GROUP BY obligation_end_date
                ORDER BY obligation_end_date
            """, (v["match_pattern"],))
            series.append({**v, "series": matched})

    return {"venues": series}


@app.get("/api/mb/venue/{slug}")
def mb_venue(slug: str) -> dict:
    """Detail view: every location matching this watchlist entry, with YoY."""
    with db() as conn:
        venue = conn.execute(
            "SELECT * FROM venue_watchlist WHERE slug = ?", (slug,)
        ).fetchone()
        if not venue:
            raise HTTPException(404, f"unknown venue {slug}")

        locations = rows(conn, """
            SELECT DISTINCT location_number, location_name, location_address,
                            location_zip, tabc_permit_number
            FROM mixed_beverage
            WHERE location_name LIKE ?
            ORDER BY location_name
        """, (venue["match_pattern"],))

        monthly = rows(conn, """
            SELECT obligation_end_date,
                   SUM(liquor_receipts) AS liquor,
                   SUM(wine_receipts)   AS wine,
                   SUM(beer_receipts)   AS beer,
                   SUM(total_receipts)  AS total
            FROM mixed_beverage
            WHERE location_name LIKE ?
            GROUP BY obligation_end_date
            ORDER BY obligation_end_date
        """, (venue["match_pattern"],))

    return {"venue": dict(venue), "locations": locations, "monthly": monthly}


@app.get("/api/mb/austin/top")
def mb_austin_top(
    n: int = Query(25, ge=1, le=200),
    months: int = Query(12, ge=1, le=60),
) -> dict:
    """Top Austin venues by total mixed-beverage receipts over the trailing N months."""
    # months and n are int-validated by Query() above, safe to inline into SQL.
    with db() as conn:
        data = rows(conn, f"""
            WITH cutoff AS (
                SELECT date(MAX(obligation_end_date), '-{months} months') AS d
                FROM mixed_beverage
            )
            SELECT location_name,
                   MIN(location_address) AS address,
                   MIN(location_zip)     AS zip,
                   SUM(total_receipts)   AS total
            FROM mixed_beverage, cutoff
            WHERE upper(location_city) = 'AUSTIN'
              AND obligation_end_date >= cutoff.d
            GROUP BY location_name
            ORDER BY total DESC
            LIMIT {n}
        """)
    return {"top": data, "trailing_months": months}


# ---------------------------------------------------------------------------
# Static frontend
#
# Serve the built React bundle from the same uvicorn process. /api/* routes
# above win because FastAPI matches them before the catch-all static mount.
# If the dist directory doesn't exist yet (e.g., first install before
# `npm run build`), we skip the mount and the API still works.
# ---------------------------------------------------------------------------

if WEB_DIST.exists() and (WEB_DIST / "index.html").exists():
    @app.get("/")
    def _index() -> FileResponse:
        return FileResponse(WEB_DIST / "index.html")

    # SPA fallback: any non-/api/* path that isn't a real file returns
    # index.html so client-side routing works.
    @app.get("/{full_path:path}")
    def _spa(full_path: str) -> FileResponse:
        if full_path.startswith("api/"):
            raise HTTPException(404)
        target = WEB_DIST / full_path
        if target.is_file():
            return FileResponse(target)
        return FileResponse(WEB_DIST / "index.html")
