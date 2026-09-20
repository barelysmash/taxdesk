#!/usr/bin/env bash
set -euo pipefail
SRC=/c/bSmash-dev/taxdesk/web/src
APP="$SRC/App.jsx"

grep -q "GeoPanel" "$APP" && { echo "already wired"; exit 0; }
cp "$APP" "$APP.bak"

echo "== import"
sed -i 's|^import YoYHeatmap from "./YoYHeatmap.jsx";|import YoYHeatmap from "./YoYHeatmap.jsx";\nimport GeoPanel from "./GeoPanel.jsx";|' "$APP"

echo "== section before the YoY heatmap card"
LINE=$(grep -n '<h2>Year-over-year heatmap</h2>' "$APP" | cut -d: -f1)
[ -n "$LINE" ] || { echo "   anchor not found"; cp "$APP.bak" "$APP"; exit 1; }
OPEN=$((LINE - 1))   # the <section> line above the h2

cat > /tmp/geo_section.txt <<'BLOCK'
      <section className="card" style={{ gridColumn: "1 / -1" }}>
        <h2>Mixed beverage by ZIP</h2>
        <div className="sub">Austin · trailing window vs. the window before it</div>
        <GeoPanel />
      </section>

BLOCK

head -n $((OPEN - 1)) "$APP" > /tmp/app.new
cat /tmp/geo_section.txt >> /tmp/app.new
tail -n +$OPEN "$APP" >> /tmp/app.new
mv /tmp/app.new "$APP"

echo "== result"
grep -n "GeoPanel" "$APP"
echo "rollback: cp $APP.bak $APP"
