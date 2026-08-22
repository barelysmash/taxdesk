import { useEffect, useMemo, useState } from "react";
import { ramp } from "./YoYHeatmap.jsx";

// Austin ZIPs on a coarse geographic grid: [col, row]. Not a projection --
// a legible arrangement that preserves rough adjacency. Edit freely.
const GRID = {
  "78727":[3,0], "78758":[3,1], "78753":[4,1], "78759":[2,1],
  "78731":[2,2], "78757":[3,2], "78752":[4,2], "78754":[5,2],
  "78756":[3,3], "78751":[4,3], "78723":[5,3],
  "78703":[2,4], "78705":[4,4], "78722":[5,4], "78721":[6,4],
  "78701":[3,5], "78702":[5,5],
  "78704":[3,6], "78741":[5,6], "78742":[6,6],
  "78745":[3,7], "78744":[4,7],
  "78748":[3,8], "78747":[4,8], "78749":[2,8], "78735":[1,7], "78746":[1,6],
};

const HOME = "78756";

const METRICS = {
  growth:    { label: "YoY growth",   fmt: v => v == null ? "\u2013" : `${v > 0 ? "+" : ""}${v.toFixed(1)}%` },
  per_venue: { label: "Per venue",    fmt: v => v == null ? "\u2013" : `$${(v/1000).toFixed(0)}K` },
  liquor_sh: { label: "Liquor share", fmt: v => v == null ? "\u2013" : `${v.toFixed(0)}%` },
};

export default function GeoPanel() {
  const [data, setData] = useState(null);
  const [metric, setMetric] = useState("growth");
  const [months, setMonths] = useState(12);

  useEffect(() => {
    setData(null);
    fetch(`/api/mb/geo?months=${months}`)
      .then(r => r.json())
      .then(d => setData(d.zips ?? []))
      .catch(() => setData([]));
  }, [months]);

  const byZip = useMemo(
    () => data ? Object.fromEntries(data.map(z => [z.zip, z])) : null,
    [data]
  );

  // Non-growth metrics are centred on their own median so the diverging ramp
  // reads as above/below typical rather than above/below zero.
  const median = useMemo(() => {
    if (!data || metric === "growth") return 0;
    const vals = data.map(z => z[metric]).filter(v => v != null).sort((a,b) => a-b);
    return vals.length ? vals[Math.floor(vals.length/2)] : 0;
  }, [data, metric]);

  if (!data) return <div className="empty">Loading map\u2026</div>;
  if (!data.length) return <div className="empty">No data yet.</div>;

  const cell = 62, gap = 3, cols = 8, rowsN = 9;
  const m = METRICS[metric];

  const shade = (z) => {
    if (!z || z[metric] == null) return "var(--rule-soft)";
    if (metric === "growth" && z.venues < 8) return "var(--rule-soft)";
    if (metric === "growth") return ramp(z[metric], 10);
    const spread = median * 0.5 || 1;
    return ramp((z[metric] - median) / spread * 10, 10);
  };

  const btn = (active) => ({
    background: "none", border: "none", cursor: "pointer", padding: "2px 0",
    fontFamily: "inherit", fontSize: 13,
    color: active ? "var(--ink)" : "var(--ink-soft)",
    borderBottom: active ? "2px solid var(--accent)" : "2px solid transparent",
  });

  return (
    <div>
      <div style={{ display:"flex", gap:16, alignItems:"baseline", marginBottom:12, flexWrap:"wrap" }}>
        {Object.entries(METRICS).map(([k,v]) => (
          <button key={k} onClick={() => setMetric(k)} style={btn(metric===k)}>{v.label}</button>
        ))}
        <span style={{ marginLeft:"auto", display:"flex", gap:10 }}>
          {[12,24].map(n => (
            <button key={n} onClick={() => setMonths(n)} style={btn(months===n)}>{n}mo</button>
          ))}
        </span>
      </div>

      <div style={{ overflowX:"auto" }}>
        <svg width={cols*(cell+gap)} height={rowsN*(cell+gap)}
             role="img" aria-label="Austin mixed beverage receipts by ZIP">
          {Object.entries(GRID).map(([zip,[c,r]]) => {
            const z = byZip[zip];
            const v = z ? z[metric] : null;
            const dark = metric === "growth" && v != null && Math.abs(v) > 6;
            return (
              <g key={zip} transform={`translate(${c*(cell+gap)},${r*(cell+gap)})`}>
                <rect width={cell} height={cell} rx="2" fill={shade(z)}
                      stroke={zip===HOME ? "var(--accent)" : "none"}
                      strokeWidth={zip===HOME ? 2.5 : 0} />
                <text x={cell/2} y={cell/2 - 6} textAnchor="middle" fontSize="11"
                      fill={dark ? "#fbf8f1" : "var(--ink-soft)"}>{zip}</text>
                <text x={cell/2} y={cell/2 + 11} textAnchor="middle" fontSize="13" fontWeight="500"
                      fill={dark ? "#fbf8f1" : "var(--ink)"}>{m.fmt(v)}</text>
                {z && (
                  <title>{`${zip}\n$${(z.total/1e6).toFixed(1)}M \u00b7 ${z.venues} venues\ngrowth ${z.growth ?? "\u2013"}% \u00b7 per venue $${((z.per_venue??0)/1000).toFixed(0)}K \u00b7 liquor ${z.liquor_sh}%`}</title>
                )}
              </g>
            );
          })}
        </svg>
      </div>

      <p style={{ fontSize:12, color:"var(--ink-soft)", marginTop:10, maxWidth:620 }}>
        Outlined cell is 78756. Growth is shaded against zero; per-venue and liquor share
        against the citywide median, so colour reads as above or below typical rather than
        absolute. Grey cells have no filings.
      </p>
    </div>
  );
}
