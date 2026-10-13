"""Tests for the IFS suite (ANNA boundary source), using synthetic GRIB files."""

import datetime
from pathlib import Path

import numpy as np
import pytest
import xarray as xr

from tests.ifs_grib_fixture import LAT_FIRST, LAT_LAST, LON_FIRST, LON_LAST, NI, NJ
from tests.ifs_grib_fixture import PRESSURE_LEVELS as FIXTURE_LEVELS
from tests.ifs_grib_fixture import (
    calc_pressure_value,
    calc_surface_value,
    write_ifs_file,
)
from zarr_creator import config_ifs
from zarr_creator import settings as s
from zarr_creator.pipeline import index_refs, runner
from zarr_creator.suites import get_suite


def _make_utc(*args):
    """
    Build a timezone-aware UTC datetime.

    Parameters
    ----------
    *args : int
        Positional arguments for `datetime.datetime` (year, month, day, ...).

    Returns
    -------
    datetime.datetime
        The datetime with ``tzinfo`` set to UTC.
    """
    return datetime.datetime(*args, tzinfo=datetime.timezone.utc)


T_ANALYSIS = _make_utc(2026, 9, 28, 0)


def test_grib_filenames():
    """Check the IFS GRIB filenames generated for a cycle's forecast steps."""
    groups = config_ifs.get_grib_file_groups(T_ANALYSIS, 6, "control")
    assert groups == {
        "dj": [
            "dei_dj_ifs-ens-cf_od_oper_fc_20260928T000000Z_20260928T000000Z_0h",
            "dei_dj_ifs-ens-cf_od_oper_fc_20260928T000000Z_20260928T030000Z_3h",
            "dei_dj_ifs-ens-cf_od_oper_fc_20260928T000000Z_20260928T060000Z_6h",
        ]
    }
    names = config_ifs.get_grib_file_groups(_make_utc(2026, 9, 28, 12), 72, "control")[
        "dj"
    ]
    assert len(names) == 25
    # valid time rolls over the month
    assert names[-1].endswith("_20260928T120000Z_20261001T120000Z_72h")


@pytest.mark.parametrize(
    "suite_name, now, expected",
    [
        # dini/ig: 2h lag, 3-hourly. A cycle becomes "latest" exactly 2h after
        # it starts, which pins both the lag and the interval
        ("dini", (13, 30), (9,)),
        ("dini", (10, 59), (6,)),
        ("dini", (11, 0), (9,)),
        # ifs: 7h lag, 6-hourly
        ("ifs", (13, 30), (6,)),
        ("ifs", (12, 59), (0,)),
        ("ifs", (13, 0), (6,)),
    ],
)
def test_latest_analysis_time_per_suite(monkeypatch, suite_name, now, expected):
    """Check "latest" resolves using each suite's own lag and cycle interval."""
    monkeypatch.delenv("ANALYSIS_LAG_HOURS", raising=False)
    monkeypatch.delenv("ANALYSIS_INTERVAL_HOURS", raising=False)
    t = s.resolve_t_analysis_for_suite(
        "latest", suite_name, now=_make_utc(2026, 9, 28, *now)
    )
    assert t == _make_utc(2026, 9, 28, *expected)


def test_explicit_analysis_time_ignores_suite_lag():
    """Check an explicit analysis time is used as given, without suite lag."""
    now = _make_utc(2026, 9, 28, 13, 30)
    assert s.resolve_t_analysis_for_suite(
        "2026-09-28T00:00:00Z", "ifs", now=now
    ) == _make_utc(2026, 9, 28)


def test_compute_analysis_time_interval():
    """Check the analysis time is floored to the cycle interval after the lag."""
    assert s.compute_analysis_time(
        _make_utc(2026, 9, 28, 12, 59), lag_hours=7, interval_hours=6
    ) == _make_utc(2026, 9, 28, 0)
    assert s.compute_analysis_time(
        _make_utc(2026, 9, 28, 13), lag_hours=7, interval_hours=6
    ) == _make_utc(2026, 9, 28, 6)


def test_suite_defaults_apply_unless_set(monkeypatch):
    """Check suite defaults for max hour and member apply unless set in env."""
    monkeypatch.delenv("MAX_HOUR", raising=False)
    monkeypatch.delenv("MEMBER_ID", raising=False)
    monkeypatch.setenv("SUITE_NAME", "ifs")
    cfg = s.load_settings()
    assert (cfg.suite_name, cfg.max_hour, cfg.member_id) == (
        "ifs",
        s.DEFAULT_MAX_HOUR,
        "control",
    )
    # switching suite switches the defaults back
    s.set_suite(cfg, "dini")
    assert (cfg.max_hour, cfg.member_id) == (s.DEFAULT_MAX_HOUR, s.DEFAULT_MEMBER_ID)

    monkeypatch.setenv("MAX_HOUR", "12")
    cfg = s.load_settings()
    assert cfg.max_hour == 12 and cfg.member_id == "control"


def test_watch_uses_suite_cycle(tmp_path, monkeypatch):
    """Check the watch loop picks the latest cycle using the suite's lag."""
    cfg = s.Settings(
        refs_root_path=str(tmp_path), suite_name="ifs", member_id="control"
    )
    processed = []
    monkeypatch.setattr(
        runner, "process_one", lambda t, settings, **k: processed.append(t)
    )
    runner.poll_once(cfg, now=_make_utc(2026, 9, 28, 13, 30))
    assert processed == [_make_utc(2026, 9, 28, 6)]


def _write_fixture(src_dir, steps=(0, 3)):
    """
    Write synthetic IFS GRIB files for ``T_ANALYSIS`` into a directory.

    Parameters
    ----------
    src_dir : pathlib.Path
        Directory to write the files to, using the real IFS filenames.
    steps : tuple of int, optional
        Forecast steps (hours) to write a file for.
    """
    for step in steps:
        write_ifs_file(
            src_dir / config_ifs.make_grib_filename(T_ANALYSIS, step), T_ANALYSIS, step
        )


@pytest.fixture(scope="module")
def ifs_zarr(tmp_path_factory):
    """
    Index, build refs and convert the synthetic IFS files end to end.

    The conversion is run twice: once on the full native grid and once
    clipped to the ANNA boundary box.

    Parameters
    ----------
    tmp_path_factory : pytest.TempPathFactory
        Pytest factory for the module-scoped temporary directory.

    Returns
    -------
    dict
        ``src`` (source GRIB directory), ``refs_dir`` (directory with the
        refs), ``ds`` (the converted dataset) and ``ds_clipped`` (the
        converted dataset, clipped).
    """
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

    def convert(out, *extra_args):
        """
        Run the ``convert`` CLI for the IFS suite and open the result.

        Parameters
        ----------
        out : pathlib.Path
            Root directory for the output zarr.
        *extra_args : str
            Extra CLI arguments, e.g. the ``--clip-*`` flags.

        Returns
        -------
        xarray.Dataset
            The written ``ifs.zarr``, opened lazily.
        """
        cli(
            [
                "convert",
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
                *extra_args,
            ]
        )
        return xr.open_zarr(out / "ifs" / "2026-09-28T000000Z" / "ifs.zarr")

    return dict(
        src=src,
        refs_dir=refs_dir,
        ds=convert(tmp_path / "out"),
        # the ANNA boundary box
        ds_clipped=convert(
            tmp_path / "out_clipped",
            *("--clip-lat-min", "40", "--clip-lat-max", "72"),
            *("--clip-lon-min", "-27", "--clip-lon-max", "39.5"),
        ),
    )


def test_index_files_kept_out_of_source(ifs_zarr):
    """Check index files go next to the refs, not into the source directory."""
    assert not list(ifs_zarr["src"].glob("*.index"))
    refs_dir = Path(ifs_zarr["refs_dir"])
    assert sorted(p.name for p in refs_dir.glob("*.json")) == [
        "hybrid.json",
        "isobaricInhPa.json",
        "surface.json",
    ]
    assert len(list((refs_dir / "index").glob("*.index"))) == 2


def test_contract_variables_and_dims(ifs_zarr):
    """Check the output has exactly the contract variables with their dims."""
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
    """Check time, step, level and native lat/lon coordinates of the output."""
    ds = ifs_zarr["ds"]
    np.testing.assert_array_equal(
        ds.time.values, np.array(["2026-09-28T00:00"], dtype="datetime64[ns]")
    )
    np.testing.assert_array_equal(
        ds.prediction_timedelta.values,
        np.array([0, 3], dtype="timedelta64[h]").astype("timedelta64[ns]"),
    )
    # all delivered levels, as delivered (no 600 hPa)
    assert ds.level.values.tolist() == list(FIXTURE_LEVELS)
    assert ds.level.attrs["units"] == "hPa"
    # the native grid, in its native order (north to south)
    np.testing.assert_allclose(ds.latitude, np.linspace(LAT_FIRST, LAT_LAST, NJ))
    np.testing.assert_allclose(ds.longitude, np.linspace(LON_FIRST, LON_LAST, NI))
    assert not any(name.startswith("clip_") for name in ds.attrs)


def test_clip_keeps_one_point_beyond_each_bound(ifs_zarr):
    """Check clipping keeps one grid point beyond each bound and records them."""
    ds = ifs_zarr["ds_clipped"]
    # 0.5 deg fixture grid: one point beyond lat 40..72 and lon -27..39.5
    np.testing.assert_allclose(ds.latitude, np.arange(72.5, 39.49, -0.5))
    np.testing.assert_allclose(ds.longitude, np.arange(-27.5, 40.01, 0.5))
    assert {k: v for k, v in ds.attrs.items() if k.startswith("clip_")} == {
        "clip_lat_min": 40.0,
        "clip_lat_max": 72.0,
        "clip_lon_min": -27.0,
        "clip_lon_max": 39.5,
    }
    # values are untouched, the statics are clipped too
    _assert_field(
        _select_step(ds, 3).mean_sea_level_pressure,
        calc_surface_value("msl", ds.latitude, ds.longitude, 3),
    )
    assert ds.land_sea_mask.shape == (ds.latitude.size, ds.longitude.size)


def _make_grid_ds(lats, lons):
    """
    Build a small all-zero dataset on a lat/lon grid.

    Parameters
    ----------
    lats : list of float
        Latitude coordinate values.
    lons : list of float
        Longitude coordinate values.

    Returns
    -------
    xarray.Dataset
        Dataset with one variable ``a`` on ``(latitude, longitude)``.
    """
    return xr.Dataset(
        {"a": (("latitude", "longitude"), np.zeros((len(lats), len(lons))))},
        coords=dict(latitude=lats, longitude=lons),
    )


def test_clip_partial_bounds():
    """Check clipping with only some bounds set, or bounds beyond the grid."""
    ds = _make_grid_ds([3.0, 2.0, 1.0, 0.0], [0.0, 1.0, 2.0, 3.0])
    clipped = config_ifs.clip_to_bounds(ds, {"lat_min": 2.0})
    # unbounded sides keep the whole grid; one point beyond the bound
    assert clipped.latitude.values.tolist() == [3.0, 2.0, 1.0]
    assert clipped.longitude.values.tolist() == [0.0, 1.0, 2.0, 3.0]
    # a bound beyond the grid edge has no point to add
    clipped = config_ifs.clip_to_bounds(ds, {"lon_max": 3.0, "lon_min": -5.0})
    assert clipped.longitude.values.tolist() == [0.0, 1.0, 2.0, 3.0]


def test_clip_rejects_box_outside_grid():
    """Check clipping to a box that misses the grid raises a ValueError."""
    ds = _make_grid_ds([3.0, 2.0], [0.0, 1.0])
    with pytest.raises(ValueError, match="contain no latitude"):
        config_ifs.clip_to_bounds(ds, {"lat_min": 10.0})


def _select_step(ds, step):
    """
    Select the first analysis time and one forecast step.

    Parameters
    ----------
    ds : xarray.Dataset
        Converted IFS dataset.
    step : int
        Forecast step in hours.

    Returns
    -------
    xarray.Dataset
        The dataset at that time and step.
    """
    return ds.isel(time=0).sel(prediction_timedelta=np.timedelta64(step, "h"))


def _assert_field(da, expected):
    """
    Assert a field matches its expected values (relative tolerance 1e-6).

    Non-index coordinates of ``da`` are dropped and ``expected`` is
    transposed to the dims of ``da`` before comparing.

    Parameters
    ----------
    da : xarray.DataArray
        Field from the converted dataset.
    expected : xarray.DataArray
        Expected values, e.g. from the fixture's analytic fields.
    """
    xr.testing.assert_allclose(
        da.reset_coords(drop=True), expected.transpose(*da.dims), rtol=1e-6
    )


def test_values_on_native_grid(ifs_zarr):
    """Check surface, pressure-level and static values on the native grid."""
    ds = ifs_zarr["ds"]
    lat, lon = ds.latitude, ds.longitude

    msl = _select_step(ds, 3).mean_sea_level_pressure
    _assert_field(msl, calc_surface_value("msl", lat, lon, 3))
    w = _select_step(ds, 0).vertical_velocity.sel(level=850)
    _assert_field(w, calc_pressure_value("w", 850, lat, lon, 0))
    # statics come from the first step
    _assert_field(ds.geopotential_at_surface, calc_surface_value("z", lat, lon, 0))


def test_geopotential_from_geopotential_height(ifs_zarr):
    """Check geopotential is derived as g0 times geopotential height."""
    ds = ifs_zarr["ds"]
    z = _select_step(ds, 3).geopotential.sel(level=925)
    _assert_field(
        z, config_ifs.G0 * calc_pressure_value("gh", 925, ds.latitude, ds.longitude, 3)
    )
    assert z.attrs["units"] == "m**2 s**-2"


def test_specific_humidity_from_relative_humidity(ifs_zarr):
    """Check specific humidity is derived from relative humidity and T."""
    ds = ifs_zarr["ds"]
    lat, lon = ds.latitude, ds.longitude
    q = _select_step(ds, 3).specific_humidity.sel(level=850)
    expected = config_ifs.calc_specific_humidity_from_relative_humidity(
        calc_pressure_value("r", 850, lat, lon, 3),
        calc_pressure_value("t", 850, lat, lon, 3),
        850e2,
    )
    _assert_field(q, expected)
    assert float(q.min()) > 0 and float(q.max()) < 0.03


def test_saturation_vapour_pressure():
    """Check saturation vapour pressure against reference values."""
    esat = config_ifs.calc_saturation_vapour_pressure
    # triple point, and reference values over water (300 K) and ice (240 K)
    np.testing.assert_allclose(esat(np.array(273.16)), 611.21)
    np.testing.assert_allclose(esat(np.array(300.0)), 3536.8, rtol=0.01)
    np.testing.assert_allclose(esat(np.array(240.0)), 27.26, rtol=0.02)
    # the mixed-phase blend lies between the ice and water values
    t = np.array(260.0)
    water = config_ifs._calc_tetens(t, **config_ifs.ESAT_WATER)
    ice = config_ifs._calc_tetens(t, **config_ifs.ESAT_ICE)
    assert ice < esat(t) < water


def test_specific_humidity_saturated_at_1000hpa():
    """Check specific humidity of saturated air at 20 degC and 1000 hPa."""
    # saturated air at 20 degC and 1000 hPa holds about 14.7 g/kg
    q = config_ifs.calc_specific_humidity_from_relative_humidity(100.0, 293.15, 1e5)
    np.testing.assert_allclose(q, 0.0147, rtol=0.02)


def test_ifs_suite_registered():
    """Check the IFS suite is registered with its config constants."""
    suite = get_suite("ifs")
    # compared with the config constants too, so that a misspelled constant
    # (silently replaced by the HARMONIE default) fails here
    assert suite.analysis_interval_hours == config_ifs.ANALYSIS_INTERVAL_HOURS == 6
    assert suite.lag_hours == config_ifs.LAG_HOURS == 7
    assert suite.default_member_id == config_ifs.DEFAULT_MEMBER_ID == "control"
    assert suite.index_next_to_source is False
    assert suite.clip_to_bounds is config_ifs.clip_to_bounds
    assert get_suite("dini").index_next_to_source is True
    assert get_suite("dini").clip_to_bounds is None
    with pytest.raises(ValueError, match="Unsupported suite"):
        get_suite("nope")
