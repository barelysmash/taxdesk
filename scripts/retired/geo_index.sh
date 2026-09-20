#!/usr/bin/env bash
set -euo pipefail

LOCAL_SQL=/c/bSmash-dev/taxdesk/sql/geo.sql
REMOTE=ocelia@100.113.110.44
REMOTE_SQL=/opt/taxdesk/sql/geo.sql
DB=/var/lib/taxdesk/taxdesk.db

mkdir -p /c/bSmash-dev/taxdesk/sql

cat > "$LOCAL_SQL" <<'SQL'
-- ZIP-level aggregates for the mixed-beverage choropleth.
--
-- Leading columns mirror idx_mb_upper_city_date so the planner can serve the
-- existing top-N query from this index too; location_zip trails so GROUP BY
-- location_zip reads from the index instead of the table. Same expression-index
-- requirement as the city predicate: upper(location_city) needs an index on
-- that exact expression, a bare-column index cannot serve it.
CREATE INDEX IF NOT EXISTS idx_mb_upper_city_date_zip
    ON mixed_beverage (upper(location_city), obligation_end_date, location_zip);

SQL

echo "== deploying $LOCAL_SQL -> $REMOTE:$REMOTE_SQL"
MSYS_NO_PATHCONV=1 ssh "$REMOTE" "mkdir -p /opt/taxdesk/sql"
MSYS_NO_PATHCONV=1 scp "$LOCAL_SQL" "$REMOTE:$REMOTE_SQL"

echo "== applying"
MSYS_NO_PATHCONV=1 ssh "$REMOTE" "sqlite3 $DB < $REMOTE_SQL"

echo "== verifying"
MSYS_NO_PATHCONV=1 ssh "$REMOTE" "sqlite3 $DB \"
  SELECT name FROM sqlite_master
   WHERE type='index' AND name='idx_mb_upper_city_date_zip';
  EXPLAIN QUERY PLAN
  SELECT location_zip, SUM(total_receipts), SUM(liquor_receipts)
    FROM mixed_beverage
   WHERE upper(location_city)='AUSTIN'
     AND obligation_end_date >= date('2025-08-01')
   GROUP BY location_zip;\""
