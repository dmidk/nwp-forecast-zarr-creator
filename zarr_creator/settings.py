"""Centralized runtime configuration (replaces ``script_defaults.sh``).

Every option maps 1:1 to an environment variable. Precedence is::

    explicit CLI flag > environment variable > built-in default

The container is configured purely via environment; CLI flags are manual
overrides.

Naming rule: ``_URI`` = fsspec-dispatched (may be ``s3://`` or a local
path), ``_PATH`` = always a local POSIX path, ``_BUCKET`` = bare bucket
name.
"""

import datetime
import os
import warnings
from dataclasses import dataclass, field

import isodate

# Built-in defaults (mirroring ``script_defaults.sh``).
DEFAULT_SRC_GRIB_ROOT_URI = "/mnt/harmonie-data-from-pds/ml"
DEFAULT_REFS_ROOT_PATH = "/home/ec2-user/nwp-forecast-zarr-creator/refs"
DEFAULT_MEMBER_ID = "CONTROL__dmi"
DEFAULT_MAX_HOUR = 36
DEFAULT_SUITE_NAME = "dini"
DEFAULT_DST_ZARR_OUTPUT_PATH = "file:///tmp/{suite_name}-recent/{dataset_id}.zarr"

FILE_TYPES = ("sf", "pl")


def _getenv(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    return value


@dataclass
class Settings:
    """Runtime configuration for the pipeline."""

    src_grib_root_uri: str = DEFAULT_SRC_GRIB_ROOT_URI
    refs_root_path: str = DEFAULT_REFS_ROOT_PATH
    member_id: str = DEFAULT_MEMBER_ID
    max_hour: int = DEFAULT_MAX_HOUR
    suite_name: str = DEFAULT_SUITE_NAME
    # Empty/None = index in place, no staging copy.
    src_grib_temp_path: str | None = None
    dst_zarr_output_path: str = DEFAULT_DST_ZARR_OUTPUT_PATH
    src_aws_profile: str | None = None
    dst_aws_profile: str | None = None
    # Unsigned S3 reads (public CI fixture bucket). Never used for writes.
    src_anon: bool = False
    # Raw env snapshot for provenance/debugging (no secrets stored here).
    _from_alias: bool = field(default=False, repr=False)


def _resolve_src_root() -> tuple[str, bool]:
    """Return (value, from_alias) for the GRIB source root URI."""
    for name in ("SRC_GRIB_ROOT_URI", "SRC_GRIB_ROOT", "SRC_GRIB_ROOT_PATH"):
        value = os.environ.get(name)
        if value:
            if name != "SRC_GRIB_ROOT_URI":
                warnings.warn(
                    f"{name} is deprecated, use SRC_GRIB_ROOT_URI instead",
                    DeprecationWarning,
                    stacklevel=3,
                )
                return value, True
            return value, False
    return DEFAULT_SRC_GRIB_ROOT_URI, False


def load_settings() -> Settings:
    """Load settings from environment variables."""
    src_root, from_alias = _resolve_src_root()

    max_hour_raw = _getenv("MAX_HOUR", str(DEFAULT_MAX_HOUR))
    try:
        max_hour = int(max_hour_raw)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ValueError(f"MAX_HOUR must be an integer, got: {max_hour_raw!r}") from exc
    if max_hour < 0:
        raise ValueError(f"MAX_HOUR must be >= 0, got: {max_hour}")

    return Settings(
        src_grib_root_uri=src_root,
        refs_root_path=_getenv("REFS_ROOT_PATH", DEFAULT_REFS_ROOT_PATH),  # type: ignore[arg-type]
        member_id=_getenv("MEMBER_ID", DEFAULT_MEMBER_ID),  # type: ignore[arg-type]
        max_hour=max_hour,
        suite_name=_getenv("SUITE_NAME", DEFAULT_SUITE_NAME),  # type: ignore[arg-type]
        src_grib_temp_path=os.environ.get("SRC_GRIB_TEMP_PATH") or None,
        dst_zarr_output_path=_getenv(  # type: ignore[arg-type]
            "DST_ZARR_OUTPUT_PATH", DEFAULT_DST_ZARR_OUTPUT_PATH
        ),
        src_aws_profile=os.environ.get("SRC_AWS_PROFILE")
        or os.environ.get("AWS_PROFILE")
        or None,
        dst_aws_profile=os.environ.get("DST_AWS_PROFILE")
        or os.environ.get("AWS_PROFILE")
        or None,
        src_anon=os.environ.get("SRC_ANON", "").lower() in {"1", "true", "yes"},
        _from_alias=from_alias,
    )


def describe_source_auth(anon: bool, profile: str | None) -> str:
    """One-line description of how S3 source reads authenticate (for logs)."""
    if anon:
        return "unsigned (SRC_ANON=1)"
    if profile:
        return f"signed (profile '{profile}')"
    return "signed (default credential chain)"


def require_utc(t_analysis: datetime.datetime) -> datetime.datetime:
    """Validate that an analysis time is tz-aware UTC (``Z`` suffix)."""
    if t_analysis.tzinfo is None:
        raise ValueError(
            f"analysis_time must be timezone-aware (UTC), got: {t_analysis!r}"
        )
    return t_analysis.astimezone(datetime.timezone.utc)


DEFAULT_ANALYSIS_INTERVAL_HOURS = 3
DEFAULT_ANALYSIS_LAG_HOURS = 2
# Sentinel for "the most recent analysis time that should be available".
LATEST = "latest"


def get_analysis_interval_in_hours() -> int:
    """Hours between analysis times (env: ``ANALYSIS_INTERVAL_HOURS``).

    Must divide 24 so the analysis times fall on the same hours every day.
    """
    raw = _getenv("ANALYSIS_INTERVAL_HOURS", str(DEFAULT_ANALYSIS_INTERVAL_HOURS))
    try:
        hours = int(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"ANALYSIS_INTERVAL_HOURS must be an integer, got: {raw!r}"
        ) from exc
    if hours <= 0 or 24 % hours != 0:
        raise ValueError(
            f"ANALYSIS_INTERVAL_HOURS must divide 24 (1, 2, 3, 4, 6, 8, 12 or 24), "
            f"got: {hours}"
        )
    return hours


def get_analysis_lag_in_hours() -> float:
    """Hours to allow for data delivery (env: ``ANALYSIS_LAG_HOURS``)."""
    raw = _getenv("ANALYSIS_LAG_HOURS", str(DEFAULT_ANALYSIS_LAG_HOURS))
    try:
        hours = float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ValueError(f"ANALYSIS_LAG_HOURS must be a number, got: {raw!r}") from exc
    if hours < 0:
        raise ValueError(f"ANALYSIS_LAG_HOURS must be >= 0, got: {hours}")
    return hours


def compute_analysis_time(
    now: datetime.datetime,
    lag_hours: float | None = None,
    interval_hours: int | None = None,
) -> datetime.datetime:
    """Most recent analysis time, allowing ``lag_hours`` for delivery.

    Subtracts the lag from ``now`` and floors to the analysis interval, e.g.
    at 05:00 UTC with a 2h lag and a 3h interval the result is 03:00 UTC.
    Unset arguments come from ``ANALYSIS_LAG_HOURS`` and
    ``ANALYSIS_INTERVAL_HOURS`` (defaults 2 and 3).
    """
    if lag_hours is None:
        lag_hours = get_analysis_lag_in_hours()
    if interval_hours is None:
        interval_hours = get_analysis_interval_in_hours()
    if now.tzinfo is None:
        now = now.replace(tzinfo=datetime.timezone.utc)
    adjusted = now - datetime.timedelta(hours=lag_hours)
    interval_seconds = interval_hours * 3600
    epoch = int(adjusted.timestamp())
    rounded = epoch // interval_seconds * interval_seconds
    return datetime.datetime.fromtimestamp(rounded, tz=datetime.timezone.utc)


def resolve_t_analysis(
    value: str | None, now: datetime.datetime | None = None
) -> datetime.datetime:
    """Turn a ``--t-analysis`` value into a UTC datetime.

    ``None`` or ``"latest"`` means the most recent analysis time (see
    :func:`compute_analysis_time`); anything else is an ISO8601 string with a
    timezone, e.g. ``2025-03-02T00:00:00Z``.
    """
    if value is None or value.strip().lower() == LATEST:
        return compute_analysis_time(
            now or datetime.datetime.now(datetime.timezone.utc)
        )
    return require_utc(isodate.parse_datetime(value))


def analysis_time_str(t_analysis: datetime.datetime) -> str:
    """Format as ``YYYYMMDDHH`` for GRIB filenames (``fc<str>+<HHH>...``)."""
    return require_utc(t_analysis).strftime("%Y%m%d%H")


def refs_dir_name(t_analysis: datetime.datetime) -> str:
    """Minute-precision refs directory name: ``YYYY-MM-DDTHHMMZ``."""
    return require_utc(t_analysis).strftime("%Y-%m-%dT%H%MZ")


def format_t_analysis(t_analysis: datetime.datetime) -> str:
    """Compact ISO format for zarr output paths (matches current S3 layout)."""
    return require_utc(t_analysis).isoformat().replace(":", "").replace("+0000", "Z")


def grib_filename(
    t_analysis: datetime.datetime, hour: int, member_id: str, file_type: str
) -> str:
    """Build a GRIB filename relative to the source root URI."""
    return f"fc{analysis_time_str(t_analysis)}+{hour:03d}{member_id}_{file_type}"


def expected_grib_filenames(
    t_analysis: datetime.datetime,
    max_hour: int,
    member_id: str,
    file_types: tuple[str, ...] = FILE_TYPES,
) -> list[str]:
    """All expected GRIB filenames for one analysis time."""
    return [
        grib_filename(t_analysis, hour, member_id, file_type)
        for file_type in file_types
        for hour in range(max_hour + 1)
    ]


def refs_dir_for(t_analysis: datetime.datetime, settings: Settings) -> str:
    """Full refs output directory for one analysis time."""
    return os.path.join(
        settings.refs_root_path,
        settings.member_id,
        f"{refs_dir_name(t_analysis)}.jsons",
    )


def validate_output_format(format: str) -> None:
    """Ensure an output-path format string contains the required fields."""
    if "{dataset_id}" not in format:
        raise ValueError(
            f"DST_ZARR_OUTPUT_PATH must contain '{{dataset_id}}', got: {format!r}"
        )


def format_output_path(
    format: str,
    *,
    suite_name: str,
    member: str,
    t_analysis: datetime.datetime,
    dataset_id: str,
) -> str:
    """Render the output-path format string for one dataset.

    If ``{t_analysis}`` is absent the path overwrites on every run
    (intended for ``recent``-style local defaults); callers should log that.
    """
    validate_output_format(format)
    t_formatted = format_t_analysis(t_analysis)
    return format.format(
        suite_name=suite_name,
        member=member,
        t_analysis=t_formatted,
        dataset_id=dataset_id,
    )
