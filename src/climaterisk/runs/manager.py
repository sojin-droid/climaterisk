"""Submit runs to the CLIMADA worker subprocess and poll for completion.

MVP mechanism (single-node, local-first): write ``request.json``, spawn the
climada-env python running ``climaterisk_worker.run_job <run_dir>`` non-blocking,
and on poll read ``result.json`` once the process exits. No broker required. Handles
both impact runs and cost-benefit runs (distinguished by the request ``mode``).
"""

from __future__ import annotations

import json
import os
import subprocess
import time
import uuid
from pathlib import Path

from climaterisk.config import Settings
from climaterisk.core.entities import Portfolio
from climaterisk.engines.base import (
    CalibrationRequest,
    CostBenefitRequest,
    ForecastRequest,
    HazardPreviewRequest,
    IngestRequest,
    LitPopRequest,
    MeasureSpec,
    PhysicalRunRequest,
    SupplyChainRequest,
    UncertaintyRequest,
)
from climaterisk.logger import get_logger
from climaterisk.runs.store import Run, RunStore

logger = get_logger(__name__)


class WorkerCapacityError(RuntimeError):
    """Raised when all worker slots (``max_workers``) are busy; surfaced as HTTP 503."""


class RunManager:
    """Orchestrate physical-risk and cost-benefit runs against the external CLIMADA worker."""

    def __init__(self, settings: Settings, store: RunStore) -> None:
        self._settings = settings
        self._store = store
        self._procs: dict[str, subprocess.Popen[bytes]] = {}
        self._started: dict[str, float] = {}  # run_id -> monotonic spawn time
        # On (re)start the previous process's worker subprocesses are gone, so any run
        # left "running" is orphaned — fail it now rather than poll it forever.
        stale = store.fail_stale_runs(
            "interrupted — the backend restarted while this run was in flight; please re-run."
        )
        if stale:
            logger.warning("marked %d orphaned run(s) as error on startup", stale)

    def _active_count(self) -> int:
        """Number of worker subprocesses still running (poll() is None)."""
        return sum(1 for p in self._procs.values() if p.poll() is None)

    def _spawn(self, run_id: str, run_dir: Path, request_json: str) -> None:
        """Write the request and spawn the worker subprocess (non-blocking).

        Raises:
            WorkerCapacityError: if ``max_workers`` runs are already in flight.
        """
        active = self._active_count()
        if active >= self._settings.max_workers:
            raise WorkerCapacityError(
                f"all {self._settings.max_workers} worker slots are busy "
                f"({active} running) — wait for a run to finish, then retry."
            )
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "request.json").write_text(request_json, encoding="utf-8")
        python = self._settings.resolve_worker_python()
        cmd = [python, "-m", self._settings.worker_module, str(run_dir)]
        # Inject the backend-resolved hazard-catalog dir so the worker resolves the
        # same single location (it never imports the backend config; GPL boundary).
        env = {
            **os.environ,
            "MPLBACKEND": "Agg",
            # Stream worker stdout/stderr unbuffered so worker.log shows live progress
            # during long multi-peril runs (otherwise it stays empty until the process exits).
            "PYTHONUNBUFFERED": "1",
            "CLIMATERISK_HAZARD_DB": str(self._settings.hazard_db_path),
        }
        if self._settings.dem_path:
            env["CLIMATERISK_DEM_PATH"] = str(self._settings.dem_path)
        if self._settings.emdat_path:
            env["CLIMATERISK_EMDAT_PATH"] = str(self._settings.emdat_path)
        logger.info("run %s: spawning worker (%s)", run_id, python)
        # The child inherits the log fd; close our copy after spawn so we don't leak
        # one descriptor per run on a long-lived server.
        with (run_dir / "worker.log").open("wb") as log:
            proc = subprocess.Popen(
                cmd,
                cwd=str(self._settings.worker_dir),
                stdout=log,
                stderr=subprocess.STDOUT,
                env=env,
            )
        self._procs[run_id] = proc
        self._started[run_id] = time.monotonic()
        self._store.update(run_id, "running")

    def submit(self, portfolio: Portfolio) -> Run:
        """Create a physical-risk run and spawn its worker."""
        run_id = uuid.uuid4().hex
        perils = [p.value for p in portfolio.run_config.perils]
        run = self._store.create(run_id, portfolio.id, portfolio.scenario.climate, perils)
        request = PhysicalRunRequest.from_portfolio(portfolio)
        self._spawn(run_id, self._settings.runs_path / run_id, request.model_dump_json(indent=2))
        run.status = "running"
        return run

    def submit_cost_benefit(self, portfolio: Portfolio, measures: list[MeasureSpec]) -> Run:
        """Create an adaptation cost-benefit run and spawn its worker."""
        run_id = uuid.uuid4().hex
        run = self._store.create(run_id, portfolio.id, portfolio.scenario.climate, ["cost_benefit"])
        request = CostBenefitRequest.from_portfolio(portfolio, measures)
        self._spawn(run_id, self._settings.runs_path / run_id, request.model_dump_json(indent=2))
        run.status = "running"
        return run

    def submit_uncertainty(self, portfolio: Portfolio, n_samples: int = 50) -> Run:
        """Create a Monte-Carlo uncertainty run and spawn its worker."""
        run_id = uuid.uuid4().hex
        run = self._store.create(run_id, portfolio.id, portfolio.scenario.climate, ["uncertainty"])
        request = UncertaintyRequest.from_portfolio(portfolio, n_samples)
        self._spawn(run_id, self._settings.runs_path / run_id, request.model_dump_json(indent=2))
        run.status = "running"
        return run

    def submit_litpop(
        self,
        portfolio: Portfolio,
        country: str,
        source: str = "litpop",
        peril: str = "tropical_cyclone",
    ) -> Run:
        """Create a modeled-exposure run (source × peril) and spawn its worker."""
        run_id = uuid.uuid4().hex
        run = self._store.create(run_id, portfolio.id, portfolio.scenario.climate, ["litpop"])
        request = LitPopRequest.from_portfolio(portfolio, country, source, peril)
        self._spawn(run_id, self._settings.runs_path / run_id, request.model_dump_json(indent=2))
        run.status = "running"
        return run

    def submit_hazard_preview(
        self,
        session_id: str,
        peril: str,
        scenario: str,
        region: str,
        year: int | None,
        bbox: list[float] | None = None,
    ) -> Run:
        """Render a catalog hazard's intensity field to a map raster (PNG in the run dir)."""
        run_id = uuid.uuid4().hex
        run = self._store.create(run_id, session_id, scenario, ["hazard_preview"])
        request = HazardPreviewRequest(
            session_id=session_id,
            peril=peril,
            scenario=scenario,
            region=region,
            year=year,
            bbox=bbox,
        )
        self._spawn(run_id, self._settings.runs_path / run_id, request.model_dump_json(indent=2))
        run.status = "running"
        return run

    def submit_ingest(
        self, portfolio: Portfolio, source: str, peril: str, scenario: str, year: int
    ) -> Run:
        """Create a data-ingest run (download + refine a source into the local catalog)."""
        run_id = uuid.uuid4().hex
        run = self._store.create(run_id, portfolio.id, scenario, [f"ingest:{source}"])
        request = IngestRequest.from_portfolio(portfolio, source, peril, scenario, year)
        self._spawn(run_id, self._settings.runs_path / run_id, request.model_dump_json(indent=2))
        run.status = "running"
        return run

    def submit_supplychain(
        self, portfolio: Portfolio, mriot_type: str = "WIOD16", mriot_year: int = 2010
    ) -> Run:
        """Create a supply-chain indirect-impact run and spawn its worker."""
        run_id = uuid.uuid4().hex
        run = self._store.create(run_id, portfolio.id, portfolio.scenario.climate, ["supplychain"])
        request = SupplyChainRequest.from_portfolio(portfolio, mriot_type, mriot_year)
        self._spawn(run_id, self._settings.runs_path / run_id, request.model_dump_json(indent=2))
        run.status = "running"
        return run

    def submit_calibration(self, portfolio: Portfolio, observed_source: str = "emdat") -> Run:
        """Create an impact-function calibration run and spawn its worker.

        ``observed_source`` selects the observed series (``base.OBSERVED_SOURCES``); the default
        keeps existing callers on the EM-DAT path.
        """
        run_id = uuid.uuid4().hex
        run = self._store.create(run_id, portfolio.id, portfolio.scenario.climate, ["calibration"])
        request = CalibrationRequest.from_portfolio(portfolio, observed_source)
        self._spawn(run_id, self._settings.runs_path / run_id, request.model_dump_json(indent=2))
        run.status = "running"
        return run

    def submit_forecast(self, portfolio: Portfolio) -> Run:
        """Create an operational TC-forecast run and spawn its worker."""
        run_id = uuid.uuid4().hex
        run = self._store.create(run_id, portfolio.id, portfolio.scenario.climate, ["forecast"])
        request = ForecastRequest.from_portfolio(portfolio)
        self._spawn(run_id, self._settings.runs_path / run_id, request.model_dump_json(indent=2))
        run.status = "running"
        return run

    def latest_runs(self, session_id: str) -> dict[str, Run]:
        """Most recent finished run of each kind for a session (see ``RunStore``)."""
        return self._store.latest_by_kind(session_id)

    def poll(self, run_id: str) -> Run | None:
        """Return the current run, finalizing it if its worker has exited."""
        run = self._store.get(run_id)
        if run is None:
            return None
        if run.status in ("done", "error"):
            return run

        proc = self._procs.get(run_id)
        run_dir = self._settings.runs_path / run_id
        result_path = run_dir / "result.json"

        if proc is None:  # backend restarted: fall back to the result file
            return self._finalize(run_id, run_dir) if result_path.is_file() else run
        if proc.poll() is None:
            # Kill runs that exceed the wall-clock cap (intractable jobs).
            started = self._started.get(run_id)
            elapsed = (time.monotonic() - started) if started is not None else 0.0
            if elapsed > self._settings.max_run_seconds:
                proc.kill()
                self._procs.pop(run_id, None)
                self._started.pop(run_id, None)
                self._store.update(
                    run_id,
                    "error",
                    detail=f"run exceeded the {self._settings.max_run_seconds}s time limit "
                    f"and was stopped. {self._log_tail(run_dir)}",
                )
                return self._store.get(run_id)
            return run  # still running
        self._procs.pop(run_id, None)
        self._started.pop(run_id, None)
        return self._finalize(run_id, run_dir)

    def _finalize(self, run_id: str, run_dir: Path) -> Run | None:
        """Read the worker's result.json (any output shape) and store it."""
        result_path = run_dir / "result.json"
        if not result_path.is_file():
            self._store.update(run_id, "error", detail=f"no result.json. {self._log_tail(run_dir)}")
            return self._store.get(run_id)
        try:
            output = json.loads(result_path.read_text(encoding="utf-8"))
        except Exception as exc:
            self._store.update(run_id, "error", detail=f"unreadable result.json: {exc}")
            return self._store.get(run_id)
        self._store.update(run_id, "done", output=output, detail=output.get("detail"))
        logger.info("run %s: done status=%s", run_id, output.get("status"))
        return self._store.get(run_id)

    @staticmethod
    def _log_tail(run_dir: Path, n: int = 800) -> str:
        log = run_dir / "worker.log"
        if not log.is_file():
            return ""
        return f"worker.log tail: …{log.read_text(encoding='utf-8', errors='replace')[-n:]}"
