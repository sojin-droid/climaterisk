import type { Sheet } from "./types";
import {
  CERTAIN,
  DEEP,
  INK,
  MUTED,
  RANGE,
  RP_KEYS,
  areaPath,
  cellEdges,
  classBand,
  classColor,
  fitHeightRange,
  formatBand,
  linePath,
  linear,
  runs,
  ticks,
} from "./scales";

const W = 1100;
const H = 480;
const X0 = 64;
const X1 = 1090;
const TOP = 34;
const BOT = 372;

/** Step line through per-sample cells, broken at nulls. */
function stairPath(
  edges: [number, number][],
  ys: (number | null)[],
  sx: (v: number) => number,
  sy: (v: number) => number,
): string {
  const d: string[] = [];
  for (const [a, b] of runs(ys)) {
    for (let i = a; i <= b; i++) {
      const y = sy(ys[i] as number).toFixed(1);
      d.push(`${i === a ? "M" : "L"}${sx(edges[i][0]).toFixed(1)} ${y}L${sx(edges[i][1]).toFixed(1)} ${y}`);
    }
  }
  return d.join("");
}

export function SectionChart({ sheet }: { sheet: Sheet }) {
  const { section, site } = sheet;
  const km = section.km;
  const L = section.cut.length_km;
  const sx = linear(0, L, X0, X1);
  const siteUpper = Math.max(
    ...RP_KEYS.map((k) => {
      const b = site.bands_m[k];
      return b ? (b[1] ?? b[0] + 3) : 0;
    }),
  );
  const { lo, hi, clippedAbove } = fitHeightRange(
    section.z_ground_m,
    section.z_surface_m,
    site.z_ground_m,
    siteUpper,
  );
  const sy = linear(lo, hi, BOT, TOP);
  const edges = cellEdges(km);
  const g = section.z_ground_m;
  const c100 = section.classes.rp100;
  const c500 = section.classes.rp500;
  const hatchTop = (z: number, base: number) => Math.min(hi, z + base);

  // Per-sample bands for rp100.
  const rects: JSX.Element[] = [];
  const hatches: JSX.Element[] = [];
  const topVals: (number | null)[] = km.map(() => null);
  km.forEach((_, i) => {
    const z = g[i];
    if (z == null) return;
    const [e0, e1] = edges[i];
    const x = sx(e0);
    const w = Math.max(0.5, sx(e1) - sx(e0) + 0.4);
    const b = classBand(c100[i]);
    if (b) {
      const [bl, bh] = b;
      const yCert = sy(hatchTop(z, bl));
      const yBase = sy(Math.min(hi, z));
      if (bl > 0)
        rects.push(
          <rect key={`c${i}`} x={x} y={yCert} width={w} height={Math.max(0, yBase - yCert)} fill={CERTAIN} />,
        );
      if (bh != null) {
        const yTop = sy(hatchTop(z, bh));
        rects.push(
          <rect key={`r${i}`} x={x} y={yTop} width={w} height={Math.max(0, yCert - yTop)} fill={RANGE} />,
        );
        topVals[i] = Math.min(hi, z + bh);
      } else {
        const yTop = sy(hatchTop(z, bl + 3));
        hatches.push(
          <rect key={`h${i}`} x={x} y={yTop} width={w} height={Math.max(0, yCert - yTop)} fill="url(#sec-hatch)" />,
        );
      }
    }
    // 500-year open class: hatch above ground + 5 m.
    const b5 = classBand(c500[i]);
    if (b5 && b5[1] == null) {
      const y0 = sy(hatchTop(z, b5[0]));
      const y1 = sy(hatchTop(z, b5[0] + 3));
      if (!(b && b[1] == null))
        hatches.push(
          <rect key={`h5${i}`} x={x} y={y1} width={w} height={Math.max(0, y0 - y1)} fill="url(#sec-hatch)" />,
        );
    }
  });

  const siteX = sx(site.km);
  const zg = site.z_ground_m;
  const b100 = site.bands_m.rp100;
  const check = site.check_depth_m?.rp100 ?? null;

  // Check water surface: dashed over the samples around the site that lie below it.
  let checkLine: JSX.Element | null = null;
  if (zg != null && check != null) {
    const level = zg + check;
    let i0 = km.findIndex((k) => k >= site.km);
    if (i0 < 0) i0 = km.length - 1;
    let a = i0;
    let b = i0;
    while (a > 0 && g[a - 1] != null && (g[a - 1] as number) < level) a--;
    while (b < km.length - 1 && g[b + 1] != null && (g[b + 1] as number) < level) b++;
    if (level <= hi) {
      checkLine = (
        <g>
          <line
            x1={sx(edges[a][0])}
            x2={sx(edges[b][1])}
            y1={sy(level)}
            y2={sy(level)}
            stroke={DEEP}
            strokeWidth={1.4}
            strokeDasharray="6 4"
          />
          <text x={siteX - 14} y={sy(level) - 6} textAnchor="end" fontSize={12} fill={DEEP} className="sheet-halo">
            보조 점검 수면 {level.toFixed(1)} m
          </text>
        </g>
      );
    }
  }

  const yTicks = ticks(lo, hi, 4);
  const major = ticks(0, L, 1);
  const minor = ticks(0, L, 0.1);
  const rows = [
    { k: "rp100" as const, label: "100년" },
    { k: "rp200" as const, label: "200년" },
    { k: "rp500" as const, label: "500년" },
  ];
  const stripTop = BOT + 26;

  return (
    <svg
      role="img"
      aria-label={`단면 — 지면 높이와 침수 깊이 (${section.cut.length_km} km, 수직 과장 ×${section.vertical_exaggeration})`}
      viewBox={`0 0 ${W} ${H}`}
      className="sheet-svg"
    >
      <defs>
        <pattern id="sec-hatch" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
          <rect width="6" height="6" fill="#ffffff" fillOpacity="0.35" />
          <line x1="0" y1="0" x2="0" y2="6" stroke="#3182bd" strokeWidth="1.2" />
        </pattern>
        <marker id="sec-arrow" viewBox="0 0 8 8" refX="4" refY="4" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
          <path d="M0 1L8 4L0 7Z" fill={INK} />
        </marker>
      </defs>

      {/* frame + y axis */}
      {yTicks.map((t) => (
        <g key={t}>
          <line x1={X0 - 5} x2={X0} y1={sy(t)} y2={sy(t)} stroke={INK} />
          <text x={X0 - 9} y={sy(t) + 4} fontSize={12} textAnchor="end" className="sheet-mono" fill={MUTED}>
            {t}
          </text>
        </g>
      ))}
      <text x={X0 - 30} y={TOP - 14} fontSize={11} textAnchor="end" fill={MUTED}>
        m
      </text>
      <line x1={X0} x2={X0} y1={TOP} y2={BOT} stroke={INK} />

      {/* surface model (grey), bands, ground */}
      <path d={areaPath(km, section.z_surface_m, sx, sy, lo, hi)} fill="#d3d6d9" />
      <path d={linePath(km, section.z_surface_m, sx, sy, hi)} fill="none" stroke="#9aa0a6" strokeWidth={1} />
      {rects}
      {hatches}
      <path d={stairPath(edges, topVals, sx, sy)} fill="none" stroke="#08519c" strokeWidth={1.6} />
      <path d={areaPath(km, g, sx, sy, lo, hi)} fill={INK} />

      {clippedAbove != null && (
        <text x={X0 + 10} y={TOP + 14} fontSize={11} fill={MUTED}>
          {hi} m 위는 생략
        </text>
      )}

      {checkLine}

      {/* site marker with leader and dimension arrows */}
      {zg != null && (
        <g>
          <line x1={siteX} x2={siteX} y1={TOP - 4} y2={sy(zg)} stroke={INK} strokeWidth={1.2} />
          {b100 && (
            <>
              <line x1={siteX + 22} x2={siteX + 22} y1={sy(zg)} y2={sy(zg + b100[0])} stroke={INK} markerStart="url(#sec-arrow)" markerEnd="url(#sec-arrow)" />
              <text x={siteX + 28} y={sy(zg + b100[0] / 2) + 4} fontSize={12} className="sheet-halo sheet-mono">
                {b100[0]} m
              </text>
              {b100[1] != null && (
                <>
                  <line x1={siteX + 70} x2={siteX + 70} y1={sy(zg)} y2={sy(zg + b100[1])} stroke={INK} markerStart="url(#sec-arrow)" markerEnd="url(#sec-arrow)" />
                  <text x={siteX + 76} y={sy(zg + (b100[0] + b100[1]) / 2) + 4} fontSize={12} className="sheet-halo sheet-mono">
                    {b100[1]} m
                  </text>
                </>
              )}
            </>
          )}
        </g>
      )}
      <circle cx={siteX} cy={TOP - 12} r={11} fill="#FAFAF8" stroke={INK} strokeWidth={1.4} />
      <text x={siteX} y={TOP - 8} fontSize={12} fontWeight={700} textAnchor="middle">
        B
      </text>

      {/* labels on the water */}
      {zg != null && b100 && (
        <text x={siteX + 120} y={sy(zg + (b100[1] ?? b100[0] + 3)) - 6} fontSize={13} fontWeight={700} fill={DEEP} className="sheet-halo">
          100년 · 지면 위 {formatBand(b100)}
        </text>
      )}
      <text x={X0 + 180} y={BOT - 22} fontSize={12} fill="#FAFAF8">
        지면 · {section.ground_source ?? "—"}
      </text>

      {/* cut ends */}
      <text x={X0} y={TOP - 14} fontSize={13} fontWeight={700} textAnchor="middle">
        A
      </text>
      <text x={X1} y={TOP - 14} fontSize={13} fontWeight={700} textAnchor="middle">
        A′
      </text>

      {/* x axis ticks */}
      <line x1={X0} x2={X1} y1={BOT} y2={BOT} stroke={INK} />
      {minor.map((t) => (
        <line key={`m${t}`} x1={sx(t)} x2={sx(t)} y1={BOT} y2={BOT + 3} stroke={INK} strokeWidth={0.6} />
      ))}
      {major.map((t) => (
        <line key={`M${t}`} x1={sx(t)} x2={sx(t)} y1={BOT} y2={BOT + 8} stroke={INK} />
      ))}

      {/* class strips under the axis */}
      {rows.map((r, ri) => (
        <g key={r.k}>
          <text x={X0 - 9} y={stripTop + ri * 14 + 9} fontSize={11} textAnchor="end" className="sheet-mono" fill={MUTED}>
            {r.label}
          </text>
          {section.classes[r.k].map((code, i) => {
            const c = classColor(code);
            if (c === "none") return null;
            const [e0, e1] = edges[i];
            return (
              <rect
                key={i}
                x={sx(e0)}
                y={stripTop + ri * 14}
                width={Math.max(0.5, sx(e1) - sx(e0) + 0.4)}
                height={11}
                fill={c}
              />
            );
          })}
        </g>
      ))}
      {major.map((t) => (
        <text key={`L${t}`} x={sx(t)} y={stripTop + 3 * 14 + 16} fontSize={12} textAnchor={t === major[major.length - 1] ? "end" : "middle"} className="sheet-mono" fill={MUTED}>
          {t === major[major.length - 1] ? `${t} km` : t}
        </text>
      ))}
      <text x={X0} y={H - 6} fontSize={11} fill={MUTED}>
        지면 {section.ground_source ?? "—"} · 표면 {section.surface_source ?? "—"}
      </text>
    </svg>
  );
}
