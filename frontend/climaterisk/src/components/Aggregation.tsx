import type { PhysicalRunOutput, Portfolio, Run, TransitionResult } from "../types";
import { money } from "../lib/format";

interface Row {
  key: string;
  value: number;
  phys: number;
  trans: number;
  count: number;
}

function GroupTable({
  title,
  rows,
  currency,
}: {
  title: string;
  rows: Row[];
  currency: string;
}) {
  const maxPhys = Math.max(1, ...rows.map((r) => r.phys));
  const maxTrans = Math.max(1, ...rows.map((r) => r.trans));
  return (
    <div style={{ marginTop: 14 }}>
      <div className="section-title" style={{ marginBottom: 6 }}>
        {title}
      </div>
      <table className="agg-table">
        <thead>
          <tr>
            <th>{title.includes("country") ? "Country" : "Sector"}</th>
            <th>#</th>
            <th>Exposed value</th>
            <th>Physical AAI/yr</th>
            <th>Transition NPV</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.key}>
              <td>{r.key.replace(/_/g, " ")}</td>
              <td>{r.count}</td>
              <td>{money(r.value, currency)}</td>
              <td>
                <div className="bar-cell">
                  <span className="bar" style={{ width: `${(r.phys / maxPhys) * 100}%`, background: "var(--accent-2)" }} />
                  <span className="bar-label">{money(r.phys, currency)}</span>
                </div>
              </td>
              <td>
                <div className="bar-cell">
                  <span className="bar" style={{ width: `${(r.trans / maxTrans) * 100}%`, background: "var(--color-carbon)" }} />
                  <span className="bar-label">{money(r.trans, currency)}</span>
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function Aggregation({
  model,
  run,
  transition,
  currency,
}: {
  model: Portfolio;
  run: Run | null;
  transition: TransitionResult | null;
  currency: string;
}) {
  const meta = new Map(model.assets.map((a) => [a.id, { sector: a.sector, value: a.value }]));
  const country = new Map<string, string>();
  const phys = new Map<string, number>();
  const physOutput = run?.output as PhysicalRunOutput | null;
  // Perils excluded from the currency rollup because their impact is not money: deaths
  // (mortality) or a fraction (productivity / yield). Summing them into a monetary AAI —
  // and from there into EBITDA / DSCR / rating — would be meaningless. Mirrors the
  // backend rule in `finance/service.py::per_asset_aai`.
  const nonMonetary: string[] = [];
  if (physOutput?.results) {
    for (const r of physOutput.results) {
      if (r.status !== "ok") continue;
      if ((r.result_kind ?? "monetary") !== "monetary") {
        nonMonetary.push(r.peril.replace(/_/g, " "));
        for (const a of r.per_asset) if (a.country) country.set(a.id, a.country);
        continue;
      }
      for (const a of r.per_asset) {
        phys.set(a.id, (phys.get(a.id) ?? 0) + a.eai);
        if (a.country) country.set(a.id, a.country);
      }
    }
  }
  const trans = new Map<string, number>();
  if (transition) for (const a of transition.per_asset) trans.set(a.id, a.npv);

  const groupBy = (keyFn: (id: string) => string): Row[] => {
    const m = new Map<string, Row>();
    for (const a of model.assets) {
      const key = keyFn(a.id) || "—";
      const row = m.get(key) ?? { key, value: 0, phys: 0, trans: 0, count: 0 };
      row.value += meta.get(a.id)?.value ?? 0;
      row.phys += phys.get(a.id) ?? 0;
      row.trans += trans.get(a.id) ?? 0;
      row.count += 1;
      m.set(key, row);
    }
    return [...m.values()].sort((x, y) => y.phys + y.trans - (x.phys + x.trans));
  };

  const bySector = groupBy((id) => meta.get(id)?.sector ?? "—");
  const byCountry = groupBy((id) => country.get(id) ?? "—");
  const hasCountry = byCountry.some((r) => r.key !== "—");

  const totalValue = model.assets.reduce((s, a) => s + a.value, 0);
  const totalPhys = [...phys.values()].reduce((s, v) => s + v, 0);
  const totalTrans = [...trans.values()].reduce((s, v) => s + v, 0);
  const nCountries = byCountry.filter((r) => r.key !== "—").length;

  return (
    <div className="card">
      <div className="section-title">
        {model.depth_level === "national" ? "National" : "Portfolio"} aggregation
      </div>
      <p className="hint">
        Physical AAI summed across perils and transition NPV, grouped by sector
        {hasCountry ? " and country (national view)" : ""}.
        {nonMonetary.length > 0 && (
          <>
            {" "}
            <strong>Excluded from the currency total:</strong> {nonMonetary.join(", ")} — reported in
            persons or as an index, not money. See that peril's own card for its result.
          </>
        )}
      </p>
      <div className="kpi-grid" style={{ marginTop: 10 }}>
        <div className="kpi">
          <div className="kpi-value">{money(totalValue, currency)}</div>
          <div className="kpi-label">Total exposed value</div>
        </div>
        <div className="kpi">
          <div className="kpi-value">{money(totalPhys, currency)}/yr</div>
          <div className="kpi-label">Physical AAI ({model.assets.length} assets)</div>
        </div>
        <div className="kpi">
          <div className="kpi-value">{money(totalTrans, currency)}</div>
          <div className="kpi-label">Transition NPV{nCountries > 1 ? ` · ${nCountries} countries` : ""}</div>
        </div>
        <div className="kpi">
          <div className="kpi-value">
            {((totalPhys / Math.max(totalValue, 1)) * 100).toFixed(2)}%
          </div>
          <div className="kpi-label">AAI / value</div>
        </div>
      </div>
      <GroupTable title="By sector" rows={bySector} currency={currency} />
      {hasCountry && <GroupTable title="By country" rows={byCountry} currency={currency} />}
    </div>
  );
}
