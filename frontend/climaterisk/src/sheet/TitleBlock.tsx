import type { Sheet } from "./types";

export function TitleBlock({ sheet }: { sheet: Sheet }) {
  return (
    <footer className="title-block">
      <div className="tb-sources">
        <b>자료</b> {sheet.sources.join(" · ")}
      </div>
      <div className="tb-cell">
        <span className="tb-key">도면명</span>
        <b>{sheet.title}</b>
      </div>
      <div className="tb-cell">
        <span className="tb-key">번호</span>
        <b className="sheet-mono">{sheet.sheet_id}</b>
      </div>
      <div className="tb-cell">
        <span className="tb-key">축척</span>
        <b>가로 눈금 1칸 = 100 m</b>
        <b>수직 과장 ×{sheet.section.vertical_exaggeration}</b>
      </div>
      <div className="tb-cell">
        <span className="tb-key">날짜</span>
        <b className="sheet-mono">{sheet.date}</b>
      </div>
    </footer>
  );
}
