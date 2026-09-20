#!/usr/bin/env bash
set -euo pipefail
cd /c/bSmash-dev/taxdesk

echo "== 1. guard: panel must be imported"
grep -q "GeoPanel" web/src/App.jsx || { echo "   GeoPanel not wired into App.jsx"; exit 1; }

echo "== 2. build"
( cd web && npm run build )

echo "== 3. commit"
git add web/src/GeoPanel.jsx web/src/YoYHeatmap.jsx web/src/App.jsx scripts/geo_panel.sh scripts/geo_ship.sh
git commit -m "add GeoPanel: ZIP grid for MB receipts, growth, per-venue yield, liquor share" || echo "   nothing to commit"
git push

echo "== 4. deploy"
./scripts/deploy.sh web/dist

echo "== 5. verify"
curl -sS -o /dev/null -w '   /api/mb/geo -> %{http_code} in %{time_total}s\n' \
  'http://100.113.110.44:8770/api/mb/geo?months=12'
