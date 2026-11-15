import type { Sheet } from "./types";
import { CERTAIN, DEEP, INK, INK2, RANGE, linear } from "./scales";

const W = 360;
const H = 300;
const GROUND_Y = 280;
const PXM = 34; // true scale: no vertical exaggeration

export function DetailChart({ sheet }: { sheet: Sheet }) {
  const b = sheet.site.bands_m.rp100;
  const check = sheet.site.check_depth_m?.rp100 ?? null;
  const sy = linear(0, 1, GROUND_Y, GROUND_Y - PXM);
  const top = 40;
  const x0 = 8;
  const x1 = W - 8;
  const personX = 52;
  const wall0 = 112;
  const wall1 = 252;

  return (
    <svg
      role="img"
      aria-label="상세 — 자산 지점 침수 깊이를 과장 없이, 사람·문·2층과 견줌"
      viewBox={`0 0 ${W} ${H}`}
      className="sheet-svg"
    >
      <defs>
        <pattern id="det-hatch" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
          <rect width="6" height="6" fill="#ffffff" fillOpacity="0.35" />
          <line x1="0" y1="0" x2="0" y2="6" stroke="#3182bd" strokeWidth="1.2" />
        </pattern>
        <marker id="det-arrow" viewBox="0 0 8 8" refX="4" refY="4" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
          <path d="M0 1L8 4L0 7Z" fill={INK} />
        </marker>
      </defs>
      <rect x={1} y={1} width={W - 2} height={H - 2} fill="#FAFAF8" stroke={INK} />
      <text x={W / 2} y={22} fontSize={11} textAnchor="middle" fill={INK2}>
        건물은 예시 (층고 3 m)
      </text>

      {/* band fills */}
      {b && (
        <g>
          <rect x={x0} y={sy(b[0])} width={x1 - x0} height={sy(0) - sy(b[0])} fill={CERTAIN} />
          {b[1] != null ? (
            <>
              <rect x={x0} y={sy(b[1])} width={x1 - x0} height={sy(b[0]) - sy(b[1])} fill={RANGE} />
              <line x1={x0} x2={x1} y1={sy(b[1])} y2={sy(b[1])} stroke="#08519c" strokeWidth={1.6} />
            </>
          ) : (
            <rect x={x0} y={top} width={x1 - x0} height={sy(b[0]) - top} fill="url(#det-hatch)" />
          )}
        </g>
      )}

      {/* example building: second floor at 3 m, door 2.1 m */}
      <rect x={wall0} y={sy(5.8)} width={wall1 - wall0} height={sy(0) - sy(5.8)} fill="none" stroke={INK2} strokeWidth={1.2} />
      <line x1={wall0} x2={wall1} y1={sy(3)} y2={sy(3)} stroke={INK2} strokeWidth={1.2} />
      <text x={wall0 + 6} y={sy(3) - 5} fontSize={11} fill={INK} className="sheet-halo">
        2층 바닥 3 m
      </text>
      <rect x={160} y={sy(2.1)} width={30} height={sy(0) - sy(2.1)} fill="none" stroke={INK2} strokeWidth={1.2} strokeDasharray="3 2" />
      <text x={175} y={sy(2.1) - 4} fontSize={11} textAnchor="middle" className="sheet-halo sheet-mono">
        문 2.1 m
      </text>

      {/* person 1.7 m */}
      <g stroke={INK} strokeWidth={1.6} fill="none" strokeLinecap="round">
        <circle cx={personX} cy={sy(1.58)} r={4.5} fill={INK} />
        <line x1={personX} x2={personX} y1={sy(1.45)} y2={sy(0.85)} />
        <line x1={personX - 9} x2={personX + 9} y1={sy(1.25)} y2={sy(1.25)} />
        <line x1={personX} x2={personX - 7} y1={sy(0.85)} y2={sy(0)} />
        <line x1={personX} x2={personX + 7} y1={sy(0.85)} y2={sy(0)} />
      </g>
      <text x={personX + 12} y={sy(1.7) + 2} fontSize={11} className="sheet-halo sheet-mono">
        1.7 m
      </text>

      {/* dimension arrows from the band */}
      {b && (
        <g>
          <line x1={286} x2={286} y1={sy(0)} y2={sy(b[0])} stroke={INK} markerStart="url(#det-arrow)" markerEnd="url(#det-arrow)" />
          <text x={280} y={sy(b[0] / 2) + 4} fontSize={12} textAnchor="end" className="sheet-halo sheet-mono">
            {b[0]} m
          </text>
          {b[1] != null && (
            <>
              <line x1={326} x2={326} y1={sy(0)} y2={sy(b[1])} stroke={INK} markerStart="url(#det-arrow)" markerEnd="url(#det-arrow)" />
              <text x={320} y={sy(b[1] / 2) + 4} fontSize={12} textAnchor="end" className="sheet-halo sheet-mono">
                {b[1]} m
              </text>
            </>
          )}
        </g>
      )}

      {check != null && (
        <g>
          <line x1={x0} x2={x1} y1={sy(check)} y2={sy(check)} stroke={DEEP} strokeWidth={1.4} strokeDasharray="6 4" />
          <text x={x0 + 4} y={sy(check) - 4} fontSize={11} fill={DEEP} className="sheet-halo sheet-mono">
            점검 {check} m
          </text>
        </g>
      )}

      <rect x={x0} y={GROUND_Y} width={x1 - x0} height={H - 8 - GROUND_Y} fill={INK} />
    </svg>
  );
}
