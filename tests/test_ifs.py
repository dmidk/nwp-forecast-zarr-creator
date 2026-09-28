"""Tests for the IFS suite (ANNA boundary input), using synthetic GRIB files."""

import datetime
from pathlib import Path

import numpy as np
import pytest
import xarray as xr

from tests.ifs_grib_fixture import PRESSURE_LEVELS as FIXTURE_LEVELS
from tests.ifs_grib_fixture import pressure_value, surface_value, write_ifs_file
from zarr_creator import config_ifs
from zarr_creator import settings as s
from zarr_creator.pipeline import index_refs, runner
from zarr_creator.suites import get_suite


def _utc(*args):
    return datetime.datetime(*args, tzinfo=datetime.timezone.utc)


T_ANALYSIS = _utc(2026, 9, 28, 0)


def test_grib_filenames():
    groups = config_ifs.grib_file_groups(T_ANALYSIS, 6, "control")
    assert groups == {
        "dj": [
            "dei_dj_ifs-ens-cf_od_oper_fc_20260928T000000Z_20260928T000000Z_0h",
            "dei_dj_ifs-ens-cf_od_oper_fc_20260928T000000Z_20260928T030000Z_3h",
            "dei_dj_ifs-ens-cf_od_oper_fc_20260928T000000Z_20260928T060000Z_6h",
        ]
    }
    names = config_ifs.grib_file_groups(_utc(2026, 9, 28, 12), 72, "control")["dj"]
    assert len(names) == 25
    # valid time rolls over the month
    assert names[-1].endswith("_20260928T120000Z_20261001T120000Z_72h")


def test_latest_analysis_time_per_suite():
    now = _utc(2026, 9, 28, 13, 30)
    # dini/ig: 2h lag, 3-hourly
    assert s.resolve_t_analysis_for_suite("latest", "dini", now=now) == _utc(
        2026, 9, 28, 9
    )
    # ifs: 7h lag, 6-hourly
    assert s.resolve_t_analysis_for_suite("latest", "ifs", now=now) == _utc(
        2026, 9, 28, 6
    )
    assert s.resolve_t_analysis_for_suite(
        "2026-09-28T00:00:00Z", "ifs", now=now
    ) == _utc(2026, 9, 28)


def test_compute_analysis_time_interval():
    assert s.compute_analysis_time(
        _utc(2026, 9, 28, 12, 59), lag_hours=7, interval_hours=6
    ) == _utc(2026, 9, 28, 0)
    assert s.compute_analysis_time(
        _utc(2026, 9, 28, 13), lag_hours=7, interval_hours=6
    ) == _utc(2026, 9, 28, 6)


def test_suite_defaults_apply_unless_set(monkeypatch):
    monkeypatch.delenv("MAX_HOUR", raising=False)
    monkeypatch.delenv("MEMBER_ID", raising=False)
    monkeypatch.setenv("SUITE_NAME", "ifs")
    cfg = s.load_settings()
    assert (cfg.suite_name, cfg.max_hour, cfg.member_id) == ("ifs", 72, "control")
    # switching suite switches the defaults back
    s.set_suite(cfg, "dini")
    assert (cfg.max_hour, cfg.member_id) == (s.DEFAULT_MAX_HOUR, s.DEFAULT_MEMBER_ID)

    monkeypatch.setenv("MAX_HOUR", "12")
    cfg = s.load_settings()
    assert cfg.max_hour == 12 and cfg.member_id == "control"


def test_watch_uses_suite_cycle(tmp_path, monkeypatch):
    cfg = s.Settings(
        refs_root_path=str(tmp_path), suite_name="ifs", member_id="control"
    )
    processed = []
    monkeypatch.setattr(
        runner, "process_one", lambda t, settings, **k: processed.append(t)
    )
    runner.poll_once(cfg, now=_utc(2026, 9, 28, 13, 30))
    assert processed == [_utc(2026, 9, 28, 6)]


def _write_fixture(src_dir, steps=(0, 3)):
    for step in steps:
        write_ifs_file(
            src_dir / config_ifs.grib_filename(T_ANALYSIS, step), T_ANALYSIS, step
        )


@pytest.fixture(scope="module")
def ifs_zarr(tmp_path_factory):
    """Index, build refs and convert the synthetic IFS files end to end."""
    from zarr_creator.__main__ import cli

    tmp_path = tmp_path_factory.mktemp("ifs")
    src = tmp_path / "ecmwf"
    src.mkdir()
    _write_fixture(src)
    settings = s.Settings(
        src_grib_root_uri=str(src),
        refs_root_path=str(tmp_path / "refs"),
        member_id="control",
        max_hour=3,
        suite_name="ifs",
    )
    refs_dir = index_refs.build_indexes_and_refs(T_ANALYSIS, settings)
    out = tmp_path / "out"
    cli(
        [
            "--t-analysis",
            T_ANALYSIS.isoformat(),
            "--suite-name",
            "ifs",
            "--refs-root-path",
            settings.refs_root_path,
            "--member-id",
            "control",
            "--dst-zarr-output-path",
            f"{out}/{{suite_name}}/{{t_analysis}}/{{dataset_id}}.zarr",
        ]
    )
    return dict(
        src=src,
        refs_dir=refs_dir,
        ds=xr.open_zarr(out / "ifs" / "2026-09-28T000000Z" / "ifs.zarr"),
    )


def test_index_files_kept_out_of_source(ifs_zarr):
    assert not list(ifs_zarr["src"].glob("*.index"))
    refs_dir = Path(ifs_zarr["refs_dir"])
    assert sorted(p.name for p in refs_dir.glob("*.json")) == [
        "hybrid.json",
        "isobaricInhPa.json",
        "surface.json",
    ]
    assert len(list((refs_dir / "index").glob("*.index"))) == 2


def test_contract_variables_and_dims(ifs_zarr):
    ds = ifs_zarr["ds"]
    expected = {
        **{
            name: ("time", "prediction_timedelta", "latitude", "longitude")
            for name in config_ifs.SURFACE_VARIABLES
        },
        **{
            name: ("time", "prediction_timedelta", "level", "latitude", "longitude")
            for name in config_ifs.PRESSURE_VARIABLES
        },
        **{name: ("latitude", "longitude") for name in config_ifs.STATIC_VARIABLES},
    }
    data_vars = {
        name: ds[name].dims
        for name in ds.data_vars
        if name != config_ifs.PROJECTION_IDENTIFIER
    }
    assert data_vars == expected


def test_contract_coords(ifs_zarr):
    ds = ifs_zarr["ds"]
    np.testing.assert_array_equal(
        ds.time.values, np.array(["2026-09-28T00:00"], dtype="datetime64[ns]")
    )
    np.testing.assert_array_equal(
        ds.prediction_timedelta.values,
        np.array([0, 3], dtype="timedelta64[h]").astype("timedelta64[ns]"),
    )
    assert ds.level.values.tolist() == config_ifs.PRESSURE_LEVELS
    assert ds.level.attrs["units"] == "hPa"
    np.testing.assert_allclose(ds.latitude, np.arange(40.25, 71.76, 0.25))
    np.testing.assert_allclose(ds.longitude, np.arange(-19.5, 32.01, 0.25))


def _step(ds, step):
    return ds.isel(time=0).sel(prediction_timedelta=np.timedelta64(step, "h"))


def _assert_field(da, expected):
    xr.testing.assert_allclose(
        da.reset_coords(drop=True), expected.transpose(*da.dims), rtol=1e-6
    )


def test_values_regridded_linearly(ifs_zarr):
    ds = ifs_zarr["ds"]
    lat, lon = ds.latitude, ds.longitude

    msl = _step(ds, 3).mean_sea_level_pressure
    _assert_field(msl, surface_value("msl", lat, lon, 3))
    w = _step(ds, 0).vertical_velocity.sel(level=850)
    _assert_field(w, pressure_value("w", 850, lat, lon, 0))
    # statics come from the first step
    _assert_field(ds.geopotential_at_surface, surface_value("z", lat, lon, 0))
    # delivered levels not needed by ANNA are dropped
    assert 500 in FIXTURE_LEVELS and 500 not in ds.level


def test_geopotential_from_geopotential_height(ifs_zarr):
    ds = ifs_zarr["ds"]
    z = _step(ds, 3).geopotential.sel(level=925)
    _assert_field(
        z, config_ifs.G0 * pressure_value("gh", 925, ds.latitude, ds.longitude, 3)
    )
    assert z.attrs["units"] == "m**2 s**-2"


def test_600hpa_interpolated_in_log_pressure(ifs_zarr):
    ds = ifs_zarr["ds"]
    assert 600 not in FIXTURE_LEVELS
    weight = np.log(600 / 500) / np.log(700 / 500)
    t = _step(ds, 0).temperature.sel(level=600)
    t500, t700 = (
        pressure_value("t", level, ds.latitude, ds.longitude, 0) for level in (500, 700)
    )
    _assert_field(t, (1 - weight) * t500 + weight * t700)


def test_specific_humidity_from_relative_humidity(ifs_zarr):
    ds = ifs_zarr["ds"]
    # q is nonlinear in r and t, so compare where the 0.25 deg grid coincides
    # with the 0.5 deg source grid (no horizontal interpolation)
    on_source = dict(
        latitude=ds.latitude[(ds.latitude * 2) % 1 == 0],
        longitude=ds.longitude[(ds.longitude * 2) % 1 == 0],
    )
    lat, lon = on_source["latitude"], on_source["longitude"]
    q = _step(ds, 3).specific_humidity.sel(level=850, **on_source)
    expected = config_ifs.specific_humidity_from_relative_humidity(
        pressure_value("r", 850, lat, lon, 3),
        pressure_value("t", 850, lat, lon, 3),
        850e2,
    )
    _assert_field(q, expected)
    assert float(q.min()) > 0 and float(q.max()) < 0.03


def test_saturation_vapour_pressure():
    esat = config_ifs.saturation_vapour_pressure
    # triple point, and reference values over water (300 K) and ice (240 K)
    np.testing.assert_allclose(esat(np.array(273.16)), 611.21)
    np.testing.assert_allclose(esat(np.array(300.0)), 3536.8, rtol=0.01)
    np.testing.assert_allclose(esat(np.array(240.0)), 27.26, rtol=0.02)
    # the mixed-phase blend lies between the ice and water values
    t = np.array(260.0)
    water = config_ifs._tetens(t, **config_ifs.ESAT_WATER)
    ice = config_ifs._tetens(t, **config_ifs.ESAT_ICE)
    assert ice < esat(t) < water


def test_specific_humidity_saturated_at_1000hpa():
    # saturated air at 20 degC and 1000 hPa holds about 14.7 g/kg
    q = config_ifs.specific_humidity_from_relative_humidity(100.0, 293.15, 1e5)
    np.testing.assert_allclose(q, 0.0147, rtol=0.02)


def test_regrid_rejects_uncovered_box():
    ds = xr.Dataset(
        {"a": (("latitude", "longitude"), np.zeros((3, 3)))},
        coords=dict(latitude=[50.0, 51.0, 52.0], longitude=[0.0, 1.0, 2.0]),
    )
    with pytest.raises(ValueError, match="doesn't cover"):
        config_ifs.regrid_to_boundary_box(ds)


def test_ifs_suite_registered():
    suite = get_suite("ifs")
    assert suite.analysis_interval_hours == 6
    assert suite.index_next_to_source is False
    assert get_suite("dini").index_next_to_source is True
    with pytest.raises(ValueError, match="Unsupported suite"):
        get_suite("nope")
