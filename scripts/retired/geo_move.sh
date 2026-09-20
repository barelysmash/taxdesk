#!/usr/bin/env bash
set -euo pipefail
APP=/c/bSmash-dev/taxdesk/web/src/App.jsx
cp "$APP" "$APP.bak2"

BLOCK='      <section className="card" style={{ gridColumn: "1 / -1" }}>
        <h2>Mixed beverage by ZIP</h2>
        <div className="sub">Austin · trailing window vs. the window before it</div>
        <GeoPanel />
      </section>
'

echo "== 1. cut from SalesTaxView"
H2=$(grep -n '<h2>Mixed beverage by ZIP</h2>' "$APP" | cut -d: -f1)
[ -n "$H2" ] || { echo "   not found"; exit 1; }
sed -i "$((H2 - 1)),$((H2 + 3))d" "$APP"     # exactly 5 lines, no trailing blank
grep -c "GeoPanel" "$APP"                     # expect 1 (import only)

echo "== 2. insert into BeverageView"
BV=$(grep -n '^function BeverageView' "$APP" | cut -d: -f1)
FRAG=$(awk -v bv="$BV" 'NR > bv && /^    <>$/ {print NR; exit}' "$APP")
[ -n "$FRAG" ] || { echo "   fragment not found"; cp "$APP.bak2" "$APP"; exit 1; }

{ head -n "$FRAG" "$APP"; printf '%s\n' "$BLOCK"; tail -n +$((FRAG + 1)) "$APP"; } > /tmp/app.new
mv /tmp/app.new "$APP"

echo "== 3. verify both edges"
echo "--- SalesTaxView seam ---"
sed -n '136,142p' "$APP"
echo "--- BeverageView ---"
sed -n "$((FRAG - 1)),$((FRAG + 8))p" "$APP"
echo "rollback: cp $APP.bak2 $APP"
