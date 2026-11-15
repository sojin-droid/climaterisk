import type { Sheet, SheetStamp } from "./types";
import { INK, MUTED } from "./scales";

function Stamp({ kind, stamp }: { kind: "author" | "review" | "approve"; stamp: SheetStamp }) {
  let text = "";
  let filled = false;
  let dashed = false;
  let caption = stamp.label;
  if (kind === "author") {
    text = stamp.state;
    filled = true;
  } else if (kind === "review") {
    if (stamp.state === "partial" || stamp.state === "agree") text = `${stamp.agree ?? 0}/${stamp.of ?? 0}`;
    else if (stamp.state === "disagree") text = "주의";
    else dashed = true;
  } else if (stamp.state === "approved") {
    text = "승인";
    filled = true;
  } else {
    dashed = true;
    caption = "빈칸";
  }
  return (
    <div className="stamp-cell">
      <svg width="56" height="56" viewBox="0 0 56 56" aria-hidden="true">
        <circle
          cx="28"
          cy="28"
          r="25"
          fill={filled ? INK : "none"}
          stroke={dashed ? MUTED : INK}
          strokeWidth={dashed ? 1 : 2}
          strokeDasharray={dashed ? "3 3" : undefined}
        />
        {text && (
          <text x="28" y="33" textAnchor="middle" fontSize="15" fontWeight="700" className="sheet-mono" fill={filled ? "#FAFAF8" : INK}>
            {text}
          </text>
        )}
      </svg>
      <span className="stamp-label">{caption}</span>
      {kind === "approve" && stamp.state !== "approved" && <span className="stamp-label">{stamp.label}</span>}
    </div>
  );
}

export function Stamps({ stamps }: { stamps: Sheet["stamps"] }) {
  return (
    <div role="group" aria-label="결재란" className="stamps">
      <div className="stamp-col">
        <div className="stamp-head">작성 · 주 방법</div>
        <Stamp kind="author" stamp={stamps.author} />
      </div>
      <div className="stamp-col">
        <div className="stamp-head">검토 · 보조 점검</div>
        <Stamp kind="review" stamp={stamps.review} />
      </div>
      <div className="stamp-col">
        <div className="stamp-head">승인 · 실측 검증</div>
        <Stamp kind="approve" stamp={stamps.approve} />
      </div>
    </div>
  );
}
