// Formatting helpers shared across the dashboard.

export const fmtUSD = (n, opts = {}) => {
  if (n == null || isNaN(n)) return "—";
  const { compact = false, cents = false } = opts;
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    notation: compact ? "compact" : "standard",
    maximumFractionDigits: cents ? 2 : 0,
    minimumFractionDigits: cents ? 2 : 0,
  }).format(n);
};

export const fmtPct = (n) => {
  if (n == null || isNaN(n)) return "—";
  const v = Number(n);
  const sign = v > 0 ? "+" : v < 0 ? "" : " ";
  return `${sign}${v.toFixed(1)}%`;
};

export const fmtPeriod = (year, month) => {
  const m = ["Jan","Feb","Mar","Apr","May","Jun",
             "Jul","Aug","Sep","Oct","Nov","Dec"][month - 1] ?? "?";
  return `${m} ${String(year).slice(2)}`;
};

export const deltaClass = (pct) => {
  if (pct == null || isNaN(pct)) return "flat";
  if (pct > 0.5) return "up";
  if (pct < -0.5) return "down";
  return "flat";
};
