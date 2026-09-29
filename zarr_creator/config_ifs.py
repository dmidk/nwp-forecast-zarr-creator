"""ECMWF IFS (medium-range control forecast, formerly HRES) suite.

Source files are ECPDS deliveries on Scale in ``/dmidata/cache/mdcprd/gdb/ecmwf/``,
one file per (cycle, step)::

    dei_dj_ifs-ens-cf_od_oper_fc_<base>_<valid>_<step>h

e.g. ``dei_dj_ifs-ens-cf_od_oper_fc_20260928T000000Z_20260928T030000Z_3h``.
The fields are on a regular 0.1 deg lat/lon grid (2101x901, 90N..0N,
120W..90E), mostly GRIB1 with a few GRIB2 messages mixed in.

The output is a single ``ifs.zarr`` per cycle laid out as the ANNA boundary
input expects (see the contract in the header of
``mlwm-deployment/configurations/ANNA/configs/ifs_7deg_model1_config.yaml``):
ERA5/WeatherBench2 variable names, dims ``time`` (analysis time, length 1),
``prediction_timedelta``, ``level`` (hPa), ``latitude`` and ``longitude``,
regridded to 0.25 deg over the ANNA boundary box.
"""

import datetime
from collections import OrderedDict

import numpy as np
import xarray as xr

FILENAME_PREFIX = "dei_dj_ifs-ens-cf_od_oper_fc"
STEP_HOURS = 3

ANALYSIS_INTERVAL_HOURS = 6
# Delivery lag before a cycle's first 0..DEFAULT_MAX_HOUR files are expected
# to be complete. To be tuned against actual arrival times on Scale.
LAG_HOURS = 7
DEFAULT_MAX_HOUR = 72
DEFAULT_MEMBER_ID = "control"
# the source is the shared ECMWF cache, keep the index files with the refs
INDEX_NEXT_TO_SOURCE = False

# ANNA boundary box: DANRA extent (47.65..64.42N, -12.14..24.58E) plus the
# 7.19 deg domain-cropping margin, rounded outward to the 0.25 deg grid
LAT_MIN, LAT_MAX = 40.25, 71.75
LON_MIN, LON_MAX = -19.5, 32.0
GRID_RESOLUTION = 0.25

PRESSURE_LEVELS = [100, 200, 400, 600, 700, 850, 925, 1000]

PROJECTION_IDENTIFIER = "latitude_longitude"
# IFS GRIB1 fields are defined on a sphere with radius 6367470 m
PROJECTION_WKT = """
GEOGCRS["ECMWF IFS spherical lat/lon",
    DATUM["ECMWF IFS sphere",
        ELLIPSOID["Sphere", 6367470, 0,
            LENGTHUNIT["metre", 1]
        ]
    ],
    PRIMEM["Greenwich", 0,
        ANGLEUNIT["degree", 0.0174532925199433]
    ],
    CS[ellipsoidal, 2],
    AXIS["latitude", north,
        ORDER[1],
        ANGLEUNIT["degree", 0.0174532925199433]
    ],
    AXIS["longitude", east,
        ORDER[2],
        ANGLEUNIT["degree", 0.0174532925199433]
    ]
]
""".strip()


def _analysis_str(t: datetime.datetime) -> str:
    return t.astimezone(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def grib_filename(t_analysis: datetime.datetime, step: int) -> str:
    t_valid = t_analysis + datetime.timedelta(hours=step)
    return (
        f"{FILENAME_PREFIX}_{_analysis_str(t_analysis)}_{_analysis_str(t_valid)}"
        f"_{step}h"
    )


def grib_file_groups(
    t_analysis: datetime.datetime, max_hour: int, member_id: str
) -> dict[str, list[str]]:
    # member_id isn't part of the IFS file names (control forecast only)
    steps = range(0, max_hour + 1, STEP_HOURS)
    return {"dj": [grib_filename(t_analysis, step) for step in steps]}


def make_magician():
    from gribscan.magician import IFSMagician

    class LevelTypeIFSMagician(IFSMagician):
        """IFS magician with one dataset per ``typeOfLevel``.

        gribscan's IFS magician splits into ``atm2d``/``atm3d``, whereas the
        conversion reads refs per level type (``surface.json``,
        ``isobaricInhPa.json``, ...) as for HARMONIE.
        """

        def m2dataset(self, meta):
            return meta["attrs"]["typeOfLevel"]

    return LevelTypeIFSMagician()


def _surface(short_name):
    return lambda ds: ds[short_name]


def _static(short_name):
    # statics are given at every step, keep the first one
    return lambda ds: ds[short_name].isel(time=0, drop=True)


# Pressure levels in the `dei_dj` files (GRIB1, checked on the 3h file of the
# 2026-09-28T00Z cycle): 50, 70, 100, 150, 200, 250, 300, 400, 500, 700, 850,
# 925, 950 and 1000 hPa, with gh, t, r, u, v and w. There is no 600 hPa and no
# specific humidity, so those are derived (see `_on_target_levels` and
# `specific_humidity_from_relative_humidity`).

G0 = 9.80665  # m s-2, used by ECMWF to define geopotential height
EPSILON = 0.621981  # R_d / R_v as in the IFS

# IFS saturation vapour pressure constants (IFS documentation, Part IV:
# Physical processes, "Moist processes"), Teten's formula over water and ice
ESAT_A1 = 611.21  # Pa
ESAT_T0 = 273.16  # K
ESAT_WATER = dict(a3=17.502, a4=32.19)
ESAT_ICE = dict(a3=22.587, a4=-0.7)
T_ICE = ESAT_T0 - 23.0  # below this saturation is w.r.t. ice only


def _tetens(t, a3, a4):
    return ESAT_A1 * np.exp(a3 * (t - ESAT_T0) / (t - a4))


def saturation_vapour_pressure(t):
    """IFS mixed-phase saturation vapour pressure (Pa) at temperature ``t`` (K).

    Over water above 0 degC, over ice below -23 degC and a quadratic blend in
    between, as the IFS uses when computing relative humidity.
    """
    alpha = np.clip((t - T_ICE) / (ESAT_T0 - T_ICE), 0, 1) ** 2
    return alpha * _tetens(t, **ESAT_WATER) + (1 - alpha) * _tetens(t, **ESAT_ICE)


def specific_humidity_from_relative_humidity(r, t, p):
    """Specific humidity (kg/kg) from IFS relative humidity ``r`` (%).

    Inverts the IFS definition of relative humidity, e / e_sat with e_sat
    over the water/ice mix, at temperature ``t`` (K) and pressure ``p`` (Pa).
    """
    e = r / 100.0 * saturation_vapour_pressure(t)
    return EPSILON * e / (p - (1 - EPSILON) * e)


def _on_target_levels(da: xr.DataArray) -> xr.DataArray:
    """Select `PRESSURE_LEVELS`, interpolating missing ones linearly in ln(p).

    Only 600 hPa is missing from the source, and it's interpolated between
    500 and 700 hPa.
    """
    available = da.level.values
    levels = []
    for level in PRESSURE_LEVELS:
        if level in available:
            levels.append(da.sel(level=level))
            continue
        below = available[available < level].max()
        above = available[available > level].min()
        weight = np.log(level / below) / np.log(above / below)
        interpolated = (1 - weight) * da.sel(level=below, drop=True) + weight * da.sel(
            level=above, drop=True
        )
        levels.append(interpolated.assign_coords(level=level))
    da_levels = xr.concat(levels, dim="level")
    da_levels.attrs = da.attrs
    return da_levels


def _pressure(short_name):
    return lambda ds: _on_target_levels(ds[short_name])


def _geopotential(ds: xr.Dataset) -> xr.DataArray:
    da = _on_target_levels(ds["gh"] * G0)
    da.attrs = dict(
        units="m**2 s**-2",
        long_name="Geopotential",
        standard_name="geopotential",
        comment="geopotential height (gh) times g0 = 9.80665 m s-2",
    )
    return da


def _specific_humidity(ds: xr.Dataset) -> xr.DataArray:
    p = ds.level * 100.0  # hPa -> Pa
    q = specific_humidity_from_relative_humidity(ds["r"], ds["t"], p)
    da = _on_target_levels(q)
    da.attrs = dict(
        units="kg kg**-1",
        long_name="Specific humidity",
        standard_name="specific_humidity",
        comment="derived from relative humidity (r) and temperature (t) with "
        "the IFS mixed-phase saturation vapour pressure",
    )
    return da


# ERA5/WeatherBench2 name -> IFS shortName
SURFACE_VARIABLES = {
    "mean_sea_level_pressure": "msl",
    "2m_temperature": "2t",
    "10m_u_component_of_wind": "10u",
    "10m_v_component_of_wind": "10v",
    "surface_pressure": "sp",
}
STATIC_VARIABLES = {
    "land_sea_mask": "lsm",
    "geopotential_at_surface": "z",
}
PRESSURE_VARIABLES = {
    "geopotential": _geopotential,
    "temperature": _pressure("t"),
    "specific_humidity": _specific_humidity,
    "u_component_of_wind": _pressure("u"),
    "v_component_of_wind": _pressure("v"),
    # IFS `w` on pressure levels is omega (Pa s-1), as ERA5 vertical_velocity
    "vertical_velocity": _pressure("w"),
}

DATA_COLLECTION = OrderedDict(
    ifs=[
        dict(
            level_type="surface",
            variables={
                **{name: _surface(sn) for name, sn in SURFACE_VARIABLES.items()},
                **{name: _static(sn) for name, sn in STATIC_VARIABLES.items()},
            },
        ),
        dict(
            level_type="isobaricInhPa",
            variables=PRESSURE_VARIABLES,
        ),
    ]
)

# keep gribscan's `level` name, as mllam-data-prep expects for the boundary
LEVEL_DIM_NAMES = {"isobaricInhPa": "level"}


def rechunk_to(ds: xr.Dataset) -> dict:
    # one full (small, 0.25 deg) field per chunk
    return dict(time=1, prediction_timedelta=1, level=1)


def _target_axis(vmin: float, vmax: float) -> np.ndarray:
    n = int(round((vmax - vmin) / GRID_RESOLUTION)) + 1
    return vmin + GRID_RESOLUTION * np.arange(n)


def regrid_to_boundary_box(ds: xr.Dataset) -> xr.Dataset:
    """Linearly interpolate onto the 0.25 deg grid covering the boundary box."""
    target_lat = _target_axis(LAT_MIN, LAT_MAX)
    target_lon = _target_axis(LON_MIN, LON_MAX)
    src_step = float(abs(ds.latitude.diff("latitude")).max())

    ds = ds.sortby("latitude").sortby("longitude")
    ds = ds.sel(
        latitude=slice(LAT_MIN - src_step, LAT_MAX + src_step),
        longitude=slice(LON_MIN - src_step, LON_MAX + src_step),
    )
    for dim, target in (("latitude", target_lat), ("longitude", target_lon)):
        if ds[dim].min() > target.min() or ds[dim].max() < target.max():
            raise ValueError(
                f"IFS source grid ({float(ds[dim].min())}..{float(ds[dim].max())}) "
                f"doesn't cover the target {dim} range "
                f"({target.min()}..{target.max()})"
            )
    ds_regridded = ds.interp(latitude=target_lat, longitude=target_lon)
    for dim in ("latitude", "longitude"):
        ds_regridded[dim].attrs = ds[dim].attrs
    return ds_regridded


def transform_part(ds: xr.Dataset, t_analysis: datetime.datetime) -> xr.Dataset:
    """Bring a converted IFS part into the layout of the ANNA boundary contract."""
    ds = ds.rename(lat="latitude", lon="longitude")
    for var_name in ds.data_vars:
        # set by the gribscan IFS magician, refers to the old names
        ds[var_name].attrs.pop("coordinates", None)

    ds = regrid_to_boundary_box(ds)

    t0 = np.datetime64(
        t_analysis.astimezone(datetime.timezone.utc).replace(tzinfo=None), "ns"
    )
    ds = ds.assign_coords(prediction_timedelta=("time", (ds.time.values - t0)))
    ds = ds.swap_dims(time="prediction_timedelta").drop_vars("time")
    ds.prediction_timedelta.attrs["long_name"] = "lead time"
    # only the forecast fields get the (length 1) analysis-time dim; the
    # statics must stay 2D
    ds = ds.assign_coords(time=("time", [t0]))
    ds.time.attrs["long_name"] = "analysis time"
    ds.time.attrs["standard_name"] = "forecast_reference_time"
    for var_name in list(ds.data_vars):
        if "prediction_timedelta" in ds[var_name].dims:
            ds[var_name] = ds[var_name].expand_dims(time=ds.time)

    dim_order = ["time", "prediction_timedelta", "level", "latitude", "longitude"]
    for var_name in ds.data_vars:
        dims = [d for d in dim_order if d in ds[var_name].dims]
        ds[var_name] = ds[var_name].transpose(*dims)

    ds.level.attrs["units"] = "hPa"
    ds.level.attrs["long_name"] = "pressure level"
    return ds
