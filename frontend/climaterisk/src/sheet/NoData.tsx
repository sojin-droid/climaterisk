import { NODATA_GREY } from "./scales";

/**
 * The one "unknown" fill of the sheet drawings: thin diagonal grey lines on white. A class that
 * is null (no map file, or a file that cannot be read) is drawn with it — never left blank like
 * "" (outside every polygon, dry). Place it in each SVG's <defs> with a per-drawing id and fill
 * with classColor(code, id).
 */
export function NoDataHatch({ id }: { id: string }) {
  return (
    <pattern id={id} width="5" height="5" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
      <rect width="5" height="5" fill="#ffffff" />
      <line x1="0" y1="0" x2="0" y2="5" stroke={NODATA_GREY} strokeWidth="0.9" />
    </pattern>
  );
}
