"""Periodic runner (port of ``run.sh``).

Modes (see ``main``):

- one-shot (default): process a single analysis time — explicit
  ``--t-analysis`` or the most recent eligible one — then exit. Suitable
  for cron / Kubernetes Jobs.
- ``--watch``: infinite poll loop like ``run.sh`` (skip + sleep when refs
  already exist, retry conversion on failure, sleep between polls).
"""

import argparse
import datetime
import os
import shutil
import time

from loguru import logger

from .. import storage
from ..settings import (
    LATEST,
    Settings,
    compute_analysis_time,
    describe_source_auth,
    refs_dir_for,
    require_utc,
    resolve_t_analysis,
)
from .cli_args import (
    T_ANALYSIS_HELP,
    add_settings_arguments,
    settings_from_args,
    t_analysis_arg,
)
from .index_refs import build_indexes_and_refs

DEFAULT_POLL_INTERVAL = 300
DEFAULT_ALREADY_DONE_SLEEP = 1200


# Left in an analysis time's refs directory once it has been converted. The
# refs JSONs are only needed for the conversion, so they are deleted to save
# disk space; the marker is what tells the watcher the time is already done.
REFS_DONE_MARKER = ".done"


def refs_done(t_analysis: datetime.datetime, settings: Settings) -> bool:
    """Whether an analysis time has been fully processed.

    A refs directory alone does not count: it is created before the refs are
    built, so it also exists after an interrupted or failed run.
    """
    return os.path.isfile(
        os.path.join(refs_dir_for(t_analysis, settings), REFS_DONE_MARKER)
    )


def mark_refs_done(
    t_analysis: datetime.datetime, settings: Settings, keep_refs: bool = False
) -> None:
    """Record an analysis time as processed.

    By default the refs are freed, keeping only the marker; ``keep_refs=True``
    leaves them in place (e.g. to inspect them during development).
    """
    refs_dir = refs_dir_for(t_analysis, settings)
    if not keep_refs:
        shutil.rmtree(refs_dir, ignore_errors=True)
    os.makedirs(refs_dir, exist_ok=True)
    with open(os.path.join(refs_dir, REFS_DONE_MARKER), "w"):
        pass


def _run_conversion(t_analysis: datetime.datetime, settings: Settings) -> None:
    # Lazy import: keeps this module side-effect free and patchable in tests.
    from ..__main__ import cli as convert

    t_str = require_utc(t_analysis).isoformat()
    convert(["--t_analysis", t_str, "--suite-name", settings.suite_name])


def process_one(
    t_analysis: datetime.datetime,
    settings: Settings,
    max_retries: int | None = None,
    retry_interval: float = 60,
    cleanup: bool = True,
) -> str:
    """Process one analysis time; return ``"skipped"`` or ``"done"``.

    ``max_retries=None`` retries forever (like ``run.sh``). With
    ``cleanup=False`` the refs and staged GRIB files are kept after a
    successful conversion (the done marker is still written).
    """
    t_analysis = require_utc(t_analysis)
    if refs_done(t_analysis, settings):
        logger.info(f"Analysis time {t_analysis.isoformat()} is already processed")
        return "skipped"

    logger.info(f"Creating indexes/refs for analysis time {t_analysis.isoformat()}")
    build_indexes_and_refs(t_analysis, settings)

    attempts = 0
    while True:
        try:
            _run_conversion(t_analysis, settings)
        except Exception:
            attempts += 1
            if max_retries is not None and attempts > max_retries:
                raise
            logger.warning("Zarr conversion failed, retrying...")
            time.sleep(retry_interval)
            continue
        break

    logger.info(
        f"Zarr conversion successful for analysis time {t_analysis.isoformat()}"
    )
    mark_refs_done(t_analysis, settings, keep_refs=not cleanup)
    if not cleanup:
        logger.info("Cleanup disabled: keeping refs and staged GRIB files")
    elif settings.src_grib_temp_path and os.path.isdir(settings.src_grib_temp_path):
        storage.cleanup_temp(settings.src_grib_temp_path)
    return "done"


def poll_once(
    settings: Settings,
    now: datetime.datetime | None = None,
    poll_interval: float = DEFAULT_POLL_INTERVAL,
    already_done_sleep: float = DEFAULT_ALREADY_DONE_SLEEP,
    **process_kwargs,
) -> float:
    """Run a single watch iteration; return seconds to sleep next."""
    t_analysis = compute_analysis_time(
        now or datetime.datetime.now(datetime.timezone.utc)
    )
    if refs_done(t_analysis, settings):
        logger.info(
            f"Analysis time {t_analysis.isoformat()} is already processed "
            f"({refs_dir_for(t_analysis, settings)}). "
            f"Sleeping for {already_done_sleep}s..."
        )
        return already_done_sleep
    try:
        process_one(t_analysis, settings, **process_kwargs)
    except FileNotFoundError as exc:
        # GRIBs arrive hour by hour, so an incomplete analysis time is the
        # normal state until the last file lands (run.sh just retried).
        logger.warning(f"{exc}. Source not complete yet, retrying later.")
    return poll_interval


def watch_loop(
    settings: Settings,
    poll_interval: float = DEFAULT_POLL_INTERVAL,
    already_done_sleep: float = DEFAULT_ALREADY_DONE_SLEEP,
    **process_kwargs,
) -> None:
    """Infinite poll loop (the ``run.sh`` ``while true``)."""
    while True:
        sleep_for = poll_once(
            settings,
            poll_interval=poll_interval,
            already_done_sleep=already_done_sleep,
            **process_kwargs,
        )
        logger.info(f"Sleeping for {sleep_for}s...")
        time.sleep(sleep_for)


def main(argv=None) -> None:
    """CLI: ``python -m zarr_creator.pipeline.runner [--t-analysis X | --watch]``."""
    parser = argparse.ArgumentParser(description="Run the NWP zarr conversion pipeline")
    # --watch always follows the latest analysis time, so a fixed one makes no
    # sense with it. ``--t-analysis`` defaults to None (= latest) rather than
    # the "latest" string so argparse reliably detects an explicit value.
    mode = parser.add_mutually_exclusive_group()
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
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=DEFAULT_POLL_INTERVAL,
        help="With --watch, seconds to sleep between polls " "(default: %(default)s)",
    )
    parser.add_argument(
        "--already-done-sleep",
        type=float,
        default=DEFAULT_ALREADY_DONE_SLEEP,
        help="With --watch, seconds to sleep once the current analysis time has "
        "already been processed (default: %(default)s)",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=None,
        help="Give up and exit non-zero after this many failed zarr conversion "
        "retries (default: retry forever)",
    )
    parser.add_argument(
        "--retry-interval",
        type=float,
        default=60,
        help="Seconds to wait between zarr conversion retries "
        "(default: %(default)s)",
    )
    parser.add_argument(
        "--no-cleanup",
        action="store_true",
        help="Keep the refs and staged GRIB files after a successful conversion "
        "instead of deleting them (useful during development)",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        help="Log level (default: %(default)s)",
    )
    add_settings_arguments(parser)
    args = parser.parse_args(argv)

    logger.remove()
    import sys

    logger.add(sys.stderr, level=args.log_level.upper())

    settings = settings_from_args(args)
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
        watch_loop(
            settings,
            poll_interval=args.poll_interval,
            already_done_sleep=args.already_done_sleep,
            max_retries=args.max_retries,
            retry_interval=args.retry_interval,
            cleanup=not args.no_cleanup,
        )
        return

    process_one(
        args.t_analysis or resolve_t_analysis(LATEST),
        settings,
        max_retries=args.max_retries,
        retry_interval=args.retry_interval,
        cleanup=not args.no_cleanup,
    )


if __name__ == "__main__":
    main()
