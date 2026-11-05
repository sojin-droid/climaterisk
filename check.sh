#!/usr/bin/env bash
# Every check AGENTS.md requires before a commit. Exit non-zero on the first failure.
set -euo pipefail
cd "$(dirname "$0")"
PY=/Users/junsojin/Developer/climaterisk/.climada-env/bin/python

# Gate tests are written by the reviewer; an agent may not edit them (AGENTS.md).
protected=$(git diff --cached --name-only -- $(cat .protected-tests) || true)
if [ -n "${protected}" ] && [ "${ALLOW_GATE_EDIT:-0}" != "1" ]; then
  echo "check.sh: protected gate tests changed: ${protected}" >&2
  exit 1
fi

uv run ruff check .
uv run ruff format --check .
uv run mypy src/
uv run pytest -q
"${PY}" -m pytest -q tests/test_synthetic_city.py tests/test_hydraulics.py tests/test_inundation.py tests/test_typhoon.py tests/test_heat.py tests/test_coastal.py tests/test_surge2d.py tests/test_viewer_time.py tests/test_site.py tests/test_pluvial.py
"${PY}" scripts/synthetic_city.py --out /tmp/twin_check >/dev/null
echo "check.sh: all checks passed"
