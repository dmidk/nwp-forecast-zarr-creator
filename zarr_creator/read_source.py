#!/usr/bin/env python
# -*- coding: utf-8 -*-
import datetime
from pathlib import Path

import gribscan
import isodate
import pkg_resources
import xarray as xr
from loguru import logger

# set the eccodes definitions path to use the definitions included with this
# repo. We need to do this because for example for land-sea mask
# (discipline=2,parameterCategory=0,parameterNumber=0) although this
# set of values is the WMO GRIB2 standard this isn't set as land-sea mask in
# all versions of eccodes. In our case we call this `lsm` as the short name.
# XXX: eventually we should completely remove the use of short-names and instead
# map from discipline,parameterCategory,parameterNumber to cf standard names)
local_defns_path = Path(__file__).parent.parent / "eccodes_definitions"
gribscan.eccodes.codes_set_definitions_path(
    f"{local_defns_path}:/usr/share/eccodes/definitions"
)

# pkg_resources.path requires that we provide the path to a file, so we give a
# temporary filename (pkg_resources.path() will create and immediately delete
# the file, because the python object representing the path is immediately
# deleted when calling .parent)
local_defns_path = pkg_resources.path(
    "zarr_creator.eccodes_definitions", "__tmp__"
).parent


def read_level_type_data(t_analysis: datetime.datetime, level_type: str) -> xr.Dataset:
    t_str = isodate.datetime_isoformat(t_analysis).replace(":", "")
    assert t_str.endswith("Z")
    fp = f"/home/ec2-user/nwp-forecast-zarr-creator/refs/CONTROL__dmi/{t_str}.jsons/{level_type}.json"
    logger.info(f"Reading {t_analysis} {level_type} data from {fp}")
    ds = xr.open_zarr(f"reference::{fp}")

    # copy over cf standard-names where eccodes provides them
    for var_name in ds.data_vars:
        if "cfName" in ds[var_name].attrs:
            ds[var_name].attrs["standard_name"] = ds[var_name].attrs["cfName"]

    if level_type == "heightAboveGround":
        # u-wind @ 10m and 100m are given as their own variables... same for v-wind
        ds = _merge_special_fields(ds)

        # land-sea mask is given for each timestep even though it doesn't
        # change, let's remove the time dimension
        ds["lsm"] = ds.isel(time=0).ls

    # add cf-complicant projection information
    _add_projection_info(ds)

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


def _merge_special_fields(ds):
    # u-wind component in general has paramter-id 131, but at 10m altitude is
    # parameter 165 and at 100m altitude is parameter 228246:
    #    shortName   paramId     level
    #    10u         165         10
    #    u           131         50
    #    100u        228246      100
    #    u           131         150
    #    u           131         250
    #    u           131         350
    #    u           131         450
    # same for v-wind component (in general paramId 132, 166 @ 10m, 228247 @ 100m):
    #    shortName   paramId     level
    #    10v         166         10
    #    v           132         50
    #    100v        228247      100
    #    v           132         150
    #    v           132         250
    #    v           132         350
    #    v           132         450

    # this means that `u` and `v` actually have nan values at 10m and 100m
    # altitudes, which we should replace with the values from `10u` and `10v`
    # and `100u` and `100v` respectively

    ds_copy = ds.copy()

    special_params = {
        "u": {
            "10u": 10,
            "100u": 100,
        },
        "v": {
            "10v": 10,
            "100v": 100,
        },
        "t": {
            "2t": 2,
        },
    }

    data_arrays = {}
    for true_param, special_param_info in special_params.items():
        da = ds[true_param]

        for special_param, level in special_param_info.items():
            keep_levels = [lev for lev in da.level.values if lev != level]
            da_subset = da.sel(level=keep_levels)

            da_special = ds[special_param]
            da_special["level"] = level

            da = xr.concat([da_subset, da_special], dim="level")
        data_arrays[true_param] = da

    ds_copy = xr.merge(
        list(data_arrays.values()) + [ds.drop_vars(list(special_params.keys()))]
    )
    return ds_copy


def merge_level_specific_params(ds, true_param, level, short_name):
    # select all levels that are not in the list, these are the ones that won't have nan values
    keep_levels = [lev for lev in ds[true_param].level.values if lev != level]
    da_subset = ds[true_param].sel(level=keep_levels)

    da_special = ds[short_name]
    da_special["level"] = level

    da = xr.concat([da_subset, da_special], dim="level")
    return da
