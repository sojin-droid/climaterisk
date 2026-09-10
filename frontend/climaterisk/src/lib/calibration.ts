/** How to present a finished calibration run — pure, so the view has no branching logic.
 *
 *  The worker can finish a run *successfully* and still refuse to calibrate: the comparability
 *  gate blocks a fit when the observed and modelled quantities are not comparable (unit, sub-peril
 *  coverage or spatial scope — worker `calibration.calibration_gate`). That refusal arrives as
 *  `output.status === "error"` on a run whose own status is `"done"`, which the view used to drop
 *  on the floor: it rendered only `status === "ok"`, so a blocked calibration looked like nothing
 *  had happened. A silent block is as misleading as a silent pass, hence this classification.
 *
 *  Backend contract unchanged — this only reads what the worker already returns.
 */
import type { CalibrationResult } from "../types";

export type CalibrationOutcomeKind = "ok" | "blocked" | "error";

export interface CalibrationOutcome {
  kind: CalibrationOutcomeKind;
  /** Short label for the badge ("Calibrated" / "Blocked" / "Failed"). */
  label: string;
  /** The worker's reason. Never empty — a generic sentence stands in when `detail` is absent. */
  detail: string;
  /** Gate blockers, one per failed check, when the worker listed them. */
  blockers: string[];
  /** `comparable` / `not_comparable` / `unknown` when the worker judged it. */
  comparisonStatus: string | null;
}

const GENERIC_ERROR =
  "The worker returned an error without a reason. Check the run log for this run id.";

/** True when a successful fit was made on a pair the gate judged not comparable. */
export function isIncomparableFit(cal: CalibrationResult): boolean {
  return cal.status === "ok" && cal.comparison_status === "not_comparable";
}

export function calibrationOutcome(cal: CalibrationResult): CalibrationOutcome {
  const blockers = cal.blockers ?? [];
  const comparisonStatus = cal.comparison_status ?? null;
  if (cal.status === "ok") {
    return {
      kind: "ok",
      label: "Calibrated",
      detail: cal.detail ?? "",
      blockers,
      comparisonStatus,
    };
  }
  // A gate refusal is distinguishable from a plain failure: it lists blockers, or it says which
  // comparability verdict stopped it. Both are worth telling apart in the UI — one is a data or
  // declaration problem the user can fix, the other is a broken run.
  const blocked = blockers.length > 0 || comparisonStatus !== null;
  return {
    kind: blocked ? "blocked" : "error",
    label: blocked ? "Not calibrated — blocked" : "Calibration failed",
    detail: cal.detail?.trim() ? cal.detail : GENERIC_ERROR,
    blockers,
    comparisonStatus,
  };
}
