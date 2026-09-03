/** Display form of a scenario id: "rcp45" → "RCP4.5", "ssp126" → "SSP1-2.6"; others unchanged. */
export function formatScenario(id: string): string {
  const rcp = /^rcp(\d)(\d)$/i.exec(id);
  if (rcp) return `RCP${rcp[1]}.${rcp[2]}`;
  const ssp = /^ssp(\d)(\d)(\d)?$/i.exec(id);
  if (ssp) return `SSP${ssp[1]}-${ssp[2]}${ssp[3] ? `.${ssp[3]}` : ""}`;
  return id;
}

/** Compact count formatting with a unit, e.g. 3322.6 -> "3.3K persons". */
export function quantity(value: number, unit: string): string {
  const n = new Intl.NumberFormat("en", {
    notation: "compact",
    maximumFractionDigits: Math.abs(value) < 100 ? 2 : 1,
  }).format(value);
  if (!unit) return n;
  const singular = value === 1 && unit.endsWith("s") ? unit.slice(0, -1) : unit;
  return `${n} ${singular}`;
}

/**
 * Formatter for a physical-risk result's impact values.
 *
 * Monetary perils format as currency; health and index perils must NOT — deaths rendered
 * as "€3.3K" would be plainly wrong. Pass the returned function wherever an impact number
 * is displayed so every surface (KPI, map tooltip, grid, return-period axis) agrees.
 */
export function impactFormatter(
  resultKind: string | null | undefined,
  currency: string,
): (value: number) => string {
  const kind = resultKind ?? "monetary";
  if (kind === "monetary") return (v) => money(v, currency);
  if (kind === "mortality") return (v) => quantity(v, "persons");
  return (v) => quantity(v, ""); // productivity / yield: a value-equivalent index
}

/** Compact money formatting, e.g. 1234567 -> "$1.2M". */
export function money(value: number, currency = "USD"): string {
  try {
    return new Intl.NumberFormat("en", {
      style: "currency",
      currency,
      notation: "compact",
      maximumFractionDigits: 1,
    }).format(value);
  } catch {
    // Unknown currency code → fall back to a plain compact number.
    return new Intl.NumberFormat("en", { notation: "compact", maximumFractionDigits: 1 }).format(
      value,
    );
  }
}
