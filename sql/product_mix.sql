-- Internal operations data: Toast product mix exports.
--
-- Kept in a separate file from schema.sql because it is loaded by
-- scripts/load_mix.py rather than by the scraper's ensure_schema(). The loader
-- executes this file on every run, and all DDL is IF NOT EXISTS, so running it
-- repeatedly is safe.
--
-- Unlike every other table in taxdesk, nothing here is scraped. Product mix
-- arrives as a manual export, so the period is whatever range was exported and
-- there is no expectation of continuity between rows.

CREATE TABLE IF NOT EXISTS mix_period (
    period_start   TEXT    NOT NULL,   -- ISO date, inclusive
    period_end     TEXT    NOT NULL,   -- ISO date, exclusive
    days           INTEGER,            -- calendar days in the range
    service_days   INTEGER,            -- days actually open; set by --service-days
    covers         REAL,               -- entree-equivalents, computed on load
    gross          REAL,               -- operating gross, excludes gift cards
    source_file    TEXT,
    loaded_at      TEXT    DEFAULT (datetime('now')),
    PRIMARY KEY (period_start, period_end)
);

-- One row per line of the export, at whichever level of the hierarchy it came
-- from. Levels are stored side by side rather than nested, so a query must
-- always filter on `level` or it will double count.
CREATE TABLE IF NOT EXISTS product_mix (
    period_start   TEXT    NOT NULL,
    period_end     TEXT    NOT NULL,
    level          TEXT    NOT NULL,   -- menu | group | subgroup | item
    menu           TEXT    NOT NULL DEFAULT '',
    menu_group     TEXT    NOT NULL DEFAULT '',
    subgroup       TEXT    NOT NULL DEFAULT '',
    item           TEXT    NOT NULL DEFAULT '',
    qty_sold       REAL,
    gross_amt      REAL,
    net_amt        REAL,
    discount_amt   REAL,
    loaded_at      TEXT    DEFAULT (datetime('now')),
    PRIMARY KEY (period_start, period_end, level, menu, menu_group, subgroup, item),
    FOREIGN KEY (period_start, period_end) REFERENCES mix_period(period_start, period_end)
);

-- Empty strings rather than NULLs in the key columns above: SQLite permits
-- NULL in a non-INTEGER primary key, which would silently defeat uniqueness
-- and let the same row load twice.

CREATE INDEX IF NOT EXISTS idx_mix_level_group
    ON product_mix(level, menu_group, period_start);
CREATE INDEX IF NOT EXISTS idx_mix_period
    ON product_mix(period_start, period_end, level);
