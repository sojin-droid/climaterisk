import type { Sheet } from "./types";
import { INK, MUTED, cellEdges, classColor, linear, ticks } from "./scales";
import { NoDataHatch } from "./NoData";

const W = 1100;
const H = 250;
const X0 = 64;
const X1 = 1090;
const TOP = 34;
const BOT = 226;

export function PlanChart({ sheet }: { sheet: Sheet }) {
  const { section, site } = sheet;
  const cut = section.cut;
  const sxLon = linear(cut.lon0, cut.lon1, X0, X1);
  const sxKm = linear(0, cut.length_km, X0, X1);
  const yCut = (TOP + BOT) / 2;
  const edges = cellEdges(section.km);
  const kmPx = (X1 - X0) / cut.length_km; // px per km at the cut latitude
  const lonTicks = ticks(Math.min(cut.lon0, cut.lon1), Math.max(cut.lon0, cut.lon1), 0.01);
  const siteX = sxKm(site.km);
  const barX = X0 + 14;
  const barY = BOT - 22;

  return (
    <svg
      role="img"
      aria-label={`평면 — 자른 선 A–A′ ${cut.length_km} km 와 자산 B 의 위치`}
      viewBox={`0 0 ${W} ${H}`}
      className="sheet-svg"
    >
      <defs>
        <marker id="plan-arrow" viewBox="0 0 10 10" refX="5" refY="5" markerWidth="8" markerHeight="8" orient="auto">
          <path d="M0 10L5 0L10 10Z" fill={INK} />
        </marker>
        <NoDataHatch id="plan-nodata" />
      </defs>
      <rect x={X0} y={TOP} width={X1 - X0} height={BOT - TOP} fill="#eef3f8" stroke={INK} strokeWidth={1.2} />

      {/* graticule */}
      {lonTicks.map((lon) => (
        <g key={lon}>
          <line x1={sxLon(lon)} x2={sxLon(lon)} y1={TOP - 6} y2={TOP} stroke={INK} />
          <line x1={sxLon(lon)} x2={sxLon(lon)} y1={TOP} y2={BOT} stroke="#c6d3df" strokeWidth={0.6} strokeDasharray="2 4" />
          <text x={sxLon(lon)} y={TOP - 10} fontSize={12} textAnchor="middle" className="sheet-mono" fill={MUTED}>
            {lon.toFixed(2)}°E
          </text>
        </g>
      ))}
      <text x={X0 - 8} y={yCut + 4} fontSize={11} textAnchor="end" className="sheet-mono" fill={MUTED}>
        {cut.lat}°N
      </text>
      <line x1={X0 - 5} x2={X0} y1={yCut} y2={yCut} stroke={INK} />

      {/* class strip along the cut (100-year class) */}
      {section.classes.rp100.map((code, i) => {
        const c = classColor(code, "plan-nodata");
        if (c === "none") return null; // outside every polygon: dry, undrawn
        return (
          <rect
            key={i}
            x={sxKm(edges[i][0])}
            y={yCut - 6}
            width={Math.max(0.5, sxKm(edges[i][1]) - sxKm(edges[i][0]) + 0.4)}
            height={12}
            fill={c}
          >
            {code == null && <title>값 없음</title>}
          </rect>
        );
      })}

      {/* cut line A–A′: chain line, heavy end ticks, section arrows */}
      <line x1={X0} x2={X1} y1={yCut} y2={yCut} stroke={INK} strokeWidth={1.4} strokeDasharray="18 4 3 4" />
      {[X0, X1].map((x) => (
        <g key={x}>
          <line x1={x} x2={x} y1={yCut - 22} y2={yCut + 22} stroke={INK} strokeWidth={3.5} />
          <line x1={x + (x === X0 ? 5 : -5)} x2={x + (x === X0 ? 5 : -5)} y1={yCut - 4} y2={yCut - 40} stroke={INK} strokeWidth={1.4} markerEnd="url(#plan-arrow)" />
        </g>
      ))}
      <text x={X0 + 6} y={yCut + 38} fontSize={13} fontWeight={700}>
        A
      </text>
      <text x={X1 - 6} y={yCut + 38} fontSize={13} fontWeight={700} textAnchor="end">
        A′
      </text>

      {/* site */}
      <circle cx={siteX} cy={yCut} r={5.5} fill={INK} stroke="#FAFAF8" strokeWidth={1.5} />
      <text x={siteX + 10} y={yCut - 12} fontSize={13} fontWeight={700} className="sheet-halo">
        자산 B
      </text>

      {/* north arrow */}
      <g transform={`translate(${X1 - 22} ${TOP + 10})`}>
        <path d="M0 0L7 22L0 17L-7 22Z" fill={INK} />
        <text x={0} y={-3} fontSize={12} fontWeight={700} textAnchor="middle">
          N
        </text>
      </g>

      {/* scale bar from the real cut length */}
      <rect x={barX - 8} y={barY - 18} width={kmPx + 66} height={40} fill="#FAFAF8" fillOpacity={0.9} />
      <rect x={barX} y={barY} width={kmPx / 2} height={6} fill={INK} />
      <rect x={barX + kmPx / 2} y={barY} width={kmPx / 2} height={6} fill="#FAFAF8" stroke={INK} strokeWidth={1} />
      {[
        [0, "0"],
        [0.5, "500 m"],
        [1, "1 km"],
      ].map(([f, t]) => (
        <g key={String(t)}>
          <line x1={barX + (f as number) * kmPx} x2={barX + (f as number) * kmPx} y1={barY - 4} y2={barY + 10} stroke={INK} />
          <text x={barX + (f as number) * kmPx} y={barY - 7} fontSize={11} textAnchor="middle" className="sheet-mono">
            {t}
          </text>
        </g>
      ))}

      <text x={X1 - 10} y={BOT - 8} fontSize={11} textAnchor="end" fill={MUTED}>
        범람 면적 칠은 다음 단계에서 붙습니다 (지도 도형은 이 도면에 싣지 않음)
      </text>
    </svg>
  );
}
