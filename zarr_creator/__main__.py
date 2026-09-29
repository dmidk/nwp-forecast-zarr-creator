#!/usr/bin/env python
# -*- coding: utf-8 -*-
import argparse
import datetime
import sys

import xarray as xr
from loguru import logger

from . import __version__
from .grib_definitions import set_local_eccodes_definitions_path
from .pipeline.cli_args import T_ANALYSIS_HELP, t_analysis_arg
from .read_source import read_level_type_data
from .settings import (
    DEFAULT_DST_ZARR_OUTPUT_PATH,
    DEFAULT_MEMBER_ID,
    DEFAULT_REFS_ROOT_PATH,
    LATEST,
    dest_profile,
    format_output_path,
    resolve_t_analysis_for_suite,
    set_suite,
)
from .suites import SUITE_NAMES, get_suite
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


def _rename_level_dim(
    ds: xr.Dataset, level_types: set[str], level_dim_names: dict[str, str]
) -> xr.Dataset:
    """Rename the ``level`` dim after the level types it came from.

    Level types without a mapping (e.g. ``entireAtmosphere`` fields that share
    the dim) don't take part, but at least one level type must be mapped and
    all mapped ones must agree.
    """
    names = {level_dim_names[lt] for lt in level_types if lt in level_dim_names}
    if not names:
        raise NotImplementedError(
            f"Level type(s) {sorted(level_types)} not implemented"
        )
    if len(names) > 1:
        raise ValueError(
            f"Level types {sorted(level_types)} map to different level dims "
            f"{sorted(names)} within one part"
        )
    (name,) = names
    if name == "level":
        return ds
    return ds.rename({"level": name})


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

    argparser.add_argument(
        "--suite-name",
        help="The suite with corresponding config file to use",
        choices=SUITE_NAMES,
        default="dini",
    )

    # Settings overrides (1:1 with env vars; explicit flag > env > default).
    # Only the options used by conversion are listed here; the full set is
    # available on the `run` subcommand.
    argparser.add_argument(
        "--refs-root-path",
        default=None,
        help="Directory the index/refs files were written to "
        f"(env: REFS_ROOT_PATH, default: {DEFAULT_REFS_ROOT_PATH})",
    )
    argparser.add_argument(
        "--member-id",
        default=None,
        help="Ensemble member id in the GRIB file names "
        f"(env: MEMBER_ID, default: {DEFAULT_MEMBER_ID}, or 'control' for ifs)",
    )
    argparser.add_argument(
        "--dst-zarr-output-path",
        default=None,
        help="Zarr output location, local or s3://, as a format string with "
        "{suite_name}, {member}, {t_analysis} and {dataset_id} placeholders "
        f"(env: DST_ZARR_OUTPUT_PATH, default: {DEFAULT_DST_ZARR_OUTPUT_PATH})",
    )
    argparser.add_argument(
        "--dest-profile",
        default=None,
        help="AWS profile for writing the output, resolved from ~/.aws "
        "(env: DST_AWS_PROFILE, falls back to AWS_PROFILE)",
    )

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

    from .settings import load_settings

    settings = set_suite(load_settings(), args.suite_name)
    if args.refs_root_path is not None:
        settings.refs_root_path = args.refs_root_path
    if args.member_id is not None:
        settings.member_id = args.member_id
    if args.dst_zarr_output_path is not None:
        settings.dst_zarr_output_path = args.dst_zarr_output_path
    if args.dest_profile is not None:
        settings.dst_aws_profile = args.dest_profile

    if "{t_analysis}" not in settings.dst_zarr_output_path:
        logger.info(
            "DST_ZARR_OUTPUT_PATH contains no {t_analysis}: "
            "each run overwrites the previous output."
        )

    suite = get_suite(args.suite_name)
    t_analysis = resolve_t_analysis_for_suite(args.t_analysis, suite.name)

    parts = {}
    for part_id, part_details in suite.data_collection.items():
        ds_part = xr.Dataset()
        # level types of the variables that carry a `level` dim
        level_types_with_level_dim = set()
        for level_details in part_details:
            level_type = level_details["level_type"]
            variables = level_details["variables"]
            level_name_mapping = level_details.get("level_name_mapping", None)

            ds_level_type = read_level_type_data(
                t_analysis=t_analysis,
                level_type=level_type,
                projection_identifier=suite.projection_identifier,
                projection_wkt=suite.projection_wkt,
                refs_root_path=settings.refs_root_path,
                member_id=settings.member_id,
            )

            for var_name, levels in variables.items():
                if callable(levels):
                    da = levels(ds_level_type)
                    ds_part[var_name] = da
                    if "level" in da.dims:
                        level_types_with_level_dim.add(level_type)
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

                if "level" in da.dims:
                    level_types_with_level_dim.add(level_type)
                if "grid_mapping" in da.attrs:
                    ds_part[da.attrs["grid_mapping"]] = ds_level_type[
                        da.attrs["grid_mapping"]
                    ]

        # rename the "level" dim per the suite, e.g. to "altitude" or "pressure"
        if "level" in ds_part.dims:
            ds_part = _rename_level_dim(
                ds_part, level_types_with_level_dim, suite.level_dim_names
            )

        # check if any of the coordinates don't have any variables, if so drop them
        for coord in ds_part.coords:
            if all(coord not in ds_part[v].coords for v in list(ds_part.data_vars)):
                ds_part = ds_part.drop_vars(coord)

        parts[part_id] = suite.transform_part(ds_part, t_analysis)

    for part_id, ds_part in parts.items():
        rechunk_to = suite.rechunk_to(ds_part)

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
                suite_name=args.suite_name,
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
