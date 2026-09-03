import { AgGridReact } from "ag-grid-react";
import {
  AllCommunityModule,
  type ColDef,
  ModuleRegistry,
  themeQuartz,
  type ValueFormatterParams,
} from "ag-grid-community";
import type { AssetImpact } from "../types";
import { money } from "../lib/format";

ModuleRegistry.registerModules([AllCommunityModule]);

// Dark theme matching the app shell (ag-grid v35 theming API — no CSS imports).
const darkTheme = themeQuartz.withParams({
  backgroundColor: "#1a1d23",
  foregroundColor: "#e6e6e6",
  headerBackgroundColor: "#23262d",
  borderColor: "#3a3f47",
  oddRowBackgroundColor: "#1e2127",
  fontSize: 12,
});

/** Sortable per-asset expected-annual-impact grid (AG-Grid) — scales to large portfolios. */
export function AssetGrid({
  impacts,
  currency,
  format,
}: {
  impacts: AssetImpact[];
  currency: string;
  /** Overrides currency formatting — health/index perils are not money. */
  format?: (value: number) => string;
}) {
  const fmt = format ?? ((v: number) => money(v, currency));
  const num3 = (p: ValueFormatterParams) =>
    typeof p.value === "number" ? p.value.toFixed(3) : "";
  const cols: ColDef[] = [
    { field: "id", headerName: "Asset", flex: 1, sortable: true, filter: true },
    { field: "country", headerName: "Country", width: 110, sortable: true, filter: true },
    { field: "lat", headerName: "Lat", width: 100, valueFormatter: num3 },
    { field: "lon", headerName: "Lon", width: 100, valueFormatter: num3 },
    {
      field: "eai",
      headerName: "EAI / yr",
      flex: 1,
      sort: "desc",
      sortable: true,
      valueFormatter: (p) => (typeof p.value === "number" ? fmt(p.value) : ""),
    },
  ];
  const h = Math.min(360, 88 + impacts.length * 34);
  return (
    <div style={{ height: h, width: "100%" }}>
      <AgGridReact theme={darkTheme} rowData={impacts} columnDefs={cols} />
    </div>
  );
}
