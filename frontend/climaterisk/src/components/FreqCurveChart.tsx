import type { FreqCurve } from "../types";
import { money } from "../lib/format";

// Compact SVG return-period (exceedance) curve. Log x-axis (return periods),
// linear y-axis (impact). No chart library — keeps the bundle small.
export function FreqCurveChart({
  curve,
  currency,
  format,
}: {
  curve: FreqCurve;
  currency: string;
  /** Overrides currency formatting — health/index perils are not money. */
  format?: (value: number) => string;
}) {
  const fmt = format ?? ((v: number) => money(v, currency));
  const W = 460;
  const H = 240;
  const m = { l: 64, r: 16, t: 16, b: 36 };
  const iw = W - m.l - m.r;
  const ih = H - m.t - m.b;

  const rps = curve.return_periods;
  const ys = curve.impact;
  if (rps.length === 0) return null;

  const xMin = Math.log10(Math.min(...rps));
  const xMax = Math.log10(Math.max(...rps));
  const yMax = Math.max(...ys, 1);

  const px = (rp: number) =>
    m.l + (xMax === xMin ? iw / 2 : ((Math.log10(rp) - xMin) / (xMax - xMin)) * iw);
  const py = (v: number) => m.t + ih - (v / yMax) * ih;

  const points = rps.map((rp, i) => `${px(rp)},${py(ys[i])}`).join(" ");

  return (
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" role="img" aria-label="Return-period curve">
      {/* axes */}
      <line x1={m.l} y1={m.t} x2={m.l} y2={m.t + ih} stroke="var(--border)" />
      <line x1={m.l} y1={m.t + ih} x2={m.l + iw} y2={m.t + ih} stroke="var(--border)" />
      {/* y ticks: 0, mid, max */}
      {[0, 0.5, 1].map((f) => (
        <g key={f}>
          <line
            x1={m.l}
            y1={py(yMax * f)}
            x2={m.l + iw}
            y2={py(yMax * f)}
            stroke="var(--border)"
            strokeDasharray="2 4"
            opacity={0.5}
          />
          <text x={m.l - 8} y={py(yMax * f) + 4} textAnchor="end" fontSize="10" fill="var(--muted)">
            {fmt(yMax * f)}
          </text>
        </g>
      ))}
      {/* x ticks at each return period */}
      {rps.map((rp) => (
        <text key={rp} x={px(rp)} y={m.t + ih + 16} textAnchor="middle" fontSize="10" fill="var(--muted)">
          {rp}
        </text>
      ))}
      <text x={m.l + iw / 2} y={H - 2} textAnchor="middle" fontSize="10" fill="var(--muted)">
        return period (years)
      </text>
      {/* curve */}
      <polyline points={points} fill="none" stroke="var(--accent-2)" strokeWidth={2} />
      {rps.map((rp, i) => (
        <circle key={rp} cx={px(rp)} cy={py(ys[i])} r={3} fill="var(--accent-2)" />
      ))}
    </svg>
  );
}
