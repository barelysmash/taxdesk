import { useEffect, useMemo, useState } from "react";
import { fmtPct } from "./format.js";

// Diverging ramp: oxblood (negative) → cream (zero) → green (positive).
// Saturates beyond ±cap so a single outlier doesn't wash out the rest.
function ramp(v, cap = 10) {
  if (v == null || isNaN(v)) return "var(--rule-soft)";
  const t = Math.max(-1, Math.min(1, v / cap));
  // cream baseline #f4f1ea (244,241,234)
  if (t < 0) {
    const a = Math.abs(t);
    const r = Math.round(244 + (110 - 244) * a);
    const g = Math.round(241 + ( 31 - 241) * a);
    const b = Math.round(234 + ( 35 - 234) * a);
    return `rgb(${r},${g},${b})`;
  }
  const r = Math.round(244 + ( 63 - 244) * t);
  const g = Math.round(241 + (107 - 241) * t);
  const b = Math.round(234 + ( 61 - 234) * t);
  return `rgb(${r},${g},${b})`;
}

const textOn = (v) => v != null && Math.abs(v) > 6 ? "#fbf8f1" : "#1a1612";

const MONTHS = ["J","F","M","A","M","J","J","A","S","O","N","D"];

export default function YoYHeatmap({ city = "AUSTIN" }) {
  const [rows, setRows] = useState(null);

  useEffect(() => {
    fetch(`/api/sales-tax/city/yoy?city=${encodeURIComponent(city)}`)
      .then(r => r.json())
      .then(d => setRows(d.data ?? []));
  }, [city]);

  // Build a {year: [12 vals]} matrix from the flat series.
  const matrix = useMemo(() => {
    if (!rows) return null;
    const grid = {};
    for (const r of rows) {
      grid[r.period_year] ||= Array(12).fill(null);
      grid[r.period_year][r.period_month - 1] = r.pct_change;
    }
    return grid;
  }, [rows]);

  if (!rows) return <div className="empty">Loading heatmap…</div>;
  if (!rows.length) return <div className="empty">No data yet.</div>;

  const years = Object.keys(matrix).sort();
  const cell = 38;
  const labelW = 34;

  return (
    <div style={{ overflowX: "auto", paddingBottom: 8 }}>
      <svg
        role="img"
        aria-label={`${city} year-over-year sales tax heatmap`}
        width={labelW + cell * 12 + 4}
        height={22 + cell * years.length + 4}
        style={{ fontFamily: "IBM Plex Mono, monospace" }}
      >
        {MONTHS.map((m, i) => (
          <text key={m + i}
                x={labelW + i * cell + cell / 2}
                y={14}
                textAnchor="middle"
                fontSize={10}
                fill="var(--ink-soft)"
                style={{ letterSpacing: "0.1em" }}>
            {m}
          </text>
        ))}
        {years.map((yr, ri) => {
          const y0 = 22 + ri * cell;
          return (
            <g key={yr}>
              <text x={labelW - 6} y={y0 + cell / 2 + 4}
                    textAnchor="end" fontSize={10} fill="var(--ink-soft)">
                {yr.slice(2)}
              </text>
              {matrix[yr].map((v, mi) => {
                const x = labelW + mi * cell;
                return (
                  <g key={mi}>
                    <rect x={x + 1} y={y0 + 1}
                          width={cell - 2} height={cell - 2}
                          fill={ramp(v)}
                          stroke="var(--rule)" strokeWidth={0.5} rx={2}>
                      <title>
                        {yr}-{String(mi + 1).padStart(2, "0")}: {fmtPct(v)}
                      </title>
                    </rect>
                    {v != null && (
                      <text x={x + cell / 2} y={y0 + cell / 2 + 4}
                            textAnchor="middle" fontSize={10}
                            fill={textOn(v)}
                            style={{ fontVariantNumeric: "tabular-nums",
                                     pointerEvents: "none" }}>
                        {fmtPct(v).replace(/\.0%$/, "%")}
                      </text>
                    )}
                  </g>
                );
              })}
            </g>
          );
        })}
      </svg>
      <Legend />
    </div>
  );
}

function Legend() {
  const stops = [];
  for (let i = -10; i <= 10; i += 2) stops.push(ramp(i));
  return (
    <div style={{
      display: "flex", alignItems: "center", gap: 8, marginTop: 10,
      fontSize: 10, color: "var(--ink-soft)",
      fontFamily: "IBM Plex Mono, monospace",
    }}>
      <span>−10%</span>
      <span style={{
        display: "inline-block",
        height: 8, width: 200,
        background: `linear-gradient(90deg, ${stops.join(",")})`,
        border: "0.5px solid var(--rule)",
        borderRadius: 2,
      }} />
      <span>+10%</span>
      <span style={{ marginLeft: 12, color: "var(--ink-soft)" }}>
        % change vs. prior year, same month
      </span>
    </div>
  );
}
