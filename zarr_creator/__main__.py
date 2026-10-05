#!/usr/bin/env python
# -*- coding: utf-8 -*-
import argparse
import datetime
import sys

import numpy as np
import xarray as xr
from loguru import logger

from . import __version__
from .config_dini import DATA_COLLECTION as DINI_DATA_COLLECTION
from .config_dini import PROJECTION_IDENTIFIER as DINI_PROJECTION_IDENTIFIER
from .config_dini import PROJECTION_WKT as DINI_PROJECTION_WKT
from .config_ig import DATA_COLLECTION as IG_DATA_COLLECTION
from .config_ig import PROJECTION_IDENTIFIER as IG_PROJECTION_IDENTIFIER
from .config_ig import PROJECTION_WKT as IG_PROJECTION_WKT
from .grib_definitions import set_local_eccodes_definitions_path
from .pipeline.cli_args import (
    T_ANALYSIS_HELP,
    add_settings_arguments,
    settings_from_args,
    t_analysis_arg,
)
from .read_source import read_level_type_data
from .settings import LATEST, Settings, dest_profile, format_output_path, require_utc
from .write_zarr import write_output_zarrs


class _HelpFormatter(argparse.ArgumentDefaultsHelpFormatter):
    """Show defaults, except ``None`` (which means "resolved from the env")."""

    def _get_help_string(self, action):
        if action.default is None:
            return action.help
        return super()._get_help_string(action)


DEFAULT_FORECAST_DURATION = "PT3H"
DEFAULT_CHUNKING = dict(time=54, x=300, y=260)

set_local_eccodes_definitions_path()


def _setup_argparse():
    argparser = argparse.ArgumentParser(
        description="Create Zarr dataset from data-catalog (dmidc)",
        formatter_class=_HelpFormatter,
    )

    argparser.add_argument(
        "--t_analysis",
        "--t-analysis",
        dest="t_analysis",
        type=t_analysis_arg,
        default=LATEST,
        help=T_ANALYSIS_HELP,
    )

    argparser.add_argument(
        "--verbose", action="store_true", help="Verbose output", default=False
    )

    argparser.add_argument("--log-level", default="INFO", help="The log level to use")

    argparser.add_argument("--log-file", default=None, help="The file to log to")

    # Settings overrides (1:1 with env vars; explicit flag > env > default),
    # shared with the other entry points.
    add_settings_arguments(argparser)

    return argparser


def cli(argv=None):
    """
    Run zarr creator.

    If the first argument is ``run``, delegate to the pipeline runner
    (``python -m zarr_creator run ...``); otherwise run the conversion.
    """

    if argv is not None and len(argv) > 0 and argv[0] == "run":
        from .pipeline.runner import main as runner_main

        return runner_main(argv[1:])
    if argv is None:
        import sys as _sys

        if len(_sys.argv) > 1 and _sys.argv[1] == "run":
            from .pipeline.runner import main as runner_main

            return runner_main(_sys.argv[2:])

    argparser = _setup_argparse()
    args = argparser.parse_args(argv)

    logger.remove()
    logger.add(sys.stderr, level=args.log_level.upper())

    settings = settings_from_args(args)
    convert(args.t_analysis, settings)


def convert(t_analysis: datetime.datetime, settings: Settings) -> None:
    """Convert the refs of one analysis time to the output zarr datasets.

    Reads the refs from ``settings.refs_root_path`` and writes one zarr
    dataset per part of the suite's data collection (``settings.suite_name``)
    to ``settings.dst_zarr_output_path``. Logging is configured by the caller.
    """
    t_analysis = require_utc(t_analysis)

    if "{t_analysis}" not in settings.dst_zarr_output_path:
        logger.info(
            "DST_ZARR_OUTPUT_PATH contains no {t_analysis}: "
            "each run overwrites the previous output."
        )

    if settings.suite_name == "ig":
        data_collection = IG_DATA_COLLECTION
        projection_identifier = IG_PROJECTION_IDENTIFIER
        projection_wkt = IG_PROJECTION_WKT
    elif settings.suite_name == "dini":
        data_collection = DINI_DATA_COLLECTION
        projection_identifier = DINI_PROJECTION_IDENTIFIER
        projection_wkt = DINI_PROJECTION_WKT
    else:
        raise ValueError(f"Unsupported suite name: {settings.suite_name}")

    parts = {}
    for part_id, part_details in data_collection.items():
        ds_part = xr.Dataset()
        for level_details in part_details:
            level_type = level_details["level_type"]
            variables = level_details["variables"]
            level_name_mapping = level_details.get("level_name_mapping", None)

            ds_level_type = read_level_type_data(
                t_analysis=t_analysis,
                level_type=level_type,
                projection_identifier=projection_identifier,
                projection_wkt=projection_wkt,
                refs_root_path=settings.refs_root_path,
                member_id=settings.member_id,
            )

            for var_name, levels in variables.items():
                if callable(levels):
                    da = levels(ds_level_type)
                    ds_part[var_name] = da
                    if "grid_mapping" in da.attrs:
                        ds_part[da.attrs["grid_mapping"]] = ds_level_type[
                            da.attrs["grid_mapping"]
                        ]
                    continue

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
        rechunk_to = dict(
            time=1,
            x=int(np.ceil(ds_part.x.size / 2)),
            y=int(np.ceil(ds_part.y.size / 2)),
        )

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
            output_path=format_output_path(
                settings.dst_zarr_output_path,
                suite_name=settings.suite_name,
                member="control",
                t_analysis=t_analysis,
                dataset_id=part_id,
            ),
            rechunk_to=rechunk_to,
            profile=dest_profile(settings),
        )


if __name__ == "__main__":
    with logger.catch(reraise=True):
        cli()
