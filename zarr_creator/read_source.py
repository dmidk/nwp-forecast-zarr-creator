#!/usr/bin/env python
# -*- coding: utf-8 -*-
import datetime
import os
from pathlib import Path

import isodate
import xarray as xr
from loguru import logger

REFS_ROOT_PATH = os.getenv("REFS_ROOT_PATH")

if REFS_ROOT_PATH is None:
    raise ValueError(
        "Environment variable REFS_ROOT_PATH must be set to the root path of "
        "gribscan reference files (i.e. the .jsons files created by gribscan)"
    )


def read_level_type_data(t_analysis: datetime.datetime, level_type: str) -> xr.Dataset:
    if t_analysis.tzinfo is None:
        t_analysis = t_analysis.replace(tzinfo=datetime.timezone.utc)
    t_analysis_utc = t_analysis.astimezone(datetime.timezone.utc)

    member_id = os.getenv("MEMBER_ID", "CONTROL__dmi")

    t_str = t_analysis_utc.strftime("%Y-%m-%dT%H%MZ")
    fp = Path(REFS_ROOT_PATH) / member_id / f"{t_str}.jsons" / f"{level_type}.json"

    logger.info(f"Reading {t_analysis} {level_type} data from {fp}")
    ds = xr.open_zarr(f"reference::{str(fp)}")

    # copy over cf standard-names where eccodes provides them
    for var_name in ds.data_vars:
        if "cfName" in ds[var_name].attrs:
            ds[var_name].attrs["standard_name"] = ds[var_name].attrs["cfName"]

        # https://www.nco.ncep.noaa.gov/pmb/docs/grib2/grib2_doc/grib2_table4-2-0-4.shtml
        if var_name == "swavr":
            ds[var_name].attrs["standard_name"] = "surface_downwelling_shortwave_flux"
            ds[var_name].attrs["long_name"] = "Surface downwelling shortwave flux"
            ds[var_name].attrs["units"] = "W m-2"
        elif var_name == "swavr_accum":
            ds[var_name].attrs[
                "long_name"
            ] = "Accumulated surface downwelling shortwave flux"
            ds[var_name].attrs["units"] = "J m-2"
        # https://www.nco.ncep.noaa.gov/pmb/docs/grib2/grib2_doc/grib2_table4-2-0-5.shtml
        elif var_name == "lwavr":
            # the parameterCategory is 5 (radiation) and the parameterNumber is
            # 4 ("Upward Longwave Radiation Flux"), but looking at the data
            # (near-zero where there is cloud, negative otherwise)
            # it seems to be net downward longwave flux, not upward flux as the
            # parameter name suggests.
            ds[var_name].attrs["standard_name"] = "surface_net_downward_longwave_flux"
            ds[var_name].attrs["long_name"] = "Surface downwelling longwave flux"
            ds[var_name].attrs["units"] = "W m-2"
        elif var_name == "lwavr_accum":
            # the accumulated values however are positive where there is no
            # cloud, which suggests to me to the flux is upwards
            ds[var_name].attrs[
                "long_name"
            ] = "Accumulated surface net upward longwave flux"
            ds[var_name].attrs["units"] = "J m-2"

    if level_type == "heightAboveGround":
        # land-sea mask is given for each timestep even though it doesn't
        # change, let's remove the time dimension
        ds["lsm"] = ds.isel(time=0).lsm

    # add cf-complicant projection information
    _add_projection_info(ds)

    # set cf-compliant standard_name for axes time, x and y
    ds.time.attrs["standard_name"] = "time"
    ds.x.attrs["standard_name"] = "projection_x_coordinate"
    ds.y.attrs["standard_name"] = "projection_y_coordinate"

    return ds


# based on
# https://opendatadocs.dmi.govcloud.dk/Data/Forecast_Data_Weather_Model_HARMONIE_DINI_IG,
# but modified to include USAGE section with BBOX that cartopy requires
DINI_CRS_WKT = """
PROJCRS["DMI HARMONIE DINI lambert projection",
    BASEGEOGCRS["DMI HARMONIE DINI lambert CRS",
        DATUM["DMI HARMONIE DINI lambert datum",
            ELLIPSOID["Sphere", 6371229, 0,
                LENGTHUNIT["metre", 1,
                    ID["EPSG", 9001]
                ]
            ]
        ],
        PRIMEM["Greenwich", 0,
            ANGLEUNIT["degree", 0.0174532925199433,
                ID["EPSG", 9122]
            ]
        ]
    ],
    CONVERSION["Lambert Conic Conformal (2SP)",
        METHOD["Lambert Conic Conformal (2SP)",
            ID["EPSG", 9802]
        ],
        PARAMETER["Latitude of false origin", 55.5,
            ANGLEUNIT["degree", 0.0174532925199433],
            ID["EPSG", 8821]
        ],
        PARAMETER["Longitude of false origin", -8,
            ANGLEUNIT["degree", 0.0174532925199433],
            ID["EPSG", 8822]
        ],
        PARAMETER["Latitude of 1st standard parallel", 55.5,
            ANGLEUNIT["degree", 0.0174532925199433],
            ID["EPSG", 8823]
        ],
        PARAMETER["Latitude of 2nd standard parallel", 55.5,
            ANGLEUNIT["degree", 0.0174532925199433],
            ID["EPSG", 8824]
        ],
        PARAMETER["Easting at false origin", 0,
            LENGTHUNIT["metre", 1],
            ID["EPSG", 8826]
        ],
        PARAMETER["Northing at false origin", 0,
            LENGTHUNIT["metre", 1],
            ID["EPSG", 8827]
        ]
    ],
    CS[Cartesian, 2],
    AXIS["(E)", east,
        ORDER[1],
        LENGTHUNIT["Metre", 1]
    ],
    AXIS["(N)", north,
        ORDER[2],
        LENGTHUNIT["Metre", 1]
    ],
    USAGE[
        AREA["Denmark and surrounding regions"],
        BBOX[37, -43, 70, 40],
        SCOPE["DINI Harmonie forecast projection"]
    ]
]

"""


def _add_projection_info(ds):
    PROJECTION_IDENTIFIER = "dini_projection"
    logger.info(
        f"Adding projection information to dataset with identifier {PROJECTION_IDENTIFIER}"
    )
    ds[PROJECTION_IDENTIFIER] = xr.DataArray()
    ds[PROJECTION_IDENTIFIER].attrs["crs_wkt"] = "".join(DINI_CRS_WKT.splitlines())

    for var_name in ds.data_vars:
        ds[var_name].attrs["grid_mapping"] = PROJECTION_IDENTIFIER


def merge_level_specific_params(ds, true_param, level, short_name):
    # select all levels that are not in the list, these are the ones that won't have nan values
    keep_levels = [lev for lev in ds[true_param].level.values if lev != level]
    da_subset = ds[true_param].sel(level=keep_levels)

    da_special = ds[short_name]
    da_special["level"] = level

    da = xr.concat([da_subset, da_special], dim="level")
    return da


def main():
    import argparse

    argparser = argparse.ArgumentParser(
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    argparser.add_argument(
        "--analysis_time", type=isodate.parse_datetime, required=True
    )
    argparser.add_argument("--level_type", default="heightAboveGround")

    args = argparser.parse_args()

    ds = read_level_type_data(t_analysis=args.analysis_time, level_type=args.level_type)

    print(ds)


if __name__ == "__main__":
    main()
