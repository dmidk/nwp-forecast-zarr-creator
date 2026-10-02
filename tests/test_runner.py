"""Tests for zarr_creator.pipeline.runner."""

import datetime
import os

import pytest

from zarr_creator.pipeline import runner
from zarr_creator.settings import Settings


def _utc(*args):
    return datetime.datetime(*args, tzinfo=datetime.timezone.utc)


def _settings(**overrides):
    base = dict(
        src_grib_root_uri="/nonexistent",
        refs_root_path="/tmp/refs",
        member_id="CONTROL__dmi",
        max_hour=36,
        suite_name="dini",
        src_grib_temp_path=None,
        dst_zarr_output_path="file:///tmp/out/{dataset_id}.zarr",
        src_aws_profile=None,
        dst_aws_profile=None,
    )
    base.update(overrides)
    return Settings(**base)


def test_compute_analysis_time_boundaries():
    # 05:00 UTC, lag 2h -> adjusted 03:00 -> floor 03:00
    assert runner.compute_analysis_time(_utc(2025, 3, 2, 5)) == _utc(2025, 3, 2, 3)
    # exactly on interval: 08:00 - 2h = 06:00 -> 06:00
    assert runner.compute_analysis_time(_utc(2025, 3, 2, 8)) == _utc(2025, 3, 2, 6)
    # just after interval: 08:01 - 2h = 06:01 -> 06:00
    assert runner.compute_analysis_time(_utc(2025, 3, 2, 8, 1)) == _utc(2025, 3, 2, 6)
    # midnight wrap: 01:00 - 2h = 23:00 prev day -> 21:00
    assert runner.compute_analysis_time(_utc(2025, 3, 2, 1)) == _utc(2025, 3, 1, 21)
    # naive datetimes treated as UTC
    assert runner.compute_analysis_time(datetime.datetime(2025, 3, 2, 5)) == _utc(
        2025, 3, 2, 3
    )


def _refs_dir(tmp_path):
    return tmp_path / "CONTROL__dmi" / "2025-03-02T0600Z.jsons"


def _mark_done(tmp_path):
    refs = _refs_dir(tmp_path)
    os.makedirs(refs, exist_ok=True)
    (refs / runner.REFS_DONE_MARKER).touch()


def test_refs_done_needs_marker_not_just_directory(tmp_path):
    settings = _settings(refs_root_path=str(tmp_path))
    t = _utc(2025, 3, 2, 6)
    assert not runner.refs_done(t, settings)
    # a directory (e.g. from an interrupted run) does not count as done
    os.makedirs(_refs_dir(tmp_path))
    (_refs_dir(tmp_path) / "heightAboveGround.json").write_text("{}")
    assert not runner.refs_done(t, settings)
    _mark_done(tmp_path)
    assert runner.refs_done(t, settings)


def test_mark_refs_done_frees_refs_and_keeps_marker(tmp_path):
    settings = _settings(refs_root_path=str(tmp_path))
    os.makedirs(_refs_dir(tmp_path))
    (_refs_dir(tmp_path) / "heightAboveGround.json").write_text("{}")
    runner.mark_refs_done(_utc(2025, 3, 2, 6), settings)
    assert os.listdir(_refs_dir(tmp_path)) == [runner.REFS_DONE_MARKER]


def test_process_one_skips_when_done(tmp_path, monkeypatch):
    settings = _settings(refs_root_path=str(tmp_path))
    t = _utc(2025, 3, 2, 6)
    _mark_done(tmp_path)
    called = []
    monkeypatch.setattr(runner, "build_indexes_and_refs", lambda *a: called.append(a))
    assert runner.process_one(t, settings) == "skipped"
    assert called == []


def test_process_one_success_cleans_temp(tmp_path, monkeypatch):
    stage = tmp_path / "stage"
    stage.mkdir()
    settings = _settings(
        refs_root_path=str(tmp_path / "refs"), src_grib_temp_path=str(stage)
    )
    t = _utc(2025, 3, 2, 6)
    monkeypatch.setattr(runner, "build_indexes_and_refs", lambda *a: "refs")
    conversions = []
    monkeypatch.setattr(runner, "_run_conversion", lambda *a: conversions.append(a))
    assert runner.process_one(t, settings) == "done"
    assert len(conversions) == 1
    assert not stage.exists()


def test_process_one_rebuilds_when_refs_dir_is_only_partial(tmp_path, monkeypatch):
    settings = _settings(refs_root_path=str(tmp_path))
    t = _utc(2025, 3, 2, 6)
    os.makedirs(_refs_dir(tmp_path))  # interrupted earlier run: dir, no marker
    built = []
    monkeypatch.setattr(runner, "build_indexes_and_refs", lambda *a: built.append(a))
    monkeypatch.setattr(runner, "_run_conversion", lambda *a: None)
    assert runner.process_one(t, settings) == "done"
    assert len(built) == 1


def test_process_one_frees_refs_after_success(tmp_path, monkeypatch):
    settings = _settings(refs_root_path=str(tmp_path))
    t = _utc(2025, 3, 2, 6)

    def build(*a):
        os.makedirs(_refs_dir(tmp_path), exist_ok=True)
        (_refs_dir(tmp_path) / "heightAboveGround.json").write_text("{}")

    monkeypatch.setattr(runner, "build_indexes_and_refs", build)
    monkeypatch.setattr(runner, "_run_conversion", lambda *a: None)
    assert runner.process_one(t, settings) == "done"
    assert os.listdir(_refs_dir(tmp_path)) == [runner.REFS_DONE_MARKER]
    # the next poll sees it as processed and does no work
    monkeypatch.setattr(runner, "build_indexes_and_refs", lambda *a: 1 / 0)
    assert runner.process_one(t, settings) == "skipped"


def test_process_one_no_cleanup_keeps_refs_and_staged_files(tmp_path, monkeypatch):
    stage = tmp_path / "stage"
    stage.mkdir()
    (stage / "fc2025030206+000CONTROL__dmi_sf").write_text("grib")
    settings = _settings(
        refs_root_path=str(tmp_path / "refs"), src_grib_temp_path=str(stage)
    )
    t = _utc(2025, 3, 2, 6)
    refs = tmp_path / "refs" / "CONTROL__dmi" / "2025-03-02T0600Z.jsons"

    def build(*a):
        os.makedirs(refs, exist_ok=True)
        (refs / "heightAboveGround.json").write_text("{}")

    monkeypatch.setattr(runner, "build_indexes_and_refs", build)
    monkeypatch.setattr(runner, "_run_conversion", lambda *a: None)
    assert runner.process_one(t, settings, cleanup=False) == "done"
    # everything is kept, but the time still counts as processed
    assert sorted(os.listdir(refs)) == [
        runner.REFS_DONE_MARKER,
        "heightAboveGround.json",
    ]
    assert (stage / "fc2025030206+000CONTROL__dmi_sf").exists()
    assert runner.refs_done(t, settings)


def test_main_passes_no_cleanup(tmp_path, monkeypatch):
    seen = {}
    monkeypatch.setattr(
        runner, "process_one", lambda t, s, **k: seen.update(k) or "done"
    )
    monkeypatch.setenv("REFS_ROOT_PATH", str(tmp_path))
    runner.main([])
    assert seen["cleanup"] is True
    runner.main(["--no-cleanup"])
    assert seen["cleanup"] is False


def test_process_one_keeps_refs_when_conversion_fails(tmp_path, monkeypatch):
    settings = _settings(refs_root_path=str(tmp_path))
    t = _utc(2025, 3, 2, 6)

    def build(*a):
        os.makedirs(_refs_dir(tmp_path), exist_ok=True)
        (_refs_dir(tmp_path) / "heightAboveGround.json").write_text("{}")

    monkeypatch.setattr(runner, "build_indexes_and_refs", build)
    monkeypatch.setattr(
        runner, "_run_conversion", lambda *a: (_ for _ in ()).throw(RuntimeError("x"))
    )
    monkeypatch.setattr(runner.time, "sleep", lambda s: None)
    with pytest.raises(RuntimeError):
        runner.process_one(t, settings, max_retries=0, retry_interval=0)
    assert os.listdir(_refs_dir(tmp_path)) == ["heightAboveGround.json"]
    assert not runner.refs_done(t, settings)


def test_process_one_retries_then_succeeds(tmp_path, monkeypatch):
    settings = _settings(refs_root_path=str(tmp_path / "refs"))
    t = _utc(2025, 3, 2, 6)
    monkeypatch.setattr(runner, "build_indexes_and_refs", lambda *a: "refs")
    attempts = []

    def flaky(*a):
        attempts.append(a)
        if len(attempts) < 3:
            raise RuntimeError("boom")

    monkeypatch.setattr(runner, "_run_conversion", flaky)
    monkeypatch.setattr(runner.time, "sleep", lambda s: None)
    assert runner.process_one(t, settings, retry_interval=0) == "done"
    assert len(attempts) == 3


def test_process_one_max_retries_exceeded(tmp_path, monkeypatch):
    settings = _settings(refs_root_path=str(tmp_path / "refs"))
    t = _utc(2025, 3, 2, 6)
    monkeypatch.setattr(runner, "build_indexes_and_refs", lambda *a: "refs")
    monkeypatch.setattr(
        runner, "_run_conversion", lambda *a: (_ for _ in ()).throw(RuntimeError("x"))
    )
    monkeypatch.setattr(runner.time, "sleep", lambda s: None)
    with pytest.raises(RuntimeError):
        runner.process_one(t, settings, max_retries=1, retry_interval=0)


def test_poll_once_done_sleeps_long(tmp_path):
    settings = _settings(refs_root_path=str(tmp_path))
    now = _utc(2025, 3, 2, 8, 30)  # -> analysis 06:00
    _mark_done(tmp_path)
    assert runner.poll_once(settings, now=now) == runner.DEFAULT_ALREADY_DONE_SLEEP


def test_poll_once_processes_and_sleeps_poll(tmp_path, monkeypatch):
    settings = _settings(refs_root_path=str(tmp_path / "refs"))
    now = _utc(2025, 3, 2, 8, 30)
    seen = []
    monkeypatch.setattr(
        runner, "process_one", lambda t, s, **k: seen.append(t) or "done"
    )
    assert runner.poll_once(settings, now=now) == runner.DEFAULT_POLL_INTERVAL
    assert seen == [_utc(2025, 3, 2, 6)]


def test_poll_once_survives_incomplete_source(tmp_path, monkeypatch):
    settings = _settings(refs_root_path=str(tmp_path / "refs"))
    now = _utc(2025, 3, 2, 8, 30)

    def missing(*a, **k):
        raise FileNotFoundError("1 expected GRIB file(s) missing")

    monkeypatch.setattr(runner, "process_one", missing)
    assert runner.poll_once(settings, now=now) == runner.DEFAULT_POLL_INTERVAL


def test_poll_once_propagates_other_errors(tmp_path, monkeypatch):
    settings = _settings(refs_root_path=str(tmp_path / "refs"))
    now = _utc(2025, 3, 2, 8, 30)

    def denied(*a, **k):
        raise RuntimeError("403")

    monkeypatch.setattr(runner, "process_one", denied)
    with pytest.raises(RuntimeError):
        runner.poll_once(settings, now=now)


def _stub_main(monkeypatch, tmp_path):
    """Stub out the work ``runner.main`` does so only arg handling runs."""
    calls = {}
    monkeypatch.setattr(runner, "watch_loop", lambda s, **k: calls.update(watch=True))
    monkeypatch.setattr(
        runner, "process_one", lambda t, s, **k: calls.update(one=t) or "done"
    )
    monkeypatch.setenv("REFS_ROOT_PATH", str(tmp_path))
    return calls


@pytest.mark.parametrize("t", ["2025-03-02T06:00:00Z", "latest"])
def test_main_watch_and_t_analysis_are_mutually_exclusive(
    tmp_path, monkeypatch, capsys, t
):
    calls = _stub_main(monkeypatch, tmp_path)
    with pytest.raises(SystemExit):
        runner.main(["--watch", "--t-analysis", t])
    assert "not allowed with argument" in capsys.readouterr().err
    assert calls == {}


def test_main_watch_alone_runs_watcher(tmp_path, monkeypatch):
    calls = _stub_main(monkeypatch, tmp_path)
    runner.main(["--watch"])
    assert calls == {"watch": True}


def test_main_one_shot_resolves_t_analysis(tmp_path, monkeypatch):
    calls = _stub_main(monkeypatch, tmp_path)
    runner.main(["--t-analysis", "2025-03-02T06:00:00Z"])
    assert calls["one"] == _utc(2025, 3, 2, 6)
    runner.main([])  # default: latest
    assert calls["one"].minute == 0 and calls["one"].hour % 3 == 0


def test_main_rejects_naive_t_analysis(tmp_path, monkeypatch):
    _stub_main(monkeypatch, tmp_path)
    with pytest.raises(SystemExit):
        runner.main(["--t-analysis", "2025-03-02T06:00:00"])


def test_main_forwards_settings_flags_to_conversion(tmp_path, monkeypatch):
    """``run`` flags reach the conversion, not just indexing (they used to be
    dropped because the conversion re-read its settings from the env)."""
    import zarr_creator.__main__ as zc_main

    seen = {}
    monkeypatch.setattr(runner, "build_indexes_and_refs", lambda t, s: None)
    monkeypatch.setattr(zc_main, "convert", lambda t, s: seen.update(t=t, s=s))
    monkeypatch.delenv("SRC_GRIB_TEMP_PATH", raising=False)
    out = f"file://{tmp_path}/out/{{dataset_id}}.zarr"
    runner.main(
        [
            "--t-analysis",
            "2025-03-02T06:00:00Z",
            "--refs-root-path",
            str(tmp_path / "refs"),
            "--member-id",
            "MEMBER",
            "--dst-zarr-output-path",
            out,
            "--dest-profile",
            "writer",
            "--suite-name",
            "ig",
        ]
    )
    assert seen["t"] == _utc(2025, 3, 2, 6)
    s = seen["s"]
    assert s.refs_root_path == str(tmp_path / "refs")
    assert s.member_id == "MEMBER"
    assert s.dst_zarr_output_path == out
    assert s.dst_aws_profile == "writer"
    assert s.suite_name == "ig"


def test_main_rejects_unsupported_suite_before_indexing(tmp_path, monkeypatch):
    calls = _stub_main(monkeypatch, tmp_path)
    with pytest.raises(SystemExit):
        runner.main(["--suite-name", "nope"])
    assert calls == {}
