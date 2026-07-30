#!/usr/bin/env bash
#
# taxdesk refresh pipeline  —  runs ON guildenstern
#
#   backup -> decide mode -> scrape -> verify -> export -> bundle
#
# Streamed over ssh by scripts/pull.sh, or run directly on the box:
#   sudo bash refresh.sh [--full|--incremental|--no-scrape] [--keep N]
#
# Idempotent. mixed_beverage has PRIMARY KEY
# (taxpayer_number, location_number, obligation_end_date) and the scraper uses
# INSERT OR REPLACE, so re-pulling overlapping windows cannot duplicate rows and
# amended filings correctly overwrite.
#
set -euo pipefail

APP_DIR="${TAXDESK_APP:-/opt/taxdesk}"
DB="${TAXDESK_DB:-/var/lib/taxdesk/taxdesk.db}"
ENV_FILE="${TAXDESK_ENV:-/etc/taxdesk/taxdesk.env}"
RUN_USER="${TAXDESK_USER:-ocelia}"
STAGE="${TAXDESK_STAGE:-/tmp/taxdesk-export}"
BUNDLE="${TAXDESK_BUNDLE:-/tmp/taxdesk-export.tgz}"
PY="$APP_DIR/.venv/bin/python"

# A month with at least this many mixed-beverage rows counts as fully reported.
# Austin files ~1,450/month; 500 is comfortably below the floor and well above
# the handful of early filings that cause the watermark ratchet.
MIN_FULL_MONTH_ROWS="${MIN_FULL_MONTH_ROWS:-500}"

MODE=auto
KEEP=5
TABLES=(mixed_beverage sales_tax_city sales_tax_county sales_tax_statewide venue_watchlist)

while [[ $# -gt 0 ]]; do
  case "$1" in
    --full)        MODE=full ;;
    --incremental) MODE=incremental ;;
    --no-scrape)   MODE=none ;;
    --keep)        KEEP="${2:?--keep needs a number}"; shift ;;
    -h|--help)     sed -n '2,20p' "$0"; exit 0 ;;
    *) echo "unknown flag: $1" >&2; exit 2 ;;
  esac
  shift
done

say()  { printf '\n\033[1;35m== %s\033[0m\n' "$*"; }
info() { printf '   %s\n' "$*"; }
warn() { printf '\033[1;33m   ! %s\033[0m\n' "$*"; }
die()  { printf '\033[1;31m   x %s\033[0m\n' "$*" >&2; exit 1; }

# All DB reads go through the runtime user so file ownership never drifts.
sq() { sudo -u "$RUN_USER" sqlite3 "$DB" "$@"; }

# ---------------------------------------------------------------- 0. preflight
say "preflight"
[[ -d $APP_DIR ]] || die "missing app dir: $APP_DIR"
[[ -x $PY ]]      || die "missing venv python: $PY"
[[ -f $DB ]]      || die "missing database: $DB"
command -v sqlite3 >/dev/null || die "sqlite3 not on PATH"
id "$RUN_USER" >/dev/null 2>&1 || die "no such user: $RUN_USER"
info "app  $APP_DIR"
info "db   $DB ($(sudo -u "$RUN_USER" stat -c %s "$DB" | numfmt --to=iec))"
info "mode $MODE"

integrity=$(sq "PRAGMA quick_check;")
[[ $integrity == ok ]] || die "quick_check failed: $integrity"
info "quick_check ok"

# ------------------------------------------------------------------- 1. backup
say "backup"
STAMP=$(date +%Y%m%d-%H%M%S)
BAK="${DB%.db}-bak-${STAMP}.db"
sq ".backup '$BAK'"
info "wrote $BAK ($(sudo -u "$RUN_USER" stat -c %s "$BAK" | numfmt --to=iec))"

# Prune oldest, keeping $KEEP. Never touches the live db or the manual
# taxdesk-prefix-* snapshot.
mapfile -t old < <(ls -1t "${DB%.db}"-bak-*.db 2>/dev/null | tail -n +$((KEEP + 1)) || true)
if ((${#old[@]})); then
  for f in "${old[@]}"; do sudo rm -f -- "$f"; info "pruned $(basename "$f")"; done
fi

# -------------------------------------------------------- 2. ratchet detection
# The incremental scrape queries obligation_end_date > MAX(obligation_end_date).
# A single early or amended filing lifts that floor above the bulk that posts a
# month later, and every subsequent run silently ingests nothing. The tell is
# MAX(month) sitting ahead of the newest fully-reported month.
say "watermark check"
MAX_YM=$(sq "SELECT substr(MAX(obligation_end_date),1,7) FROM mixed_beverage;")
LAST_FULL=$(sq "SELECT substr(obligation_end_date,1,7) FROM mixed_beverage
                GROUP BY 1 HAVING COUNT(*) >= $MIN_FULL_MONTH_ROWS
                ORDER BY 1 DESC LIMIT 1;")
info "max month        $MAX_YM"
info "last full month  $LAST_FULL  (>= $MIN_FULL_MONTH_ROWS rows)"

RATCHET=0
if [[ -n $MAX_YM && -n $LAST_FULL && $MAX_YM != "$LAST_FULL" ]]; then
  RATCHET=1
  STRAGGLERS=$(sq "SELECT COUNT(*) FROM mixed_beverage
                   WHERE substr(obligation_end_date,1,7) > '$LAST_FULL';")
  warn "watermark ratchet: $STRAGGLERS row(s) sit above $LAST_FULL"
  warn "incremental pulls will ingest nothing until this is cleared"
fi

EFFECTIVE=$MODE
if [[ $MODE == auto ]]; then
  if ((RATCHET)); then EFFECTIVE=full; info "auto -> full (ratchet detected)"
  else                 EFFECTIVE=incremental; info "auto -> incremental"; fi
fi

# -------------------------------------------------------------------- 3. scrape
if [[ $EFFECTIVE == none ]]; then
  say "scrape (skipped)"
else
  say "scrape ($EFFECTIVE)"
  ARGS=()
  [[ $EFFECTIVE == full ]] && ARGS+=(--full-backfill)
  BEFORE=$(sq "SELECT COUNT(*) FROM mixed_beverage;")

  # cd is load-bearing: python -m needs $APP_DIR on sys.path.
  set +e
  sudo -u "$RUN_USER" bash -c "
    cd '$APP_DIR' || exit 1
    set -a; [ -f '$ENV_FILE' ] && . '$ENV_FILE'; set +a
    '$PY' -m scraper.scrape ${ARGS[*]:-}
  " 2>&1 | tail -30
  rc=${PIPESTATUS[0]}
  set -e
  ((rc == 0)) || die "scraper exited $rc — database unchanged beyond backup at $BAK"

  AFTER=$(sq "SELECT COUNT(*) FROM mixed_beverage;")
  info "mixed_beverage rows: $BEFORE -> $AFTER (net $((AFTER - BEFORE)))"
fi

# -------------------------------------------------------------------- 4. verify
say "verify"
printf '   %-9s %8s %16s\n' month rows receipts
sq -separator '|' "
  SELECT substr(obligation_end_date,1,7) AS m,
         COUNT(*),
         ROUND(SUM(total_receipts))
  FROM mixed_beverage GROUP BY m ORDER BY m DESC LIMIT 8;" |
while IFS='|' read -r m rows rcpt; do
  flag=""
  (( rows < MIN_FULL_MONTH_ROWS )) && flag="  <- partial"
  printf '   %-9s %8s %16s%s\n' "$m" "$rows" "$rcpt" "$flag"
done

info ""
for t in "${TABLES[@]}"; do
  n=$(sq "SELECT COUNT(*) FROM $t;")
  [[ $n == 0 ]] && warn "$t is EMPTY" || printf '   %-22s %8s rows\n' "$t" "$n"
done

POST_MAX=$(sq "SELECT substr(MAX(obligation_end_date),1,7) FROM mixed_beverage;")
POST_FULL=$(sq "SELECT substr(obligation_end_date,1,7) FROM mixed_beverage
                GROUP BY 1 HAVING COUNT(*) >= $MIN_FULL_MONTH_ROWS
                ORDER BY 1 DESC LIMIT 1;")
info ""
info "complete through $POST_FULL  (max obligation month $POST_MAX)"
if [[ $POST_MAX != "$POST_FULL" ]]; then
  warn "stragglers still sit above the last full month"
  warn "the next INCREMENTAL run will ingest 0 rows — this run must stay --full"
  warn "permanent fix: lookback window in ingest_mixed_beverage(), not a strict >"
fi

# -------------------------------------------------------------------- 5. export
say "export"
sudo rm -rf "$STAGE"; sudo -u "$RUN_USER" mkdir -p "$STAGE"
for t in "${TABLES[@]}"; do
  sudo -u "$RUN_USER" sqlite3 -header -csv "$DB" "SELECT * FROM $t;" \
    > "$STAGE/$t.csv"
  printf '   %-22s %8s\n' "$t.csv" "$(stat -c %s "$STAGE/$t.csv" | numfmt --to=iec)"
done

cat > "$STAGE/MANIFEST.txt" <<EOF
taxdesk export
generated      $(date -Iseconds)
host           $(hostname)
database       $DB
backup         $BAK
scrape mode    $EFFECTIVE
ratchet found  $((RATCHET))
complete thru  $POST_FULL
max obligation $POST_MAX
mb rows        $(sq "SELECT COUNT(*) FROM mixed_beverage;")
EOF
info "MANIFEST.txt"

# -------------------------------------------------------------------- 6. bundle
say "bundle"
sudo rm -f "$BUNDLE"
tar czf "$BUNDLE" -C "$(dirname "$STAGE")" "$(basename "$STAGE")"
info "$BUNDLE ($(stat -c %s "$BUNDLE" | numfmt --to=iec))"

say "done"
cat "$STAGE/MANIFEST.txt" | sed 's/^/   /'
