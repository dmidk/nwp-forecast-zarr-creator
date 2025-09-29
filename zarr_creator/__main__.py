#!/usr/bin/env python
# -*- coding: utf-8 -*-
import argparse
import sys

import isodate
import xarray as xr
from loguru import logger

from . import __version__
from .config import DATA_COLLECTION
from .read_source import read_level_type_data
from .write_zarr import write_zarr_to_s3

DEFAULT_ANALYSIS_TIME = "2025-02-17T01:00:00Z"
DEFAULT_FORECAST_DURATION = "PT3H"
DEFAULT_CHUNKING = dict(time=54, x=300, y=260)


def _setup_argparse():
    argparser = argparse.ArgumentParser(
        description="Create Zarr dataset from data-catalog (dmidc)",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    argparser.add_argument(
        "--t_analysis",
        default=DEFAULT_ANALYSIS_TIME,
        type=isodate.parse_datetime,
        help="Analysis time as ISO8601 string",
    )

    argparser.add_argument(
        "--verbose", action="store_true", help="Verbose output", default=False
    )

    argparser.add_argument("--log-level", default="INFO", help="The log level to use")

    argparser.add_argument("--log-file", default=None, help="The file to log to")

    return argparser


def cli(argv=None):
    """
    Run zarr creator
    """

    argparser = _setup_argparse()
    args = argparser.parse_args(argv)

    logger.remove()
    logger.add(sys.stderr, level=args.log_level.upper())

    parts = {}
    for part_id, part_details in DATA_COLLECTION.items():
        ds_part = xr.Dataset()
        for level_details in part_details:
            level_type = level_details["level_type"]
            variables = level_details["variables"]
            level_name_mapping = level_details.get("level_name_mapping", None)

            ds_level_type = read_level_type_data(
                t_analysis=args.t_analysis, level_type=level_type
            )
            for var_name, levels in variables.items():
                da = ds_level_type[var_name]

                if levels is None:
                    if level_name_mapping is None:
                        new_name = var_name
                    else:
                        new_name = level_name_mapping.format(var_name=var_name)
                    ds_part[new_name] = da
                elif level_name_mapping is None:
                    # assuming we're just selecting levels and not changing the name
                    da = da.sel(level=levels)
                    ds_part[var_name] = da
                else:
                    # mapping each level to a new variable name
                    for level in levels:
                        da_level = da.sel(level=level)
                        new_name = level_name_mapping.format(
                            level=level, var_name=var_name
                        )
                        ds_part[new_name] = da_level

                if "grid_mapping" in da.attrs:
                    ds_part[da.attrs["grid_mapping"]] = ds_level_type[
                        da.attrs["grid_mapping"]
                    ]

        # use "altitude" and "pressure" as dimension names instead of "level"
        if "level" in ds_part.dims:
            if level_type == "isobaricInhPa":
                ds_part = ds_part.rename({"level": "pressure"})
            elif level_type == "heightAboveGround":
                ds_part = ds_part.rename({"level": "altitude"})
            elif level_type == "heightAboveSea":
                ds_part = ds_part.rename({"level": "altitude"})
            else:
                raise NotImplementedError(f"Level type {level_type} not implemented")

        # check if any of the coordinates don't have any variables, if so drop them
        for coord in ds_part.coords:
            if all(coord not in ds_part[v].coords for v in list(ds_part.data_vars)):
                ds_part = ds_part.drop_vars(coord)

        parts[part_id] = ds_part

    for part_id, ds_part in parts.items():
        rechunk_to = dict(time=1, x=ds_part.x.size // 2, y=ds_part.y.size // 2)
        # check that with the chunking provided that the arrays exactly fit into the chunks
        for dim in rechunk_to:
            assert ds_part[dim].size % rechunk_to[dim] == 0

        # set zarr-creator version
        ds_part.attrs["zarr_creator_version"] = __version__

        write_zarr_to_s3(
            ds=ds_part,
            member="control",
            dataset_id=part_id,
            rechunk_to=rechunk_to,
            t_analysis=args.t_analysis,
        )


if __name__ == "__main__":
    with logger.catch(reraise=True):
        cli()
