#!/usr/bin/env python3
"""Load the nightly operations email into taxdesk.

    python scripts/load_ops.py 2026-09-19.txt
    python scripts/load_ops.py --date 2026-09-19 --stdin < pasted.txt
    cat email.txt | python scripts/load_ops.py --date 2026-09-19 --stdin
    python scripts/load_ops.py ops/ --dir          # every .txt in a folder

Save the email body to a text file named for its business date and run this.
The parser reads "Label: value" lines in any order and ignores anything it
does not recognise, so pasting the whole email including signatures is fine.

Expected labels, all optional:

    SPLH, Labor, Hours, Anniversary, Birthday, Manager, Voids,
    Reservations, Dining Room, Bar / Atrium, Total

This is the only source in taxdesk with a business date on every row, and the
only one with a real cover count. It is also the only daily sales signal on
days without a product mix export: SPLH multiplied by hours implies net sales.
"""
from __future__ import annotations

import argparse
import logging
import os
import re
import sqlite3
import sys
from contextlib import closing
from datetime import date
from pathlib import Path

LOG = logging.getLogger("taxdesk.ops")

DB_PATH = os.environ.get("TAXDESK_DB", "/var/lib/taxdesk/taxdesk.db")
APP_DIR = Path(os.environ.get("TAXDESK_APP", "/opt/taxdesk"))
DDL = APP_DIR / "sql" / "daily_ops.sql"

# Label as it appears in the email -> column. Matching is case-insensitive and
# ignores spacing and punctuation, so "Bar / Atrium", "bar/atrium" and
# "BAR / ATRIUM" all land in the same place.
FIELDS = {
    "splh": "splh",
    "labor": "labor_cost",
    "hours": "labor_hours",
    "anniversary": "comp_anniversary",
    "birthday": "comp_birthday",
    "manager": "comp_manager",
    "voids": "voids",
    "reservations": "reservations",
    "diningroom": "covers_dining",
    "baratrium": "covers_bar",
    "total": "covers_total",
}
INTS = {"reservations", "covers_dining", "covers_bar", "covers_total"}


def norm_label(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def parse_value(raw: str) -> float | None:
    """Pull a number out of '$ 4,903.21', '332.10', '(42.00)' or '253'."""
    t = raw.strip().replace("$", "").replace(",", "").strip()
    neg = t.startswith("(") and t.endswith(")")
    t = t.strip("()").strip()
    if not t:
        return None
    m = re.search(r"-?\d+(?:\.\d+)?", t)
    if not m:
        return None
    v = float(m.group(0))
    return -v if neg else v


def parse_email(text: str) -> dict:
    """Read every recognised 'Label: value' line. Unknown lines are ignored."""
    out: dict[str, float] = {}
    seen_unknown: list[str] = []
    for line in text.splitlines():
        if ":" not in line:
            continue
        label, _, raw = line.partition(":")
        key = FIELDS.get(norm_label(label))
        if key is None:
            lab = label.strip()
            if lab and len(lab) < 40:
                seen_unknown.append(lab)
            continue
        v = parse_value(raw)
        if v is None:
            continue
        out[key] = int(round(v)) if key in INTS else v
    if seen_unknown:
        LOG.debug("ignored labels: %s", ", ".join(sorted(set(seen_unknown))[:12]))
    return out


def date_from_name(path: Path) -> str | None:
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", path.name)
    if m:
        return m.group(0)
    # 09-19-26 or 09/19/26, as the email subject tends to write it
    m = re.search(r"(\d{2})[-_/](\d{2})[-_/](\d{2,4})", path.name)
    if m:
        mo, dy, yr = m.groups()
        yr = yr if len(yr) == 4 else f"20{yr}"
        return f"{yr}-{mo}-{dy}"
    return None


def check(rec: dict, when: str) -> None:
    """Warn on anything internally inconsistent. Never fatal — the email is the
    source of truth and a mismatch is worth seeing, not worth refusing."""
    d, b, t = rec.get("covers_dining"), rec.get("covers_bar"), rec.get("covers_total")
    if None not in (d, b, t) and d + b != t:
        LOG.warning("%s: dining %s + bar %s = %s, but Total says %s", when, d, b, d + b, t)
    splh, hrs = rec.get("splh"), rec.get("labor_hours")
    if splh and hrs:
        LOG.info("%s: implied net sales $%s", when, f"{splh * hrs:,.2f}")
    lc = rec.get("labor_cost")
    if lc and hrs:
        LOG.info("%s: average wage $%.2f/hr over %.2f hours", when, lc / hrs, hrs)
    if lc and splh and hrs:
        LOG.info("%s: labor is %.1f%% of implied net sales (hourly only)",
                 when, lc / (splh * hrs) * 100)
    if t and hrs:
        LOG.info("%s: %.2f covers per labor hour", when, t / hrs)


def load_one(conn: sqlite3.Connection, text: str, when: str, src: str) -> dict:
    rec = parse_email(text)
    if not rec:
        LOG.warning("%s: nothing recognised in %s", when, src)
        return {}
    check(rec, when)
    cols = ["business_date"] + list(rec) + ["source_file"]
    vals = [when] + list(rec.values()) + [src]
    marks = ",".join("?" * len(cols))
    conn.execute(
        f"INSERT OR REPLACE INTO daily_ops ({','.join(cols)}) VALUES ({marks})", vals)
    return rec


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source", nargs="?", type=Path,
                    help="a text file, or a folder with --dir")
    ap.add_argument("--date", help="business date, ISO. Required with --stdin "
                                   "and when the filename carries no date.")
    ap.add_argument("--stdin", action="store_true", help="read the email from stdin")
    ap.add_argument("--dir", action="store_true", help="load every .txt in the folder")
    ap.add_argument("--db", default=DB_PATH)
    ap.add_argument("--ddl", type=Path, default=DDL)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")

    jobs: list[tuple[str, str, str]] = []   # (business_date, text, source label)
    if args.stdin:
        if not args.date:
            sys.exit("--stdin needs --date")
        jobs.append((args.date, sys.stdin.read(), "stdin"))
    elif args.dir:
        if not args.source or not args.source.is_dir():
            sys.exit("--dir needs a folder")
        for f in sorted(args.source.glob("*.txt")):
            when = date_from_name(f) or args.date
            if not when:
                LOG.warning("skipping %s: no date in the filename", f.name)
                continue
            jobs.append((when, f.read_text(encoding="utf-8"), f.name))
    else:
        if not args.source or not args.source.is_file():
            sys.exit("pass a text file, a folder with --dir, or --stdin with --date")
        when = args.date or date_from_name(args.source)
        if not when:
            sys.exit(f"no date in {args.source.name!r}; pass --date")
        jobs.append((when, args.source.read_text(encoding="utf-8"), args.source.name))

    for when, _, _ in jobs:
        try:
            date.fromisoformat(when)
        except ValueError:
            sys.exit(f"not an ISO date: {when!r}")

    if args.dry_run:
        for when, text, src in jobs:
            rec = parse_email(text)
            LOG.info("%s (%s): %d fields", when, src, len(rec))
            for k, v in rec.items():
                LOG.info("    %-18s %s", k, v)
            check(rec, when)
        LOG.info("dry run: %d day(s) parsed, nothing written", len(jobs))
        return 0

    with closing(sqlite3.connect(args.db)) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        if not args.ddl.exists():
            sys.exit(f"DDL not found at {args.ddl}; pass --ddl")
        conn.executescript(args.ddl.read_text())
        n = 0
        for when, text, src in jobs:
            if load_one(conn, text, when, src):
                n += 1
        conn.commit()
        total = conn.execute("SELECT COUNT(*) FROM daily_ops").fetchone()[0]
        span = conn.execute(
            "SELECT MIN(business_date), MAX(business_date) FROM daily_ops").fetchone()

    LOG.info("loaded %d day(s); %d in the database, %s to %s", n, total, span[0], span[1])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
