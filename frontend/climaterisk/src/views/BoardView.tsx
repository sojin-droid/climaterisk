import { useEffect, useMemo, useState } from "react";
import { GeoJSON, MapContainer, TileLayer, useMapEvents } from "react-leaflet";
import type { PolylineOptions } from "leaflet";
import {
  NULL_COLOR,
  POSITIVE_CLASSES,
  ZERO_COLOR,
  getBoardGeojson,
  getBoardLayers,
  getBoardLookup,
  getBoardRegion,
  rpKey,
  valueColor,
  type BoardCollection,
  type BoardFeature,
  type BoardLookup,
  type BoardMeta,
  type BoardRegion,
} from "../lib/board";
import { OFFLINE_MAP_NOTE, useOffline } from "../lib/offline";

const fmt = (v: number | null | undefined, units: string) =>
  v === null || v === undefined ? "자료 없음" : v === 0 ? `0 ${units} (침수 없음)` : `${v.toFixed(2)} ${units}`;

function ClickLookup({ onPick }: { onPick: (lon: number, lat: number) => void }) {
  useMapEvents({ click: (e) => onPick(e.latlng.lng, e.latlng.lat) });
  return null;
}

export function BoardView() {
  const offline = useOffline();
  const [layers, setLayers] = useState<BoardMeta[] | null>(null);
  const [layerName, setLayerName] = useState<string>("");
  const [rp, setRp] = useState<number | null>(null);
  const [fc, setFc] = useState<BoardCollection | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [picked, setPicked] = useState<BoardLookup | null>(null);
  const [outside, setOutside] = useState(false);
  const [region, setRegion] = useState<BoardRegion | null>(null);

  useEffect(() => {
    getBoardLayers()
      .then((ls) => {
        setLayers(ls);
        if (ls.length > 0) setLayerName(ls[0].layer);
      })
      .catch((e: Error) => setError(e.message));
  }, []);

  const meta = layers?.find((l) => l.layer === layerName) ?? null;

  // Default to the longest computable period ≤ 100 y (else the longest kept).
  useEffect(() => {
    if (!meta) return;
    const kept = meta.periods_kept;
    setRp((cur) => (cur !== null && kept.includes(cur) ? cur : (kept.filter((p) => p <= 100).pop() ?? kept[kept.length - 1] ?? null)));
    setPicked(null);
    setRegion(null);
    setOutside(false);
  }, [meta]);

  useEffect(() => {
    if (!meta || rp === null) return;
    let live = true;
    setLoading(true);
    setError(null);
    getBoardGeojson(meta.layer, rp)
      .then((c) => live && setFc(c))
      .catch((e: Error) => live && setError(e.message))
      .finally(() => live && setLoading(false));
    return () => {
      live = false;
    };
  }, [meta, rp]);

  const allPeriods = useMemo(() => {
    if (!meta) return [];
    const req = meta.requested_periods ?? meta.periods_kept;
    return [...new Set([...req, ...meta.periods_kept])].sort((a, b) => a - b);
  }, [meta]);

  const pickPoint = (lon: number, lat: number) => {
    if (!meta) return;
    setRegion(null);
    setOutside(false);
    getBoardLookup(meta.layer, lon, lat)
      .then(setPicked)
      .catch(() => {
        setPicked(null);
        setOutside(true);
      });
  };

  const loadRegion = () => {
    if (!meta || !picked) return;
    getBoardRegion(meta.layer, picked.code)
      .then(setRegion)
      .catch((e: Error) => setError(e.message));
  };

  const style = (f?: { properties?: { value?: number | null } }): PolylineOptions => ({
    fillColor: valueColor(f?.properties?.value),
    fillOpacity: 0.85,
    color: "#1b2733",
    weight: 0.4,
    opacity: 0.7,
    smoothFactor: 0, // no simplification: crisp grid edges
  });

  const highlighted: BoardFeature | undefined = useMemo(
    () => (picked && fc ? fc.features.find((f) => f.properties.id === picked.id) : undefined),
    [picked, fc],
  );

  const units = meta?.units ?? "";

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
          {fc && meta && rp !== null && (
            <GeoJSON key={`${meta.layer}|${rp}`} data={fc as never} style={style as never} />
          )}
          {highlighted && (
            <GeoJSON
              key={`hl-${picked?.id}`}
              data={highlighted as never}
              style={() => ({ fill: false, color: "#ffd24a", weight: 3, opacity: 1, smoothFactor: 0 })}
              interactive={false}
            />
          )}
          <ClickLookup onPick={pickPoint} />
        </MapContainer>
      </div>

      <aside className="sidepanel" aria-label="시각 보드 패널">
        <div className="section-title">시각 보드 · Visual board</div>
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
                {layers?.map((l) => (
                  <option key={l.layer} value={l.layer}>
                    {l.layer === "river_flood_hist_kor" ? "하천 홍수 · 과거(1980–2000) · 한국" : l.layer}
                  </option>
                ))}
              </select>
            </div>

            <div className="field">
              <label>재현주기 · Return period</label>
              <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }} role="group" aria-label="재현주기">
                {allPeriods.map((p) => {
                  const ok = meta.periods_kept.includes(p);
                  return (
                    <button
                      key={p}
                      className={`btn secondary${p === rp ? " active" : ""}`}
                      style={p === rp ? { borderColor: "var(--accent)", color: "var(--accent)" } : undefined}
                      disabled={!ok}
                      aria-pressed={p === rp}
                      title={ok ? `${p}년 빈도` : `계산 불가: 사건 기록 상한 ${meta.max_supported ?? "?"}년`}
                      onClick={() => setRp(p)}
                    >
                      {p}년
                    </button>
                  );
                })}
              </div>
              {loading && <div className="hint">불러오는 중… (large GeoJSON)</div>}
            </div>

            <div aria-label="배지" style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              <div className="hint" data-testid="badge-resolution">
                <strong>재해 격자 {Math.round(meta.resolution_arcsec)}″ ≈ {meta.cell_km_ns.toFixed(1)}×{meta.cell_km_ew.toFixed(1)} km</strong>
              </div>
              <div className="hint" data-testid="badge-tier">
                <strong>{meta.use_tier} — {meta.use_tier_reason}</strong>
              </div>
              <div className="hint" data-testid="source-line">
                재해: {meta.hazard_source}<br />경계: {meta.boundary_source}
              </div>
            </div>

            <div>
              <div className="section-title">범례 · Legend ({units})</div>
              <div className="legend" style={{ display: "flex", flexDirection: "column", gap: 3, marginTop: 6 }}>
                <LegendRow color={NULL_COLOR} label="자료 없음 (null)" />
                <LegendRow color={ZERO_COLOR} label="0 (침수 없음)" />
                {POSITIVE_CLASSES.map((c) => (
                  <LegendRow key={c.color} color={c.color} label={`${c.label} ${units}`} />
                ))}
              </div>
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
                  <table style={{ fontSize: 12, width: "100%" }}>
                    <tbody>
                      {meta.periods_kept.map((p) => (
                        <tr key={p}>
                          <td>{p}년</td>
                          <td style={{ textAlign: "right" }}>{fmt(picked.values[rpKey(p)], units)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  <div className="hint">
                    이 지점은 격자 한 칸(약 {picked.cell_km_ns.toFixed(1)}×{picked.cell_km_ew.toFixed(1)} km) 값 하나를 읽습니다. 칸 안 어디를 찍어도 같은 값입니다.
                  </div>
                  <button className="btn secondary" onClick={loadRegion}>
                    {picked.name} 시군구 요약
                  </button>
                  {region && (
                    <div data-testid="region-panel">
                      <div className="hint">조각 수 {region.n_pieces}개 · 최소–최대 (평균 없음)</div>
                      <table style={{ fontSize: 12, width: "100%" }}>
                        <tbody>
                          {meta.periods_kept.map((p) => {
                            const r = region.by_rp[rpKey(p)];
                            return (
                              <tr key={p}>
                                <td>{p}년</td>
                                <td style={{ textAlign: "right" }}>
                                  {r ? `${r.min.toFixed(2)} – ${r.max.toFixed(2)} ${units}` : "자료 없음"}
                                </td>
                              </tr>
                            );
                          })}
                        </tbody>
                      </table>
                    </div>
                  )}
                </div>
              )}
            </div>

            <div className="card" style={{ border: "1px dashed var(--border)", borderRadius: 8, padding: 10 }}>
              <strong>사례 트윈: 강남역 (미검증)</strong>
              <div className="hint">사례 트윈 페이지는 이후 단계에서 제공됩니다. 전국 격자 결과와 합산·비교하지 않습니다. (Case twin page: later step.)</div>
            </div>
          </>
        )}
      </aside>
    </div>
  );
}

function LegendRow({ color, label }: { color: string; label: string }) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12 }}>
      <span style={{ width: 18, height: 12, background: color, border: "1px solid var(--border)", display: "inline-block" }} />
      {label}
    </div>
  );
}
