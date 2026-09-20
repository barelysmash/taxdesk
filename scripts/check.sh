#!/usr/bin/env bash
#
# taxdesk check  —  runs ON rosencrantz (Git Bash / MINGW64)
#
# Asserts the invariants behind every bug that has cost real debugging time on
# this project. Each check exists because the thing it tests actually broke.
#
#   bash /c/bSmash-dev/taxdesk/scripts/check.sh            # static, on the repo
#   bash /c/bSmash-dev/taxdesk/scripts/check.sh --remote    # also check guildenstern
#
# Exit code is 0 only if everything passes, so this works as a pre-commit hook:
#   ln -s ../../scripts/check.sh /c/bSmash-dev/taxdesk/.git/hooks/pre-commit
#
# Run it after applying any patch. Twice now a patch authored against a stale
# copy of the tree has silently reverted an earlier fix, and both times the
# tell was visible before deployment.
#
set -uo pipefail   # deliberately NOT -e: we want every failure reported, not the first

REPO="${TAXDESK_REPO:-/c/bSmash-dev/taxdesk}"
REMOTE="${TAXDESK_REMOTE:-barelysmash@100.113.110.44}"
APP_DIR="${TAXDESK_APP:-/opt/taxdesk}"
DB="${TAXDESK_DB:-/var/lib/taxdesk/taxdesk.db}"
RUN_USER="${TAXDESK_USER:-ocelia}"
API_URL="${TAXDESK_API_URL:-http://100.113.110.44:8770}"

DO_REMOTE=0
[[ ${1:-} == --remote ]] && DO_REMOTE=1
[[ ${1:-} == -h || ${1:-} == --help ]] && { sed -n '2,18p' "$0"; exit 0; }

PASS=0; FAIL=0
say()  { printf '\n\033[1;35m== %s\033[0m\n' "$*"; }
ok()   { printf '   \033[32m✓\033[0m %s\n' "$*"; PASS=$((PASS+1)); }
bad()  { printf '   \033[1;31m✗ %s\033[0m\n' "$*"; FAIL=$((FAIL+1)); }
note() { printf '     \033[33m%s\033[0m\n' "$*"; }

SCRAPE="$REPO/scraper/scrape.py"
MAIN="$REPO/api/main.py"
SCHEMA="$REPO/sql/schema.sql"

# ------------------------------------------------------------ 1. watermark
# d301971. A strict '>' against MAX(obligation_end_date) let early filings lift
# the floor above the returns that post in arrears; the scraper then ingested
# nothing while exiting 0. Reverted once by 7c73344, which was authored against
# a pre-fix copy. Froze the data for ten weeks.
say "watermark (regression: data silently stops updating)"
if grep -q "obligation_end_date_yyyymmdd > '" "$SCRAPE"; then
  bad "strict '>' watermark is back in ingest_mixed_beverage()"
  note "incremental pulls will report mb: 0 forever; see README Caveats"
else
  ok "no strict '>' watermark"
fi
grep -q 'MB_LOOKBACK_MONTHS' "$SCRAPE"      && ok "MB_LOOKBACK_MONTHS defined"      || bad "MB_LOOKBACK_MONTHS missing"
grep -q 'ALLOC_LOOKBACK_PERIODS' "$SCRAPE"  && ok "ALLOC_LOOKBACK_PERIODS defined"  || bad "ALLOC_LOOKBACK_PERIODS missing"
grep -q "yyyymmdd >= '{floor}" "$SCRAPE"    && ok "lookback floor applied"          || bad "lookback floor not applied"

# refresh.sh derives the window from source; if that extraction breaks it falls
# back to 6 and the two can disagree without anyone noticing.
lb=$(sed -n 's/^MB_LOOKBACK_MONTHS *= *\([0-9][0-9]*\).*/\1/p' "$SCRAPE" | head -1)
if [[ -n $lb ]]; then ok "refresh.sh can read the lookback from source (=$lb)"
else bad "refresh.sh cannot parse MB_LOOKBACK_MONTHS; it will silently use 6"; fi

# ------------------------------------------------------------ 2. statewide
# 7c73344. The original parser matched markdown pipe tables against raw HTML —
# written against a rendered view of the page — so it never matched a byte.
say "statewide parser (regression: sales_tax_statewide stays empty)"
grep -q 'TABLE_ROW_RE' "$SCRAPE"   && { bad "markdown pipe-table regex is back"; note "it cannot match raw HTML; verify against the SOURCE, not a rendering"; } || ok "no markdown table regex"
grep -q '_TableParser' "$SCRAPE"   && ok "_TableParser (html.parser) present"    || bad "_TableParser missing"
# Match URL construction only. The docstring names the old form deliberately,
# so a bare grep for '?page=' reports the explanation as the bug.
if grep -qE '(f"|f.)[^"]*\?page=\{|NEWS_INDEX\}\?page=' "$SCRAPE"; then
  bad "?page=N pagination is back"
  note "the news index ignores it; paginate with fromDate/toDate"
else
  ok "no ?page=N pagination"
fi
grep -q 'fromDate=' "$SCRAPE"      && ok "date-range pagination present"          || bad "date-range pagination missing"
grep -q 'ytd_yoy_pct' "$SCRAPE"    && ok "ytd_yoy_pct populated"                  || bad "ytd_yoy_pct not written"

# ------------------------------------------------------- 2b. feature survival
# Patches written against a stale copy of the tree have silently reverted work
# three times: the lookback window, the watchlist seed, and — caught in the
# working tree rather than by this script — the whole GeoPanel. Naming every
# endpoint and panel makes that class of revert impossible to commit.
#
# When you add an endpoint or a panel, add it here. The cost is one line; the
# thing it prevents is deleting someone's feature and not noticing.
say "feature survival (regression: a stale-base patch deletes a feature)"
for ep in \
  "/api/health" "/api/sales-tax/city" "/api/sales-tax/city/yoy" \
  "/api/sales-tax/county" "/api/sales-tax/statewide" \
  "/api/mb/watchlist" "/api/mb/venue/{slug}" "/api/mb/austin/top" \
  "/api/mb/beta" "/api/mb/geo" "/api/mix/periods" "/api/mix/groups"
do
  if grep -qF "@app.get(\"$ep\")" "$MAIN"; then
    ok "endpoint $ep"
  else
    bad "endpoint $ep is MISSING from api/main.py"
    note "a patch built on an older copy of the file would do exactly this"
  fi
done

APP="$REPO/web/src/App.jsx"
if [[ -f $APP ]]; then
  for comp in GeoPanel MarketBeta OpsView YoYHeatmap Sparkline; do
    grep -q "$comp" "$APP" && ok "panel $comp" || bad "panel $comp is MISSING from App.jsx"
  done
else
  note "App.jsx not found, panel checks skipped"
fi

# ------------------------------------------------------------ 3. hot query
# 1baef13. 180s -> 23ms. Both halves matter: the scalar subquery and the
# expression index. Either one reverting brings the stall back.
say "hot query (regression: /api/mb/austin/top hangs, stalls the single worker)"
HAVE_SCHEMA=1; [[ -f $SCHEMA ]] || { HAVE_SCHEMA=0; note "schema.sql not present, schema checks skipped"; }
grep -q '(SELECT d FROM cutoff)' "$MAIN" && ok "cutoff is a scalar subquery" || { bad "cutoff is not a scalar subquery"; note "FROM mixed_beverage, cutoff re-drives the CTE: 23ms -> 180s"; }
# The docstring mentions the old form on purpose, so only flag it inside SQL.
if grep -q '^\s*FROM mixed_beverage, cutoff' "$MAIN"; then
  bad "cross join to cutoff present in the query"
else
  ok "no cross join to cutoff"
fi
if ((HAVE_SCHEMA)); then
  grep -q 'idx_mb_upper_city_date' "$SCHEMA" && ok "expression index declared in schema.sql" || bad "idx_mb_upper_city_date missing from schema.sql"
fi
if ((HAVE_SCHEMA)); then
  grep -q 'idx_mb_city_date_total' "$SCHEMA" && ok "idx_mb_city_date_total declared" || bad "idx_mb_city_date_total missing from schema.sql"
fi

# ------------------------------------------------------------ 4. watchlist
# 9b15112. Trailing '%' patterns matched unrelated venues and inflated totals.
# Reverted once by a schema.sql built from a stale base.
say "watchlist (regression: peer totals silently inflated)"
if [[ ! -f $SCHEMA ]]; then
  note "schema.sql not present, watchlist checks skipped"
else
# Uses python's sqlite3 module, not the sqlite3 CLI: Git Bash on Windows does
# not ship the CLI, so the original version skipped this whole section — the
# exact checks that would have caught the stale-base schema.sql revert — while
# still reporting "all invariants hold".
wl_out=$(python - "$SCHEMA" <<'PY' 2>&1
import sqlite3, sys
schema = open(sys.argv[1], encoding="utf-8").read()
def emit(good, msg, hint=""):
    print(("PASS|" if good else "FAIL|") + msg + ("|" + hint if hint else ""))
try:
    c = sqlite3.connect(":memory:")
    c.executescript(schema)
except Exception as e:
    emit(False, "schema.sql failed to execute: %s" % e); sys.exit(0)
emit(True, "schema.sql executes cleanly")

n = c.execute("SELECT COUNT(*) FROM venue_watchlist").fetchone()[0]
emit(n == 12, "12 venues seeded" if n == 12 else "expected 12 venues, found %d" % n,
     "" if n == 12 else "a stale-base schema.sql reverts this to 8")

row = c.execute("SELECT match_pattern FROM venue_watchlist WHERE slug='fonda_san_miguel'").fetchone()
f = row[0] if row else "<missing>"
emit(f == "SAN MIGUEL RESTAURANT",
     "fonda pattern is the filing name" if f == "SAN MIGUEL RESTAURANT" else "fonda pattern is %r" % f,
     "" if f == "SAN MIGUEL RESTAURANT" else "must be SAN MIGUEL RESTAURANT, not the trading name")

# MIDNIGHT COWBOY% is the one known-inert wildcard: that venue files nothing.
w = c.execute(r"SELECT COUNT(*) FROM venue_watchlist WHERE match_pattern LIKE '%\%' ESCAPE '\' AND slug <> 'midnight_cowboy'").fetchone()[0]
emit(w == 0, "no trailing-wildcard patterns" if w == 0 else "%d wildcard pattern(s) present" % w,
     "" if w == 0 else "'GARAGE%' style patterns match unrelated venues")

idx = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='mixed_beverage'")}
for want in ("idx_mb_upper_city_date", "idx_mb_city_date_total"):
    emit(want in idx, "%s created by schema.sql" % want)

try:
    c.executescript(schema)
    n2 = c.execute("SELECT COUNT(*) FROM venue_watchlist").fetchone()[0]
    emit(n2 == n, "schema.sql is idempotent" if n2 == n else "re-run changed venue count %d -> %d" % (n, n2))
except Exception as e:
    emit(False, "schema.sql is not idempotent: %s" % e)
PY
)
if [[ -z $wl_out ]]; then
  bad "watchlist checks produced no output (is python on PATH?)"
else
  while IFS='|' read -r verdict msg hint; do
    [[ -z ${verdict:-} ]] && continue
    if [[ $verdict == PASS ]]; then ok "$msg"; else bad "$msg"; [[ -n ${hint:-} ]] && note "$hint"; fi
  done <<<"$wl_out"
fi

fi

# ------------------------------------------------------------ 5. hygiene
# refresh.sh is piped into bash on Linux; CRLF makes it die as $'\r'.
say "file hygiene"
crlf=0
for f in "$REPO"/scripts/*.sh "$REPO"/scraper/*.py "$REPO"/api/*.py "$REPO"/sql/*.sql; do
  [[ -f $f ]] || continue
  if grep -qU $'\r' "$f" 2>/dev/null; then bad "CRLF in $(basename "$f")"; crlf=1; fi
done
((crlf)) && note "fix: sed -i 's/\\r\$//' <file>" || ok "no CRLF in files that run on Linux"

for f in "$SCRAPE" "$MAIN" "$REPO"/scraper/probe.py; do
  [[ -f $f ]] || continue
  python -c "import ast,sys; ast.parse(open(sys.argv[1],encoding='utf-8').read())" "$f" 2>/dev/null \
    && ok "$(basename "$f") parses" || bad "$(basename "$f") has a syntax error"
done

for f in "$REPO"/scripts/*.sh; do
  [[ -f $f ]] || continue
  bash -n "$f" 2>/dev/null && ok "$(basename "$f") parses" || bad "$(basename "$f") has a syntax error"
done

if [[ -f $REPO/.gitignore ]] && grep -q 'web/.env.local' "$REPO/.gitignore"; then
  ok "web/.env.local ignored"
else
  bad "web/.env.local not in .gitignore"
fi
if git -C "$REPO" ls-files --error-unmatch web/.env.local >/dev/null 2>&1; then
  bad "web/.env.local is TRACKED"
else
  ok "web/.env.local not tracked"
fi

# ------------------------------------------------------------ 6. remote
if ((DO_REMOTE)); then
  say "guildenstern"
  for svc in taxdesk-api taxdesk-scrape.timer; do
    st=$(ssh "$REMOTE" "systemctl is-active $svc" 2>/dev/null || echo unknown)
    [[ $st == active ]] && ok "$svc active" || bad "$svc is $st"
  done

  # Deployed file should match the repo, or the box is running something else.
  for rel in scraper/scrape.py api/main.py sql/schema.sql; do
    l=$(sed 's/\r$//' "$REPO/$rel" | sha256sum | cut -d' ' -f1)
    r=$(ssh "$REMOTE" "sha256sum '$APP_DIR/$rel'" 2>/dev/null | cut -d' ' -f1)
    [[ -n $r && $l == "$r" ]] && ok "$rel matches deployed copy" || bad "$rel differs from guildenstern"
  done

  plan=$(ssh "$REMOTE" "sudo -u $RUN_USER sqlite3 '$DB' \"EXPLAIN QUERY PLAN SELECT location_name, SUM(total_receipts) FROM mixed_beverage WHERE upper(location_city)='AUSTIN' AND obligation_end_date >= '2025-07-01' GROUP BY location_name;\"" 2>/dev/null)
  if grep -q 'idx_mb_upper_city_date' <<<"$plan"; then
    ok "query planner uses idx_mb_upper_city_date"
  else
    bad "expression index NOT used — the 180s stall will return"
    note "$plan"
  fi

  top=$(ssh "$REMOTE" "curl -s -o /dev/null -w '%{http_code} %{time_total}' '$API_URL/api/mb/austin/top?n=25&months=12'" 2>/dev/null) || top="000 0"
  c=${top%% *}; t=${top##* }
  if [[ $c == 200 ]] && awk "BEGIN{exit !($t < 2)}"; then ok "/api/mb/austin/top -> $c in ${t}s"
  else bad "/api/mb/austin/top -> $c in ${t}s"; fi

  gap=$(ssh "$REMOTE" "sudo -u $RUN_USER sqlite3 '$DB' \"SELECT (SELECT COUNT(*) FROM mixed_beverage WHERE substr(obligation_end_date,1,7) = (SELECT substr(obligation_end_date,1,7) FROM mixed_beverage GROUP BY 1 HAVING COUNT(*) >= 500 ORDER BY 1 DESC LIMIT 1));\"" 2>/dev/null)
  [[ -n $gap && $gap -ge 500 ]] && ok "latest complete month has $gap rows" || bad "no fully-reported month found"

  for t in sales_tax_statewide sales_tax_city sales_tax_county venue_watchlist; do
    n=$(ssh "$REMOTE" "sudo -u $RUN_USER sqlite3 '$DB' 'SELECT COUNT(*) FROM $t;'" 2>/dev/null)
    [[ -n $n && $n -gt 0 ]] && ok "$t has $n rows" || bad "$t is EMPTY"
  done
fi

# ------------------------------------------------------------------ summary
say "summary"
printf '   %d passed, %d failed\n' "$PASS" "$FAIL"
if ((FAIL)); then
  printf '\n   \033[1;31mDo not deploy.\033[0m Each failure above marks a bug that has already\n'
  printf '   shipped once on this project.\n\n'
  exit 1
fi
printf '\n   \033[32mAll invariants hold.\033[0m\n\n'
