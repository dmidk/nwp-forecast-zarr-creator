"""Tests for the ``python -m zarr_creator`` subcommand CLI."""

import datetime

import pytest

import zarr_creator.__main__ as zc_main
from zarr_creator.pipeline import index_refs


def _utc(*args):
    return datetime.datetime(*args, tzinfo=datetime.timezone.utc)


def test_subcommand_is_required(capsys):
    with pytest.raises(SystemExit):
        zc_main.cli([])
    assert "required" in capsys.readouterr().err


def test_index_subcommand_builds_refs_with_flags(tmp_path, monkeypatch):
    seen = {}
    monkeypatch.setattr(
        index_refs,
        "build_indexes_and_refs",
        lambda t, s: seen.update(t=t, s=s) or str(tmp_path),
    )
    zc_main.cli(
        [
            "index",
            "--t-analysis",
            "2025-03-02T06:00:00Z",
            "--refs-root-path",
            str(tmp_path),
            "--max-hour",
            "2",
        ]
    )
    assert seen["t"] == _utc(2025, 3, 2, 6)
    assert seen["s"].refs_root_path == str(tmp_path)
    assert seen["s"].max_hour == 2


@pytest.mark.parametrize("flag", ["--t-analysis", "--t_analysis"])
def test_convert_subcommand_passes_settings(tmp_path, monkeypatch, flag):
    seen = {}
    monkeypatch.setattr(zc_main, "convert", lambda t, s: seen.update(t=t, s=s))
    out = f"file://{tmp_path}/{{dataset_id}}.zarr"
    zc_main.cli(
        [
            "convert",
            flag,
            "2025-03-02T06:00:00Z",
            "--suite-name",
            "ig",
            "--dst-zarr-output-path",
            out,
        ]
    )
    assert seen["t"] == _utc(2025, 3, 2, 6)
    assert seen["s"].suite_name == "ig"
    assert seen["s"].dst_zarr_output_path == out


def test_convert_subcommand_rejects_unsupported_suite(monkeypatch, capsys):
    monkeypatch.setattr(zc_main, "convert", lambda t, s: pytest.fail("converted"))
    with pytest.raises(SystemExit):
        zc_main.cli(["convert", "--suite-name", "nope"])
    assert "invalid choice: 'nope'" in capsys.readouterr().err
