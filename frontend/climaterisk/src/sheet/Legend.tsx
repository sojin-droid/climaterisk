import { DEEP, INK, NODATA_LABEL, RAMP, RAMP_LABELS } from "./scales";
import { NoDataHatch } from "./NoData";

function Line({ dash, color = INK, width = 1.6 }: { dash?: string; color?: string; width?: number }) {
  return (
    <svg width="34" height="10" viewBox="0 0 34 10" aria-hidden="true">
      <line x1="0" x2="34" y1="5" y2="5" stroke={color} strokeWidth={width} strokeDasharray={dash} />
    </svg>
  );
}

export function Legend() {
  return (
    <ul aria-label="범례" className="sheet-legend">
      {RAMP.map((c, i) => (
        <li key={c}>
          <span className="swatch" style={{ background: c }} />
          {RAMP_LABELS[i]}
        </li>
      ))}
      <li>
        <svg width="22" height="12" viewBox="0 0 22 12" aria-hidden="true" className="swatch">
          <defs>
            <NoDataHatch id="legend-nodata" />
          </defs>
          <rect x="0.5" y="0.5" width="21" height="11" fill="url(#legend-nodata)" stroke="#9aa0a6" />
        </svg>
        {NODATA_LABEL}
      </li>
      <li>
        <Line color="#08519c" />
        공식 등급 (한국 자료)
      </li>
      <li>
        <Line color={DEEP} dash="6 4" />
        지형 점검
      </li>
      <li>
        <Line color={DEEP} dash="14 3 2 3 2 3" />
        전 지구 모형
      </li>
      <li>
        <span className="swatch" style={{ background: "#d3d6d9", border: "1px solid #9aa0a6" }} />
        건물 포함 표면
      </li>
    </ul>
  );
}
