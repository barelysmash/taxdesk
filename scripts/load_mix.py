#!/usr/bin/env python3
"""Load a Toast product mix export into taxdesk.

    python scripts/load_mix.py ProductMix_2025-08-12_2026-08-12.xlsx
    python scripts/load_mix.py export.xlsx --service-days 312
    python scripts/load_mix.py All_levels.csv --start 2026-09-19 --service-days 1

Accepts either an xlsx workbook or the All_levels.csv that the same Toast
report exports. The CSV carries the whole hierarchy in one file but no dates at
all, so --start is required for it; --end defaults to the day after --start.

The period otherwise comes from the filename (ProductMix_START_END.xlsx).

Idempotent: every row is INSERT OR REPLACE against a natural key, so reloading
the same export changes nothing. Loading an overlapping period does NOT merge —
each export is stored under its own period, and the API compares periods rather
than stitching them together.

Requires openpyxl. On guildenstern:
    sudo -u ocelia /opt/taxdesk/.venv/bin/pip install openpyxl
"""
from __future__ import annotations

import argparse
import logging
import os
import csv as csvmod
import re
import sqlite3
import sys
from contextlib import closing
from datetime import date, timedelta
from pathlib import Path

try:
    from openpyxl import load_workbook
except ImportError:
    sys.exit("openpyxl not installed. Run: pip install openpyxl")

LOG = logging.getLogger("taxdesk.mix")

DB_PATH = os.environ.get("TAXDESK_DB", "/var/lib/taxdesk/taxdesk.db")
APP_DIR = Path(os.environ.get("TAXDESK_APP", "/opt/taxdesk"))
DDL = APP_DIR / "sql" / "product_mix.sql"

# Menu groups counted as one cover each. A guest ordering two mains counts
# twice and a shared plate counts once, so this is an estimate — but it is the
# only cover proxy available in a product mix export, which has no check count.
COVER_GROUPS = {
    "platos fuertes", "enchiladas", "tacos", "rellenos",
    "chef specials", "de la tierra", "$90 menu", "backroom",
}

# Gift cards are deferred revenue, not a sale. Excluded from operating gross.
EXCLUDE_FROM_GROSS = {"gift card", "misc"}

# Which sheet supplies which level. A Toast export repeats the same money at
# several levels of nesting; loading all of them without the level tag would
# multiply the totals.
SHEETS = {
    "Menus": "menu",
    "Menu groups": "group",
    "Items": "item",
}

# Toast spells the item column differently between the workbook and the CSV,
# and the CSV header carries an embedded newline ("Item\n open item").
COLS = {
    "menu": "Menu",
    "menu_group": "Menu group",
    "subgroup": "Subgroup",
    "item": ("Item", "Item\n open item", "Item, open item"),
    "qty_sold": "Qty sold",
    "gross_amt": "Gross item amt",
    "net_amt": "Net item amt",
    "discount_amt": "Discount amt",
}


def parse_period(path: Path, start: str | None, end: str | None) -> tuple[str, str]:
    if start and end:
        return start, end
    if start:
        # A single day: end is exclusive, so it is the next morning.
        return start, (date.fromisoformat(start) + timedelta(days=1)).isoformat()
    m = re.search(r"(\d{4}-\d{2}-\d{2})[_-](\d{4}-\d{2}-\d{2})", path.name)
    if not m:
        sys.exit(f"cannot read a period from {path.name!r}; pass --start "
                 f"(and --end for a range longer than one day)")
    return m.group(1), m.group(2)


def _map_columns(head: list[str]) -> dict[str, int]:
    """Locate our columns in a header row, tolerating Toast's naming variants."""
    norm = [re.sub(r"\s+", " ", h).strip().lower() for h in head]
    idx = {}
    for key, labels in COLS.items():
        for label in (labels if isinstance(labels, tuple) else (labels,)):
            want = re.sub(r"\s+", " ", label).strip().lower()
            if want in norm:
                idx[key] = norm.index(want)
                break
    return idx


def read_csv(path: Path) -> list[dict]:
    """Read All_levels.csv, which holds every level of the hierarchy at once."""
    with path.open(newline="", encoding="utf-8-sig") as fh:
        raw = list(csvmod.reader(fh))
    if not raw:
        return []
    idx = _map_columns(raw[0])
    if "menu" not in idx:
        sys.exit(f"{path.name} has no Menu column; is this an All_levels export?")
    out = []
    for r in raw[1:]:
        rec = {}
        for key, i in idx.items():
            v = r[i] if i < len(r) else ""
            rec[key] = num(v) if key in ("qty_sold", "gross_amt", "net_amt", "discount_amt") \
                else (v or "").strip()
        if not any((rec.get("menu"), rec.get("menu_group"),
                    rec.get("subgroup"), rec.get("item"))):
            continue
        if rec.get("gross_amt") is None and rec.get("qty_sold") is None:
            continue
        out.append(rec)
    return out


def num(v):
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).replace("$", "").replace(",", "").strip())
    except ValueError:
        return None


def read_sheet(wb, sheet: str) -> list[dict]:
    """Read one sheet into dicts keyed by our column names."""
    if sheet not in wb.sheetnames:
        LOG.warning("sheet %r absent, skipping", sheet)
        return []
    ws = wb[sheet]
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return []
    head = [str(h).strip() if h is not None else "" for h in rows[0]]
    idx = _map_columns(head)
    out = []
    for raw in rows[1:]:
        rec = {}
        for key, i in idx.items():
            v = raw[i] if i < len(raw) else None
            rec[key] = num(v) if key in ("qty_sold", "gross_amt", "net_amt", "discount_amt") \
                else (str(v).strip() if v is not None else "")
        # A row is real if it names something and moved either money or units.
        names = (rec.get("menu", ""), rec.get("menu_group", ""),
                 rec.get("subgroup", ""), rec.get("item", ""))
        if not any(names):
            continue
        if rec.get("gross_amt") is None and rec.get("qty_sold") is None:
            continue
        out.append(rec)
    return out


def pick_level_rows(rows: list[dict], level: str) -> list[dict]:
    """Keep only the rows belonging to this level of the hierarchy.

    A Toast sheet lists parents and children together: the 'Menu groups' sheet
    holds both group totals (Subgroup blank) and subgroup breakdowns. Loading
    both under one level would double the money.
    """
    if level == "menu":
        # In the workbook each level has its own sheet, so "has a menu" is
        # enough. In All_levels.csv every row carries its full ancestry, so the
        # test must also require that nothing below the menu is filled in —
        # otherwise every row in the file counts as a menu total and the money
        # is summed several times over.
        return [r for r in rows
                if r.get("menu") and not r.get("menu_group")
                and not r.get("subgroup") and not r.get("item")]
    if level == "group":
        return [r for r in rows
                if r.get("menu_group") and not r.get("subgroup") and not r.get("item")]
    if level == "subgroup":
        return [r for r in rows if r.get("subgroup") and not r.get("item")]
    if level == "item":
        return [r for r in rows if r.get("item")]
    return []


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("export", type=Path)
    ap.add_argument("--db", default=DB_PATH)
    ap.add_argument("--ddl", type=Path, default=DDL)
    ap.add_argument("--start")
    ap.add_argument("--end")
    ap.add_argument("--service-days", type=int,
                    help="days actually open in the period. Fonda is closed Sunday, "
                         "so a full year is 312, not 365.")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")

    if not args.export.exists():
        sys.exit(f"no such file: {args.export}")
    start, end = parse_period(args.export, args.start, args.end)
    LOG.info("period %s to %s", start, end)

    staged: list[tuple] = []
    if args.export.suffix.lower() == ".csv":
        table = read_csv(args.export)
        LOG.info("%s: %d rows across all levels", args.export.name, len(table))
        sources = [(args.export.name, lvl, table)
                   for lvl in ("menu", "group", "subgroup", "item")]
    else:
        wb = load_workbook(args.export, read_only=True, data_only=True)
        sources = [(sheet, lvl, read_sheet(wb, sheet)) for sheet, lvl in SHEETS.items()]

    for label, level, table in sources:
        rows = pick_level_rows(table, level)
        LOG.info("%-14s -> level %-9s %5d rows", label[:14], level, len(rows))
        for r in rows:
            staged.append((
                start, end, level,
                r.get("menu", "") or "", r.get("menu_group", "") or "",
                r.get("subgroup", "") or "", r.get("item", "") or "",
                r.get("qty_sold"), r.get("gross_amt"),
                r.get("net_amt"), r.get("discount_amt"),
            ))

    # Covers and gross both come from the group level, which is the only one
    # that names menu groups without splitting them into items.
    group_rows = [s for s in staged if s[2] == "group"]
    covers = sum(s[7] or 0 for s in group_rows if s[4].strip().lower() in COVER_GROUPS)
    menu_rows = [s for s in staged if s[2] == "menu"]
    gross = sum(s[8] or 0 for s in menu_rows
                if s[3].strip().lower() not in EXCLUDE_FROM_GROSS)

    d0 = date.fromisoformat(start)
    d1 = date.fromisoformat(end)
    days = (d1 - d0).days
    service_days = args.service_days or days

    LOG.info("covers (entree-equivalents): %s", f"{covers:,.0f}")
    LOG.info("operating gross:             $%s", f"{gross:,.2f}")
    LOG.info("calendar days %d | service days %d", days, service_days)
    if covers:
        LOG.info("spend per cover:             $%.2f", gross / covers)
    if service_days:
        LOG.info("gross per service day:       $%s", f"{gross/service_days:,.0f}")

    if args.dry_run:
        LOG.info("dry run: %d rows staged, nothing written", len(staged))
        return 0

    with closing(sqlite3.connect(args.db)) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        if args.ddl.exists():
            conn.executescript(args.ddl.read_text())
        else:
            sys.exit(f"DDL not found at {args.ddl}; pass --ddl")
        conn.execute("""
            INSERT OR REPLACE INTO mix_period
                (period_start, period_end, days, service_days, covers, gross, source_file)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (start, end, days, service_days, covers, gross, args.export.name))
        conn.executemany("""
            INSERT OR REPLACE INTO product_mix
                (period_start, period_end, level, menu, menu_group, subgroup, item,
                 qty_sold, gross_amt, net_amt, discount_amt)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, staged)
        conn.commit()
        n = conn.execute(
            "SELECT COUNT(*) FROM product_mix WHERE period_start=? AND period_end=?",
            (start, end)).fetchone()[0]
        periods = conn.execute("SELECT COUNT(*) FROM mix_period").fetchone()[0]

    LOG.info("loaded %d rows for this period; %d period(s) in the database", n, periods)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
