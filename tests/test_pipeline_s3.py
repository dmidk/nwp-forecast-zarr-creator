"""S3 end-to-end test against the frozen public fixture bucket.

Runs the full pipeline (index -> refs -> zarr) for the fixture's trimmed
analysis time. Only ``FIXTURE_SRC_URI`` is configured; analysis time,
max hour, and suite are read from the fixture's ``manifest.json`` (so they
cannot disagree with the data)::

    FIXTURE_SRC_URI=s3://<bucket>/<suite>/<analysis>/ml \\
        uv run pytest -m integration

Skips when ``FIXTURE_SRC_URI`` is unset. Reads are unsigned (``SRC_ANON=1``);
output goes to a local temp dir.
"""

import json
import os

import isodate
import pytest
import xarray as xr

pytestmark = pytest.mark.integration


def _manifest(src_uri):
    """Load the fixture manifest (sibling of the ``.../ml`` URI)."""
    from zarr_creator import storage

    anon = os.environ.get("SRC_ANON", "").lower() in {"1", "true", "yes"}
    prefix = src_uri.rstrip("/").rsplit("/", 1)[0]
    manifest_url = f"{prefix}/manifest.json"
    fs, path = storage.resolve_fs(manifest_url, anon=anon)
    try:
        with fs.open(path) as f:
            return json.load(f)
    except FileNotFoundError:
        pytest.skip(f"Could not read {manifest_url} — check FIXTURE_SRC_URI")
    except OSError as exc:
        # s3fs translates S3 auth failures (e.g. 403 on unsigned reads of a
        # private bucket) to PermissionError: fail with remediation guidance
        # rather than skipping, since the coordinates are likely wrong auth.
        hint = storage.auth_error_hint(exc, anon=anon)
        if hint is not None:
            raise RuntimeError(hint) from exc
        raise


def _config():
    """Return fixture coordinates from the manifest, or skip."""
    src_uri = os.environ.get("FIXTURE_SRC_URI")
    if not src_uri:
        pytest.skip("FIXTURE_SRC_URI not set — skipping S3 e2e test")
    manifest = _manifest(src_uri)
    return (
        src_uri,
        isodate.parse_datetime(manifest["analysis_time"]),
        int(manifest["max_hour"]),
        manifest.get("suite_name", "dini"),
    )


def test_manifest_auth_error_fails_with_hint(monkeypatch):
    """Regression test: unsigned reads of a private bucket must explain
    themselves (raw PermissionError from s3fs is not actionable)."""
    from tests.test_pipeline_s3 import _manifest

    class Fake403FS:
        def open(self, path):
            raise PermissionError("Forbidden")

    monkeypatch.setattr(
        "zarr_creator.storage.resolve_fs",
        lambda url, profile=None, anon=False: (Fake403FS(), "manifest.json"),
    )
    monkeypatch.setenv("SRC_ANON", "1")
    with pytest.raises(RuntimeError, match="SRC_ANON"):
        _manifest("s3://bucket/dini/2025-03-02T0600Z/ml")


def test_s3_fixture_end_to_end(tmp_path):
    from zarr_creator.pipeline.index_refs import build_indexes_and_refs
    from zarr_creator.settings import Settings, refs_dir_for, require_utc

    src_uri, t_analysis, max_hour, suite_name = _config()
    t_analysis = require_utc(t_analysis)

    settings = Settings(
        src_grib_root_uri=src_uri,
        refs_root_path=str(tmp_path / "refs"),
        member_id="CONTROL__dmi",
        max_hour=max_hour,
        suite_name=suite_name,
        # Honor a pre-warmed staging dir (e.g. the CI cache mount);
        # otherwise stage in a fresh temp dir.
        src_grib_temp_path=os.environ.get(
            "SRC_GRIB_TEMP_PATH", str(tmp_path / "stage")
        ),
        dst_zarr_output_path=f"file://{tmp_path}/out/{{dataset_id}}.zarr",
        src_aws_profile=os.environ.get(
            "SRC_AWS_PROFILE", os.environ.get("AWS_PROFILE")
        ),
        dst_aws_profile=None,
        src_anon=os.environ.get("SRC_ANON", "").lower() in {"1", "true", "yes"},
    )
    refs_dir = build_indexes_and_refs(t_analysis, settings)
    assert refs_dir == refs_dir_for(t_analysis, settings)
    assert os.path.isdir(refs_dir)
    assert [f for f in os.listdir(refs_dir) if f.endswith(".json")] != []

    from zarr_creator.__main__ import cli as convert

    convert(
        [
            "--t_analysis",
            t_analysis.isoformat(),
            "--suite-name",
            suite_name,
            "--refs-root-path",
            str(tmp_path / "refs"),
            "--dst-zarr-output-path",
            f"file://{tmp_path}/out/{{dataset_id}}.zarr",
        ]
    )
    for part in ("single_levels", "pressure_levels", "height_levels"):
        ds = xr.open_zarr(f"{tmp_path}/out/{part}.zarr")
        assert len(ds.data_vars) > 0
        assert ds.sizes["time"] == max_hour + 1
        assert not all(bool(ds[v].isnull().all()) for v in ds.data_vars)
