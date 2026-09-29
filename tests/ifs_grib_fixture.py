"""Write small synthetic IFS-like GRIB files for tests.

Mimics the ``dei_dj_ifs-ens-cf_od_oper_fc_*`` files on Scale: one file per
(cycle, step) with

- GRIB1 ``regular_ll`` surface fields and pressure-level fields (``gh``,
  ``t``, ``r``, ``u``, ``v``, ``w`` on the delivered levels, which don't
  include 600 hPa, and no ``q``)
- GRIB2 messages mixed in, like the real files: ``ptype`` on the surface and
  a few model-level (``hybrid``) fields, with longitudes in 0..360

The grid is coarse (0.5 deg) but covers the ANNA boundary box so the 0.25 deg
regridding can be exercised. Every field is linear in lat, lon and step (with
a realistic base value and scale per field), so linear interpolation onto the
target grid is exact at points shared with the source grid.
"""

import datetime

import eccodes
import numpy as np

LAT_FIRST, LAT_LAST = 75.0, 35.0
LON_FIRST, LON_LAST = -25.0, 40.0
INC = 0.5
NI = int(round((LON_LAST - LON_FIRST) / INC)) + 1
NJ = int(round((LAT_FIRST - LAT_LAST) / INC)) + 1

# shortName -> (base, scale)
SURFACE_FIELDS = {
    "msl": (101000.0, 10.0),
    "2t": (270.0, 0.2),
    "10u": (2.0, 0.05),
    "10v": (-1.0, 0.05),
    "sp": (95000.0, 20.0),
    "lsm": (0.2, 0.01),
    "z": (500.0, 10.0),
}
PRESSURE_FIELDS = {
    "gh": (0.0, 1.0),  # base per level, see `pressure_base`
    "t": (0.0, 0.1),
    "r": (50.0, 0.3),
    "u": (5.0, 0.1),
    "v": (-2.0, 0.1),
    "w": (0.05, 0.002),
}
# as delivered in dei_dj (no 600 hPa)
PRESSURE_LEVELS = (50, 70, 100, 150, 200, 250, 300, 400, 500, 700, 850, 925, 950, 1000)
HYBRID_SHORT_NAMES = ("t", "q")
HYBRID_LEVELS = (136, 137)


def field_value(lat, lon, step, base, scale):
    """Analytic field used for every message (linear in lat, lon and step)."""
    return base + scale * (lat + 0.1 * lon + step)


def pressure_base(short_name: str, level: int) -> float:
    """Level-dependent base value, roughly like a standard atmosphere."""
    if short_name == "gh":
        # geopotential height (gpm), decreasing with pressure
        return 16000.0 * np.log(1000.0 / level)
    if short_name == "t":
        return 215.0 + 0.07 * level
    return PRESSURE_FIELDS[short_name][0]


def surface_value(short_name, lat, lon, step):
    base, scale = SURFACE_FIELDS[short_name]
    return field_value(lat, lon, step, base, scale)


def pressure_value(short_name, level, lat, lon, step):
    scale = PRESSURE_FIELDS[short_name][1]
    return field_value(lat, lon, step, pressure_base(short_name, level), scale)


def _grid_keys(lon_first=LON_FIRST, lon_last=LON_LAST):
    return dict(
        Ni=NI,
        Nj=NJ,
        latitudeOfFirstGridPointInDegrees=LAT_FIRST,
        longitudeOfFirstGridPointInDegrees=lon_first,
        latitudeOfLastGridPointInDegrees=LAT_LAST,
        longitudeOfLastGridPointInDegrees=lon_last,
        iDirectionIncrementInDegrees=INC,
        jDirectionIncrementInDegrees=INC,
    )


def _write_message(f, sample, t_analysis, step, values, grid_keys, **keys):
    handle = eccodes.codes_grib_new_from_samples(sample)
    eccodes.codes_set_key_vals(handle, grid_keys)
    eccodes.codes_set(handle, "dataDate", int(t_analysis.strftime("%Y%m%d")))
    eccodes.codes_set(handle, "dataTime", int(t_analysis.strftime("%H%M")))
    eccodes.codes_set(handle, "step", step)
    for key, value in keys.items():
        eccodes.codes_set(handle, key, value)
    eccodes.codes_set_values(handle, np.asarray(values, dtype=float).ravel())
    eccodes.codes_write(handle, f)
    eccodes.codes_release(handle)


def _lat_lon():
    lats = np.linspace(LAT_FIRST, LAT_LAST, NJ)[:, None]
    lons = np.linspace(LON_FIRST, LON_LAST, NI)[None, :]
    return lats, lons


def write_ifs_file(path, t_analysis: datetime.datetime, step: int) -> None:
    lats, lons = _lat_lon()
    ones = np.ones((NJ, NI))
    grib1_grid = _grid_keys()
    # the real GRIB2 messages give longitudes in 0..360 (240 instead of -120)
    grib2_grid = _grid_keys(LON_FIRST % 360, LON_LAST % 360)
    with open(path, "wb") as f:
        for short_name in SURFACE_FIELDS:
            values = surface_value(short_name, lats, lons, step) * ones
            _write_message(
                f,
                "regular_ll_sfc_grib1",
                t_analysis,
                step,
                values,
                grib1_grid,
                shortName=short_name,
            )
        for short_name in PRESSURE_FIELDS:
            for level in PRESSURE_LEVELS:
                values = pressure_value(short_name, level, lats, lons, step) * ones
                _write_message(
                    f,
                    "regular_ll_pl_grib1",
                    t_analysis,
                    step,
                    values,
                    grib1_grid,
                    shortName=short_name,
                    level=level,
                )
        _write_message(
            f,
            "regular_ll_sfc_grib2",
            t_analysis,
            step,
            0 * ones,
            grib2_grid,
            shortName="ptype",
        )
        for short_name in HYBRID_SHORT_NAMES:
            for level in HYBRID_LEVELS:
                _write_message(
                    f,
                    "regular_ll_sfc_grib2",
                    t_analysis,
                    step,
                    ones,
                    grib2_grid,
                    typeOfLevel="hybrid",
                    level=level,
                    shortName=short_name,
                )
