# taxdesk

Monthly sales-tax allocations and TABC mixed-beverage receipts from the Texas
Comptroller, rendered as a dashboard for Austin / Travis County.

Built to run on guildenstern alongside BarelyTrade and OpenClaw.

## Data sources

Three Socrata datasets from `data.texas.gov`, plus a small HTML scrape:

| Source | Resource ID | Frequency | Coverage in taxdesk |
| --- | --- | --- | --- |
| Sales Tax Allocation, City | `vfba-b57j` | Monthly | Austin only |
| Sales Tax Allocation, County / MTA / SPD | `qsh8-tby8` | Monthly | Travis ESDs, CapMetro |
| Mixed Beverage Gross Receipts | `naix-2893` | Monthly | All Austin locations |
| Comptroller news releases (statewide rollup) | HTML | Monthly | Statewide totals + YoY |

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
        ┌──────────────┐   /api/*    ┌──────────────┐
        │  FastAPI     │ ◄────────── │  React +      │
        │  uvicorn     │             │  Vite (built  │
        │  :8770       │             │  to /dist)    │
        └──────────────┘             └──────────────┘
                ▲                            ▲
                └────────── Caddy ───────────┘
```

- Scraper, API, and DB all run under `ocelia` (no sudo).
- Scraper is the only writer. API is read-only.
- SQLite in WAL mode handles the read concurrency comfortably.

## Layout

```
taxdesk/
├── scraper/
│   └── scrape.py          # Socrata + news-release scraper
├── api/
│   └── main.py            # FastAPI app
├── web/                   # Vite + React + Recharts
│   ├── index.html
│   ├── package.json
│   ├── vite.config.js
│   └── src/
│       ├── App.jsx
│       ├── format.js
│       ├── index.css
│       └── main.jsx
├── sql/
│   └── schema.sql         # SQLite schema + watchlist seed
├── deploy/
│   ├── install.sh         # idempotent install on guildenstern
│   ├── taxdesk-api.service
│   ├── taxdesk-scrape.service
│   ├── taxdesk-scrape.timer
│   ├── taxdesk.env.example
│   └── Caddyfile.snippet
├── requirements.txt
└── README.md
```

## Install on guildenstern

From your Windows workstation in PowerShell — full pipeline, idempotent:

```powershell
cd C:\path\to\taxdesk\deploy
.\deploy.ps1                      # all steps: push + install + build + status
```

Or step-by-step:

```powershell
.\deploy.ps1 -Step push           # rsync source to /tmp/taxdesk-deploy
.\deploy.ps1 -Step install        # probe schemas + backfill + start services
.\deploy.ps1 -Step build          # npm install + npm run build
.\deploy.ps1 -Step status         # systemctl + curl /api/health + recent logs
```

The API binds to the Tailscale interface (`100.113.110.44:8770` by default —
override via `TAXDESK_BIND_HOST` in `/etc/taxdesk/taxdesk.env`). FastAPI also
serves the built frontend from the same port, so the entire dashboard is at
`http://100.113.110.44:8770/` from any tailnet device. No reverse proxy
needed.

### Schema probe (the install gate)

Before the first backfill, the installer runs `scraper/probe.py` to verify
the Socrata dataset IDs are still correct and the column names match what
`scraper/scrape.py` expects. If a probe fails:

1. The installer stops before backfill or starting services.
2. It writes `/var/lib/taxdesk/schema_probe.json` with the real column names
   returned by each dataset.
3. Read that file, update `CANDIDATES` in `scraper/probe.py` and the column
   names in `scraper/scrape.py`, then re-run the installer.

This is the safety net for the dataset IDs being educated guesses — the
Comptroller occasionally renames or restructures these.

## Local dev

```bash
# API
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
| Manual scrape | `sudo systemctl start taxdesk-scrape` |
| Tail scrape log | `journalctl -u taxdesk-scrape -f` |
| Tail API log | `journalctl -u taxdesk-api -f` |
| Restart API | `sudo systemctl restart taxdesk-api` |
| Force backfill | `sudo -u ocelia /opt/taxdesk/.venv/bin/python -m scraper.scrape --full-backfill` |
| Inspect DB | `sudo -u ocelia sqlite3 /var/lib/taxdesk/taxdesk.db` |

## Watchlist editing

The `venue_watchlist` table is seeded by `sql/schema.sql`. To add or rename:

```sql
INSERT OR REPLACE INTO venue_watchlist (slug, display_name, bucket, match_pattern, notes)
VALUES ('uchiko', 'Uchiko', 'cocktail', 'UCHIKO%', 'Added 2026-05');
```

The dashboard re-reads on every request, so changes show up immediately.

## Instagram card (optional)

The Mixed Beverage view has a card for an Instagram feed next to the home
venue. It uses [Behold](https://behold.so) as the data layer — Behold handles
the Instagram OAuth and exposes a public JSON feed URL, no API keys in the
frontend bundle.

To enable:

1. Create a free Behold account and connect the `@barelysmash` Instagram source.
2. Create a JSON feed and copy its feed ID from the Behold dashboard.
3. Set `VITE_BEHOLD_FEED_ID=<id>` in `/etc/taxdesk/taxdesk.env`.
4. Rebuild the frontend: `cd /opt/taxdesk/web && npm run build`.

The install script mirrors `VITE_*` vars from the shared env file into
`web/.env.local` automatically on each run, so re-running `install.sh`
also picks up changes.

Leave blank to hide the card.

## YoY heatmap

The Sales Tax view includes a year-over-year heatmap (months × years grid,
colored by % change vs. prior year). It pulls from `/api/sales-tax/city/yoy`,
which returns every period in the database — no `years` filter — so the more
history the scraper backfills, the taller the heatmap grows.

The color ramp diverges around zero: cream is flat, oxblood saturates at
−10%, green saturates at +10%. Cells beyond the ±10% cap clip to the endpoint
color so a single outlier doesn't wash out the rest.

## Caveats and notes

- **Comptroller column names drift.** Field names on Socrata occasionally
  change between dataset revisions. `_f` / `_s` / `_i` in `scraper/scrape.py`
  accept fallback column names; if a column rename breaks ingestion, add the
  new name to the relevant call.
- **Statewide release parser is regex-based** and assumes the table format
  used in 2026 releases. If the Comptroller changes layout, the regex in
  `_parse_release` needs an update — the rest of the pipeline keeps working.
- **Mixed-beverage matching is fuzzy.** A `match_pattern` like `'GARAGE%'`
  could match unrelated venues. Check `/api/mb/venue/<slug>` to see what
  locations matched, and tighten the pattern if needed.
- **CapMetro appears as "AUSTIN MTA"** in the County/MTA dataset, not
  "CAPITAL METRO" — both patterns are in the watch list to be safe.

## License

Private to barelysmash. No license granted.
