"""
taxdesk schema probe.

Run this BEFORE the first full backfill. It hits each Socrata dataset for a
single row, prints the actual column names, and writes them to
schema_probe.json next to the database. If a dataset ID is wrong or the
columns have drifted from what scrape.py expects, you'll see it here in
seconds instead of after a 20-minute backfill that ingested nothing useful.

Usage:
    python -m scraper.probe
    python -m scraper.probe --resource vfba-b57j   # one dataset
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import httpx

# Candidate Socrata resource IDs. If a primary fails, the probe tries
# alternates. Update scrape.py constants with whatever the probe confirms.
CANDIDATES = {
    "city_alloc": {
        "primary": "vfba-b57j",
        "alternates": ["53pa-m7sm"],   # City-County Comparison Summary
        "expected": ["city", "report_year", "report_month",
                     "net_payment_this_period"],
    },
    "county_alloc": {
        "primary": "qsh8-tby8",
        "alternates": ["nvcm-ec62"],   # County/MTA/SPD Comparison Summary
        "expected": ["name", "type", "report_year", "report_month",
                     "net_payment_this_period"],
    },
    "mixed_beverage": {
        "primary": "naix-2893",
        "alternates": [],
        "expected": ["taxpayer_name", "location_name",
                     "obligation_end_date_yyyymmdd", "total_receipts"],
    },
}

SOCRATA_BASE = "https://data.texas.gov/resource"
TOKEN = os.environ.get("SOCRATA_APP_TOKEN", "")
OUT_PATH = Path(os.environ.get("TAXDESK_DB",
                               "/var/lib/taxdesk/taxdesk.db")).parent / "schema_probe.json"


def probe(client: httpx.Client, resource: str) -> dict | None:
    url = f"{SOCRATA_BASE}/{resource}.json?$limit=1"
    try:
        r = client.get(url, timeout=15.0)
    except Exception as e:
        return {"resource": resource, "ok": False, "error": str(e)}
    if r.status_code != 200:
        return {"resource": resource, "ok": False,
                "status": r.status_code, "body": r.text[:200]}
    rows = r.json()
    if not rows:
        return {"resource": resource, "ok": True, "columns": [], "note": "empty"}
    return {"resource": resource, "ok": True,
            "columns": sorted(rows[0].keys()),
            "sample": rows[0]}


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--resource", help="Probe a single resource ID")
    args = p.parse_args()

    headers = {"User-Agent": "taxdesk-probe/0.1"}
    if TOKEN:
        headers["X-App-Token"] = TOKEN

    results: dict = {}
    failed = 0
    with httpx.Client(headers=headers) as client:
        targets = (
            {"adhoc": {"primary": args.resource, "alternates": [],
                       "expected": []}}
            if args.resource else CANDIDATES
        )
        for key, cfg in targets.items():
            print(f"\n== {key} ==", file=sys.stderr)
            r = probe(client, cfg["primary"])
            if not r or not r.get("ok"):
                print(f"  primary {cfg['primary']!r}: FAIL ({r})",
                      file=sys.stderr)
                for alt in cfg["alternates"]:
                    print(f"  trying alternate {alt!r}...", file=sys.stderr)
                    r2 = probe(client, alt)
                    if r2 and r2.get("ok"):
                        r = r2
                        r["used_alternate"] = alt
                        break
            if not r or not r.get("ok"):
                failed += 1
                results[key] = r or {"ok": False}
                continue

            cols = r["columns"]
            print(f"  resource: {r['resource']}", file=sys.stderr)
            print(f"  columns ({len(cols)}):", file=sys.stderr)
            for c in cols:
                print(f"    {c}", file=sys.stderr)
            missing = [e for e in cfg["expected"] if e not in cols]
            if missing:
                print(f"  ⚠  expected columns missing: {missing}",
                      file=sys.stderr)
                r["missing_expected"] = missing
                failed += 1
            results[key] = r

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(results, indent=2, default=str))
    print(f"\nwrote {OUT_PATH}", file=sys.stderr)

    if failed:
        print(f"\n{failed} probe(s) failed or missing columns. "
              f"Fix scraper/scrape.py before running --full-backfill.",
              file=sys.stderr)
        return 2
    print("\nall probes OK — safe to backfill.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
