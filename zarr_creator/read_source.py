#!/usr/bin/env python
# -*- coding: utf-8 -*-
import datetime
from pathlib import Path

import isodate
import xarray as xr
from loguru import logger


def read_level_type_data(
    t_analysis: datetime.datetime,
    level_type: str,
    projection_identifier: str,
    projection_wkt: str,
    refs_root_path: str | None = None,
    member_id: str | None = None,
) -> xr.Dataset:
    if refs_root_path is None or member_id is None:
        # Resolved lazily (not at import) so `--help` and unrelated
        # commands work without the environment configured.
        from .settings import load_settings

        settings = load_settings()
        if refs_root_path is None:
            refs_root_path = settings.refs_root_path
        if member_id is None:
            member_id = settings.member_id
    if t_analysis.tzinfo is None:
        t_analysis = t_analysis.replace(tzinfo=datetime.timezone.utc)
    t_analysis_utc = t_analysis.astimezone(datetime.timezone.utc)

    t_str = t_analysis_utc.strftime("%Y-%m-%dT%H%MZ")
    fp = Path(refs_root_path) / member_id / f"{t_str}.jsons" / f"{level_type}.json"

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
    _add_projection_info(ds, projection_identifier, projection_wkt)

    # set cf-compliant standard_name for axes time, x and y
    ds.time.attrs["standard_name"] = "time"
    ds.x.attrs["standard_name"] = "projection_x_coordinate"
    ds.y.attrs["standard_name"] = "projection_y_coordinate"

    return ds


def _add_projection_info(ds, projection_identifier: str, projection_wkt: str | None):
    if projection_wkt is None:
        raise ValueError("projection_wkt must be provided for the active suite")

    logger.info(
        f"Adding projection information to dataset with identifier {projection_identifier}"
    )
    ds[projection_identifier] = xr.DataArray()
    ds[projection_identifier].attrs["crs_wkt"] = "".join(projection_wkt.splitlines())

    for var_name in ds.data_vars:
        ds[var_name].attrs["grid_mapping"] = projection_identifier


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

    projection_identifier = "dummy_projection"
    projection_wkt = "dummy_wkt"

    ds = read_level_type_data(
        t_analysis=args.analysis_time,
        level_type=args.level_type,
        projection_identifier=projection_identifier,
        projection_wkt=projection_wkt,
    )

    print(ds)


if __name__ == "__main__":
    main()
