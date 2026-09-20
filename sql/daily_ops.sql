-- Daily operations summary, transcribed from the nightly email.
--
-- The only source in taxdesk with a business date on every row, and the only
-- one carrying a real cover count. Product mix counts entree-equivalents,
-- which undercounted 423 against an actual 491 on 2026-09-19 — a 13.8% gap,
-- because guests share, skip a main, or eat only antojitos. Where a date
-- appears here, this count wins.
--
-- Loaded by scripts/load_ops.py. Shipped and applied by deploy.sh with every
-- other sql/*.sql.

CREATE TABLE IF NOT EXISTS daily_ops (
    business_date     TEXT    NOT NULL PRIMARY KEY,   -- ISO date

    -- Sales per labor hour, as the email reports it. Multiplied by hours it
    -- implies a net sales figure, which is the only daily sales number
    -- available on days without a product mix export.
    splh              REAL,
    labor_cost        REAL,   -- hourly only: no salaried managers, no benefits
    labor_hours       REAL,

    comp_anniversary  REAL,
    comp_birthday     REAL,
    comp_manager      REAL,
    voids             REAL,

    reservations      INTEGER,
    covers_dining     INTEGER,
    covers_bar        INTEGER,   -- "Bar / Atrium": one service area, not the 14 seats
    covers_total      INTEGER,

    source_file       TEXT,
    loaded_at         TEXT    DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_ops_date ON daily_ops(business_date);
