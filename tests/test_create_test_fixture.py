"""Tests for zarr_creator.create_test_fixture (memory:// filesystems, no network)."""

import datetime
import json
import os

import pytest

from zarr_creator import create_test_fixture as cf
from zarr_creator import storage


def _utc(*args):
    return datetime.datetime(*args, tzinfo=datetime.timezone.utc)


def _plant_source(uri, t, max_hour=1, member="CONTROL__dmi"):
    from zarr_creator.settings import expected_grib_filenames

    fs, _ = storage.resolve_fs(uri)
    for name in expected_grib_filenames(t, max_hour, member):
        fs.pipe(f"{uri.rstrip('/')}/{name}", b"fake-grib:" + name.encode())


def test_dest_prefix_includes_suite_and_analysis_time():
    assert (
        cf.dest_prefix("my-bucket", "dini", _utc(2025, 3, 2, 6))
        == "s3://my-bucket/dini/2025-03-02T0600Z"
    )
    assert (
        cf.dest_prefix("my-bucket", "ig", _utc(2025, 3, 2, 6))
        == "s3://my-bucket/ig/2025-03-02T0600Z"
    )
    assert (
        cf.dest_prefix("memory://my-bucket", "dini", _utc(2025, 3, 2, 6))
        == "memory://my-bucket/dini/2025-03-02T0600Z"
    )


def test_invalid_suite_rejected():
    with pytest.raises(ValueError, match="suite_name"):
        cf.create_test_fixture(
            t_analysis=_utc(2025, 3, 2, 6),
            source_uri="memory://cf-bad-src",
            fixture_bucket="memory://cf-bad-dst",
            suite_name="nonsense",
            max_hour=1,
        )


def test_resolve_explicit_complete():
    _plant_source("memory://cf-src", _utc(2025, 3, 2, 6))
    t = cf.resolve_analysis_time(
        _utc(2025, 3, 2, 6), "memory://cf-src", 1, "CONTROL__dmi", ("sf", "pl"), None
    )
    assert t == _utc(2025, 3, 2, 6)


def test_resolve_explicit_incomplete_raises():
    with pytest.raises(FileNotFoundError, match="Incomplete"):
        cf.resolve_analysis_time(
            _utc(2025, 3, 2, 6),
            "memory://cf-empty",
            1,
            "CONTROL__dmi",
            ("sf", "pl"),
            None,
        )


def test_resolve_auto_finds_recent_complete():
    now = datetime.datetime.now(datetime.timezone.utc)
    candidates = cf.candidate_times(now)
    _plant_source("memory://cf-auto", candidates[1])
    t = cf.resolve_analysis_time(
        None, "memory://cf-auto", 1, "CONTROL__dmi", ("sf", "pl"), None, now=now
    )
    # candidates[0] is incomplete, so the second-newest is picked
    assert t == candidates[1]


def test_create_dry_run_uploads_nothing():
    _plant_source("memory://cf-dry-src", _utc(2025, 3, 2, 6))
    prefix = cf.create_test_fixture(
        t_analysis=_utc(2025, 3, 2, 6),
        source_uri="memory://cf-dry-src",
        fixture_bucket="memory://cf-dry-dst",
        max_hour=1,
        dry_run=True,
    )
    assert prefix == "memory://cf-dry-dst/dini/2025-03-02T0600Z"
    assert storage.find_missing([f"{prefix}/ml/x", f"{prefix}/README.md"]) == [
        f"{prefix}/ml/x",
        f"{prefix}/README.md",
    ]


def test_create_upload_and_verify():
    _plant_source("memory://cf-up-src", _utc(2025, 3, 2, 6))
    prefix = cf.create_test_fixture(
        t_analysis=_utc(2025, 3, 2, 6),
        source_uri="memory://cf-up-src",
        fixture_bucket="memory://cf-up-dst",
        max_hour=1,
    )
    assert (
        storage.find_missing(
            [
                f"{prefix}/ml/fc2025030206+000CONTROL__dmi_sf",
                f"{prefix}/ml/fc2025030206+001CONTROL__dmi_pl",
                f"{prefix}/README.md",
                f"{prefix}/manifest.json",
            ]
        )
        == []
    )
    # README mentions analysis time and layout
    fs, readme_path = storage.resolve_fs(f"{prefix}/README.md")
    with fs.open(readme_path) as f:
        readme = f.read().decode()
    assert "2025-03-02T06:00:00+00:00" in readme
    assert "retains ~2 weeks" in readme
    fs_m, manifest_path = storage.resolve_fs(f"{prefix}/manifest.json")
    with fs_m.open(manifest_path) as f:
        manifest = json.load(f)
    assert manifest["max_hour"] == 1
    assert manifest["suite_name"] == "dini"
    assert len(manifest["files"]) == 4
    assert all(len(f["sha256"]) == 64 for f in manifest["files"])


def test_create_ig_suite_namespaced():
    _plant_source("memory://cf-ig-src", _utc(2025, 3, 2, 6))
    prefix = cf.create_test_fixture(
        t_analysis=_utc(2025, 3, 2, 6),
        source_uri="memory://cf-ig-src",
        fixture_bucket="memory://cf-ig-dst",
        suite_name="ig",
        max_hour=1,
    )
    assert prefix == "memory://cf-ig-dst/ig/2025-03-02T0600Z"
    fs_m, manifest_path = storage.resolve_fs(f"{prefix}/manifest.json")
    with fs_m.open(manifest_path) as f:
        manifest = json.load(f)
    assert manifest["suite_name"] == "ig"


def test_no_overwrite_guard():
    _plant_source("memory://cf-ow-src", _utc(2025, 3, 2, 6))
    kwargs = dict(
        t_analysis=_utc(2025, 3, 2, 6),
        source_uri="memory://cf-ow-src",
        fixture_bucket="memory://cf-ow-dst",
        max_hour=1,
    )
    cf.create_test_fixture(**kwargs)
    with pytest.raises(FileExistsError, match="already exists"):
        cf.create_test_fixture(**kwargs)
    # overwrite succeeds
    cf.create_test_fixture(**kwargs, overwrite=True)


def test_dest_dir_stage_only(tmp_path):
    _plant_source("memory://cf-local-src", _utc(2025, 3, 2, 6))
    dest = str(tmp_path / "ml")
    out = cf.create_test_fixture(
        t_analysis=_utc(2025, 3, 2, 6),
        source_uri="memory://cf-local-src",
        # dest bucket must remain untouched in stage-only mode
        fixture_bucket="memory://cf-local-dst",
        max_hour=1,
        dest_dir=dest,
    )
    assert out == dest
    assert sorted(os.listdir(dest)) == sorted(
        f"fc2025030206+{h:03d}CONTROL__dmi_{t}" for h in (0, 1) for t in ("sf", "pl")
    )
    # nothing uploaded
    assert storage.find_missing(
        ["memory://cf-local-dst/dini/2025-03-02T0600Z/ml/x"]
    ) == ["memory://cf-local-dst/dini/2025-03-02T0600Z/ml/x"]
    # second run skips existing files without error
    cf.create_test_fixture(
        t_analysis=_utc(2025, 3, 2, 6),
        source_uri="memory://cf-local-src",
        fixture_bucket="memory://cf-local-dst",
        max_hour=1,
        dest_dir=dest,
    )
