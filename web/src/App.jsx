import { useEffect, useState } from "react";
import {
  ResponsiveContainer, LineChart, Line, BarChart, Bar,
  XAxis, YAxis, Tooltip, CartesianGrid, Legend, ReferenceLine,
} from "recharts";
import { fmtUSD, fmtPct, fmtPeriod, deltaClass } from "./format.js";
import YoYHeatmap from "./YoYHeatmap.jsx";

const SECTIONS = [
  { id: "sales",     label: "Sales Tax" },
  { id: "transit",   label: "CapMetro & ESDs" },
  { id: "beverage",  label: "Mixed Beverage" },
  { id: "ops",      label: "Operations" },
];

const AXIS = { fontSize: 11, fontFamily: "IBM Plex Mono" };
const GRID = "#e8e1d2";
const INK  = "#1a1612";
const OX   = "#6e1f23";
const GOLD = "#a87f2f";
const GREEN= "#3f6b3d";

export default function App() {
  const [section, setSection] = useState("sales");
  const today = new Date().toLocaleDateString("en-US", {
    year: "numeric", month: "long", day: "numeric",
  });

  return (
    <>
      <header className="masthead">
        <h1>tax<em>desk</em></h1>
        <span className="tag">vol. 1</span>
        <span className="tag">austin · travis</span>
        <span className="date">{today}</span>
      </header>
      <p className="dek">
        Monthly sales-tax allocations and mixed-beverage receipts from the Texas
        Comptroller, with year-over-year comparisons across the Austin trade area.
      </p>
      <nav className="sections" role="tablist">
        {SECTIONS.map(s => (
          <button key={s.id}
                  role="tab"
                  aria-selected={section === s.id}
                  onClick={() => setSection(s.id)}>
            {s.label}
          </button>
        ))}
      </nav>
      <main>
        {section === "sales"    && <SalesTaxView />}
        {section === "transit"  && <TransitView />}
        {section === "beverage" && <BeverageView />}
        {section === "ops"      && <OpsView />}
      </main>
      <footer className="colophon">
        <span>Source · Texas Comptroller of Public Accounts</span>
        <span>guildenstern · taxdesk/0.1</span>
      </footer>
    </>
  );
}

// ---------------------------------------------------------------------------
// Sales Tax — Austin city allocations + statewide context
// ---------------------------------------------------------------------------

function SalesTaxView() {
  const [city, setCity] = useState(null);
  const [state, setState] = useState(null);

  useEffect(() => {
    fetch("/api/sales-tax/city?city=AUSTIN&years=5").then(r => r.json()).then(setCity);
    fetch("/api/sales-tax/statewide?years=5").then(r => r.json()).then(setState);
  }, []);

  const series = (city?.series ?? []).map(r => ({
    ...r,
    label: fmtPeriod(r.period_year, r.period_month),
  }));
  const latest = series.at(-1);

  return (
    <div className="grid cols-2">
      <section className="card">
        <h2>City of Austin · 1% local share</h2>
        <div className="sub">Monthly allocation · last 5 years</div>
        {latest && (
          <div className="kpi">
            <span className="v">{fmtUSD(latest.net_payment, { compact: true })}</span>
            <span className={`delta ${deltaClass(latest.pct_change)}`}>
              YoY {fmtPct(latest.pct_change)}
            </span>
            <span className="mono" style={{ fontSize: 11, color: "#4a423a" }}>
              {latest.label}
            </span>
          </div>
        )}
        <ResponsiveContainer width="100%" height={260}>
          <LineChart data={series} margin={{ top: 8, right: 12, left: 0, bottom: 0 }}>
            <CartesianGrid stroke={GRID} vertical={false} />
            <XAxis dataKey="label" tick={AXIS} stroke={INK}
                   interval={Math.ceil(series.length / 8)} />
            <YAxis tick={AXIS} stroke={INK}
                   tickFormatter={v => fmtUSD(v, { compact: true })} />
            <Tooltip content={<TaxTooltip />} />
            <Line type="monotone" dataKey="net_payment"
                  stroke={OX} strokeWidth={1.75} dot={false}
                  name="Allocation" />
            <Line type="monotone" dataKey="comparable_prior"
                  stroke={INK} strokeWidth={1} strokeDasharray="3 3"
                  dot={false} name="Prior year" />
          </LineChart>
        </ResponsiveContainer>
      </section>

      <section className="card">
        <h2>Year-over-year change</h2>
        <div className="sub">Austin city · % delta vs. prior year</div>
        <ResponsiveContainer width="100%" height={300}>
          <BarChart data={series} margin={{ top: 8, right: 12, left: 0, bottom: 0 }}>
            <CartesianGrid stroke={GRID} vertical={false} />
            <XAxis dataKey="label" tick={AXIS} stroke={INK}
                   interval={Math.ceil(series.length / 8)} />
            <YAxis tick={AXIS} stroke={INK} tickFormatter={v => `${v}%`} />
            <ReferenceLine y={0} stroke={INK} />
            <Tooltip content={<PctTooltip />} />
            <Bar dataKey="pct_change" name="YoY %">
              {series.map((r, i) => (
                <Bar key={i} dataKey="pct_change"
                     fill={(r.pct_change ?? 0) >= 0 ? GREEN : OX} />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </section>

      <section className="card" style={{ gridColumn: "1 / -1" }}>
        <h2>Year-over-year heatmap</h2>
        <div className="sub">Austin · % change per month, color-coded across years</div>
        <YoYHeatmap city="AUSTIN" />
      </section>

      <section className="card" style={{ gridColumn: "1 / -1" }}>
        <h2>Statewide context</h2>
        <div className="sub">All Texas locals · monthly total + YoY</div>
        <StatewideTable rows={state?.series ?? []} />
      </section>
    </div>
  );
}

function StatewideTable({ rows }) {
  if (!rows.length) return <div className="empty">No statewide data yet.</div>;
  const recent = [...rows].reverse().slice(0, 14);
  return (
    <table className="ledger">
      <thead>
        <tr>
          <th>Period</th>
          <th className="r" style={{ textAlign: "right" }}>Total</th>
          <th className="r" style={{ textAlign: "right" }}>Cities</th>
          <th className="r" style={{ textAlign: "right" }}>Counties</th>
          <th className="r" style={{ textAlign: "right" }}>Transit</th>
          <th className="r" style={{ textAlign: "right" }}>SPDs</th>
          <th className="r" style={{ textAlign: "right" }}>YoY</th>
        </tr>
      </thead>
      <tbody>
        {recent.map((r, i) => (
          <tr key={i}>
            <td className="mono">{fmtPeriod(r.period_year, r.period_month)}</td>
            <td className="r">{fmtUSD(r.total_allocations, { compact: true })}</td>
            <td className="r">{fmtUSD(r.cities_total, { compact: true })}</td>
            <td className="r">{fmtUSD(r.counties_total, { compact: true })}</td>
            <td className="r">{fmtUSD(r.transit_total, { compact: true })}</td>
            <td className="r">{fmtUSD(r.spd_total, { compact: true })}</td>
            <td className="r" style={{
              color: (r.yoy_pct ?? 0) >= 0 ? GREEN : OX,
            }}>{fmtPct(r.yoy_pct)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

// ---------------------------------------------------------------------------
// Transit / ESDs / MUDs
// ---------------------------------------------------------------------------

function TransitView() {
  const [data, setData] = useState(null);
  useEffect(() => {
    fetch("/api/sales-tax/county?years=5").then(r => r.json()).then(setData);
  }, []);

  if (!data) return <div className="empty">Loading…</div>;

  // Group rows by entity
  const byEntity = {};
  for (const r of data.series) {
    (byEntity[r.entity_name] ||= []).push({
      ...r, label: fmtPeriod(r.period_year, r.period_month),
    });
  }
  const ordered = Object.entries(byEntity).sort((a, b) => {
    const lastA = a[1].at(-1)?.net_payment ?? 0;
    const lastB = b[1].at(-1)?.net_payment ?? 0;
    return lastB - lastA;
  });

  return (
    <div className="grid cols-2">
      {ordered.map(([name, rows]) => {
        const latest = rows.at(-1);
        return (
          <section className="card" key={name}>
            <h2 style={{ fontSize: 18 }}>{name}</h2>
            <div className="sub">{rows.at(-1)?.entity_type ?? "ENTITY"}</div>
            {latest && (
              <div className="kpi">
                <span className="v">
                  {fmtUSD(latest.net_payment, { compact: true })}
                </span>
                <span className={`delta ${deltaClass(latest.pct_change)}`}>
                  {fmtPct(latest.pct_change)}
                </span>
              </div>
            )}
            <ResponsiveContainer width="100%" height={140}>
              <LineChart data={rows}>
                <XAxis dataKey="label" hide />
                <YAxis hide />
                <Tooltip content={<TaxTooltip />} />
                <Line type="monotone" dataKey="net_payment"
                      stroke={GOLD} strokeWidth={1.5} dot={false} />
              </LineChart>
            </ResponsiveContainer>
          </section>
        );
      })}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Mixed Beverage
// ---------------------------------------------------------------------------

function BeverageView() {
  const [data, setData]   = useState(null);
  const [top,  setTop]    = useState(null);
  const [beta, setBeta]   = useState(null);

  useEffect(() => {
    fetch("/api/mb/watchlist").then(r => r.json()).then(setData);
    fetch("/api/mb/austin/top?n=25&months=12").then(r => r.json()).then(setTop);
    fetch("/api/mb/beta?months=36").then(r => r.json()).then(setBeta);
  }, []);

  if (!data) return <div className="empty">Loading…</div>;

  const buckets = { home: [], mexican: [], cocktail: [], alumni: [] };
  for (const v of data.venues) (buckets[v.bucket] ||= []).push(v);

  return (
    <>
      <div className="grid cols-2">
        <VenueBucket title="Fonda San Miguel"     venues={buckets.home}     accent={OX} />
        <VenueBucket title="Mexican fine dining"  venues={buckets.mexican}  accent={GOLD} />
        <VenueBucket title="Cocktail-forward"     venues={buckets.cocktail} accent={GREEN} />
      </div>
      <MarketBeta data={beta} />
      <section className="card" style={{ marginTop: 20 }}>
        <h2>Austin · top 25 venues, trailing 12 months</h2>
        <div className="sub">By total mixed-beverage gross receipts</div>
        <TopTable rows={top?.top ?? []} />
      </section>
    </>
  );
}

function VenueBucket({ title, venues, accent }) {
  return (
    <section className="card">
      <h2>{title}</h2>
      <div className="sub">{venues.length} venue{venues.length === 1 ? "" : "s"}</div>
      {venues.length === 0 && <div className="empty">No matches in dataset.</div>}
      {venues.map(v => {
        const series = (v.series ?? []).map(p => ({
          label: p.obligation_end_date.slice(0, 7),
          total: Number(p.total),
        }));
        const latest = series.at(-1);
        const prior  = series.length >= 13 ? series.at(-13) : null;
        const yoy = prior && prior.total
          ? ((latest.total - prior.total) / prior.total) * 100 : null;

        return (
          <div key={v.slug} style={{
            paddingTop: 10, marginTop: 10, borderTop: "1px dotted var(--rule)",
          }}>
            <div style={{ display: "flex", alignItems: "baseline", gap: 10 }}>
              <strong style={{ fontSize: 14 }}>{v.display_name}</strong>
              <span className={`bucket-pill ${v.bucket}`}>{v.bucket}</span>
              {latest && (
                <span className="num" style={{ marginLeft: "auto", fontSize: 13 }}>
                  {fmtUSD(latest.total, { compact: true })}
                </span>
              )}
              {yoy != null && (
                <span className={`delta ${deltaClass(yoy)}`}
                      style={{ fontSize: 11 }}>
                  {fmtPct(yoy)}
                </span>
              )}
            </div>
            <ResponsiveContainer width="100%" height={70}>
              <LineChart data={series}>
                <XAxis dataKey="label" hide />
                <YAxis hide />
                <Tooltip content={<MBTooltip />} />
                <Line type="monotone" dataKey="total"
                      stroke={accent} strokeWidth={1.5} dot={false} />
              </LineChart>
            </ResponsiveContainer>
          </div>
        );
      })}
    </section>
  );
}

function MarketBeta({ data }) {
  if (!data) return null;
  const { market, venues } = data;
  const ttm   = market.ttm_total;
  const prior = market.prior_ttm_total;
  const drift = prior ? (ttm / prior - 1) * 100 : null;

  // Beta only means something when the venue actually tracks the market. Below
  // this, month-to-month noise dominates and the slope is not interpretable.
  const MIN_CORR = 0.4;

  return (
    <section className="card" style={{ marginTop: 20 }}>
      <h2>Market resilience · sensitivity to Austin</h2>
      <div className="sub">
        Beta is how far a venue moves when the whole Austin market moves.
        Below 1.00 means it falls less than the market in a downturn.
      </div>

      <div className="grid cols-2" style={{ marginTop: 12, marginBottom: 16 }}>
        <div>
          <div className="sub">Austin market, trailing 12 months</div>
          <div style={{ fontSize: 22, fontFamily: "Instrument Serif, serif" }}>
            {fmtUSD(ttm, { compact: true })}
          </div>
          {drift != null && (
            <div className={deltaClass(drift)} style={{ fontSize: 12 }}>
              {fmtPct(drift)} vs prior twelve months
            </div>
          )}
        </div>
        <div>
          <div className="sub">Market monthly volatility</div>
          <div style={{ fontSize: 22, fontFamily: "Instrument Serif, serif" }}>
            {(market.volatility * 100).toFixed(1)}%
          </div>
          <div className="sub" style={{ fontSize: 12 }}>
            {market.series.length} months to {market.series.at(-1)?.ym}
          </div>
        </div>
      </div>

      <table className="ledger">
        <thead>
          <tr>
            <th>Venue</th>
            <th className="r" style={{ textAlign: "right" }}>Beta</th>
            <th className="r" style={{ textAlign: "right" }}>Correlation</th>
            <th className="r" style={{ textAlign: "right" }}>Own volatility</th>
            <th className="r" style={{ textAlign: "right" }}>TTM receipts</th>
          </tr>
        </thead>
        <tbody>
          {venues.map(v => {
            const weak = v.corr < MIN_CORR;
            return (
              <tr key={v.slug}>
                <td style={{ fontWeight: v.bucket === "home" ? 600 : 400 }}>
                  {v.display_name}
                </td>
                <td className="r mono" style={{ color: weak ? "var(--ink-soft)" : undefined }}>
                  {v.beta.toFixed(2)}
                </td>
                <td className="r mono" style={{ color: "var(--ink-soft)", fontSize: 12 }}>
                  {v.corr.toFixed(2)}{weak ? " · weak" : ""}
                </td>
                <td className="r mono" style={{ fontSize: 12 }}>
                  {(v.volatility * 100).toFixed(1)}%
                </td>
                <td className="r">{fmtUSD(v.ttm_total, { compact: true })}</td>
              </tr>
            );
          })}
        </tbody>
      </table>

      <div className="sub" style={{ marginTop: 10, fontSize: 12 }}>
        Rows marked weak have a correlation below {MIN_CORR.toFixed(1)}; their beta is
        noise rather than signal. The most recent month is excluded because
        filings arrive in arrears.
      </div>
    </section>
  );
}


// ---------------------------------------------------------------------------
// Operations — internal product mix. Unlike every other view, this data is not
// scraped: it is loaded from Toast exports by scripts/load_mix.py.
// ---------------------------------------------------------------------------

function OpsView() {
  const [periods, setPeriods] = useState(null);
  const [groups,  setGroups]  = useState(null);
  const [sel,     setSel]     = useState(null);

  useEffect(() => {
    fetch("/api/mix/periods").then(r => r.json()).then(d => {
      setPeriods(d);
      if (d.periods?.length) setSel(d.periods[0].period_start);
    });
  }, []);

  useEffect(() => {
    const q = sel ? `?period_start=${encodeURIComponent(sel)}` : "";
    fetch(`/api/mix/groups${q}`).then(r => r.json()).then(setGroups);
  }, [sel]);

  if (!periods) return <div className="sub">Loading…</div>;

  if (!periods.periods?.length) {
    return (
      <section className="card">
        <h2>No product mix loaded</h2>
        <div className="sub">
          Export a product mix from Toast and load it with:
          <pre style={{ marginTop: 8, fontSize: 12 }}>
{`python scripts/load_mix.py ProductMix_START_END.xlsx --service-days N`}
          </pre>
          Fonda is closed Sunday, so a full year is 312 service days, not 365.
        </div>
      </section>
    );
  }

  const cur = periods.periods.find(p => p.period_start === sel) || periods.periods[0];

  return (
    <>
      <section className="card">
        <h2>Operations · product mix</h2>
        <div className="sub">
          Internal POS data, loaded manually. Not scraped, and not comparable to
          the market figures elsewhere in taxdesk.
        </div>

        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", margin: "12px 0" }}>
          {periods.periods.map(p => (
            <button key={p.period_start}
                    onClick={() => setSel(p.period_start)}
                    aria-pressed={p.period_start === sel}
                    style={{
                      fontSize: 12,
                      fontWeight: p.period_start === sel ? 600 : 400,
                      opacity:    p.period_start === sel ? 1 : 0.6,
                    }}>
              {p.period_start} → {p.period_end}
            </button>
          ))}
        </div>

        <div className="grid cols-2" style={{ marginTop: 12 }}>
          <div className="kpi">
            <span className="v">{fmtUSD(cur.gross, { compact: true })}</span>
            <span className="mono" style={{ fontSize: 11, color: "#4a423a" }}>operating gross · ex gift cards</span>
          </div>
          <div className="kpi">
            <span className="v">{fmtUSD(cur.spend_per_cover)}</span>
            <span className="mono" style={{ fontSize: 11, color: "#4a423a" }}>spend per cover</span>
          </div>
          <div className="kpi">
            <span className="v">{cur.covers?.toLocaleString()}</span>
            <span className="mono" style={{ fontSize: 11, color: "#4a423a" }}>covers · entree-equivalents</span>
          </div>
          <div className="kpi">
            <span className="v">{fmtUSD(cur.gross_per_service_day, { compact: true })}</span>
            <span className="mono" style={{ fontSize: 11, color: "#4a423a" }}>gross per service day</span>
          </div>
        </div>
        <div className="sub" style={{ marginTop: 10, fontSize: 12 }}>
          {cur.service_days} service days of {cur.days} calendar days ·{" "}
          {cur.covers_per_service_day} covers per service day · covers are
          entree-equivalents, not a check count
        </div>
      </section>

      {groups?.groups?.length > 0 && (
        <section className="card" style={{ marginTop: 20 }}>
          <h2>Menu groups</h2>
          <div className="sub">
            {groups.compared_to
              ? `Per-cover change against ${groups.compared_to.period_start} → ${groups.compared_to.period_end}. `
              : "Load a second period to enable comparison. "}
            Per-cover is the comparable figure: it strips out both period length
            and how busy the restaurant was.
          </div>
          <table className="ledger" style={{ marginTop: 12 }}>
            <thead>
              <tr>
                <th>Menu group</th>
                <th style={{ textAlign: "right" }}>Gross per day</th>
                <th style={{ textAlign: "right" }}>Units per day</th>
                <th style={{ textAlign: "right" }}>Units per cover</th>
                <th style={{ textAlign: "right" }}>Per-cover change</th>
              </tr>
            </thead>
            <tbody>
              {groups.groups.map(g => {
                const ch = g.units_per_cover_change;
                return (
                  <tr key={g.menu_group}>
                    <td>{g.menu_group}</td>
                    <td className="r">{fmtUSD(g.gross_per_day)}</td>
                    <td className="r mono">{g.units_per_day.toFixed(1)}</td>
                    <td className="r mono">{g.units_per_cover.toFixed(2)}</td>
                    <td className={`r ${ch != null ? deltaClass(ch * 100) : ""}`}>
                      {ch != null ? fmtPct(ch * 100) : "—"}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </section>
      )}
    </>
  );
}

function TopTable({ rows }) {
  if (!rows.length) return <div className="empty">No data yet.</div>;
  return (
    <table className="ledger">
      <thead>
        <tr>
          <th>#</th><th>Venue</th><th>Address</th>
          <th className="r" style={{ textAlign: "right" }}>TTM Receipts</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r, i) => (
          <tr key={i}>
            <td className="mono">{String(i + 1).padStart(2, "0")}</td>
            <td>{r.location_name}</td>
            <td style={{ color: "var(--ink-soft)", fontSize: 12 }}>
              {r.address}
            </td>
            <td className="r">{fmtUSD(r.total, { compact: true })}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

// ---------------------------------------------------------------------------
// Tooltips
// ---------------------------------------------------------------------------

function TaxTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null;
  return (
    <div style={tooltipBox}>
      <div className="mono" style={{ fontSize: 10, color: "#4a423a" }}>{label}</div>
      {payload.map((p, i) => (
        <div key={i} className="num" style={{ fontSize: 12, color: p.color }}>
          {p.name}: {fmtUSD(p.value, { compact: false })}
        </div>
      ))}
    </div>
  );
}

function PctTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null;
  return (
    <div style={tooltipBox}>
      <div className="mono" style={{ fontSize: 10 }}>{label}</div>
      <div className="num" style={{ fontSize: 12 }}>
        {fmtPct(payload[0].value)}
      </div>
    </div>
  );
}

function MBTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null;
  return (
    <div style={tooltipBox}>
      <div className="mono" style={{ fontSize: 10 }}>{label}</div>
      <div className="num" style={{ fontSize: 12 }}>
        {fmtUSD(payload[0].value, { compact: true })}
      </div>
    </div>
  );
}

const tooltipBox = {
  background: "var(--bg-card)",
  border: "1px solid var(--ink)",
  padding: "6px 8px",
  fontFamily: "IBM Plex Mono, monospace",
};
