"""Tests for zarr_creator.settings."""

import datetime

import pytest

from zarr_creator import settings as s


def _utc(*args):
    return datetime.datetime(*args, tzinfo=datetime.timezone.utc)


def test_defaults(monkeypatch):
    for var in (
        "SRC_GRIB_ROOT_URI",
        "SRC_GRIB_ROOT",
        "SRC_GRIB_ROOT_PATH",
        "REFS_ROOT_PATH",
        "MEMBER_ID",
        "MAX_HOUR",
        "SUITE_NAME",
        "SRC_GRIB_TEMP_PATH",
        "DST_ZARR_OUTPUT_PATH",
        "SRC_AWS_PROFILE",
        "DST_AWS_PROFILE",
        "AWS_PROFILE",
        "SRC_ANON",
    ):
        monkeypatch.delenv(var, raising=False)
    cfg = s.load_settings()
    assert cfg.src_grib_root_uri == s.DEFAULT_SRC_GRIB_ROOT_URI
    assert cfg.refs_root_path == s.DEFAULT_REFS_ROOT_PATH
    assert cfg.member_id == s.DEFAULT_MEMBER_ID
    assert cfg.max_hour == s.DEFAULT_MAX_HOUR
    assert cfg.suite_name == s.DEFAULT_SUITE_NAME
    assert cfg.src_grib_temp_path is None
    assert cfg.dst_zarr_output_path == s.DEFAULT_DST_ZARR_OUTPUT_PATH
    assert cfg.src_aws_profile is None
    assert cfg.dst_aws_profile is None
    assert cfg.src_anon is False


def test_env_overrides(monkeypatch):
    monkeypatch.setenv("SRC_GRIB_ROOT_URI", "s3://bucket/prefix")
    monkeypatch.setenv("MAX_HOUR", "2")
    monkeypatch.setenv("SRC_GRIB_TEMP_PATH", "/tmp/stage")
    monkeypatch.setenv(
        "DST_ZARR_OUTPUT_PATH", "s3://out/{suite_name}/{dataset_id}.zarr"
    )
    cfg = s.load_settings()
    assert cfg.src_grib_root_uri == "s3://bucket/prefix"
    assert cfg.max_hour == 2
    assert cfg.src_grib_temp_path == "/tmp/stage"
    assert cfg.dst_zarr_output_path == "s3://out/{suite_name}/{dataset_id}.zarr"


def test_deprecated_alias_warns(monkeypatch):
    monkeypatch.delenv("SRC_GRIB_ROOT_URI", raising=False)
    monkeypatch.delenv("SRC_GRIB_ROOT", raising=False)
    monkeypatch.setenv("SRC_GRIB_ROOT_PATH", "/mnt/old")
    with pytest.warns(DeprecationWarning, match="SRC_GRIB_ROOT_PATH"):
        cfg = s.load_settings()
    assert cfg.src_grib_root_uri == "/mnt/old"


def test_canonical_wins_over_alias(monkeypatch):
    monkeypatch.setenv("SRC_GRIB_ROOT_URI", "s3://new/prefix")
    monkeypatch.setenv("SRC_GRIB_ROOT_PATH", "/mnt/old")
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        cfg = s.load_settings()
    assert cfg.src_grib_root_uri == "s3://new/prefix"


def test_max_hour_invalid(monkeypatch):
    monkeypatch.setenv("MAX_HOUR", "not-an-int")
    with pytest.raises(ValueError, match="MAX_HOUR must be an integer"):
        s.load_settings()


def test_profile_fallback_matrix(monkeypatch):
    for var in ("SRC_AWS_PROFILE", "DST_AWS_PROFILE", "AWS_PROFILE"):
        monkeypatch.delenv(var, raising=False)
    cfg = s.load_settings()
    assert s.source_profile(cfg) is None
    assert s.dest_profile(cfg) is None

    monkeypatch.setenv("AWS_PROFILE", "base")
    cfg = s.load_settings()
    assert s.source_profile(cfg) == "base"
    assert s.dest_profile(cfg) == "base"

    monkeypatch.setenv("SRC_AWS_PROFILE", "src")
    cfg = s.load_settings()
    assert s.source_profile(cfg) == "src"
    assert s.dest_profile(cfg) == "base"
    assert s.source_profile(cfg, explicit="cli") == "cli"


def test_src_anon_parsing(monkeypatch):
    monkeypatch.delenv("SRC_ANON", raising=False)
    assert s.load_settings().src_anon is False
    monkeypatch.setenv("SRC_ANON", "1")
    assert s.load_settings().src_anon is True
    monkeypatch.setenv("SRC_ANON", "yes")
    assert s.load_settings().src_anon is True


def test_describe_source_auth():
    assert s.describe_source_auth(True, "any-profile") == "unsigned (SRC_ANON=1)"
    assert (
        s.describe_source_auth(False, "my-profile") == "signed (profile 'my-profile')"
    )
    assert s.describe_source_auth(False, None) == "signed (default credential chain)"


def test_time_helpers():
    t = _utc(2025, 3, 2, 6)
    assert s.analysis_time_str(t) == "2025030206"
    assert s.refs_dir_name(t) == "2025-03-02T0600Z"
    assert (
        s.grib_filename(t, 4, "CONTROL__dmi", "sf") == "fc2025030206+004CONTROL__dmi_sf"
    )
    names = s.expected_grib_filenames(t, 1, "CONTROL__dmi")
    assert len(names) == 4  # 2 hours x sf/pl
    assert names[0] == "fc2025030206+000CONTROL__dmi_sf"


def test_require_utc_rejects_naive():
    with pytest.raises(ValueError, match="timezone-aware"):
        s.analysis_time_str(datetime.datetime(2025, 3, 2, 6))


def test_output_format():
    t = _utc(2025, 3, 2, 6)
    out = s.format_output_path(
        "s3://b/{suite_name}/{member}/{t_analysis}/{dataset_id}.zarr",
        suite_name="dini",
        member="control",
        t_analysis=t,
        dataset_id="single_levels",
    )
    assert out == "s3://b/dini/control/2025-03-02T060000Z/single_levels.zarr"
    with pytest.raises(ValueError, match="dataset_id"):
        s.format_output_path(
            "s3://b/static.zarr",
            suite_name="dini",
            member="control",
            t_analysis=t,
            dataset_id="x",
        )


def test_resolve_t_analysis_latest_uses_now_with_lag():
    now = datetime.datetime(2025, 3, 2, 5, 30, tzinfo=datetime.timezone.utc)
    expected = datetime.datetime(2025, 3, 2, 3, tzinfo=datetime.timezone.utc)
    # 05:30 - 2h = 03:30 -> floored to the 03:00 analysis time
    assert s.resolve_t_analysis("latest", now=now) == expected
    assert s.resolve_t_analysis("LATEST", now=now) == expected
    assert s.resolve_t_analysis(None, now=now) == expected


def test_resolve_t_analysis_explicit_and_naive():
    t = s.resolve_t_analysis("2025-03-02T06:00:00Z")
    assert t == datetime.datetime(2025, 3, 2, 6, tzinfo=datetime.timezone.utc)
    with pytest.raises(ValueError):
        s.resolve_t_analysis("2025-03-02T06:00:00")  # no timezone
    with pytest.raises(ValueError):
        s.resolve_t_analysis("not-a-time")
