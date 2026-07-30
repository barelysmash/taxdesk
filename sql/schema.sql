-- taxdesk schema
-- Source: Texas Comptroller via data.texas.gov (Socrata API)

-- Sales tax allocations to cities (dataset: vfba-b57j)
CREATE TABLE IF NOT EXISTS sales_tax_city (
    city               TEXT    NOT NULL,
    period_year        INTEGER NOT NULL,
    period_month       INTEGER NOT NULL,
    net_payment        REAL,
    comparable_prior   REAL,
    pct_change         REAL,
    payment_ytd        REAL,
    payment_ytd_prior  REAL,
    pct_change_ytd     REAL,
    fetched_at         TEXT    DEFAULT (datetime('now')),
    PRIMARY KEY (city, period_year, period_month)
);

-- County / MTA / SPD allocations (dataset: qsh8-tby8 mirror; canonical is on data.texas.gov)
CREATE TABLE IF NOT EXISTS sales_tax_county (
    entity_name        TEXT    NOT NULL,
    entity_type        TEXT,             -- COUNTY | MTA | CTD | SPD
    period_year        INTEGER NOT NULL,
    period_month       INTEGER NOT NULL,
    net_payment        REAL,
    comparable_prior   REAL,
    pct_change         REAL,
    payment_ytd        REAL,
    payment_ytd_prior  REAL,
    pct_change_ytd     REAL,
    fetched_at         TEXT    DEFAULT (datetime('now')),
    PRIMARY KEY (entity_name, period_year, period_month)
);

-- Statewide allocation rollup (parsed from monthly news releases)
CREATE TABLE IF NOT EXISTS sales_tax_statewide (
    period_year        INTEGER NOT NULL,
    period_month       INTEGER NOT NULL,
    total_allocations  REAL,
    cities_total       REAL,
    counties_total     REAL,
    transit_total      REAL,
    spd_total          REAL,
    yoy_pct            REAL,
    ytd_yoy_pct        REAL,
    source_url         TEXT,
    fetched_at         TEXT    DEFAULT (datetime('now')),
    PRIMARY KEY (period_year, period_month)
);

-- Mixed beverage gross receipts by taxpayer (dataset: naix-2893)
-- One row per taxpayer location per obligation period
CREATE TABLE IF NOT EXISTS mixed_beverage (
    taxpayer_number       TEXT    NOT NULL,
    taxpayer_name         TEXT,
    location_number       TEXT    NOT NULL,
    location_name         TEXT,
    location_address      TEXT,
    location_city         TEXT,
    location_county       TEXT,
    location_zip          TEXT,
    tabc_permit_number    TEXT,
    obligation_end_date   TEXT    NOT NULL,   -- ISO date
    liquor_receipts       REAL,
    wine_receipts         REAL,
    beer_receipts         REAL,
    total_receipts        REAL,
    fetched_at            TEXT    DEFAULT (datetime('now')),
    PRIMARY KEY (taxpayer_number, location_number, obligation_end_date)
);

-- Watchlist for the competitor set
CREATE TABLE IF NOT EXISTS venue_watchlist (
    slug          TEXT PRIMARY KEY,
    display_name  TEXT NOT NULL,
    bucket        TEXT NOT NULL,           -- 'home' | 'mexican' | 'cocktail' | 'alumni'
    match_pattern TEXT NOT NULL,           -- SQL LIKE pattern against location_name
    notes         TEXT
);

CREATE INDEX IF NOT EXISTS idx_mb_city_period ON mixed_beverage(location_city, obligation_end_date);
CREATE INDEX IF NOT EXISTS idx_mb_name        ON mixed_beverage(location_name);
CREATE INDEX IF NOT EXISTS idx_stc_period     ON sales_tax_city(period_year, period_month);
CREATE INDEX IF NOT EXISTS idx_stcounty_period ON sales_tax_county(period_year, period_month);

-- Seed the watchlist
INSERT OR IGNORE INTO venue_watchlist (slug, display_name, bucket, match_pattern, notes) VALUES
    ('fonda_san_miguel', 'Fonda San Miguel',    'home',     'FONDA SAN MIGUEL%',  'Home venue'),
    ('suerte',           'Suerte',              'mexican',  'SUERTE%',            'Mexican fine dining peer'),
    ('el_naranjo',       'El Naranjo',          'mexican',  'EL NARANJO%',        'Mexican fine dining peer'),
    ('comedor',          'Comedor',             'mexican',  'COMEDOR%',           'Mexican fine dining peer'),
    ('roosevelt_room',   'The Roosevelt Room',  'cocktail', '%ROOSEVELT ROOM%',   'Cocktail-forward peer'),
    ('pelons',           'Pelons Tex-Mex',      'cocktail', 'PELONS%',            'Cocktail-forward peer'),
    ('midnight_cowboy',  'Midnight Cowboy',     'cocktail', 'MIDNIGHT COWBOY%',   'Cocktail-forward peer'),
    ('garage',           'Garage',              'cocktail', 'GARAGE%',            'Cocktail-forward peer; verify match on import');
