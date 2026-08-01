# taxdesk

Monthly sales-tax allocations and TABC mixed-beverage receipts from the Texas
Comptroller, rendered as a dashboard for Austin / Travis County.

Built to run on guildenstern alongside BarelyTrade and OpenClaw.

- **Repo:** `github.com/barelysmash/taxdesk` (private)
- **Working copy:** `C:\bSmash-dev\taxdesk` on Rosencrantz
- **Deployed at:** `/opt/taxdesk` on guildenstern, served on `http://100.113.110.44:8770/`

## Data sources

Three Socrata datasets from `data.texas.gov`, plus a small HTML scrape:

| Source | Resource ID | Frequency | Coverage in taxdesk |
| --- | --- | --- | --- |
| Sales Tax Allocation, City | `vfba-b57j` | Monthly | Austin only |
| Sales Tax Allocation, County / MTA / SPD | `qsh8-tby8` | Monthly | Travis ESDs, CapMetro |
| Mixed Beverage Gross Receipts | `naix-2893` | Monthly | All Austin locations |
| Comptroller news releases (statewide rollup) | HTML | Monthly | Statewide totals, YoY, YTD |

Travis County itself does not levy a sales tax — its 0% rate is real, not
missing data. What we track instead: the 1% City of Austin share, the 1%
CapMetro share, and any Travis County ESDs / MUDs that levy their own.

## Architecture

```
┌───────────────────┐   daily 06:00 CT   ┌──────────────┐
│ data.texas.gov    │ ◄────────────────  │ scraper       │
│ + Comptroller HTML│                    │ (oneshot)     │
└───────────────────┘                    └──────┬────────┘
                                                │ writes
                                                ▼
                                         ┌──────────────┐
                          reads          │  SQLite      │
                ┌─────────────────────── │  taxdesk.db  │
                │                        └──────────────┘
                ▼
        ┌──────────────────────────────┐
        │  FastAPI + uvicorn  :8770    │
        │  /api/*  and the built React │
        │  bundle from the same process│
        └──────────────────────────────┘
                       ▲
                  Tailscale only
```

- Scraper, API, and DB all run under `ocelia` (no sudo).
- Scraper is the only writer. API is read-only.
- SQLite in WAL mode handles the read concurrency comfortably.
- **No reverse proxy.** uvicorn binds the Tailscale IP directly and serves the
  frontend itself. There is no Caddy, nginx, or other proxy in the path.

## Layout

```
taxdesk/
├── scraper/
│   ├── scrape.py          # Socrata + news-release scraper
│   └── probe.py           # dataset/column verification, run by install.sh
├── api/
│   └── main.py            # FastAPI app
├── web/                   # Vite + React + Recharts
│   ├── index.html
│   ├── package.json
│   ├── vite.config.js
│   └── src/
│       ├── App.jsx
│       ├── InstagramCard.jsx
│       ├── YoYHeatmap.jsx
│       ├── format.js
│       ├── index.css
│       └── main.jsx
├── scripts/
│   ├── refresh.sh         # runs ON guildenstern: backup→scrape→verify→export
│   └── pull.sh            # runs ON rosencrantz: streams refresh.sh over ssh
├── sql/
│   └── schema.sql         # SQLite schema, indexes, watchlist seed
├── deploy/
│   ├── install.sh         # idempotent install on guildenstern
│   ├── deploy.ps1         # PowerShell push/install/build/status
│   ├── taxdesk-api.service
│   ├── taxdesk-scrape.service
│   ├── taxdesk-scrape.timer
│   └── taxdesk.env.example
├── .gitattributes         # forces LF on everything that runs on Linux
├── .gitignore
├── requirements.txt
└── README.md
```

`.gitattributes` matters: `scripts/refresh.sh` is piped into `bash` on
guildenstern. If it is checked out with CRLF endings it dies with
`$'\r': command not found`.

## Refreshing the data

This is the normal path. From Git Bash on Rosencrantz:

```bash
bash "/c/bSmash-dev/taxdesk/scripts/pull.sh"
```

`pull.sh` streams `refresh.sh` to guildenstern over stdin — nothing is
installed remotely, so the version in this repo is always the version that
runs. Stages: preflight → backup → coverage check → scrape → verify → export →
bundle, then scp's the export tarball to `/c/Users/bgama/Downloads/`.

| Flag | Effect |
| --- | --- |
| *(none)* | auto: incremental, escalating to full only if the coverage gap exceeds the lookback window |
| `--full` | force a three-year backfill |
| `--incremental` | force incremental |
| `--no-scrape` | export the current DB without touching Socrata |
| `--keep N` | number of timestamped backups to retain (default 5) |

The daily timer at 06:00 CT runs the same scraper unattended. `pull.sh` is for
when you want the export, or want to watch it happen.

## Deploying a code change

```bash
Remote="barelysmash@100.113.110.44"
scp "/c/bSmash-dev/taxdesk/scraper/scrape.py" "$Remote:/tmp/scrape.py"
ssh $Remote "sudo install -m 644 -o ocelia -g ocelia /tmp/scrape.py /opt/taxdesk/scraper/scrape.py"
```

Same shape for `api/main.py` (restart `taxdesk-api` afterward) and
`sql/schema.sql` (picked up on the next scrape, since `ensure_schema()` runs
`executescript` every run and all DDL is `IF NOT EXISTS`).

`deploy/deploy.ps1` predates the git repo and stages through
`/tmp/taxdesk-deploy`. It still works for a full push + build, but for single
files the scp above is faster and is what has actually been used.

## Install from scratch on guildenstern

```powershell
cd C:\bSmash-dev\taxdesk\deploy
.\deploy.ps1                      # all steps: push + install + build + status
```

Or step-by-step: `-Step push` / `install` / `build` / `status`.

The API binds to the Tailscale interface (`100.113.110.44:8770` by default —
override via `TAXDESK_BIND_HOST` in `/etc/taxdesk/taxdesk.env`).

### Schema probe (the install gate)

Before the first backfill, the installer runs `scraper/probe.py` to verify the
Socrata dataset IDs are still correct and the column names match what
`scraper/scrape.py` expects. If a probe fails:

1. The installer stops before backfill or starting services.
2. It writes `/var/lib/taxdesk/schema_probe.json` with the real column names.
3. Update `CANDIDATES` in `scraper/probe.py` and the column names in
   `scraper/scrape.py`, then re-run the installer.

## Local dev

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
TAXDESK_DB=./dev.db python -m scraper.scrape --full-backfill
TAXDESK_DB=./dev.db uvicorn api.main:app --reload --port 8770

# Web (in another shell, proxies /api → :8770)
cd web && npm install && npm run dev
```

## Operations

| Task | Command |
| --- | --- |
| Refresh + export | `bash /c/bSmash-dev/taxdesk/scripts/pull.sh` |
| Manual scrape | `sudo systemctl start taxdesk-scrape` |
| Tail scrape log | `journalctl -u taxdesk-scrape -f` |
| Tail API log | `journalctl -u taxdesk-api -f` |
| Restart API | `sudo systemctl restart taxdesk-api` |
| Force backfill | `sudo -u ocelia bash -c 'cd /opt/taxdesk && .venv/bin/python -m scraper.scrape --full-backfill'` |
| Inspect DB | `sudo -u ocelia sqlite3 /var/lib/taxdesk/taxdesk.db` |

The `cd /opt/taxdesk` in the backfill command is required, not cosmetic —
`python -m scraper.scrape` needs the package root on `sys.path`.

## Watchlist editing

The `venue_watchlist` table is seeded by `sql/schema.sql`. To add a venue:

```sql
INSERT OR REPLACE INTO venue_watchlist (slug, display_name, bucket, match_pattern, notes)
VALUES ('uchiko', 'Uchiko', 'cocktail', 'UCHIKO', 'Added 2026-05');
```

**Use the exact filing name, with no trailing `%`.** `match_pattern` is matched
with SQL `LIKE` against `mixed_beverage.location_name`, which is the name on the
TABC return, not the trading name — Fonda San Miguel files as
`SAN MIGUEL RESTAURANT` under `CUISINES OF MEXICO, INC.` Wildcards were removed
from every seed pattern in July 2026 because `'GARAGE%'`-style patterns matched
unrelated venues and quietly inflated their totals.

To find the right string:

```sql
SELECT DISTINCT location_name FROM mixed_beverage WHERE location_name LIKE '%UCHIKO%';
```

Then confirm with `/api/mb/venue/<slug>` that only the intended locations
matched. The dashboard re-reads on every request, so changes show up
immediately.

Note that a venue can be on the watchlist and legitimately report nothing:
Midnight Cowboy holds a beer/wine permit and files no mixed-beverage returns,
so its zeroes are real.

## Instagram card (optional)

The Mixed Beverage view has a card for an Instagram feed next to the home
venue, using [Behold](https://behold.so) as the data layer — Behold handles the
Instagram OAuth and exposes a public JSON feed URL, so no API keys land in the
frontend bundle.

1. Create a free Behold account and connect the `@barelysmash` Instagram source.
2. Create a JSON feed and copy its feed ID.
3. Set `VITE_BEHOLD_FEED_ID=<id>` in `/etc/taxdesk/taxdesk.env`.
4. Rebuild: `cd /opt/taxdesk/web && npm run build`.

`install.sh` mirrors `VITE_*` vars from the shared env file into
`web/.env.local` on each run. Leave blank to hide the card.

## YoY heatmap

The Sales Tax view includes a year-over-year heatmap (months × years grid,
colored by % change vs. prior year), from `/api/sales-tax/city/yoy`, which
returns every period in the database — no `years` filter — so the more history
the scraper backfills, the taller the heatmap grows.

The color ramp diverges around zero: cream is flat, oxblood saturates at −10%,
green saturates at +10%. Cells beyond ±10% clip to the endpoint color so a
single outlier doesn't wash out the rest.

## Caveats and notes

- **The mixed-beverage watermark is a lookback window, not a high-water mark.**
  A few venues file early, so `MAX(obligation_end_date)` normally sits a month
  or two ahead of the ~1,450 returns that post in arrears. An earlier version
  queried `obligation_end_date > MAX(...)`, which put those real filings
  permanently *behind* the floor — the scraper ingested zero rows while exiting
  successfully, and mixed-beverage data silently froze at 2026-03 for ten
  weeks. `MB_LOOKBACK_MONTHS` (6) and `ALLOC_LOOKBACK_PERIODS` (3) in
  `scraper/scrape.py` now re-read a trailing window. This is safe because every
  ingest is `INSERT OR REPLACE` against a natural primary key, so overlap
  cannot duplicate rows and amended filings correct themselves. **Do not
  "optimize" this back into a strict `>`.**

- **`/api/mb/austin/top` is index-sensitive.** It filters on
  `upper(location_city)`, which is served by `idx_mb_upper_city_date`, an index
  on that exact expression — a plain index on the bare column cannot satisfy a
  function call. The cutoff must also stay a scalar subquery; written as a
  cross join, SQLite re-drives the CTE and the query goes from 23 ms to 180
  seconds, which exhausts the single uvicorn worker as soon as a browser
  retries. Run `EXPLAIN QUERY PLAN` after touching it: expect
  `SEARCH ... USING INDEX idx_mb_upper_city_date`, never `SCAN`.

- **The API runs a single uvicorn worker.** Fine for a tailnet-only dashboard,
  but it means one pathological query can stall every request. The
  `/api/mb/austin/top` fix above removed the only known offender; if another
  appears, fix the query rather than raising the worker count, since SQLite
  writers and readers share the file.

- **The statewide release parser walks HTML, not markdown.** `_TableParser` in
  `scraper/scrape.py` subclasses `html.parser.HTMLParser` and treats `<th>` and
  `<td>` alike, because the Comptroller publishes row labels as `<th>`. An
  earlier version matched markdown pipe-table syntax against raw HTML — written
  against a rendered view of the page rather than its source — and never
  matched a single byte, so `sales_tax_statewide` sat empty from the day the
  feature shipped. If the layout changes, verify against the *raw* HTML.

- **The news index has no `?page=N`.** It paginates by
  `?fromDate=YYYY-01-01&toDate=YYYY-12-31`, and the default view reaches back
  only about six months. A page parameter is silently ignored, which is how an
  earlier version fetched the same page three times per run.

- **Comptroller column names drift.** Field names on Socrata occasionally
  change between dataset revisions. `_f` / `_s` / `_i` in `scraper/scrape.py`
  accept fallback column names; if a rename breaks ingestion, add the new name
  to the relevant call.

- **CapMetro appears as "AUSTIN MTA"** in the County/MTA dataset, not
  "CAPITAL METRO" — both patterns are in the watchlist to be safe.

## License

Private to barelysmash. No license granted.
