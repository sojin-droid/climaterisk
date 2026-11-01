export type ViewId =
  | "map"
  | "scenarios"
  | "vulnerability"
  | "results"
  | "models"
  | "adaptation"
  | "finance"
  | "supplychain"
  | "forecast"
  | "data"
  | "method";

export const VIEW_IDS: readonly ViewId[] = [
  "map",
  "scenarios",
  "vulnerability",
  "results",
  "models",
  "adaptation",
  "finance",
  "supplychain",
  "forecast",
  "data",
  "method",
];

const ITEMS: { id: ViewId; label: string; ico: string }[] = [
  { id: "map", label: "Map", ico: "🗺️" },
  { id: "scenarios", label: "Scenarios", ico: "🌡️" },
  { id: "vulnerability", label: "Vuln", ico: "📐" },
  { id: "results", label: "Results", ico: "📊" },
  { id: "models", label: "Models", ico: "🧭" },
  { id: "adaptation", label: "Adapt", ico: "🛡️" },
  { id: "finance", label: "Finance", ico: "💵" },
  { id: "supplychain", label: "Supply", ico: "🏭" },
  { id: "forecast", label: "Forecast", ico: "🌀" },
  { id: "data", label: "Data", ico: "🗄️" },
  { id: "method", label: "Method", ico: "📖" },
];

export function ActivityBar({
  view,
  onChange,
}: {
  view: ViewId;
  onChange: (v: ViewId) => void;
}) {
  return (
    <nav className="activitybar">
      <div className="brand">climate<br />risk</div>
      {ITEMS.map((it) => (
        <button
          key={it.id}
          className={view === it.id ? "active" : ""}
          onClick={() => onChange(it.id)}
          title={it.label}
        >
          <span className="ico">{it.ico}</span>
          {it.label}
        </button>
      ))}
    </nav>
  );
}
