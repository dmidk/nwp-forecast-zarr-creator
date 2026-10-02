"""Tests for zarr_creator.pipeline.index_refs (gribscan calls mocked)."""

import datetime
import os

import pytest

from zarr_creator.pipeline import index_refs
from zarr_creator.settings import Settings


def _utc(*args):
    return datetime.datetime(*args, tzinfo=datetime.timezone.utc)


def _settings(**overrides):
    base = dict(
        src_grib_root_uri="/nonexistent",
        refs_root_path="/tmp/refs",
        member_id="CONTROL__dmi",
        max_hour=1,
        suite_name="dini",
        src_grib_temp_path=None,
        dst_zarr_output_path="file:///tmp/out/{dataset_id}.zarr",
        src_aws_profile=None,
        dst_aws_profile=None,
    )
    base.update(overrides)
    return Settings(**base)


def _touch_files(root, t, member="CONTROL__dmi", max_hour=1):
    from zarr_creator.settings import expected_grib_filenames

    for name in expected_grib_filenames(t, max_hour, member):
        p = os.path.join(root, name)
        with open(p, "w") as f:
            f.write("fake-grib")


def test_missing_files_raise(tmp_path):
    settings = _settings(
        src_grib_root_uri=str(tmp_path), refs_root_path=str(tmp_path / "refs")
    )
    with pytest.raises(FileNotFoundError, match="missing"):
        index_refs.build_indexes_and_refs(_utc(2025, 3, 2, 6), settings)


def test_s3_auth_error_gets_remediation_hint(monkeypatch):
    settings = _settings(src_grib_root_uri="s3://bucket/ml")

    def raise_403(urls, profile=None, anon=False):
        # What s3fs raises for a 403 / AccessDenied response.
        raise PermissionError("Forbidden")

    monkeypatch.setattr(index_refs.storage, "find_missing", raise_403)
    with pytest.raises(RuntimeError, match="SRC_ANON"):
        index_refs.build_indexes_and_refs(_utc(2025, 3, 2, 6), settings)


def test_indexes_in_place_no_staging(tmp_path, monkeypatch):
    src = tmp_path / "ml"
    src.mkdir()
    t = _utc(2025, 3, 2, 6)
    _touch_files(str(src), t)
    settings = _settings(
        src_grib_root_uri=str(src), refs_root_path=str(tmp_path / "refs")
    )
    calls = []
    monkeypatch.setattr(
        index_refs,
        "_run_index",
        lambda inputs, nprocs=2: calls.append(("index", inputs)),
    )
    monkeypatch.setattr(
        index_refs,
        "_run_build_refs",
        lambda indexes, refs_dir, prefix: calls.append(
            ("build", indexes, refs_dir, prefix)
        ),
    )
    monkeypatch.setattr(index_refs, "set_local_eccodes_definitions_path", lambda: None)
    refs_dir = index_refs.build_indexes_and_refs(t, settings)
    assert refs_dir == str(
        tmp_path / "refs" / "CONTROL__dmi" / "2025-03-02T0600Z.jsons"
    )
    assert os.path.isdir(refs_dir)
    kinds = [c[0] for c in calls]
    assert kinds == ["index", "build", "index", "build"]
    # sf inputs come before pl inputs; each type has max_hour+1 = 2 files
    assert len(calls[0][1]) == 2 and calls[0][1][0].endswith("_sf")
    assert len(calls[2][1]) == 2 and calls[2][1][0].endswith("_pl")
    assert calls[1][2] == refs_dir
    assert calls[1][3] == str(src) + "/"


def test_staging_used_when_set(tmp_path, monkeypatch):
    src = tmp_path / "ml"
    src.mkdir()
    t = _utc(2025, 3, 2, 6)
    _touch_files(str(src), t)
    stage = tmp_path / "stage"
    settings = _settings(
        src_grib_root_uri=str(src),
        refs_root_path=str(tmp_path / "refs"),
        src_grib_temp_path=str(stage),
    )
    staged_with = []
    import shutil

    def fake_stage(urls, tmpdir, profile=None, anon=False):
        os.makedirs(tmpdir, exist_ok=True)
        for url in urls:
            shutil.copy(url.replace("file://", ""), tmpdir)
        staged_with.append(tmpdir)
        return tmpdir

    monkeypatch.setattr(index_refs.storage, "download_to_temp", fake_stage)
    monkeypatch.setattr(index_refs, "_run_index", lambda inputs, nprocs=2: None)
    monkeypatch.setattr(index_refs, "_run_build_refs", lambda *a, **k: None)
    monkeypatch.setattr(index_refs, "set_local_eccodes_definitions_path", lambda: None)
    # local file:// URLs: storage.join on a plain path returns plain path
    index_refs.build_indexes_and_refs(t, settings)
    assert staged_with == [str(stage)]


def test_s3_no_temp_warns_and_attempts(monkeypatch):
    from loguru import logger

    settings = _settings(src_grib_root_uri="s3://bucket/ml")
    monkeypatch.setattr(
        index_refs.storage, "find_missing", lambda urls, profile=None, anon=False: []
    )
    indexed = []
    monkeypatch.setattr(
        index_refs, "_run_index", lambda inputs, nprocs=2: indexed.extend(inputs)
    )
    monkeypatch.setattr(index_refs, "_run_build_refs", lambda *a, **k: None)
    monkeypatch.setattr(index_refs, "set_local_eccodes_definitions_path", lambda: None)
    messages = []
    handler = logger.add(messages.append, format="{message}")
    try:
        index_refs.build_indexes_and_refs(_utc(2025, 3, 2, 6), settings)
    finally:
        logger.remove(handler)
    assert any("SRC_GRIB_TEMP_PATH" in m for m in messages)
    assert indexed and indexed[0].startswith("s3://bucket/ml/")


def test_t_analysis_cli_arg_default_and_errors():
    import argparse

    from zarr_creator import settings as s
    from zarr_creator.pipeline.cli_args import t_analysis_arg

    parser = argparse.ArgumentParser()
    parser.add_argument("--t-analysis", type=t_analysis_arg, default=s.LATEST)
    # default is resolved to a real UTC datetime on the 3-hour grid
    t = parser.parse_args([]).t_analysis
    assert t.tzinfo is not None and t.hour % 3 == 0 and t.minute == 0
    assert parser.parse_args(["--t-analysis", "2025-03-02T06:00:00Z"]).t_analysis == (
        s.resolve_t_analysis("2025-03-02T06:00:00Z")
    )
    with pytest.raises(SystemExit):
        parser.parse_args(["--t-analysis", "2025-03-02T06:00:00"])
