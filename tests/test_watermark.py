"""The mixed-beverage watermark.

This is the function whose earlier form froze mixed-beverage data at
2026-03 for ten weeks while the scraper exited 0 every night. It is
worth testing directly.
"""

import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scraper.scrape import _latest_mb_date, _shift_iso_month  # noqa: E402

SCHEMA = """
CREATE TABLE mixed_beverage (
    tabc_permit_number  TEXT,
    obligation_end_date TEXT,
    total_receipts      REAL
);
"""


@pytest.fixture
def conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.executescript(SCHEMA)
    return conn


def fill(conn, month: str, filings: int, receipts: float = 1000.0) -> None:
    conn.executemany(
        "INSERT INTO mixed_beverage VALUES (?, ?, ?)",
        [
            (f"MB{n:05d}", f"{month}-28", receipts)
            for n in range(filings)
        ],
    )
    conn.commit()


def full_year(conn, filings: int = 1400) -> None:
    for month in range(1, 7):
        fill(conn, f"2026-{month:02d}", filings)


def test_an_empty_table_has_no_watermark(conn):
    assert _latest_mb_date(conn) is None


def test_a_reported_month_sets_the_watermark(conn):
    full_year(conn)

    assert _latest_mb_date(conn) == "2026-06-28"


def test_early_filers_do_not_drag_the_watermark_forward(conn):
    """Two venues filing July early must not anchor the window to July."""
    full_year(conn)
    fill(conn, "2026-07", 2, receipts=0.0)

    assert _latest_mb_date(conn) == "2026-06-28"


def test_the_lookback_floor_still_reaches_the_arrears(conn):
    """The whole point: the floor must sit behind the months still filing."""
    full_year(conn)
    fill(conn, "2026-07", 2, receipts=0.0)

    floor = _shift_iso_month(_latest_mb_date(conn), -6)

    assert floor == "2025-12-01"


def test_a_month_still_filing_is_not_yet_the_watermark(conn):
    """A month at a third of normal volume is arrears, not the new anchor."""
    full_year(conn)
    fill(conn, "2026-07", 450)

    assert _latest_mb_date(conn) == "2026-06-28"


def test_a_month_that_has_mostly_reported_becomes_the_watermark(conn):
    full_year(conn)
    fill(conn, "2026-07", 1300)

    assert _latest_mb_date(conn) == "2026-07-28"


def test_a_first_backfill_falls_back_to_the_maximum(conn):
    """One sparse month is its own median; there is nothing to compare to."""
    fill(conn, "2026-06", 3)

    assert _latest_mb_date(conn) == "2026-06-28"


def test_a_growing_venue_population_does_not_strand_the_watermark(conn):
    """Older months hold fewer filings because fewer venues existed."""
    for month in range(1, 7):
        fill(conn, f"2025-{month:02d}", 700)
    for month in range(1, 7):
        fill(conn, f"2026-{month:02d}", 1400)

    assert _latest_mb_date(conn) == "2026-06-28"


def test_blank_dates_are_ignored(conn):
    full_year(conn)
    conn.execute("INSERT INTO mixed_beverage VALUES ('MB99999', '', 0)")
    conn.commit()

    assert _latest_mb_date(conn) == "2026-06-28"
