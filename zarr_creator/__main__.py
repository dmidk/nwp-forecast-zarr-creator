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
import sys

from loguru import logger

from . import storage
from .convert import convert
from .pipeline import index_refs, runner
from .pipeline.cli_args import (
    T_ANALYSIS_HELP,
    add_settings_arguments,
    settings_from_args,
    t_analysis_arg,
)
from .settings import LATEST, Settings, describe_source_auth, resolve_t_analysis


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
    if not storage.is_local_uri(settings.src_grib_root_uri):
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


if __name__ == "__main__":
    with logger.catch(reraise=True):
        cli()
