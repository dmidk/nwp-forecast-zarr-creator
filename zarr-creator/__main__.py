#!/usr/bin/env python
# -*- coding: utf-8 -*-
import argparse
import sys

from loguru import logger

from .write_zarr import create_single_levels_zarr

DEFAULT_ANALYSIS_TIME = "2025-02-17T01:00:00Z"
DEFAULT_FORECAST_DURATION = "PT3H"
DEFAULT_CHUNKING = dict(time=54, x=300, y=260)


def _setup_argparse():
    argparser = argparse.ArgumentParser(
        description="Create Zarr dataset from data-catalog (dmidc)",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    argparser.add_argument(
        "--analysis_time",
        default=DEFAULT_ANALYSIS_TIME,
        help="Analysis time as ISO8601 string",
    )

    argparser.add_argument(
        "--forecast_duration",
        default=DEFAULT_FORECAST_DURATION,
        help="Forecast duration as ISO8601 duration",
    )

    argparser.add_argument(
        "--verbose", action="store_true", help="Verbose output", default=False
    )

    argparser.add_argument(
        "--source",
        type=str,
        help="Which data-catalog source to use",
        default="dinisf",
        required=False,
    )

    argparser.add_argument(
        "--output_path",
        type=str,
        help="Output path for zarr dataset",
        default="{source}_{analysis_time}_{forecast_duration}.zarr",
        required=False,
    )

    argparser.add_argument(
        "--pds_receive_path",
        type=str,
        help="Path to the PDS receive directory",
        default="/mnt/zarr-from-dini/",
        required=False,
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

    logger.debug("Arguments: {}".format(args))

    if args.source not in ["dinisf"]:
        raise ValueError("Invalid data-catalog source: {}".format(args.source))

    # Format analysis_time to remove colons
    formatted_analysis_time = args.analysis_time.replace(":", "")

    fp_out = args.output_path.format(
        source=args.source,
        analysis_time=formatted_analysis_time,
        forecast_duration=args.forecast_duration,
    )

    logger.info("Creating zarr dataset from source: {}".format(args.source))

    # ds = read_source(
    #     source_name=args.source,
    #     t_analysis=args.analysis_time,
    #     forecast_duration=args.forecast_duration,
    #     pds_receive_path=args.pds_receive_path,
    # )

    # write_zarr(
    #     ds=ds, fp_out=fp_out, rechunk_to=DEFAULT_CHUNKING, t_analysis=args.analysis_time
    # )

    fp_out = "single_levels.zarr"

    create_single_levels_zarr(
        source_name=args.source,
        t_analysis=formatted_analysis_time,
        forecast_duration=args.forecast_duration,
        pds_receive_path=args.pds_receive_path,
        output_path=fp_out,
    )


if __name__ == "__main__":
    import ipdb

    with ipdb.launch_ipdb_on_exception():
        with logger.catch(reraise=True):
            cli()
