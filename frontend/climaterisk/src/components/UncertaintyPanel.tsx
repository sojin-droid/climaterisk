import type { Run, UncertaintyResult } from "../types";
import { money } from "../lib/format";
import { MethodNote } from "./MethodNote";
import { BarsChart } from "./BarsChart";

function Histogram({ values, currency }: { values: number[]; currency: string }) {
  const W = 460;
  const H = 150;
  const m = { l: 8, r: 8, t: 8, b: 24 };
  if (values.length === 0) return null;
  const lo = values[0];
  const hi = values[values.length - 1];
  const bins = 12;
  const span = hi - lo || 1;
  const counts = new Array(bins).fill(0);
  for (const v of values) {
    const b = Math.min(bins - 1, Math.floor(((v - lo) / span) * bins));
    counts[b] += 1;
  }
  const maxC = Math.max(...counts, 1);
  const bw = (W - m.l - m.r) / bins;
  const ih = H - m.t - m.b;
  return (
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" role="img" aria-label="AAI distribution">
      {counts.map((c, i) => (
        <rect
          key={i}
          x={m.l + i * bw + 1}
          y={m.t + ih - (c / maxC) * ih}
          width={bw - 2}
          height={(c / maxC) * ih}
          fill="var(--accent-2)"
          opacity={0.7}
        />
      ))}
      <text x={m.l} y={H - 6} fontSize="10" fill="var(--muted)">
        {money(lo, currency)}
      </text>
      <text x={W - m.r} y={H - 6} fontSize="10" fill="var(--muted)" textAnchor="end">
        {money(hi, currency)}
      </text>
      <text x={W / 2} y={H - 6} fontSize="10" fill="var(--muted)" textAnchor="middle">
        AAI/yr distribution
      </text>
    </svg>
  );
}

export function UncertaintyPanel({
  run,
  busy,
  error,
  onRun,
}: {
  run: Run | null;
  busy: boolean;
  error: string | null;
  onRun: () => void;
}) {
  const running = run?.status === "queued" || run?.status === "running";
  const u = run?.status === "done" ? (run.output as UncertaintyResult | null) : null;
  const cur = u?.currency ?? "USD";

  return (
    <div className="card">
      <div className="section-title">Uncertainty &amp; sensitivity</div>
      <p className="hint">
        Sobol variance decomposition (SALib) over exposure value, vulnerability and hazard
        frequency → the AAI as a range, not a point, plus which input drives the spread.
      </p>
      <button className="btn" onClick={onRun} disabled={busy || running}>
        {running ? "Running Sobol analysis…" : busy ? "Submitting…" : "Run uncertainty"}
      </button>
      {error && <p className="hint" style={{ color: "var(--danger)" }}>{error}</p>}

      {u && u.status === "ok" && (
        <>
          <div className="kpi-grid" style={{ marginTop: 12 }}>
            <Kpi label="Mean AAI/yr" value={money(u.aai_mean, cur)} />
            <Kpi label="P5 – P95" value={`${money(u.aai_p5, cur)} – ${money(u.aai_p95, cur)}`} />
            <Kpi label="Std dev" value={money(u.aai_std, cur)} />
          </div>
          {u.delta_mean != null && u.delta_p5 != null && u.delta_p95 != null && (
            <p className="hint" style={{ marginTop: 8 }}>
              <strong>Climate-change delta</strong> (future vs present baseline{" "}
              {u.present_aai != null ? money(u.present_aai, cur) : "—"}/yr): mean{" "}
              <strong>{money(u.delta_mean, cur)}</strong>/yr, P5–P95 {money(u.delta_p5, cur)} –{" "}
              {money(u.delta_p95, cur)}.
            </p>
          )}
          <div style={{ marginTop: 12 }}>
            <Histogram values={u.distribution} currency={cur} />
          </div>
          <div className="section-title" style={{ marginTop: 8, marginBottom: 6 }}>
            Sobol total-order sensitivity (share of AAI variance)
          </div>
          <BarsChart
            data={Object.entries(u.sensitivity)
              .sort((a, b) => b[1] - a[1])
              .map(([k, v]) => ({ name: k.replace(/_/g, " "), value: v }))}
            fmt={(v) => v.toFixed(2)}
          />
          <MethodNote>
            {u.n_samples} model evaluations on a Saltelli design — a platform SALib Sobol wrapper
            re-running CLIMADA <code>ImpactCalc</code> (not CLIMADA&apos;s <code>unsequa</code>
            module); tropical cyclone only. Bars are total-order Sobol indices — each input&apos;s
            share of AAI variance including interactions. The three input ranges (value ±20 %,
            v½ ±10 %, frequency ±15 %) are indicative platform assumptions without a literature
            source, so read this as a sensitivity screen, not a calibrated uncertainty band.
            Frequency is applied as a linear multiplier on AAI. {u.detail}
          </MethodNote>
        </>
      )}
    </div>
  );
}

function Kpi({ label, value }: { label: string; value: string }) {
  return (
    <div className="kpi">
      <div className="kpi-value" style={{ fontSize: 16 }}>{value}</div>
      <div className="kpi-label">{label}</div>
    </div>
  );
}
