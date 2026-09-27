"""CLIMADA worker — runs in a separate conda env (heavy geospatial stack).

The orchestration backend invokes this package as a subprocess:

    conda run -n <env> python -m climaterisk_worker.run_job <run_dir>

It reads ``<run_dir>/request.json`` (shape: PhysicalRunRequest from
``climaterisk.engines.base``), runs CLIMADA, and writes ``<run_dir>/result.json``
(shape: PhysicalRunResult). It must NOT import the ``climaterisk`` backend package
(different environment) — the JSON contract is the only coupling.
"""

__version__ = "0.1.0"


def _bootstrap_climada() -> None:
    """Import CLIMADA once under the project-scoped ``climada.conf`` (when one is configured).

    CLIMADA (and climada_petals) read ``climada.conf`` from the working directory at import
    time and derive every data path from ``local_data.system``. When
    ``CLIMATERISK_CLIMADA_DATA_DIR`` is set, the config is generated under the data root and
    both packages are imported from that folder, then the working directory is restored. No
    effect when CLIMADA is not installed (backend environment), when no relocation is
    configured, or when CLIMADA was already imported (a warning is issued in that case).
    """
    import importlib.util
    import os
    import sys
    import warnings

    if importlib.util.find_spec("climada") is None:
        return
    from ._paths import paths

    conf_dir = paths.write_climada_conf()
    if conf_dir is None:
        return
    if "climada" in sys.modules:
        warnings.warn(
            "CLIMADA was imported before climaterisk_worker; CLIMATERISK_CLIMADA_DATA_DIR "
            "cannot take effect in this process. Import climaterisk_worker first.",
            RuntimeWarning,
            stacklevel=2,
        )
        return
    here = os.getcwd()
    os.chdir(conf_dir)
    try:
        import climada  # noqa: F401  (reads ./climada.conf)

        if importlib.util.find_spec("climada_petals") is not None:
            import climada_petals  # noqa: F401  (re-reads ./climada.conf)
    finally:
        os.chdir(here)


_bootstrap_climada()
