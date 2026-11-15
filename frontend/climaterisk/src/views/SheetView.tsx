import { useEffect, useState } from "react";
import { getSheet, listSheets } from "../lib/api";
import type { Sheet, SheetSummary } from "../sheet/types";
import { PlanChart } from "../sheet/PlanChart";
import { SectionChart } from "../sheet/SectionChart";
import { FrequencyChart } from "../sheet/FrequencyChart";
import { DetailChart } from "../sheet/DetailChart";
import { Stamps } from "../sheet/Stamps";
import { Legend } from "../sheet/Legend";
import { TitleBlock } from "../sheet/TitleBlock";
import { formatBand } from "../sheet/scales";
import "../sheet/sheet.css";

const HAZARD_KO: Record<string, string> = { river_flood: "하천 범람" };

function Headline({ sheet }: { sheet: Sheet }) {
  const b = sheet.site.bands_m.rp100;
  const outside = !sheet.site.classes.rp100 || !b;
  const sub = (["rp200", "rp500"] as const)
    .map((k, i) => {
      const t = formatBand(sheet.site.bands_m[k]);
      return `${i === 0 ? "200" : "500"}년 빈도${i === 0 ? "도" : "는"} ${t ?? "범위 밖"}`;
    })
    .join(", ");
  return (
    <>
      <h1>
        {outside ? (
          "이 자리는 100년 빈도 침수 범위 밖입니다."
        ) : (
          <>
            제방이 무너지면, 100년에 한 번 오는 홍수에 이 자리는 <em>{formatBand(b)}</em>{" "}
            잠깁니다.
          </>
        )}
      </h1>
      <p className="sheet-sub">
        {sub}입니다. 깊이는 환경부 홍수위험지도의 수심 등급이고, 이 지도는 제방이 무너지거나 물이 넘는 경우를 가정합니다.
      </p>
    </>
  );
}

export function SheetView() {
  const [list, setList] = useState<SheetSummary[] | null>(null);
  const [sheet, setSheet] = useState<Sheet | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState<string | null>(null);

  useEffect(() => {
    listSheets()
      .then(setList)
      .catch((e) => setError(String(e)));
  }, []);

  const open = (id: string) => {
    setLoading(id);
    setError(null);
    getSheet(id)
      .then(setSheet)
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(null));
  };

  return (
    <div className="sheet-view">
      <div className="sheet-list">
        <span className="sheet-list-title">도면</span>
        {list === null && !error && <span className="sheet-muted">불러오는 중…</span>}
        {list?.length === 0 && <span className="sheet-muted">쓸 수 있는 도면이 없습니다.</span>}
        {list?.map((s) => (
          <button
            key={s.sheet_id}
            className={sheet?.sheet_id === s.sheet_id ? "on" : ""}
            onClick={() => open(s.sheet_id)}
            title={s.place}
          >
            {s.sheet_id} {s.title}
          </button>
        ))}
        {loading && <span className="sheet-muted">{loading} 불러오는 중…</span>}
        {error && <span className="sheet-error">{error}</span>}
      </div>

      {sheet && (
        <section aria-label={`자산 도면 ${sheet.sheet_id}`} className="sheet">
          <div className="sheet-main">
            <header className="sheet-head">
              <div className="sheet-meta sheet-mono">
                {sheet.sheet_id} · {HAZARD_KO[sheet.hazard] ?? sheet.hazard} · {sheet.place} · {sheet.site.lat}°N{" "}
                {sheet.site.lon}°E
              </div>
              <Headline sheet={sheet} />
            </header>
            <figure>
              <figcaption>
                <b>평면</b> <span>어디가 얼마나 넓게</span>
              </figcaption>
              <PlanChart sheet={sheet} />
            </figure>
            <figure>
              <figcaption>
                <b>단면</b> <span>얼마나 깊게</span>
              </figcaption>
              <SectionChart sheet={sheet} />
            </figure>
          </div>
          <aside className="sheet-side">
            <Stamps stamps={sheet.stamps} />
            <p className="sheet-use">
              <b>쓸 수 있는 곳</b> {sheet.use}
            </p>
            <figure>
              <figcaption>
                <b>상세 B</b> <span>과장 없음 · 세로와 가로가 같은 축척</span>
              </figcaption>
              <DetailChart sheet={sheet} />
            </figure>
            <figure>
              <figcaption>
                <b>곡선</b> <span>얼마나 자주 · 단면과 같은 높이</span>
              </figcaption>
              <FrequencyChart sheet={sheet} />
            </figure>
            <Legend />
          </aside>
          <TitleBlock sheet={sheet} />
        </section>
      )}
    </div>
  );
}
