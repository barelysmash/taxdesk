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


@app.get("/api/mix/periods")
def mix_periods():
    """Product mix periods loaded so far, newest first."""
    with db() as conn:
        try:
            data = rows(conn, """
                SELECT period_start, period_end, days, service_days,
                       covers, gross, source_file, loaded_at
                FROM mix_period
                ORDER BY period_start DESC
            """)
        except sqlite3.OperationalError:
            return {"periods": [], "note": "no product mix loaded yet"}
    for r in data:
        cov, gr, sd = r.get("covers"), r.get("gross"), r.get("service_days")
        r["spend_per_cover"] = round(gr / cov, 2) if cov else None
        r["gross_per_service_day"] = round(gr / sd, 2) if sd else None
        r["covers_per_service_day"] = round(cov / sd, 1) if sd else None
    return {"periods": data}


@app.get("/api/mix/groups")
def mix_groups(period_start: str | None = None,
               compare_to: str | None = None,
               limit: int = Query(25, ge=1, le=100)):
    """Menu group performance for a period, optionally against another.

    Everything is normalised per service day and per cover, because raw totals
    from a 26-day export and a 365-day export cannot be compared directly.
    """
    with db() as conn:
        try:
            periods = rows(conn, """
                SELECT period_start, period_end, service_days, covers, gross
                FROM mix_period ORDER BY period_start DESC
            """)
        except sqlite3.OperationalError:
            return {"groups": [], "note": "no product mix loaded yet"}
        if not periods:
            return {"groups": [], "note": "no product mix loaded yet"}

        cur = next((p for p in periods if p["period_start"] == period_start), periods[0])
        base = None
        if compare_to:
            base = next((p for p in periods if p["period_start"] == compare_to), None)
        elif len(periods) > 1:
            base = periods[1]

        def fetch(p):
            return {r["menu_group"]: r for r in rows(conn, """
                SELECT menu_group,
                       SUM(qty_sold)     AS qty,
                       SUM(gross_amt)    AS gross,
                       SUM(discount_amt) AS discount
                FROM product_mix
                WHERE level = 'group' AND period_start = ? AND period_end = ?
                GROUP BY menu_group
            """, (p["period_start"], p["period_end"]))}

        cur_rows = fetch(cur)
        base_rows = fetch(base) if base else {}

    def norm(r, p):
        sd = p["service_days"] or 1
        cv = p["covers"] or 1
        return {
            "gross": round(r["gross"] or 0, 2),
            "qty": round(r["qty"] or 0, 1),
            "gross_per_day": round((r["gross"] or 0) / sd, 2),
            "units_per_day": round((r["qty"] or 0) / sd, 2),
            "units_per_cover": round((r["qty"] or 0) / cv, 4),
            "discount": round(r["discount"] or 0, 2),
        }

    out = []
    for g, r in cur_rows.items():
        rec = {"menu_group": g, **norm(r, cur)}
        if g in base_rows:
            b = norm(base_rows[g], base)
            rec["base_units_per_cover"] = b["units_per_cover"]
            rec["base_gross_per_day"] = b["gross_per_day"]
            # Per-cover change is the honest comparison: it strips out both
            # period length and how busy the restaurant was.
            rec["units_per_cover_change"] = (
                round(rec["units_per_cover"] / b["units_per_cover"] - 1, 4)
                if b["units_per_cover"] else None)
            rec["gross_per_day_change"] = (
                round(rec["gross_per_day"] / b["gross_per_day"] - 1, 4)
                if b["gross_per_day"] else None)
        out.append(rec)
    out.sort(key=lambda r: r["gross"], reverse=True)

    return {
        "period": cur,
        "compared_to": base,
        "groups": out[:limit],
        "note": "Per-cover figures are the comparable ones. Raw totals scale with "
                "period length and are shown for reference only.",
    }


@app.get("/api/mb/beta")
def mb_beta(months: int = Query(36, ge=12, le=120),
            min_months: int = Query(24, ge=6, le=120)):
    """Each watchlist venue's sensitivity to the Austin mixed-beverage market.

    Beta is the slope of the venue's monthly percentage change regressed on the
    market's. Below 1.0 means the venue falls less than the market in a
    downturn, which is what a resilient regular base looks like in the data.

    Computed in Python rather than SQL: SQLite has no regression function, and
    the series is at most a few hundred points per venue.
    """
    with db() as conn:
        mkt_rows = rows(conn, f"""
            WITH cutoff AS (
                SELECT date(MAX(obligation_end_date), '-{months} months') AS d
                FROM mixed_beverage
            )
            SELECT substr(obligation_end_date, 1, 7) AS ym,
                   SUM(total_receipts) AS total
            FROM mixed_beverage
            WHERE upper(location_city) = 'AUSTIN'
              AND obligation_end_date >= (SELECT d FROM cutoff)
            GROUP BY ym
            ORDER BY ym
        """)
        venue_rows = rows(conn, f"""
            WITH cutoff AS (
                SELECT date(MAX(obligation_end_date), '-{months} months') AS d
                FROM mixed_beverage
            )
            SELECT w.slug, w.display_name, w.bucket,
                   substr(m.obligation_end_date, 1, 7) AS ym,
                   SUM(m.total_receipts) AS total
            FROM venue_watchlist w
            JOIN mixed_beverage m ON m.location_name LIKE w.match_pattern
            WHERE upper(m.location_city) = 'AUSTIN'
              AND m.obligation_end_date >= (SELECT d FROM cutoff)
            GROUP BY w.slug, ym
            ORDER BY w.slug, ym
        """)

    market = {r["ym"]: float(r["total"] or 0) for r in mkt_rows}
    ordered = sorted(market)
    # The most recent month is nearly always partial: venues file in arrears,
    # so including it would read as a market collapse every single month.
    if len(ordered) > 1:
        ordered = ordered[:-1]

    def pct_changes(series, keys):
        out = {}
        for a, b in zip(keys, keys[1:]):
            pa = series.get(a)
            if pa:
                out[b] = series.get(b, 0) / pa - 1
        return out

    mkt_ch = pct_changes(market, ordered)

    by_venue = {}
    for r in venue_rows:
        v = by_venue.setdefault(r["slug"], {"name": r["display_name"],
                                            "bucket": r["bucket"], "series": {}})
        v["series"][r["ym"]] = float(r["total"] or 0)

    def stats(ch):
        common = [k for k in ordered[1:] if k in ch and k in mkt_ch]
        n = len(common)
        if n < min_months - 1:
            return None
        xs = [mkt_ch[k] for k in common]
        ys = [ch[k] for k in common]
        mx = sum(xs) / n
        my = sum(ys) / n
        sxx = sum((x - mx) ** 2 for x in xs)
        sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
        syy = sum((y - my) ** 2 for y in ys)
        if sxx == 0 or syy == 0:
            return None
        beta = sxy / sxx
        corr = sxy / ((sxx * syy) ** 0.5)
        vol = (syy / (n - 1)) ** 0.5 if n > 1 else 0.0
        return {"beta": round(beta, 3), "corr": round(corr, 3),
                "volatility": round(vol, 4), "n_months": n}

    mkt_vals = [mkt_ch[k] for k in ordered[1:] if k in mkt_ch]
    mn = len(mkt_vals)
    mkt_mean = sum(mkt_vals) / mn if mn else 0.0
    mkt_vol = ((sum((x - mkt_mean) ** 2 for x in mkt_vals) / (mn - 1)) ** 0.5) if mn > 1 else 0.0

    out = []
    for slug, v in by_venue.items():
        st = stats(pct_changes(v["series"], ordered))
        if not st:
            continue
        window = [v["series"].get(k, 0) for k in ordered[-12:]]
        out.append({"slug": slug, "display_name": v["name"], "bucket": v["bucket"],
                    "ttm_total": round(sum(window), 2), **st})
    out.sort(key=lambda r: r["beta"])

    return {
        "window_months": months,
        "market": {
            "series": [{"ym": k, "total": round(market[k], 2)} for k in ordered],
            "volatility": round(mkt_vol, 4),
            "ttm_total": round(sum(market[k] for k in ordered[-12:]), 2),
            "prior_ttm_total": round(sum(market[k] for k in ordered[-24:-12]), 2)
                                if len(ordered) >= 24 else None,
        },
        "venues": out,
        "note": "Beta below 1.0 means the venue moves less than the market. "
                "The latest month is excluded because filings arrive in arrears.",
    }


@app.get("/api/mb/austin/top")
def mb_austin_top(
    n: int = Query(25, ge=1, le=200),
    months: int = Query(12, ge=1, le=60),
) -> dict:
    """Top Austin venues by total mixed-beverage receipts over the trailing N months.

    Two details here are load-bearing for performance; measured on 53k rows:

    1. The cutoff is a scalar subquery, not a cross join. Written as
       `FROM mixed_beverage, cutoff` SQLite plans the CTE as a co-routine and
       re-drives it, which turned this query into a 180-second table scan and
       exhausted the single uvicorn worker whenever a browser retried.
    2. `upper(location_city)` is matched by idx_mb_upper_city_date, an index on
       that exact expression. A plain index on the bare column cannot serve a
       function call, so without the expression index this falls back to a scan.

    180,000 ms -> 23 ms. Check `EXPLAIN QUERY PLAN` before editing: it should
    read SEARCH ... USING INDEX idx_mb_upper_city_date, never SCAN.
    """
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
            FROM mixed_beverage
            WHERE upper(location_city) = 'AUSTIN'
              AND obligation_end_date >= (SELECT d FROM cutoff)
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
