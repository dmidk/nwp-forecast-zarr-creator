"""Shared CLI helpers for pipeline stages.

Every option maps 1:1 to an environment variable; precedence is
explicit flag > env > built-in default (see ``zarr_creator.settings``).
"""

import argparse

from ..settings import (
    DEFAULT_DST_ZARR_OUTPUT_PATH,
    DEFAULT_MAX_HOUR,
    DEFAULT_MEMBER_ID,
    DEFAULT_REFS_ROOT_PATH,
    DEFAULT_SRC_GRIB_ROOT_URI,
    DEFAULT_SUITE_NAME,
    LATEST,
    SUITE_NAMES,
    Settings,
    load_settings,
    resolve_t_analysis,
)

T_ANALYSIS_HELP = (
    "Analysis time as an ISO8601 string with a timezone, e.g. "
    f"2025-03-02T00:00:00Z, or '{LATEST}' for the most recent analysis time "
    "(now minus ANALYSIS_LAG_HOURS, default 2, floored to a multiple of "
    "ANALYSIS_INTERVAL_HOURS, default 3)"
)


def t_analysis_arg(value: str):
    """``argparse`` type for ``--t-analysis``: ISO8601 string or ``latest``."""
    try:
        return resolve_t_analysis(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"{value!r} is not '{LATEST}' or an ISO8601 time with a timezone "
            f"(e.g. 2025-03-02T00:00:00Z): {exc}"
        ) from exc


def add_settings_arguments(parser: argparse.ArgumentParser) -> None:
    """Add settings-override flags mirroring every env var."""
    parser.add_argument(
        "--src-grib-root-uri",
        default=None,
        help="Where the source GRIB files live: a local path or s3://bucket/prefix "
        f"(env: SRC_GRIB_ROOT_URI, default: {DEFAULT_SRC_GRIB_ROOT_URI})",
    )
    parser.add_argument(
        "--refs-root-path",
        default=None,
        help="Directory the index/refs files are written to "
        f"(env: REFS_ROOT_PATH, default: {DEFAULT_REFS_ROOT_PATH})",
    )
    parser.add_argument(
        "--member-id",
        default=None,
        help=f"Ensemble member id in the GRIB file names (env: MEMBER_ID, "
        f"default: {DEFAULT_MEMBER_ID})",
    )
    parser.add_argument(
        "--max-hour",
        type=int,
        default=None,
        help="Last forecast hour to include, so hours 0..MAX_HOUR are expected "
        f"(env: MAX_HOUR, default: {DEFAULT_MAX_HOUR})",
    )
    parser.add_argument(
        "--suite-name",
        default=None,
        choices=SUITE_NAMES,
        help=f"Suite to process (env: SUITE_NAME, default: {DEFAULT_SUITE_NAME})",
    )
    parser.add_argument(
        "--src-grib-temp-path",
        default=None,
        help="Local directory to stage GRIB files in before indexing. Pass an "
        "empty string to index in place with no copy "
        "(env: SRC_GRIB_TEMP_PATH, default: unset = index in place)",
    )
    parser.add_argument(
        "--dst-zarr-output-path",
        default=None,
        help="Zarr output location, local or s3://, as a format string with "
        "{suite_name}, {member}, {t_analysis} and {dataset_id} placeholders "
        "(must contain {dataset_id}) "
        f"(env: DST_ZARR_OUTPUT_PATH, default: {DEFAULT_DST_ZARR_OUTPUT_PATH})",
    )
    parser.add_argument(
        "--source-profile",
        default=None,
        help="AWS profile for reading the source, resolved from ~/.aws "
        "(env: SRC_AWS_PROFILE, falls back to AWS_PROFILE)",
    )
    parser.add_argument(
        "--dest-profile",
        default=None,
        help="AWS profile for writing the output, resolved from ~/.aws "
        "(env: DST_AWS_PROFILE, falls back to AWS_PROFILE)",
    )
    parser.add_argument(
        "--src-anon",
        action="store_true",
        default=False,
        help="Read the source from S3 unsigned, for public buckets "
        "(env: SRC_ANON=1). Never used for writes",
    )


def settings_from_args(
    args: argparse.Namespace, base: Settings | None = None
) -> Settings:
    """Apply explicit CLI flags over env-loaded settings (in place)."""
    settings = base or load_settings()
    if args.src_grib_root_uri is not None:
        settings.src_grib_root_uri = args.src_grib_root_uri
    if args.refs_root_path is not None:
        settings.refs_root_path = args.refs_root_path
    if args.member_id is not None:
        settings.member_id = args.member_id
    if args.max_hour is not None:
        settings.max_hour = args.max_hour
    if args.suite_name is not None:
        settings.suite_name = args.suite_name
    if args.src_grib_temp_path is not None:
        # Empty string disables staging (same as unset env var).
        settings.src_grib_temp_path = args.src_grib_temp_path or None
    if args.dst_zarr_output_path is not None:
        settings.dst_zarr_output_path = args.dst_zarr_output_path
    if args.source_profile is not None:
        settings.src_aws_profile = args.source_profile
    if args.dest_profile is not None:
        settings.dst_aws_profile = args.dest_profile
    if args.src_anon:
        settings.src_anon = True
    return settings
