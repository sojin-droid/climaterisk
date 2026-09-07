# CLAUDE.md

Scientific modelling project (data science / energy / finance / economic).
Docs: `docs/ARCHITECTURE.md` (structure), `docs/METHODOLOGY.md` + `docs/CLIMADA_METHODS.md` (methods, native-vs-custom audit), `docs/CLIMADA_COVERAGE.md`, `docs/GAP_ANALYSIS_KO.md`, `docs/RISK_REGISTER.md`, `docs/USER_GUIDE.md`.

## Commands

```bash
uv sync --all-extras           # install
uv run pytest                  # tests
uv run pytest --cov=src        # tests + coverage
uv run ruff check . --fix      # lint + autofix
uv run ruff format .           # format
uv run mypy src/               # type-check
```

If `uv` is not yet adopted, fall back to `pip install -e ".[dev]"` and `pytest` / `ruff` / `mypy` directly. Don't introduce `setup.py`, `requirements.txt`, `flake8`, or `black` configs — `pyproject.toml` is the single source of truth.

## Conventions

- **Python 3.11+**, type hints mandatory on public functions, Google-style docstrings.
- **Math docstrings**: include an `Algorithm:` section with LaTeX (`$$...$$`) primary and an ASCII fallback line. Define every symbol with units.
- **Variable names**: descriptive in general (`temperature_kelvin`), but **single letters are OK** when they mirror equations (`T`, `x`, `ε`, `dt`, `i`, `j`). Don't fight the math.
- **No hardcoded values**: load via `src/<pkg>/config.py` from `.env`. Mirror every var into `.env.example`.
- **Reproducibility**: pin random seeds (`numpy.random.default_rng(seed)` over the legacy global API). Commit `uv.lock`. Pin upstream versions.
- **Units**: use `pint` for any quantity with physical units (energy, power, currency rates, time-of-day). Don't pass bare floats across module boundaries when units matter.
- **Numerical correctness**: when changing math, add a test against an analytical solution OR a captured baseline (`np.testing.assert_allclose` with explicit `rtol`/`atol`).

## Logging

Use `src/<pkg>/logger.py`. Log shape and dtype, never full arrays. Never log secrets, PII, or raw data rows.

| level | use for |
|-------|---------|
| DEBUG | branch decisions, scalar values, shapes |
| INFO  | milestones (data loaded, fit complete) |
| WARNING | recoverable degradation |
| ERROR | failure that returns or skips |
| CRITICAL | abort |

## Tests

Pytest. New features need tests. Aim for **meaningful** coverage of math correctness, not a line-coverage %. For numerical code, regression tests against analytical solutions beat 100% line coverage every time.

## Git workflow

- Feature branch → PR → CI green → merge to main → delete branch.
- **Conventional commits**: `feat:`, `fix:`, `refactor:`, `docs:`, `test:`, `chore:`.
- **Self-review before merge**: re-read the diff. For math changes, paste before/after equations into the PR description. CI passing ≠ math correct.
- Never `--force` push to main. Use `git revert` to undo merged commits.

## Project layout

```
src/<pkg>/
  core/       algorithms (no I/O)
  data/       loaders, validators, transforms
  config.py   loads .env, validates types
  logger.py   centralized logging
tests/        mirrors src/
docs/         ARCHITECTURE.md, METHODOLOGY.md, CLIMADA_METHODS.md, CLIMADA_COVERAGE.md, GAP_ANALYSIS_KO.md, RISK_REGISTER.md, USER_GUIDE.md, API.md
.env, .env.example
pyproject.toml    single source of truth
```

There is no `docs/HANDBOOK.md`; directory layout is in `docs/ARCHITECTURE.md`, and the per-peril method record in `docs/METHODOLOGY.md` / `docs/CLIMADA_METHODS.md`.
