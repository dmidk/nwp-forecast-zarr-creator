#!/usr/bin/env python
# -*- coding: utf-8 -*-
import argparse
import datetime
import sys
from pathlib import Path

import isodate
import xarray as xr
from loguru import logger

from . import __version__
from .config import DATA_COLLECTION
from .grib_definitions import set_local_eccodes_definitions_path
from .read_source import read_level_type_data
from .transforms import apply_variable_transforms, resolve_variable_transforms
from .write_zarr import write_output_zarrs

DEFAULT_ANALYSIS_TIME = "2025-02-17T01:00:00Z"
DEFAULT_FORECAST_DURATION = "PT3H"
DEFAULT_CHUNKING = dict(time=54, x=300, y=260)
LOCAL_COPY_STORAGE_PATH = Path("/tmp/dini-recent")
DEFAULT_VARIABLE_SCALE_FACTORS = {"z0m": 1.0 / 9.82}
DEFAULT_VARIABLE_RENAMES = {"z0m": "orography"}
DEFAULT_VARIABLE_ATTRIBUTE_UPDATES = {
    "z0m": {
        "units": "m",
        "standard_name": "surface_altitude",
        "cfName": "surface_altitude",
        "long_name": "Surface altitude (orography)",
        "name": "Surface altitude (orography)",
    }
}
DEFAULT_DROP_TIME_DIMENSION_FOR = ["orography"]


set_local_eccodes_definitions_path()


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
    argparser.add_argument(
        "--skip-s3-bucket-upload",
        action="store_true",
        help=(
            "If provided, skip uploading zarr outputs to the S3 bucket. "
            "A local copy is still written to /tmp/dini-recent."
        ),
    )

    argparser.add_argument(
        "--scale-variable",
        action="append",
        default=[],
        metavar="VARIABLE:FACTOR",
        help=(
            "Scale an output variable by a multiplicative factor (can be provided "
            "multiple times). Example: --scale-variable z0m:0.10183299389002037"
        ),
    )

    argparser.add_argument(
        "--rename-variable",
        action="append",
        default=[],
        metavar="OLD:NEW",
        help=(
            "Rename an output variable (can be provided multiple times). "
            "Example: --rename-variable z0m:orography"
        ),
    )

    argparser.add_argument(
        "--drop-time-dimension-for",
        action="append",
        default=[],
        metavar="VARIABLE",
        help=(
            "Drop the time dimension from a variable by selecting the first "
            "timestep (can be provided multiple times)."
        ),
    )

    return argparser


def cli(argv=None):
    """
    Run zarr creator
    """

    argparser = _setup_argparse()
    args = argparser.parse_args(argv)

    scale_map = DEFAULT_VARIABLE_SCALE_FACTORS.copy()
    for item in args.scale_variable:
        if ":" not in item:
            raise ValueError(
                "Invalid --scale-variable value. Expected format `VARIABLE:FACTOR`, "
                f"got `{item}`"
            )
        var_name, raw_factor = item.split(":", 1)
        if not var_name or not raw_factor:
            raise ValueError(
                "Invalid --scale-variable value. VARIABLE and FACTOR must both be non-empty"
            )
        try:
            factor = float(raw_factor)
        except ValueError as exc:
            raise ValueError(
                f"Invalid --scale-variable value. FACTOR must be numeric, got `{raw_factor}`"
            ) from exc
        scale_map[var_name] = factor

    rename_map = DEFAULT_VARIABLE_RENAMES.copy()
    for item in args.rename_variable:
        if ":" not in item:
            raise ValueError(
                "Invalid --rename-variable value. Expected format `OLD:NEW`, "
                f"got `{item}`"
            )
        old_name, new_name = item.split(":", 1)
        if not old_name or not new_name:
            raise ValueError(
                "Invalid --rename-variable value. OLD and NEW must both be non-empty"
            )
        rename_map[old_name] = new_name

    drop_time_dimension_for = [
        *DEFAULT_DROP_TIME_DIMENSION_FOR,
        *args.drop_time_dimension_for,
    ]

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

        (
            part_rename_map,
            part_scale_map,
            part_attrs_map,
            part_drop_time_dimension_for,
        ) = resolve_variable_transforms(
            ds_part,
            rename_map=rename_map,
            scale_map=scale_map,
            attrs_map=DEFAULT_VARIABLE_ATTRIBUTE_UPDATES,
            drop_time_dimension_for=drop_time_dimension_for,
        )

        ds_part = apply_variable_transforms(
            ds_part,
            rename_map=part_rename_map,
            scale_map=part_scale_map,
            attrs_map=part_attrs_map,
            drop_time_dimension_for=part_drop_time_dimension_for,
        )

        parts[part_id] = ds_part

    for part_id, ds_part in parts.items():
        rechunk_to = dict(time=1, x=ds_part.x.size // 2, y=ds_part.y.size // 2)
        # check that with the chunking provided that the arrays exactly fit into the chunks
        for dim in rechunk_to:
            assert ds_part[dim].size % rechunk_to[dim] == 0

        # set zarr-creator version
        ds_part.attrs["zarr_creator_version"] = __version__
        # set creation timestamp
        ds_part.attrs["zarr_creation_time"] = datetime.datetime.now(
            datetime.timezone.utc
        ).isoformat()
        # add link to repo
        ds_part.attrs["zarr_creator_repo"] = (
            "https://github.com/dmidk/nwp-forecast-zarr-creator"
        )

        write_output_zarrs(
            ds=ds_part,
            member="control",
            dataset_id=part_id,
            rechunk_to=rechunk_to,
            t_analysis=args.t_analysis,
            skip_s3_bucket_upload=args.skip_s3_bucket_upload,
            local_copy_path=LOCAL_COPY_STORAGE_PATH,
        )


if __name__ == "__main__":
    with logger.catch(reraise=True):
        cli()
