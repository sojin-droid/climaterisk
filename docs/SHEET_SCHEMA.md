# 단면 도면 자료 형식 (`danmyeon.sheet/1`)

화면의 자산 도면(삼면도)은 이 JSON 한 개로 그린다. 예시는 `assets/libraries/sheets/DM-12.json`(성수동1가, 실제 값)이다. 디자인 원본은 캔버스 「단면」 2번 장이다.

## 필드

| 필드 | 형 | 뜻 |
|---|---|---|
| `schema` | `"danmyeon.sheet/1"` | 형식 이름 |
| `sheet_id` | 문자열, `^[A-Z]{2}-[0-9A-Za-z_-]{1,40}$` | 도면 번호 (예: `DM-12`) |
| `hazard` | `"river_flood"` | 지금은 하천 범람만 |
| `title`, `place`, `use`, `date` | 문자열 | 표제란, 쓸 수 있는 곳, 날짜 |
| `site` | 객체 | 자산 지점 (아래) |
| `section` | 객체 | 단면 (아래) |
| `frequency` | 객체 | 재현주기별 값 (아래) |
| `stamps` | 객체 | 결재란: `author`, `review`, `approve` 각각 `{state, label, ...}` |
| `sources` | 문자열 목록 | 자료 출처 |

### `site`

| 필드 | 뜻 |
|---|---|
| `lat`, `lon` | WGS84 |
| `km` | 단면 시작점부터 자산까지의 거리 (0 ≤ km ≤ `section.cut.length_km`) |
| `z_ground_m`, `z_surface_m` | 지면 높이와 표면 높이. 모르면 `null` (0이 아님) |
| `classes` | `{rp100, rp200, rp500}` → 공식 수심 등급 코드, `""` (그 지도의 모든 폴리곤 밖 = 0 m), 또는 `null` (그 빈도의 국가하천 지도 파일이 없거나 읽을 수 없음 = 값을 알 수 없음; 까닭은 `reasons`) |
| `reasons` | (선택) `{rp100, rp200, rp500}` → 등급이 `null`인 까닭: `"no_file_for_rp"` (그 빈도 지도 파일 없음), `"map_unreadable"` (파일은 있으나 읽을 수 없음), 등급을 알면 `null`. 2026-11-16 이전 도면에는 없다 |
| `bands_m` | 등급별 `[하한, 상한]`, 상한이 없으면 `null`. 등급이 `null`이면 띠 전체가 `null` (알 수 없음, 0 m 아님) |
| `depth_m` | 계산에 쓴 대표 깊이. 등급이 `null`이면 `null` (알 수 없음, 0 m 아님) |
| `check_depth_m` | 보조 점검(지형) 깊이. 없으면 `null` |
| `check_dem` | 보조 점검에 쓴 지형 자료 이름 |

### `section`

| 필드 | 뜻 |
|---|---|
| `cut` | `{lon0, lon1, lat, bearing_deg, length_km}`: 자른 선 |
| `km` | 표본 거리, 엄격히 증가 |
| `z_ground_m`, `z_surface_m` | 표본 높이, 같은 길이, `null` 허용 |
| `ground_source`, `surface_source` | 자료 이름 |
| `classes` | `{rp100, rp200, rp500}` → 표본마다 등급 코드, `""` (밖), 또는 `null` (그 빈도 지도 파일 없음 = 알 수 없음) |
| `vertical_exaggeration` | 화면 단면의 수직 과장 배율 |

### `frequency`

| 필드 | 뜻 |
|---|---|
| `return_periods` | `[100, 200, 500]` |
| `official` | `{tier: "T2", classes: [...]}` |
| `global` | `{tier: "T1", source, <기간>: {rp: [...], depth_m: [...]}}`. 빈 목록이면 그 기간에 침수 없음 |

### `stamps.*.state`

| 칸 | 허용 값 |
|---|---|
| `author` | `T1` \| `T2` \| `T3` |
| `review` | `agree` \| `partial` \| `disagree` \| `empty` (`partial`, `agree`, `disagree`에는 `agree`, `of` 정수) |
| `approve` | `approved` \| `empty` |

## 규칙

- 지도 도형(폴리곤 좌표)은 이 파일에 넣지 않는다. 등급 코드만 넣는다(공공누리 제4유형).
- 모르는 값은 `null`이다. 0으로 바꾸지 않는다.
- 등급 코드는 `N330`–`N334` 또는 `""`이다.
