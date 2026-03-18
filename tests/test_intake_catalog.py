import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from zarr_creator import open_intake_catalog


def _catalog_path() -> Path:
    return (
        Path(__file__).resolve().parents[1] / "zarr_creator" / "catalog" / "catalog.yml"
    )


def _latest_available_analysis_time(now: datetime | None = None) -> datetime:
    current_utc = now or datetime.now(timezone.utc)
    lagged = current_utc - timedelta(hours=6)
    rounded_hour = (lagged.hour // 3) * 3
    return lagged.replace(hour=rounded_hour, minute=0, second=0, microsecond=0)


def test_intake_catalog_accepts_datetime_analysis_time():
    pytest.importorskip("intake")
    pytest.importorskip("intake_xarray")

    catalog = open_intake_catalog(_catalog_path())
    analysis_time = _latest_available_analysis_time()
    source_template = catalog["height_levels"]
    source = source_template._entry(analysis_time=analysis_time)

    reader_kwargs = getattr(getattr(source, "reader", None), "kwargs", {}) or {}
    reader_args = reader_kwargs.get("args")
    urlpath = None
    if isinstance(reader_args, tuple):
        if len(reader_args) >= 1:
            urlpath = getattr(reader_args[0], "url", None)

    expected_time = analysis_time.strftime("%Y-%m-%dT%H%M%SZ")

    assert isinstance(urlpath, str)
    assert expected_time in urlpath
    assert urlpath.endswith("/height_levels.zarr/")


@pytest.mark.integration
def test_intake_catalog_to_dask_reads_remote_dataset():
    if os.getenv("RUN_INTAKE_S3_INTEGRATION", "").lower() not in {"1", "true", "yes"}:
        pytest.skip(
            "Set RUN_INTAKE_S3_INTEGRATION=1 to run S3-backed intake integration test"
        )

    pytest.importorskip("intake")
    pytest.importorskip("intake_xarray")

    catalog = open_intake_catalog(_catalog_path())
    analysis_time = _latest_available_analysis_time()
    source_template = catalog["height_levels"]
    source = source_template._entry(analysis_time=analysis_time)

    ds = source.to_dask()
    assert ds is not None
    assert len(ds.data_vars) > 0
