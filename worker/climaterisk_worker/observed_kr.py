"""행정안전부 재해연보 — national observed annual losses (data.go.kr 15107318). Spec F1.

The Korean counterpart of the EM-DAT reader: turns the 통계연보 연도별 자연재난 피해 API into
a :class:`~climaterisk_worker.validation.ObservedSeries`.

**This module is a supply layer and nothing else.** It fetches, validates and declares. Fitting
``v_half``, running ``ImpactCalc``, computing a capture ratio and deciding whether a comparison
is legitimate all live downstream — ``calibration.py`` and ``validation.py`` — so that loading
observed data can never by itself authorise a calibration. F1 provides the observed Korean
annual loss series; the TC capture diagnostic is a separate downstream analysis.

Endpoint and schema (spec ``docs/OBSERVED_LOSSES_KR_SPEC.md`` §4, §5-A, §5-B)
-----------------------------------------------------------------------------
``GET`` :data:`ENDPOINT` with ``ServiceKey``, ``pageNo``, ``numOfRows`` — and ``type=xml``,
which the published spec does not mention: without it the gateway answers ``HTTP_ERROR``
(returnReasonCode 04) rather than data. There is no year filter; the whole table is paged.

Verified against the live API on 2026-09-11 with a real service key:

* ``totalCount`` = 8 — the series is **2016–2023 only**, not the long record the spec assumed.
* Every row carries ``seq`` = 1 and there is exactly one row per ``wrttimeid``. The spec's open
  question (§12) of whether ``seq`` separates money rows from casualty rows is settled for this
  dataset: it does not, because the source table (행정안전 통계연보 7-3-2-2) has property damage
  as its only measure. :func:`parse_rows` still refuses a year that arrives with two rows rather
  than guessing which one to take.
* Values are 백만원, confirmed live: 2020 = 1,318,177 = 1조 3,182억 원, the published figure.
  Price basis is 당해연도 (nominal) — §5-A 확정 3.
* ``wrttimeid`` maxes at 2023, so this is **not** the 2026 통계연보 vintage whose 2024 row is
  recorded in 천원 (a 1000× error, §5-B-1). :func:`scale_outliers` guards against that vintage
  appearing later: the loader refuses such a year instead of rescaling it.

Honest caveats carried into the series
--------------------------------------
* **``tot`` is not the sum of the cause columns.** The API exposes 10 of the source table's 13
  causes — 우박, 폭풍·해일, 냉해·동해 are absent — so in 2023 ``tot`` exceeds the column sum by
  11.8 %. This loader reads a **named cause column**, never ``tot``, and never mixes the two.
* **``typhoon_heavy_rain``** is the source's "could not separate typhoon from rain" column. In a
  year where it is non-zero (2018: 6,416 against typhoon 64,200) the typhoon figure is a lower
  bound. Those years are reported in :attr:`ObservedSeries.notes` rather than silently summed.
* A typhoon loss statistic books **wind + surge + rain together** while the platform's TC runner
  models wind only (§6-1). The series therefore declares
  :data:`~climaterisk_worker.validation.TC_AGGREGATE_SUBPERILS`, which is exactly what makes the
  calibration gate refuse a fit — by design, not by accident.
"""

from __future__ import annotations

import os
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections.abc import Callable
from statistics import median
from typing import Any

from climaterisk_worker.validation import TC_AGGREGATE_SUBPERILS, ObservedSeries

#: data.go.kr operation. The origin is ``data.mois.go.kr/openapi/NaturalDisasterDamageByYear``.
ENDPOINT = (
    "https://apis.data.go.kr/1741000/NaturalDisasterDamageByYear/getNaturalDisasterDamageByYear"
)

#: Environment variable holding the data.go.kr service key (the **Decoding** form — the
#: request encodes it once, so an already-escaped key double-encodes and fails auth).
KEY_ENV = "CLIMATERISK_DATAGOKR_KEY"

DATASET_URL = "https://www.data.go.kr/data/15107318/openapi.do"

#: Unit, currency and price basis established from the source table, not assumed (§5-A).
UNIT = "KRW million"
CURRENCY = "KRW"
PRICE_BASIS = "nominal"

#: The number is a national total; declaring it is what lets the gate catch a scope mismatch.
SCOPE = "national:KOR"

#: Cause columns the API publishes, by name. Position-based parsing is banned (§5-B-2).
CAUSE_COLUMNS: tuple[str, ...] = (
    "typhoon",
    "heavy_rain",
    "heavy_snow",
    "heavy_wind",
    "wind_wave_strong_wind",
    "typhoon_heavy_rain",
    "lightning",
    "cold_wave",
    "earthquak",  # source spelling — kept verbatim
    "heatwave",
)

#: Platform peril → the cause column that measures it.
PERIL_CAUSE: dict[str, str] = {"tropical_cyclone": "typhoon"}

#: Cause column → the column holding losses the source could not separate from it.
UNSEPARATED_FROM: dict[str, str] = {"typhoon": "typhoon_heavy_rain"}

#: A row whose ``tot`` departs from the series median by at least this factor is treated as
#: carrying a different unit (the 천원 defect of §5-B-1), not as a real extreme.
_SCALE_ALARM = 100.0


class YearbookUnavailable(Exception):
    """Raised when the series cannot be loaded; carries what the caller should do."""


def _require_key() -> str:
    key = os.environ.get(KEY_ENV, "").strip()
    if not key:
        raise YearbookUnavailable(
            f"재해연보 needs a data.go.kr service key in {KEY_ENV}. Apply at {DATASET_URL} "
            "(활용신청) and put the Decoding key in .env."
        )
    return key


def fetch_raw(page: int = 1, rows: int = 100, key: str | None = None, timeout: int = 60) -> bytes:
    """One raw XML page from the API.

    ``type=xml`` is required — the published parameter list omits it and the gateway answers
    ``HTTP_ERROR`` without it.
    """
    query = urllib.parse.urlencode(
        {
            "ServiceKey": key or _require_key(),
            "pageNo": str(int(page)),
            "numOfRows": str(int(rows)),
            "type": "xml",
        }
    )
    with urllib.request.urlopen(f"{ENDPOINT}?{query}", timeout=timeout) as response:
        return bytes(response.read())


def parse_rows(xml: bytes | str) -> list[dict[str, Any]]:
    """Parse one XML page into ``{wrttimeid, seq, tot, <cause>: int}`` rows.

    Raises:
        YearbookUnavailable: on a gateway error envelope, an unparseable body, a year that
            arrives more than once, or a cause value that is not an integer.
    """
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        raise YearbookUnavailable(f"재해연보 response is not XML: {exc}") from exc
    if root.tag != "NaturalDisasterDamageByYear":
        message = root.findtext(".//errMsg") or root.findtext(".//returnAuthMsg") or root.tag
        raise YearbookUnavailable(f"재해연보 API returned an error envelope: {message}")

    code = root.findtext("head/RESULT/resultCode") or ""
    if code and not code.startswith("INFO-0"):
        raise YearbookUnavailable(
            f"재해연보 API resultCode {code}: {root.findtext('head/RESULT/resultMsg') or ''}"
        )

    out: list[dict[str, Any]] = []
    seen: dict[int, str] = {}
    for row in root.findall("row"):
        year_text = (row.findtext("wrttimeid") or "").strip()
        if not year_text.isdigit():
            raise YearbookUnavailable(f"재해연보 row has no usable wrttimeid: {year_text!r}")
        year = int(year_text)
        seq = (row.findtext("seq") or "").strip()
        if year in seen:
            raise YearbookUnavailable(
                f"재해연보 returned two rows for {year} (seq {seen[year]!r} and {seq!r}); the "
                "source table has one property-damage row per year, so the extra row's meaning "
                "is undocumented — refusing rather than picking one (spec §4)"
            )
        seen[year] = seq
        record: dict[str, Any] = {"wrttimeid": year, "seq": seq}
        for column in ("tot", *CAUSE_COLUMNS):
            raw = (row.findtext(column) or "0").strip().replace(",", "") or "0"
            try:
                record[column] = int(raw)
            except ValueError as exc:
                raise YearbookUnavailable(
                    f"재해연보 {year} column {column!r} is not an integer: {raw!r}"
                ) from exc
        out.append(record)
    return out


def fetch_rows(key: str | None = None, fetch: Callable[..., bytes] | None = None) -> list[dict]:  # type: ignore[type-arg]
    """Every row of the table, paged until ``totalCount`` is covered.

    Args:
        key: Service key; read from the environment when omitted.
        fetch: Injection seam for tests — same signature as :func:`fetch_raw`.
    """
    get = fetch or fetch_raw
    first = get(page=1, rows=100, key=key)
    rows = parse_rows(first)
    try:
        total = int(ET.fromstring(first).findtext("head/totalCount") or len(rows))
    except (ET.ParseError, ValueError):  # pragma: no cover - parse_rows already validated
        total = len(rows)
    page = 2
    while len(rows) < total and page < 100:
        more = parse_rows(get(page=page, rows=100, key=key))
        if not more:
            break
        rows.extend(more)
        page += 1
    return sorted(rows, key=lambda r: r["wrttimeid"])


def scale_outliers(rows: list[dict[str, Any]]) -> list[int]:
    """Years whose ``tot`` is off the series scale by :data:`_SCALE_ALARM` or more.

    The 2026 통계연보 vintage publishes its newest year in 천원 under a 백만원 header — a silent
    1000× error that would inflate that year's typhoon loss a thousandfold (§5-B-1). The API
    vintage seen on 2026-09-11 stops at 2023 and is clean, but a later refresh may not be, so
    the check runs on every load and the offending year is dropped with a stated reason rather
    than rescaled: the correct multiplier is a guess until it is checked against 재해연보.
    """
    totals = [int(r["tot"]) for r in rows if int(r["tot"]) > 0]
    if len(totals) < 3:
        return []
    mid = median(totals)
    if mid <= 0:  # pragma: no cover - defensive
        return []
    return sorted(
        int(r["wrttimeid"])
        for r in rows
        if int(r["tot"]) > 0 and int(r["tot"]) / mid >= _SCALE_ALARM
    )


def load_year_series(
    peril: str = "tropical_cyclone",
    key: str | None = None,
    fetch: Callable[..., bytes] | None = None,
) -> ObservedSeries:
    """The national annual loss series for one platform peril.

    Reads the **named cause column** for the peril — never ``tot``, which includes three causes
    the API does not publish (11.8 % of the 2023 total).

    Args:
        peril: Platform peril key; only those in :data:`PERIL_CAUSE` have a cause column.
        key: data.go.kr service key; read from :data:`KEY_ENV` when omitted.
        fetch: Injection seam for tests.

    Returns:
        An :class:`ObservedSeries` declaring unit, currency, price basis, spatial scope and
        sub-peril coverage, so ``comparability_report`` can judge it.

    Raises:
        YearbookUnavailable: no key, no cause column for the peril, or a malformed response.
    """
    column = PERIL_CAUSE.get(peril)
    if column is None:
        raise YearbookUnavailable(
            f"재해연보 has no cause column for peril {peril!r} (have: {sorted(PERIL_CAUSE)})"
        )
    rows = fetch_rows(key=key, fetch=fetch)
    if not rows:
        raise YearbookUnavailable("재해연보 returned no rows")

    dropped = scale_outliers(rows)
    kept = [r for r in rows if int(r["wrttimeid"]) not in dropped]
    if not kept:
        raise YearbookUnavailable(
            f"every 재해연보 year failed the unit-scale check ({dropped}) — the vintage is "
            "probably the one that publishes 천원 under a 백만원 header (spec §5-B-1)"
        )

    losses = {int(r["wrttimeid"]): float(r[column]) for r in kept}
    unseparated = UNSEPARATED_FROM.get(column)
    ambiguous = (
        [int(r["wrttimeid"]) for r in kept if int(r.get(unseparated, 0)) > 0] if unseparated else []
    )
    zero_years = sorted(y for y, v in losses.items() if v == 0.0)

    notes = [
        f"{DATASET_URL}; 행정안전 통계연보 7-3-2-2 재산피해; column {column!r} read by name, "
        "not 'tot' (the API omits 우박·폭풍해일·냉해동해, 11.8% of the 2023 total)",
        f"{len(losses)} years {min(losses)}-{max(losses)}; "
        f"{len(losses) - len(zero_years)} with a non-zero loss",
    ]
    if ambiguous:
        notes.append(
            f"years {ambiguous} also carry {unseparated!r} (losses the source could not split "
            f"off this cause), so those {column} figures are a lower bound — not summed here"
        )
    if zero_years:
        notes.append(f"years {zero_years} report zero for this cause")
    if dropped:
        notes.append(
            f"years {dropped} dropped: 'tot' is >={_SCALE_ALARM:g}x the series median, the "
            "signature of the 천원-under-백만원-header defect (spec §5-B-1); not rescaled"
        )

    return ObservedSeries(
        source=(
            f"행정안전부 재해연보 API (data.go.kr 15107318), {column}, 전국 합계, "
            f"{min(losses)}-{max(losses)}"
        ),
        unit=UNIT,
        losses=losses,
        peril=peril,
        notes=" | ".join(notes),
        currency=CURRENCY,
        covers_subperils=TC_AGGREGATE_SUBPERILS if peril == "tropical_cyclone" else (),
        scope=SCOPE,
        price_basis=PRICE_BASIS,
    )
