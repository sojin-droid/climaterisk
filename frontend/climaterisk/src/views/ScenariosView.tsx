import type { Libraries, Portfolio } from "../types";

export function ScenariosView({
  model,
  libraries,
  patchModel,
}: {
  model: Portfolio;
  libraries: Libraries;
  patchModel: (patch: Partial<Portfolio>) => void;
}) {
  const { scenario, run_config } = model;

  const setScenario = (patch: Partial<Portfolio["scenario"]>) =>
    patchModel({ scenario: { ...scenario, ...patch } });
  const setRun = (patch: Partial<Portfolio["run_config"]>) =>
    patchModel({ run_config: { ...run_config, ...patch } });

  const togglePeril = (id: string) => {
    const has = run_config.perils.includes(id);
    setRun({
      perils: has ? run_config.perils.filter((p) => p !== id) : [...run_config.perils, id],
    });
  };
  const toggleYear = (y: number) => {
    const has = scenario.anchor_years.includes(y);
    setScenario({
      anchor_years: (has
        ? scenario.anchor_years.filter((x) => x !== y)
        : [...scenario.anchor_years, y]
      ).sort((a, b) => a - b),
    });
  };

  return (
    <div className="panelview">
      <h2>Scenarios &amp; horizon</h2>

      <div className="card">
        <div className="section-title">Physical — climate forcing</div>
        <div className="field" style={{ marginTop: 10 }}>
          <label>Climate scenario (RCP / SSP)</label>
          <select
            value={scenario.climate}
            onChange={(e) => setScenario({ climate: e.target.value })}
          >
            {libraries.scenarios.climate.map((c) => (
              <option key={c.id} value={c.id}>
                {c.label}
              </option>
            ))}
          </select>
        </div>
        {run_config.perils.includes("tropical_cyclone") && (
          <div className="field">
            <label>Tropical-cyclone future hazard</label>
            <select
              value={(run_config.options?.tc_future_method as string) ?? "dataapi"}
              onChange={(e) => {
                const opts = { ...run_config.options };
                if (e.target.value === "knutson") opts.tc_future_method = "knutson";
                else delete opts.tc_future_method;
                setRun({ options: opts });
              }}
            >
              <option value="dataapi">CLIMADA Data API future sets (default)</option>
              <option value="knutson">Knutson/Jewson climate scaling (any year)</option>
            </select>
          </div>
        )}
        {run_config.perils.includes("tropical_cyclone") && (
          <div className="field">
            <label>Tropical-cyclone vulnerability default (v½)</label>
            <select
              value={(run_config.options?.tc_impf_default as string) ?? "regional"}
              onChange={(e) => {
                const opts = { ...run_config.options };
                if (e.target.value === "regional") delete opts.tc_impf_default;
                else opts.tc_impf_default = e.target.value;
                setRun({ options: opts });
              }}
              title="Applies only to assets without an explicit v½ override in the Vulnerability studio"
            >
              <option value="regional">
                Regional preset by country — Eberenz et al. 2021 (default)
              </option>
              <option value="class">Indicative class default (70–110 m/s, not regional)</option>
              <option value="calibrated">
                Calibrated record for the portfolio country (falls back to regional)
              </option>
            </select>
          </div>
        )}
        {["river_flood", "coastal_flood", "tc_surge"].some((p) => run_config.perils.includes(p)) && (
          <div className="field">
            <label>Flood-family vulnerability default (depth–damage)</label>
            <select
              value={(run_config.options?.flood_impf_default as string) ?? "regional"}
              onChange={(e) => {
                const opts = { ...run_config.options };
                if (e.target.value === "regional") delete opts.flood_impf_default;
                else opts.flood_impf_default = e.target.value;
                setRun({ options: opts });
              }}
              title="Applies only to assets without an explicit depth-damage override"
            >
              <option value="regional">
                Regional preset by continent — JRC Huizinga 2017 residential (default)
              </option>
              <option value="class">Generic class curve (Europe-like, not regional)</option>
            </select>
          </div>
        )}
        {run_config.perils.includes("european_windstorm") && (
          <div className="field">
            <label>EU windstorm damage function</label>
            <select
              value={(run_config.options?.windstorm_impf as string) ?? "schwierz"}
              onChange={(e) => {
                const opts = { ...run_config.options };
                if (e.target.value === "welker") opts.windstorm_impf = "welker";
                else delete opts.windstorm_impf;
                setRun({ options: opts });
              }}
            >
              <option value="schwierz">Schwierz et al. (default, calibrated)</option>
              <option value="welker">Welker et al. (calibrated)</option>
            </select>
          </div>
        )}
      </div>

      <div className="card">
        <div className="section-title">Transition — policy pathway</div>
        <div className="field" style={{ marginTop: 10 }}>
          <label>NGFS scenario</label>
          <select
            value={scenario.transition}
            onChange={(e) => setScenario({ transition: e.target.value })}
          >
            {libraries.scenarios.transition.map((t) => (
              <option key={t.id} value={t.id}>
                {t.label}
              </option>
            ))}
          </select>
        </div>
        <div className="field">
          <label>Discount rate</label>
          <input
            type="number"
            step="0.005"
            value={run_config.discount_rate}
            onChange={(e) => setRun({ discount_rate: Number(e.target.value) })}
          />
        </div>
      </div>

      <div className="card">
        <div className="section-title">Perils</div>
        {libraries.perils.perils.map((p) => (
          <label
            key={p.id}
            className={`checkrow ${p.supported_mvp ? "" : "disabled"}`}
            title={p.supported_mvp ? p.future_source : p.reason}
          >
            <input
              type="checkbox"
              disabled={!p.supported_mvp}
              checked={run_config.perils.includes(p.id)}
              onChange={() => togglePeril(p.id)}
            />
            {p.label}
            {p.historical_only && <span className="pill">historical</span>}
            {p.coverage && <span className="pill">{p.coverage}</span>}
            {!p.supported_mvp && <span className="pill">no data</span>}
          </label>
        ))}
      </div>

      <div className="card">
        <div className="section-title">Horizon — anchor years</div>
        <div style={{ display: "flex", gap: 14, marginTop: 10, flexWrap: "wrap" }}>
          {libraries.scenarios.anchor_years.map((y) => (
            <label key={y} className="checkrow">
              <input
                type="checkbox"
                checked={scenario.anchor_years.includes(y)}
                onChange={() => toggleYear(y)}
              />
              {y}
            </label>
          ))}
        </div>
      </div>
    </div>
  );
}
