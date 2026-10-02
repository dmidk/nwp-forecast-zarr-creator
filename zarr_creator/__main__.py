#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Command line interface: ``python -m zarr_creator {run,index,convert}``.

- ``run``: build indexes/refs and convert to zarr, once or with ``--watch``.
- ``index``: build GRIB indexes and refs for one analysis time.
- ``convert``: convert the refs of one analysis time to zarr.

Every subcommand takes the settings flags from ``add_settings_arguments``
(explicit flag > env var > built-in default, see ``zarr_creator.settings``).
"""
import argparse
import datetime
import sys

import numpy as np
import xarray as xr
from loguru import logger

from . import __version__, storage
from .config_dini import DATA_COLLECTION as DINI_DATA_COLLECTION
from .config_dini import PROJECTION_IDENTIFIER as DINI_PROJECTION_IDENTIFIER
from .config_dini import PROJECTION_WKT as DINI_PROJECTION_WKT
from .config_ig import DATA_COLLECTION as IG_DATA_COLLECTION
from .config_ig import PROJECTION_IDENTIFIER as IG_PROJECTION_IDENTIFIER
from .config_ig import PROJECTION_WKT as IG_PROJECTION_WKT
from .grib_definitions import set_local_eccodes_definitions_path
from .pipeline import index_refs, runner
from .pipeline.cli_args import (
    T_ANALYSIS_HELP,
    add_settings_arguments,
    settings_from_args,
    t_analysis_arg,
)
from .read_source import read_level_type_data
from .settings import (
    LATEST,
    Settings,
    describe_source_auth,
    dest_profile,
    format_output_path,
    require_utc,
    resolve_t_analysis,
)
from .write_zarr import write_output_zarrs

DEFAULT_FORECAST_DURATION = "PT3H"
DEFAULT_CHUNKING = dict(time=54, x=300, y=260)

set_local_eccodes_definitions_path()


def _build_parser() -> argparse.ArgumentParser:
    # Flags shared by every subcommand.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--log-level",
        default="INFO",
        help="Log level (default: %(default)s)",
    )
    add_settings_arguments(common)

    parser = argparse.ArgumentParser(
        description="Convert Harmonie GRIB forecasts to zarr datasets"
    )
    commands = parser.add_subparsers(dest="command", required=True)

    run_parser = commands.add_parser(
        "run",
        parents=[common],
        help="Build indexes/refs and convert to zarr, once or with --watch",
        description="Run the NWP zarr conversion pipeline",
    )
    # --watch always follows the latest analysis time, so a fixed one makes no
    # sense with it. ``--t-analysis`` defaults to None (= latest) rather than
    # the "latest" string so argparse reliably detects an explicit value.
    mode = run_parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--t-analysis",
        type=t_analysis_arg,
        default=None,
        help="Process this analysis time once and exit. "
        + T_ANALYSIS_HELP
        + f" (default: {LATEST})",
    )
    mode.add_argument(
        "--watch",
        action="store_true",
        help="Keep running: poll for the latest analysis time, build indexes/refs "
        "and convert to zarr when it is available",
    )
    run_parser.add_argument(
        "--poll-interval",
        type=float,
        default=runner.DEFAULT_POLL_INTERVAL,
        help="With --watch, seconds to sleep between polls (default: %(default)s)",
    )
    run_parser.add_argument(
        "--already-done-sleep",
        type=float,
        default=runner.DEFAULT_ALREADY_DONE_SLEEP,
        help="With --watch, seconds to sleep once the current analysis time has "
        "already been processed (default: %(default)s)",
    )
    run_parser.add_argument(
        "--max-retries",
        type=int,
        default=None,
        help="Give up and exit non-zero after this many failed zarr conversion "
        "retries (default: retry forever)",
    )
    run_parser.add_argument(
        "--retry-interval",
        type=float,
        default=60,
        help="Seconds to wait between zarr conversion retries "
        "(default: %(default)s)",
    )
    run_parser.add_argument(
        "--no-cleanup",
        action="store_true",
        help="Keep the refs and staged GRIB files after a successful conversion "
        "instead of deleting them (useful during development)",
    )

    index_parser = commands.add_parser(
        "index",
        parents=[common],
        help="Build GRIB indexes and refs for one analysis time",
        description="Build GRIB indexes and refs",
    )
    index_parser.add_argument(
        "--t-analysis",
        type=t_analysis_arg,
        default=LATEST,
        help=T_ANALYSIS_HELP + " (default: %(default)s)",
    )

    convert_parser = commands.add_parser(
        "convert",
        parents=[common],
        help="Convert the refs of one analysis time to zarr",
        description="Create Zarr dataset from data-catalog (dmidc)",
    )
    convert_parser.add_argument(
        "--t-analysis",
        "--t_analysis",
        dest="t_analysis",
        type=t_analysis_arg,
        default=LATEST,
        help=T_ANALYSIS_HELP + " (default: %(default)s)",
    )
    convert_parser.add_argument(
        "--verbose", action="store_true", help="Verbose output", default=False
    )
    convert_parser.add_argument("--log-file", default=None, help="The file to log to")

    return parser


def _run(args: argparse.Namespace, settings: Settings) -> None:
    """``run``: process one analysis time, or keep watching with ``--watch``."""
    logger.info(f"SRC_GRIB_ROOT_URI: {settings.src_grib_root_uri}")
    logger.info(f"REFS_ROOT_PATH: {settings.refs_root_path}")
    logger.info(f"SRC_GRIB_TEMP_PATH: {settings.src_grib_temp_path or 'not set'}")
    logger.info(f"SUITE_NAME: {settings.suite_name}")
    if settings.src_grib_root_uri.startswith("s3://"):
        logger.info(
            f"S3 source auth: "
            f"{describe_source_auth(settings.src_anon, settings.src_aws_profile)}"
        )
    if not storage.s3_verify_ssl():
        logger.warning("S3_VERIFY_SSL is off: S3 TLS certificates are not verified")

    if args.watch:
        runner.watch_loop(
            settings,
            poll_interval=args.poll_interval,
            already_done_sleep=args.already_done_sleep,
            max_retries=args.max_retries,
            retry_interval=args.retry_interval,
            cleanup=not args.no_cleanup,
        )
        return

    runner.process_one(
        args.t_analysis or resolve_t_analysis(LATEST),
        settings,
        max_retries=args.max_retries,
        retry_interval=args.retry_interval,
        cleanup=not args.no_cleanup,
    )


def cli(argv=None) -> None:
    """Entry point for ``python -m zarr_creator`` and the ``nwp-zarr`` script."""
    parser = _build_parser()
    args = parser.parse_args(argv)

    logger.remove()
    logger.add(sys.stderr, level=args.log_level.upper())

    settings = settings_from_args(args)

    if args.command == "run":
        _run(args, settings)
    elif args.command == "index":
        refs_dir = index_refs.build_indexes_and_refs(args.t_analysis, settings)
        logger.info(f"Refs written to {refs_dir}")
    else:
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
