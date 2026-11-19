import type { GlobalPeriod, Sheet } from "./types";
import { CERTAIN, DEEP, INK, MUTED, NODATA_GREY, RANGE, RP_KEYS, linear, ticks } from "./scales";
import { NoDataHatch } from "./NoData";

const W = 360;
const H = 320;
const TOP = 30;
const BOT = 244;
const CAP = 8;

function periodLabel(key: string): string {
  const m = key.match(/(\d{4})_(\d{4})$/);
  const span = m ? `${m[1]}–${m[2]}` : key;
  return key.startsWith("hist") ? `과거 ${span}` : `미래 ${span}`;
}

export function FrequencyChart({ sheet }: { sheet: Sheet }) {
  const rps = sheet.frequency.return_periods;
  const site = sheet.site;
  const periods = Object.entries(sheet.frequency.global).filter(
    ([, v]) => v && typeof v === "object" && "rp" in (v as object),
  ) as [string, GlobalPeriod][];

  // values that set the axis (outliers beyond CAP get an arrow instead)
  const vals: number[] = [];
  RP_KEYS.forEach((k) => {
    const b = site.bands_m[k];
    if (b) vals.push(b[1] ?? b[0] + 2);
    const c = site.check_depth_m?.[k];
    if (c != null) vals.push(c);
  });
  periods.forEach(([, p]) => p.depth_m.forEach((d) => d <= CAP && vals.push(d)));
  const axisMax = Math.min(CAP, Math.max(4, Math.ceil(Math.max(...vals, 0) + 1)));
  const sy = linear(0, axisMax, BOT, TOP);
  const lnRp = (rp: number) => Math.log(rp);
  const sx = linear(lnRp(70), lnRp(700), 52, 340);

  return (
    <svg
      role="img"
      aria-label="곡선 — 재현주기별 침수 깊이: 공식 등급 범위, 지형 점검, 전 지구 모형"
      viewBox={`0 0 ${W} ${H}`}
      className="sheet-svg"
    >
      <defs>
        <pattern id="freq-hatch" width="5" height="5" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
          <rect width="5" height="5" fill="#ffffff" fillOpacity="0.35" />
          <line x1="0" y1="0" x2="0" y2="5" stroke="#3182bd" strokeWidth="1.1" />
        </pattern>
        <marker id="freq-arrow" viewBox="0 0 8 8" refX="4" refY="4" markerWidth="6" markerHeight="6" orient="auto">
          <path d="M0 8L4 0L8 8Z" fill={DEEP} />
        </marker>
        <NoDataHatch id="freq-nodata" />
      </defs>
      <rect x={1} y={1} width={W - 2} height={H - 2} fill="none" stroke={INK} />
      <line x1={44} x2={44} y1={TOP} y2={BOT} stroke={INK} />
      {ticks(0, axisMax, 2).map((t) => (
        <g key={t}>
          <line x1={40} x2={44} y1={sy(t)} y2={sy(t)} stroke={INK} />
          <text x={37} y={sy(t) + 4} fontSize={10} textAnchor="end" className="sheet-mono" fill={MUTED}>
            {t}
          </text>
        </g>
      ))}
      <text x={44} y={TOP - 10} fontSize={10} fill={MUTED} textAnchor="middle">
        지면 위 m
      </text>

      {/* phantom line = global model, historical period */}
      {periods
        .filter(([k, p]) => k.startsWith("hist") && p.rp.length === 0)
        .map(([k]) => (
          <g key={k}>
            <line x1={44} x2={W - 6} y1={sy(0)} y2={sy(0)} stroke={DEEP} strokeWidth={1.4} strokeDasharray="14 3 2 3 2 3" />
            <text x={12} y={BOT + 36} fontSize={11} fill={DEEP}>
              전 지구 모형 · {periodLabel(k)}: 0 m
            </text>
          </g>
        ))}

      {rps.map((rp, i) => {
        const key = RP_KEYS[i];
        const b = site.bands_m[key];
        const x = sx(lnRp(rp));
        const w = 20;
        const check = site.check_depth_m?.[key] ?? null;
        // unknown at this return period (no map file / unreadable): an empty marker, never 0 m
        const unknown = b == null && site.classes[key] !== "";
        const reason = site.reasons?.[key] ?? null;
        return (
          <g key={rp}>
            <line x1={x} x2={x} y1={TOP} y2={BOT} stroke={INK} strokeWidth={0.5} strokeOpacity={0.35} />
            <line x1={x} x2={x} y1={BOT} y2={BOT + 5} stroke={INK} />
            <text x={x} y={BOT + 18} fontSize={11} textAnchor="middle" className="sheet-mono">
              {rp}
            </text>
            {b && (
              <>
                <rect x={x - w / 2} y={sy(b[0])} width={w} height={sy(0) - sy(b[0])} fill={CERTAIN} />
                {b[1] != null ? (
                  <>
                    <rect x={x - w / 2} y={sy(b[1])} width={w} height={sy(b[0]) - sy(b[1])} fill={RANGE} />
                    <line x1={x - w / 2} x2={x + w / 2} y1={sy(b[1])} y2={sy(b[1])} stroke="#08519c" strokeWidth={1.6} />
                  </>
                ) : (
                  <rect x={x - w / 2} y={sy(b[0] + 2)} width={w} height={sy(b[0]) - sy(b[0] + 2)} fill="url(#freq-hatch)" />
                )}
              </>
            )}
            {unknown && (
              <g aria-label={`${rp}년 값 없음`}>
                <title>{reason ? `값 없음 (${reason})` : "값 없음"}</title>
                <rect
                  x={x - w / 2}
                  y={sy(1.2)}
                  width={w}
                  height={sy(0) - sy(1.2)}
                  fill="url(#freq-nodata)"
                  stroke={NODATA_GREY}
                  strokeWidth={1}
                  strokeDasharray="3 2"
                />
                <text x={x} y={sy(1.2) - 5} fontSize={10} textAnchor="middle" fill={NODATA_GREY} className="sheet-halo">
                  값 없음
                </text>
              </g>
            )}
            {check != null && (
              <circle cx={x + w / 2 + 12} cy={sy(Math.min(check, axisMax))} r={5} fill="none" stroke={DEEP} strokeWidth={1.3} strokeDasharray="2 2" />
            )}
          </g>
        );
      })}
      <text x={W - 8} y={BOT + 52} fontSize={10} fill={MUTED} textAnchor="end">
        재현주기 (년)
      </text>

      {/* global model future markers */}
      {periods.map(([k, p]) =>
        p.rp.map((rp, j) => {
          const d = p.depth_m[j];
          const x = sx(lnRp(rp));
          const label = `${k.startsWith("hist") ? "과거" : "미래"} ${rp}년: ${d.toFixed(1)} m`;
          if (d > axisMax) {
            return (
              <g key={`${k}${rp}`}>
                <line x1={x} x2={x} y1={TOP + 14} y2={TOP + 2} stroke={DEEP} strokeWidth={1.3} markerEnd="url(#freq-arrow)" />
                <text x={x - 8} y={TOP + 12} fontSize={11} fill={DEEP} textAnchor="end" className="sheet-halo">
                  {label}
                </text>
              </g>
            );
          }
          return (
            <g key={`${k}${rp}`}>
              <rect x={x - 5} y={sy(d) - 5} width={10} height={10} transform={`rotate(45 ${x} ${sy(d)})`} fill="none" stroke={DEEP} strokeWidth={1.3} strokeDasharray="2 2" />
              <text x={x + 10} y={sy(d) + 4} fontSize={11} fill={DEEP} className="sheet-halo">
                {label}
              </text>
            </g>
          );
        }),
      )}
    </svg>
  );
}
