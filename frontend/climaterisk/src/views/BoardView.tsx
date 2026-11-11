import { useEffect, useMemo, useRef, useState } from "react";
import { CircleMarker, GeoJSON, MapContainer, Marker, TileLayer, useMap, useMapEvents } from "react-leaflet";
import L from "leaflet";
import type { Asset, Libraries, Portfolio } from "../types";
import type { ViewId } from "../layout/ActivityBar";
import { DEFAULT_SECTOR, newAsset } from "../lib/assets";
import type { PolylineOptions } from "leaflet";
import {
  getBoardGeojson,
  getBoardLayers,
  getBoardLookup,
  getBoardPalettes,
  getBoardRegion,
  getBoardCase,
  getBoardCases,
  getBoardLadder,
  getBoardRegionAll,
  getBoardRegionNames,
  caseFigureUrl,
  fmtHighlight,
  fmtStatusCounts,
  headlineLabel,
  headlineUnits,
  changePieceStyle,
  fmtChange,
  fmtUsdKo,
  featureCentre,
  litpopRadiusPx,
  isChangeLayer,
  keyQuantity,
  paletteClasses,
  paletteColor,
  rpKey,
  speiStageName,
  type PaletteSpec,
  type BoardCollection,
  type BoardExposure,
  type BoardFeature,
  type BoardKey,
  type BoardLitpop,
  type BoardLookup,
  type BoardMeta,
  type BoardRegion,
  type BoardRegionAll,
  type BoardRegionName,
  type BoardRegionRow,
  type BoardCase,
  type BoardCaseSummary,
  type BoardStatus,
  type LadderId,
  type LadderRung,
} from "../lib/board";
import { OFFLINE_MAP_NOTE, useOffline } from "../lib/offline";

const fmt = (v: number | null | undefined, units: string) =>
  v === null || v === undefined ? "자료 없음" : v === 0 ? `0 ${units} (침수 없음)` : `${v.toFixed(2)} ${units}`;

const LAYER_LABELS: Record<string, string> = {
  river_flood_hist_kor: "하천 홍수 · 과거(1980–2000) · 한국",
};
const layerLabel = (l: BoardMeta) => l.label ?? LAYER_LABELS[l.layer] ?? l.layer;

// The selector is grouped by kind (absolute / change) and then by peril, so ~25 layers stay scannable.
const PERIL_LABELS: Record<string, string> = {
  river_flood: "하천 홍수",
  coastal_flood: "해안 침수",
  tropical_cyclone: "태풍",
  wildfire: "산불",
  drought: "가뭄",
  heatwave: "폭염",
  heat_mortality: "폭염 건강",
};
const perilLabel = (p?: string) => (p && PERIL_LABELS[p]) || p || "기타";
const ABSOLUTE_HEAD = "절대값 · Absolute";
const CHANGE_HEAD = "변화 · Change (미래 ÷ 기준)";

function layerGroups(layers: BoardMeta[]): { label: string; layers: BoardMeta[] }[] {
  const groups = new Map<string, BoardMeta[]>();
  for (const head of [ABSOLUTE_HEAD, CHANGE_HEAD]) {
    for (const l of layers.filter((x) => isChangeLayer(x) === (head === CHANGE_HEAD))) {
      const label = `${head} — ${perilLabel(l.peril)}`;
      groups.set(label, [...(groups.get(label) ?? []), l]);
    }
  }
  return [...groups].map(([label, ls]) => ({ label, layers: ls }));
}

const EXPOSURE_ROWS: { key: string; label: string; unit: string; digits: number; unassessed: boolean }[] = [
  { key: "upland_ha", label: "밭", unit: "ha", digits: 1, unassessed: false },
  { key: "orchard_ha", label: "과수", unit: "ha", digits: 1, unassessed: false },
  { key: "rice_ha", label: "논", unit: "ha", digits: 1, unassessed: true },
  { key: "greenhouse_ha", label: "시설", unit: "ha", digits: 1, unassessed: true },
  { key: "pop_total", label: "인구", unit: "명", digits: 0, unassessed: false },
  { key: "pop_65plus", label: "65세 이상 인구", unit: "명", digits: 0, unassessed: false },
  { key: "litpop_value_usd", label: "LitPop 노출 자산", unit: "USD", digits: 0, unassessed: false },
  { key: "litpop_eal_usd", label: "LitPop 연평균 손실", unit: "USD", digits: 0, unassessed: false },
];

const fmtMetric = (
  v: number | null | undefined,
  k: BoardKey | undefined,
  spec: PaletteSpec | null,
  status?: BoardStatus | null,
) => {
  if (k?.quantity === "change") return fmtChange(spec, v, status);
  if (v === null || v === undefined) return "자료 없음";
  if (k?.scale === "level") return `${v.toFixed(v >= 10 ? 1 : 2)} ${k.units}`;
  if (k?.scale === "share") return `${(v * 100).toFixed(0)} % (${v.toFixed(2)} ${k.units})`;
  if (k?.scale === "spei") return `${v.toFixed(2)} · ${spec ? speiStageName(spec, v) : ""}`;
  return String(v);
};

function ExposureTable({ exposure, sum }: { exposure: BoardExposure; sum?: boolean }) {
  return (
    <table style={{ fontSize: 12, width: "100%" }} data-testid={sum ? "exposure-sum" : "exposure"}>
      <tbody>
        {EXPOSURE_ROWS.filter((r) => r.key in exposure).map((r) => (
          <tr key={r.key}>
            <td>
              {r.label}
              {sum ? " 합계" : ""}
            </td>
            <td style={{ textAlign: "right" }}>
              {exposure[r.key].toLocaleString("ko-KR", { maximumFractionDigits: r.digits })} {r.unit}
              {r.unassessed ? " · 미평가(면적만)" : ""}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

const CASE_TRANSITION_NOTICE = "사례 트윈으로 전환합니다. 해상도와 방법이 달라 전국 결과와 합산·비교할 수 없습니다.";

/** Number text for the min–max line: a computed 0 stays "0" (null is handled by the caller). */
const fmtNum = (v: number) => String(Number(v.toFixed(2)));

function fmtRange(r: { min: number; max: number }, units: string, ratio: boolean): string {
  if (ratio) return `×${fmtNum(r.min)} – ×${fmtNum(r.max)}`;
  return `${fmtNum(r.min)} – ${fmtNum(r.max)}${units && units !== "-" ? ` ${units}` : ""}`;
}

/** Search option text: the name, plus the code only where several 시군구 share the name. */
function regionOptions(names: BoardRegionName[]): { value: string; code: string }[] {
  const count = new Map<string, number>();
  for (const n of names) count.set(n.name, (count.get(n.name) ?? 0) + 1);
  return names.map((n) => ({ value: (count.get(n.name) ?? 0) > 1 ? `${n.name} (${n.code})` : n.name, code: n.code }));
}

/** Fit the map to the chosen 시군구's pieces whenever the selection changes. */
function FitTo({ features, token }: { features: BoardFeature[]; token: string }) {
  const map = useMap();
  useEffect(() => {
    if (!features.length) return;
    const bounds = L.geoJSON(features as never).getBounds();
    if (bounds.isValid()) map.fitBounds(bounds, { maxZoom: 11, padding: [20, 20] });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token, features.length > 0]);
  return null;
}

function CasePin({ c, onOpen }: { c: BoardCaseSummary; onOpen: (id: string) => void }) {
  const label = `사례 트윈: ${c.name}`;
  const icon = useMemo(
    () =>
      L.divIcon({
        className: "",
        html: '<span style="display:block;width:16px;height:16px;border-radius:50% 50% 50% 0;transform:rotate(-45deg);background:var(--accent);border:2px solid var(--bg)"></span>',
        iconSize: [16, 16],
        iconAnchor: [8, 16],
      }),
    [],
  );
  return (
    <Marker
      position={[c.lat, c.lon]}
      icon={icon}
      title={label}
      keyboard
      eventHandlers={{
        add: (e) => e.target.getElement()?.setAttribute("aria-label", label),
        click: () => onOpen(c.id),
      }}
    />
  );
}

function ResolutionLadder({
  rungs,
  active,
  meta,
  cases,
}: {
  rungs: LadderRung[];
  active: LadderId;
  meta: BoardMeta | null;
  cases: BoardCaseSummary[];
}) {
  if (rungs.length === 0) return null;
  const extra = (id: LadderId): string => {
    if (id === "national" && meta) return `현재 층 격자 약 ${meta.cell_km_ns.toFixed(1)}×${meta.cell_km_ew.toFixed(1)} km`;
    if (id === "case" && cases.length > 0) return cases.map((c) => `${c.name.split(" · ")[0]} ${c.cell_m} m`).join(" · ");
    return "";
  };
  return (
    <div>
      <div className="section-title">해상도 사다리 · Resolution ladder</div>
      <ol data-testid="resolution-ladder" style={{ listStyle: "none", margin: "6px 0 0", padding: 0, display: "flex", flexDirection: "column", gap: 4 }}>
        {rungs.map((r) => {
          const on = r.id === active;
          return (
            <li
              key={r.id}
              data-testid={`ladder-${r.id}`}
              aria-current={on ? "step" : undefined}
              style={{
                border: `1px solid ${on ? "var(--accent)" : "var(--border)"}`,
                borderRadius: 6,
                padding: "4px 8px",
                fontSize: 12,
                fontWeight: on ? 600 : 400,
              }}
            >
              {r.label_ko} <span className="hint">· {r.label_en}</span>
              <div className="hint" style={{ fontWeight: 400 }}>{r.note_ko}</div>
              {extra(r.id) && <div className="hint" style={{ fontWeight: 400 }}>{extra(r.id)}</div>}
            </li>
          );
        })}
      </ol>
    </div>
  );
}

function CasePage({
  data,
  rungs,
  meta,
  cases,
  onBack,
}: {
  data: BoardCase;
  rungs: LadderRung[];
  meta: BoardMeta | null;
  cases: BoardCaseSummary[];
  onBack: () => void;
}) {
  const check = data.summary.check;
  return (
    <div className="sidepanel" data-testid="case-page" style={{ width: "auto", maxWidth: 900, margin: "0 auto", display: "flex", flexDirection: "column", gap: 12 }}>
      <div>
        <button className="btn secondary" onClick={onBack}>← 전국 지도로</button>
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
        <h2 style={{ margin: 0 }}>{data.name}</h2>
        <span
          data-testid="case-badge"
          style={{ border: "2px solid var(--danger)", color: "var(--danger)", borderRadius: 6, padding: "2px 10px", fontWeight: 700 }}
        >
          {data.badge}
        </span>
      </div>
      <div className="hint" data-testid="case-resolution">
        {meta ? `전국 격자(${meta.label ?? meta.layer}) 약 ${meta.cell_km_ns.toFixed(1)}×${meta.cell_km_ew.toFixed(1)} km vs 사례 ${data.cell_m} m` : `사례 ${data.cell_m} m`} — 해상도와 방법이 달라 전국 결과와 합산·비교할 수 없습니다.
      </div>
      <ResolutionLadder rungs={rungs} active="case" meta={meta} cases={cases} />

      <div>
        <div className="section-title">핵심 값 · Highlights</div>
        <table data-testid="case-highlights" style={{ fontSize: 13, width: "100%" }}>
          <tbody>
            {data.highlights.map((h) => (
              <tr key={h.label_ko}>
                <td>{h.label_ko}</td>
                <td style={{ textAlign: "right" }}>{fmtHighlight(h.value, h.unit)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <div className="hint" data-testid="case-check" style={check?.warning ? { color: "var(--danger)" } : undefined} role={check?.warning ? "alert" : undefined}>
          {check === undefined
            ? "보조 점검 기록 없음"
            : check.warning
              ? "보조 점검 경고: 주 방법과 보조 점검이 크게 어긋납니다. 결과를 조심해서 읽으세요."
              : "보조 점검(진단용): 경고 없음"}
        </div>
      </div>

      <div style={{ display: "flex", flexWrap: "wrap", gap: 12 }}>
        {data.figures.map((f) => (
          <figure key={f.file} style={{ margin: 0, flex: "1 1 320px" }}>
            <img src={caseFigureUrl(data.id, f.file)} alt={f.caption_ko} style={{ width: "100%", height: "auto" }} />
            <figcaption className="hint">{f.caption_ko}</figcaption>
          </figure>
        ))}
      </div>

      <div style={{ display: "flex", flexWrap: "wrap", gap: 12 }}>
        <div style={{ flex: "1 1 320px" }} data-testid="case-can-say">
          <div className="section-title">말할 수 있는 것</div>
          <ul style={{ paddingLeft: 18, margin: "4px 0" }}>{data.can_say.map((t) => <li key={t}>{t}</li>)}</ul>
        </div>
        <div style={{ flex: "1 1 320px" }} data-testid="case-cannot-say">
          <div className="section-title">말할 수 없는 것</div>
          <ul style={{ paddingLeft: 18, margin: "4px 0" }}>{data.cannot_say.map((t) => <li key={t}>{t}</li>)}</ul>
        </div>
      </div>

      <div data-testid="case-next-data">
        <div className="section-title">다음에 필요한 자료</div>
        <ul style={{ paddingLeft: 18, margin: "4px 0" }}>{data.next_data.map((t) => <li key={t}>{t}</li>)}</ul>
      </div>

      <details open data-testid="case-assumptions">
        <summary style={{ cursor: "pointer", fontWeight: 600 }}>가정 {data.n_assumptions}개</summary>
        {!data.assumptions_complete && (
          <div className="hint" role="alert" style={{ color: "var(--danger)" }}>
            가정 목록이 불완전합니다: {data.n_assumptions}개 중 {data.assumptions.length}개만 기록되어 있습니다.
          </div>
        )}
        <ol style={{ paddingLeft: 22, margin: "4px 0", fontSize: 12 }}>
          {data.assumptions.map((a) => <li key={a}>{a}</li>)}
        </ol>
      </details>

      <div className="hint" data-testid="case-source">출처 문서: {data.source_doc}</div>
    </div>
  );
}

/**
 * LitPop exposure circles (step 7): one hollow circle per piece with a positive LitPop annual
 * expected loss, at the piece's area centroid, area proportional to the loss (radius from the
 * largest legend step). Drawn with an SVG renderer so each circle is an element of its own;
 * they are not interactive (clicks reach the map) and never touch the hazard fills.
 */
function LitpopCircles({ fc, palettes, litpop }: { fc: BoardCollection; palettes: PaletteSpec; litpop: BoardLitpop }) {
  const renderer = useMemo(() => L.svg(), []);
  const ref = litpop.size_legend_usd[litpop.size_legend_usd.length - 1];
  const circles = useMemo(() => {
    const out: { id: number; centre: [number, number]; radius: number }[] = [];
    for (const f of fc.features) {
      const eal = f.properties.exposure?.litpop_eal_usd;
      if (typeof eal !== "number" || !(eal > 0)) continue;
      const centre = featureCentre(f.geometry);
      if (centre) out.push({ id: f.properties.id, centre, radius: litpopRadiusPx(eal, ref) });
    }
    return out;
  }, [fc, ref]);
  return (
    <>
      {circles.map((c) => (
        <CircleMarker
          key={c.id}
          center={c.centre}
          radius={c.radius}
          className="litpop-circle"
          renderer={renderer}
          fill={false}
          fillOpacity={0}
          color={palettes.shared.exposure_point.stroke}
          weight={1.5}
          interactive={false}
        />
      ))}
    </>
  );
}

function LitpopLegend({ litpop, stroke }: { litpop: BoardLitpop; stroke: string }) {
  const ref = litpop.size_legend_usd[litpop.size_legend_usd.length - 1];
  return (
    <ul data-testid="litpop-legend" style={{ listStyle: "none", margin: "4px 0", padding: 0, display: "flex", flexDirection: "column", gap: 2 }}>
      {litpop.size_legend_usd.map((v) => {
        const r = litpopRadiusPx(v, ref);
        return (
          <li key={v} style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12 }}>
            <svg width={2 * r + 4} height={2 * r + 4} aria-hidden="true">
              <circle cx={r + 2} cy={r + 2} r={r} fill="none" stroke={stroke} strokeWidth={1.5} />
            </svg>
            연평균 손실 {fmtUsdKo(v)}
          </li>
        );
      })}
    </ul>
  );
}

function ClickLookup({ onPick }: { onPick: (lon: number, lat: number) => void }) {
  useMapEvents({ click: (e) => onPick(e.latlng.lng, e.latlng.lat) });
  return null;
}

export function BoardView({
  model,
  libraries,
  patchModel,
  onGoTo,
}: {
  model: Portfolio;
  libraries: Libraries;
  patchModel: (patch: Partial<Portfolio>) => void;
  onGoTo: (view: ViewId) => void;
}) {
  const offline = useOffline();
  const [palettes, setPalettes] = useState<PaletteSpec | null>(null);
  const [layers, setLayers] = useState<BoardMeta[] | null>(null);
  const [layerName, setLayerName] = useState<string>("");
  const [key, setKey] = useState<string | null>(null);
  const [fc, setFc] = useState<BoardCollection | null>(null);
  // which (layer, key) `fc` was fetched for: the map is drawn only when it matches the
  // selection, so a new colour scale is never applied to the previous key's values
  const [fcFor, setFcFor] = useState<string>("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [picked, setPicked] = useState<BoardLookup | null>(null);
  // the clicked coordinate (what the lookup was asked for) and the add-as-asset form state
  const [pickedAt, setPickedAt] = useState<{ lon: number; lat: number } | null>(null);
  const [assetName, setAssetName] = useState("");
  const [assetValue, setAssetValue] = useState("");
  const [assetSector, setAssetSector] = useState(DEFAULT_SECTOR);
  const [assetAdded, setAssetAdded] = useState(false);
  const [outside, setOutside] = useState(false);
  const [region, setRegion] = useState<BoardRegion | null>(null);
  // 시군구 panel (all layers), resolution ladder, case twins
  const [regionNames, setRegionNames] = useState<BoardRegionName[]>([]);
  const [searchText, setSearchText] = useState("");
  const [sigungu, setSigungu] = useState<BoardRegionAll | null>(null);
  const [rungs, setRungs] = useState<LadderRung[]>([]);
  const [cases, setCases] = useState<BoardCaseSummary[]>([]);
  const [pendingCase, setPendingCase] = useState<string | null>(null);
  const [openCase, setOpenCase] = useState<BoardCase | null>(null);
  const wantKey = useRef<{ layer: string; key: string } | null>(null);
  // LitPop exposure weighting (option C): off by default and off again on every layer switch
  const [litpopOn, setLitpopOn] = useState(false);

  // The ladder, search names and case list are side information: a failure leaves them empty
  // and never blocks the map.
  useEffect(() => {
    getBoardLadder().then(setRungs).catch(() => setRungs([]));
    getBoardRegionNames().then(setRegionNames).catch(() => setRegionNames([]));
    getBoardCases().then(setCases).catch(() => setCases([]));
  }, []);

  useEffect(() => {
    getBoardPalettes()
      .then(setPalettes)
      .catch((e: Error) => setError(e.message));
  }, []);

  useEffect(() => {
    getBoardLayers()
      .then((ls) => {
        setLayers(ls);
        // open on an absolute layer; the change layers are the second group of the selector
        const first = ls.find((l) => !isChangeLayer(l)) ?? ls[0];
        if (first) setLayerName(first.layer);
      })
      .catch((e: Error) => setError(e.message));
  }, []);

  const meta = layers?.find((l) => l.layer === layerName) ?? null;

  const metric = meta?.key_kind === "metric";
  const metricKeys = useMemo<BoardKey[]>(() => (metric ? (meta?.keys ?? []) : []), [meta, metric]);
  // single-year SPEI keys go in a drop-down; every other key is a button
  const freqKeys = metricKeys.filter((k) => k.scale !== "spei");
  const yearKeys = metricKeys.filter((k) => k.scale === "spei");
  const keyGroupLabel = freqKeys.every((k) => k.scale === "share") ? "장기 빈도 · Long-run frequency" : "표시 항목 · Metric";
  const activeKey = metricKeys.find((k) => k.key === key);

  // Default: return-period layer -> longest computable period ≤ 100 y (else the longest
  // kept); metric layer -> meta.default_key if valid (else the first key).
  useEffect(() => {
    if (!meta) return;
    if (meta.key_kind === "metric") {
      const ks = (meta.keys ?? []).map((k) => k.key);
      const want = wantKey.current?.layer === meta.layer ? wantKey.current.key : null;
      wantKey.current = null;
      setKey((cur) => (want !== null && ks.includes(want) ? want : cur !== null && ks.includes(cur) ? cur : meta.default_key !== undefined && ks.includes(meta.default_key) ? meta.default_key : (ks[0] ?? null)));
    } else {
      const kept = meta.periods_kept;
      const want = wantKey.current?.layer === meta.layer ? wantKey.current.key : null;
      wantKey.current = null;
      setKey((cur) => {
        if (want !== null && kept.map(rpKey).includes(want)) return want;
        if (cur !== null && kept.map(rpKey).includes(cur)) return cur;
        const pick = kept.filter((p) => p <= 100).pop() ?? kept[kept.length - 1];
        return pick === undefined ? null : rpKey(pick);
      });
    }
    setPicked(null);
    setRegion(null);
    setOutside(false);
    setLitpopOn(false);
  }, [meta]);

  useEffect(() => {
    if (!meta || key === null) return;
    let live = true;
    setLoading(true);
    setError(null);
    getBoardGeojson(meta.layer, key, meta.key_kind === "metric")
      .then((c) => {
        if (!live) return;
        setFc(c);
        setFcFor(`${meta.layer}|${key}`);
      })
      .catch((e: Error) => live && setError(e.message))
      .finally(() => live && setLoading(false));
    return () => {
      live = false;
    };
  }, [meta, key]);

  const allPeriods = useMemo(() => {
    if (!meta || meta.key_kind === "metric") return [];
    const req = meta.requested_periods ?? meta.periods_kept;
    return [...new Set([...req, ...meta.periods_kept])].sort((a, b) => a - b);
  }, [meta]);

  const pickPoint = (lon: number, lat: number) => {
    if (!meta) return;
    setRegion(null);
    setOutside(false);
    // one focus at a time on the ladder: a clicked point closes the 시군구 panel
    setSigungu(null);
    setSearchText("");
    getBoardLookup(meta.layer, lon, lat)
      .then((p) => {
        setPicked(p);
        setPickedAt({ lon, lat });
        setAssetName(`${p.name} 지점`);
        setAssetAdded(false);
      })
      .catch(() => {
        setPicked(null);
        setOutside(true);
      });
  };

  const valueNum = Number(assetValue);
  const valueOk = assetValue.trim() !== "" && Number.isFinite(valueNum) && valueNum > 0;
  const addAsset = () => {
    if (!picked || !pickedAt || !meta || !valueOk) return;
    const asset: Asset = {
      ...newAsset(pickedAt.lat, pickedAt.lon),
      name: assetName.trim() || `${picked.name} 지점`,
      value: valueNum,
      sector: assetSector,
      properties: { source: "visual_board", board_layer: meta.layer, board_code: picked.code },
    };
    patchModel({ assets: [...model.assets, asset] });
    setAssetAdded(true);
  };

  const options = useMemo(() => regionOptions(regionNames), [regionNames]);

  const selectRegion = (code: string) => {
    setPicked(null);
    setRegion(null);
    setOutside(false);
    getBoardRegionAll(code)
      .then(setSigungu)
      .catch((e: Error) => setError(e.message));
  };

  const onSearch = (text: string) => {
    setSearchText(text);
    const hit = options.find((o) => o.value === text) ?? options.find((o) => o.code === text);
    if (hit) selectRegion(hit.code);
  };

  // A row of the 시군구 panel makes its layer (and its headline key) the active one.
  const activateRow = (row: BoardRegionRow) => {
    if (row.layer === layerName) {
      const m = layers?.find((l) => l.layer === row.layer);
      const valid = m?.key_kind === "metric" ? (m.keys ?? []).map((k) => k.key) : (m?.periods_kept ?? []).map(rpKey);
      if (valid.includes(row.headline_key)) setKey(row.headline_key);
      return;
    }
    wantKey.current = { layer: row.layer, key: row.headline_key };
    setLayerName(row.layer);
  };

  const askCase = (id: string) => setPendingCase(id);
  const confirmCase = () => {
    const id = pendingCase;
    setPendingCase(null);
    if (!id) return;
    getBoardCase(id)
      .then(setOpenCase)
      .catch((e: Error) => setError(e.message));
  };

  const loadRegion = () => {
    if (!meta || !picked) return;
    getBoardRegion(meta.layer, picked.code)
      .then(setRegion)
      .catch((e: Error) => setError(e.message));
  };

  // colour = the active key's quantity (spec), never the layer's units or scale
  const quantity = meta ? keyQuantity(meta, key) : null;
  const family = quantity === "loss_ratio" && palettes && meta?.peril ? palettes.perils[meta.peril] : undefined;
  const legendClasses = useMemo(
    () => (palettes && quantity ? paletteClasses(palettes, quantity, family) : []),
    [palettes, quantity, family],
  );

  const change = quantity === "change";

  const style = (f?: { properties?: { value?: number | null; status?: BoardStatus | null } }): PolylineOptions => {
    if (!palettes || !quantity) return {};
    const chg = change ? changePieceStyle(palettes, f?.properties?.value, f?.properties?.status) : null;
    return {
      fillColor: chg ? chg.fill : paletteColor(palettes, quantity, f?.properties?.value, family),
      fillOpacity: palettes.shared.fill_opacity,
      color: chg?.outline ?? palettes.shared.piece_outline.color,
      weight: palettes.shared.piece_outline.weight,
      opacity: 1,
      smoothFactor: 0, // no simplification: crisp grid edges
    };
  };

  const highlighted: BoardFeature | undefined = useMemo(
    () => (picked && fc ? fc.features.find((f) => f.properties.id === picked.id) : undefined),
    [picked, fc],
  );

  const sigunguPieces = useMemo(
    () => (sigungu && fc ? fc.features.filter((f) => f.properties.code === sigungu.code) : []),
    [sigungu, fc],
  );
  const rung: LadderId = openCase ? "case" : sigungu ? "sigungu" : picked ? "point" : "national";
  const pendingCaseName = cases.find((c) => c.id === pendingCase)?.name;

  const units = meta?.units ?? "";
  // key rows shown in the lookup / region panels: the long-run frequencies + the active year
  const metricShown = metricKeys.filter((k) => k.scale !== "spei" || k.key === key);

  if (openCase) {
    return (
      <div data-testid="board-view" style={{ overflow: "auto", height: "100%", padding: 12 }}>
        <CasePage data={openCase} rungs={rungs} meta={layers?.find((l) => l.layer === openCase.national_layer) ?? meta} cases={cases} onBack={() => setOpenCase(null)} />
      </div>
    );
  }

  return (
    <div className="mapview" data-testid="board-view">
      <div className="map">
        {offline && (
          <div className="hint" style={{ position: "absolute", zIndex: 500, margin: 8, padding: "2px 6px", background: "var(--bg)", borderRadius: 4 }}>
            {OFFLINE_MAP_NOTE}
          </div>
        )}
        <MapContainer center={[36.2, 127.9]} zoom={7} scrollWheelZoom preferCanvas>
          {!offline && (
            <TileLayer
              attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
              url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
            />
          )}
          {fc && meta && palettes && quantity && key !== null && fcFor === `${meta.layer}|${key}` && (
            <GeoJSON key={`${fcFor}|${quantity}`} data={fc as never} style={style as never} />
          )}
          {litpopOn && meta?.litpop && fc && palettes && fcFor === `${meta.layer}|${key}` && (
            <LitpopCircles key={`lp-${fcFor}`} fc={fc} palettes={palettes} litpop={meta.litpop} />
          )}
          {highlighted && palettes && (
            <GeoJSON
              key={`hl-${picked?.id}`}
              data={highlighted as never}
              style={() => ({
                fill: false,
                color: palettes?.shared.selected_outline.color,
                weight: palettes?.shared.selected_outline.weight,
                opacity: 1,
                smoothFactor: 0,
              })}
              interactive={false}
            />
          )}
          {sigungu && palettes && sigunguPieces.length > 0 && (
            <GeoJSON
              key={`rg-${sigungu.code}-${fcFor}`}
              data={{ type: "FeatureCollection", features: sigunguPieces } as never}
              style={() => ({
                fill: false,
                color: palettes.shared.region_outline.color,
                weight: palettes.shared.region_outline.weight,
                opacity: 1,
                smoothFactor: 0,
              })}
              interactive={false}
            />
          )}
          {sigungu && <FitTo features={sigunguPieces} token={sigungu.code} />}
          {cases.map((c) => (
            <CasePin key={c.id} c={c} onOpen={(id) => c.available && askCase(id)} />
          ))}
          <ClickLookup onPick={pickPoint} />
        </MapContainer>
      </div>

      <aside className="sidepanel" aria-label="시각 보드 패널">
        <div className="section-title">시각 보드 · Visual board</div>
        {pendingCase && (
          <div className="card" data-testid="case-transition" role="alertdialog" aria-label="사례 트윈 전환 안내" style={{ border: "2px solid var(--accent)", borderRadius: 8, padding: 10 }}>
            <div>{CASE_TRANSITION_NOTICE}</div>
            {pendingCaseName && <div className="hint">{pendingCaseName}</div>}
            <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
              <button className="btn" onClick={confirmCase}>열기</button>
              <button className="btn secondary" onClick={() => setPendingCase(null)}>취소</button>
            </div>
          </div>
        )}
        <ResolutionLadder rungs={rungs} active={rung} meta={meta} cases={cases} />

        <div className="field">
          <label htmlFor="region-search">시군구 검색 · Municipality</label>
          <input
            id="region-search"
            data-testid="region-search"
            list="region-search-names"
            value={searchText}
            placeholder="시군구 이름…"
            autoComplete="off"
            onChange={(e) => onSearch(e.target.value)}
          />
          <datalist id="region-search-names">
            {options.map((o) => (
              <option key={o.code} value={o.value} />
            ))}
          </datalist>
        </div>
        {sigungu && (
          <div data-testid="sigungu-panel">
            <div className="section-title">{sigungu.name} · 재해별 조각 최소–최대 (평균·합산·순위 없음)</div>
            <ul style={{ listStyle: "none", margin: "6px 0 0", padding: 0, display: "flex", flexDirection: "column", gap: 4 }}>
              {sigungu.layers.map((row) => {
                const m = layers?.find((l) => l.layer === row.layer);
                const on = row.layer === layerName;
                return (
                  <li key={row.layer}>
                    <button
                      className={`btn secondary${on ? " active" : ""}`}
                      data-testid={`sigungu-row-${row.layer}`}
                      aria-pressed={on}
                      style={{ width: "100%", textAlign: "left", fontSize: 12, ...(on ? { borderColor: "var(--accent)" } : {}) }}
                      onClick={() => activateRow(row)}
                    >
                      <strong>{row.label}</strong>
                      <div className="hint">
                        {headlineLabel(m, row.headline_key)} · 조각 {row.n_pieces}개 ·{" "}
                        {row.headline === null
                          ? fmtStatusCounts(palettes, row.headline_status) || "자료 없음"
                          : fmtRange(row.headline, headlineUnits(m, row.headline_key), row.quantity === "change")}
                        {row.headline !== null && fmtStatusCounts(palettes, row.headline_status)
                          ? ` · ${fmtStatusCounts(palettes, row.headline_status)}`
                          : ""}
                      </div>
                    </button>
                  </li>
                );
              })}
            </ul>
          </div>
        )}
        {error && <div className="hint" role="alert" style={{ color: "var(--danger)" }}>{error}</div>}
        {layers && layers.length === 0 && (
          <div className="hint">
            표시할 층이 없습니다. <code>scripts/board_layer.py</code>로 층을 만드세요. (No board layer built yet.)
          </div>
        )}

        {meta && (
          <>
            <div className="field">
              <label htmlFor="board-layer">재해 층 · Layer</label>
              <select id="board-layer" value={layerName} onChange={(e) => setLayerName(e.target.value)}>
                {layerGroups(layers ?? []).map((g) => (
                  <optgroup key={g.label} label={g.label}>
                    {g.layers.map((l) => (
                      <option key={l.layer} value={l.layer}>
                        {layerLabel(l)}
                      </option>
                    ))}
                  </optgroup>
                ))}
              </select>
            </div>

            {!metric && (
              <div className="field">
                <label>재현주기 · Return period</label>
                <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }} role="group" aria-label="재현주기">
                  {allPeriods.map((p) => {
                    const ok = meta.periods_kept.includes(p);
                    const on = rpKey(p) === key;
                    return (
                      <button
                        key={p}
                        className={`btn secondary${on ? " active" : ""}`}
                        style={on ? { borderColor: "var(--accent)", color: "var(--accent)" } : undefined}
                        disabled={!ok}
                        aria-pressed={on}
                        title={ok ? `${p}년 빈도` : `계산 불가: 사건 기록 상한 ${meta.max_supported ?? "?"}년`}
                        onClick={() => setKey(rpKey(p))}
                      >
                        {p}년
                      </button>
                    );
                  })}
                </div>
                {loading && <div className="hint">불러오는 중… (large GeoJSON)</div>}
              </div>
            )}

            {metric && (
              <div className="field">
                <label>{keyGroupLabel}</label>
                <div style={{ display: "flex", flexDirection: "column", gap: 6 }} role="group" aria-label={freqKeys.every((k) => k.scale === "share") ? "장기 빈도" : "표시 항목"}>
                  {freqKeys.map((k) => {
                    const on = k.key === key;
                    return (
                      <button
                        key={k.key}
                        className={`btn secondary${on ? " active" : ""}`}
                        style={on ? { borderColor: "var(--accent)", color: "var(--accent)", textAlign: "left" } : { textAlign: "left" }}
                        aria-pressed={on}
                        onClick={() => setKey(k.key)}
                      >
                        {k.label}
                        {k.comparable_across_regions === false && (
                          <span className="hint" data-testid="not-comparable-tag" style={{ marginLeft: 6, border: "1px solid var(--border)", borderRadius: 4, padding: "0 4px", fontSize: 11 }}>
                            지역 비교용 아님
                          </span>
                        )}
                      </button>
                    );
                  })}
                </div>
                {activeKey?.comparable_across_regions === false && (
                  <div className="hint" data-testid="freq-hint" style={{ marginTop: 6 }}>
                    {meta.notes?.find((n) => n.includes("지역 비교용이 아닙니다")) ?? "지역 비교용이 아닙니다."}
                  </div>
                )}
                {yearKeys.length > 0 && (
                  <>
                    <label htmlFor="board-year" style={{ marginTop: 8 }}>연도별 · Single year</label>
                    <select
                      id="board-year"
                      value={yearKeys.some((k) => k.key === key) ? (key ?? "") : ""}
                      onChange={(e) => e.target.value && setKey(e.target.value)}
                    >
                      <option value="">연도 선택…</option>
                      {yearKeys.map((k) => (
                        <option key={k.key} value={k.key}>{k.label}</option>
                      ))}
                    </select>
                  </>
                )}
                {loading && <div className="hint">불러오는 중… (large GeoJSON)</div>}
              </div>
            )}

            <div aria-label="배지" style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              <div className="hint" data-testid="badge-resolution">
                {metric ? (
                  <strong>기상 격자 1/{Math.round(3600 / meta.resolution_arcsec)}° ≈ {meta.cell_km_ns.toFixed(1)}×{meta.cell_km_ew.toFixed(1)} km</strong>
                ) : (
                  <strong>재해 격자 {Math.round(meta.resolution_arcsec)}″ ≈ {meta.cell_km_ns.toFixed(1)}×{meta.cell_km_ew.toFixed(1)} km</strong>
                )}
              </div>
              <div className="hint" data-testid="badge-tier">
                <strong>{meta.use_tier} — {meta.use_tier_reason}</strong>
              </div>
              {(meta.notes?.length ?? 0) > 0 && (
                <ul className="hint" data-testid="badge-notes" style={{ margin: 0, paddingLeft: 18 }}>
                  {meta.notes?.map((n) => <li key={n}>{n}</li>)}
                </ul>
              )}
              <div className="hint" data-testid="source-line">
                재해: {meta.hazard_source}<br />경계: {meta.boundary_source}
              </div>
            </div>

            {meta.litpop && (
              <div data-testid="litpop-panel">
                <label style={{ display: "flex", alignItems: "center", gap: 6 }}>
                  <input
                    type="checkbox"
                    data-testid="litpop-toggle"
                    checked={litpopOn}
                    onChange={(e) => setLitpopOn(e.target.checked)}
                  />
                  노출 가중(LitPop)
                </label>
                {litpopOn && (
                  <>
                    <div className="hint" data-testid="litpop-banner">{meta.litpop.banner_ko}</div>
                    {palettes && <LitpopLegend litpop={meta.litpop} stroke={palettes.shared.exposure_point.stroke} />}
                    {palettes && <div className="hint" style={{ fontSize: 11 }}>{palettes.shared.exposure_point.meaning}</div>}
                    {meta.litpop.check.warning && (
                      <div className="hint" data-testid="litpop-check" role="alert" style={{ color: "var(--danger)" }}>
                        보조 점검 경고: 주 방법(CLIMADA)과 재현주기 보조 추정이 조각의 {Math.round((meta.litpop.check.disagree_share ?? 0) * 100)} %에서 크게 어긋납니다. 금액은 어림값으로만 읽으세요.
                      </div>
                    )}
                    {(meta.litpop.eal_unassigned_usd ?? 0) > 0 && (
                      <div className="hint" data-testid="litpop-unassigned" style={{ fontSize: 11 }}>
                        LitPop 점 {meta.litpop.n_assigned.toLocaleString("ko-KR")}/{meta.litpop.n_points.toLocaleString("ko-KR")}개만 조각에 놓였습니다. 조각 밖 점의 연평균 손실 약 {Math.round(meta.litpop.eal_unassigned_usd ?? 0).toLocaleString("ko-KR")} USD는 지도와 합계에 넣지 않았습니다.
                      </div>
                    )}
                    {(meta.litpop.assumptions?.length ?? 0) > 0 && (
                      <details data-testid="litpop-assumptions">
                        <summary style={{ cursor: "pointer", fontSize: 12 }}>가정 {meta.litpop.assumptions?.length}개</summary>
                        <ul className="hint" style={{ margin: "4px 0", paddingLeft: 18 }}>
                          {meta.litpop.assumptions?.map((a) => <li key={a}>{a}</li>)}
                          {meta.litpop.impf && <li>피해곡선: {meta.litpop.impf}</li>}
                        </ul>
                      </details>
                    )}
                  </>
                )}
              </div>
            )}

            <div>
              <div className="section-title" data-testid="legend-title">
                범례 · Legend
                {change && palettes
                  ? ` — ${palettes.change.label}`
                  : quantity && palettes?.quantities[quantity]
                    ? ` — ${palettes.quantities[quantity].label} (${palettes.quantities[quantity].units})`
                    : ""}
              </div>
              <div className="legend" data-testid="legend" style={{ display: "flex", flexDirection: "column", gap: 3, marginTop: 6 }}>
                {palettes && (
                  <>
                    <LegendRow color={palettes.shared.null.color} label={palettes.shared.null.label} />
                    {/* zero swatch only where 0 means "none"; stage quantities show it as their own last class */}
                    {((quantity && palettes.quantities[quantity]?.zero_means_none) || change) && (
                      <LegendRow color={palettes.shared.zero.color} label={palettes.shared.zero.label} />
                    )}
                    {change && (
                      <LegendRow
                        color={palettes.shared.base_zero.color}
                        outline={palettes.shared.base_zero.outline}
                        label={palettes.shared.base_zero.label}
                      />
                    )}
                  </>
                )}
                {palettes && quantity && !change && !palettes.quantities[quantity] && (
                  <div className="hint" role="alert">알 수 없는 색 기준: {quantity}</div>
                )}
                {palettes && !quantity && (
                  <div className="hint" role="alert">이 층 파일에 색 기준(quantity)이 없습니다. 층을 다시 내보내세요.</div>
                )}
                {legendClasses.map((c) => (
                  <LegendRow
                    key={`${c.color}|${c.label}`}
                    color={c.color}
                    label={change || (quantity && palettes?.quantities[quantity]?.stages) ? c.label : `${c.label} ${palettes?.quantities[quantity ?? ""]?.units ?? ""}`}
                  />
                ))}
              </div>
              {change && palettes && (
                <div className="hint" data-testid="legend-basis" style={{ marginTop: 4, fontSize: 11 }}>
                  {palettes.change.basis}
                </div>
              )}
              {!change && quantity && palettes?.quantities[quantity]?.basis && (
                <div className="hint" data-testid="legend-basis" style={{ marginTop: 4, fontSize: 11 }}>
                  {palettes.quantities[quantity].basis}
                </div>
              )}
              <div className="hint" style={{ marginTop: 4 }}>
                격자 값 그대로 칠하며 평균하지 않습니다. 시군구 경계에 걸친 칸은 조각으로 나뉘고 값은 같습니다.
              </div>
            </div>

            <div data-testid="pick-panel">
              <div className="section-title">지점 조회 · Lookup</div>
              {!picked && !outside && <div className="hint">지도를 클릭하세요. (Click the map.)</div>}
              {outside && <div className="hint">이 지점에는 재해 자료 조각이 없습니다.</div>}
              {picked && (
                <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                  <div>
                    <strong>{picked.name}</strong> <span className="hint">시군구 코드 {picked.code}</span>
                  </div>
                  {metric ? (
                    <>
                      <table style={{ fontSize: 12, width: "100%" }} data-testid="metric-values">
                        <tbody>
                          {metricShown.map((k) => (
                            <tr key={k.key}>
                              <td>{k.label}</td>
                              <td style={{ textAlign: "right" }}>{fmtMetric(picked.values[k.key], k, palettes, picked.status?.[k.key])}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                      {picked.exposure && <ExposureTable exposure={picked.exposure} />}
                    </>
                  ) : (
                    <table style={{ fontSize: 12, width: "100%" }}>
                      <tbody>
                        {meta.periods_kept.map((p) => (
                          <tr key={p}>
                            <td>{p}년</td>
                            <td style={{ textAlign: "right" }}>
                              {change
                                ? fmtChange(palettes, picked.values[rpKey(p)], picked.status?.[rpKey(p)])
                                : fmt(picked.values[rpKey(p)], units)}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  )}
                  {!metric && litpopOn && picked.exposure && <ExposureTable exposure={picked.exposure} />}
                  <div className="hint">
                    이 지점은 격자 한 칸(약 {picked.cell_km_ns.toFixed(1)}×{picked.cell_km_ew.toFixed(1)} km) 값 하나를 읽습니다. 칸 안 어디를 찍어도 같은 값입니다.
                  </div>
                  <div data-testid="board-add-asset" style={{ display: "flex", flexDirection: "column", gap: 6, borderTop: "1px solid var(--border)", paddingTop: 8 }}>
                    <div className="section-title">자산으로 추가</div>
                    <label style={{ display: "flex", flexDirection: "column", gap: 2, fontSize: 12 }}>
                      자산 이름
                      <input value={assetName} onChange={(e) => { setAssetName(e.target.value); setAssetAdded(false); }} />
                    </label>
                    <label style={{ display: "flex", flexDirection: "column", gap: 2, fontSize: 12 }}>
                      자산 가치 (USD)
                      <input
                        type="number"
                        min="0"
                        value={assetValue}
                        onChange={(e) => { setAssetValue(e.target.value); setAssetAdded(false); }}
                      />
                    </label>
                    <label style={{ display: "flex", flexDirection: "column", gap: 2, fontSize: 12 }}>
                      업종
                      <select value={assetSector} onChange={(e) => setAssetSector(e.target.value)}>
                        {libraries.sectors.sectors.map((s) => (
                          <option key={s.id} value={s.id}>{s.label}</option>
                        ))}
                      </select>
                    </label>
                    <button className="btn" disabled={!valueOk} onClick={addAsset}>자산으로 추가</button>
                    <div className="hint">보드는 금액을 계산하지 않습니다. 자산 가치는 직접 넣은 값만 씁니다.</div>
                    {assetAdded && (
                      <div data-testid="board-asset-added" style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                        <div>자산 목록에 추가했습니다 — 계산과 엑셀은 모델 탭의 기존 엔진(가장 가까운 재해 격자, 이 지점 조회와 같은 값)이 합니다.</div>
                        <button className="btn secondary" onClick={() => onGoTo("models")}>모델 탭에서 실행·엑셀 →</button>
                      </div>
                    )}
                  </div>
                  <button className="btn secondary" onClick={loadRegion}>
                    {picked.name} 시군구 요약
                  </button>
                  {region && (
                    <div data-testid="region-panel">
                      <div className="hint">조각 수 {region.n_pieces}개 · 최소–최대 (평균 없음)</div>
                      {metric ? (
                        <>
                          <table style={{ fontSize: 12, width: "100%" }}>
                            <tbody>
                              {metricShown.map((k) => {
                                const r = (region.by_key ?? region.by_rp)[k.key];
                                const f = (v: number) => v.toFixed(2);
                                return (
                                  <tr key={k.key}>
                                    <td>{k.label}</td>
                                    <td style={{ textAlign: "right" }}>
                                      {r
                                        ? k.quantity === "change"
                                          ? `×${f(r.min)} – ×${f(r.max)}`
                                          : `${f(r.min)} – ${f(r.max)} ${k.units}`
                                        : "자료 없음"}
                                    </td>
                                  </tr>
                                );
                              })}
                            </tbody>
                          </table>
                          {region.exposure_sum && <ExposureTable exposure={region.exposure_sum} sum />}
                        </>
                      ) : (
                        <>
                        <table style={{ fontSize: 12, width: "100%" }}>
                          <tbody>
                            {meta.periods_kept.map((p) => {
                              const r = region.by_rp[rpKey(p)];
                              return (
                                <tr key={p}>
                                  <td>{p}년</td>
                                  <td style={{ textAlign: "right" }}>
                                    {r
                                      ? change
                                        ? `×${r.min.toFixed(2)} – ×${r.max.toFixed(2)}`
                                        : `${r.min.toFixed(2)} – ${r.max.toFixed(2)} ${units}`
                                      : "자료 없음"}
                                  </td>
                                </tr>
                              );
                            })}
                          </tbody>
                        </table>
                        {litpopOn && region.exposure_sum && <ExposureTable exposure={region.exposure_sum} sum />}
                        </>
                      )}
                    </div>
                  )}
                </div>
              )}
            </div>

          </>
        )}

        <div data-testid="case-section">
          <div className="section-title">사례 트윈 · Case twins</div>
          <div className="hint">고해상도 사례 페이지입니다. 전국 격자 결과와 합산·비교하지 않으며 금액은 내지 않습니다.</div>
          <ul data-testid="case-list" style={{ listStyle: "none", margin: "6px 0 0", padding: 0, display: "flex", flexDirection: "column", gap: 6 }}>
            {cases.map((c) => (
              <li key={c.id}>
                <button
                  className="btn secondary"
                  data-testid={`case-open-${c.id}`}
                  disabled={!c.available}
                  title={c.available ? undefined : "아직 만들어지지 않은 사례입니다"}
                  style={{ width: "100%", textAlign: "left" }}
                  onClick={() => askCase(c.id)}
                >
                  {c.name} <span className="hint">· {c.badge} · {c.cell_m} m{c.available ? "" : " · 미생성"}</span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      </aside>
    </div>
  );
}

function LegendRow({ color, label, outline }: { color: string; label: string; outline?: string }) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12 }}>
      <span style={{ width: 18, height: 12, background: color, border: `1px solid ${outline ?? "var(--border)"}`, display: "inline-block" }} />
      {label}
    </div>
  );
}
