"""Build GRIB indexes and gribscan refs (port of ``build_indexes_and_refs.sh``).

Flow per analysis time, for each of the ``sf``/``pl`` file types:

1. Enumerate expected files ``fc<YYYYMMDDHH>+<HHH><member>_<type>`` for
   ``0..MAX_HOUR`` and verify completeness (replaces the ``test -f`` loop).
2. If ``SRC_GRIB_TEMP_PATH`` is set, download/copy files there first and
   index the staged copy (replaces ``rsync``); otherwise index in place.
   gribscan only reads plain local paths, so a source on any other
   filesystem (e.g. ``s3://``) requires ``SRC_GRIB_TEMP_PATH``.
3. Run ``gribscan-index`` in-process (with the local DMI eccodes
   definitions path set) and ``gribscan-build`` with
   ``--prefix <src>/ -m harmonie``.
"""

import argparse
import datetime
import os

from fsspec.core import split_protocol
from loguru import logger

from .. import storage
from ..grib_definitions import set_local_eccodes_definitions_path
from ..settings import (
    FILE_TYPES,
    LATEST,
    Settings,
    describe_source_auth,
    expected_grib_filenames,
    refs_dir_for,
    require_utc,
    source_profile,
)
from .cli_args import (
    T_ANALYSIS_HELP,
    add_settings_arguments,
    settings_from_args,
    t_analysis_arg,
)


def _is_s3_uri(uri: str) -> bool:
    return uri.startswith("s3://")


def _run_index(inputs: list[str], nprocs: int = 2) -> None:
    import gribscan.tools

    # -f: always rebuild; the staging dir may be a warm cache containing
    # indexes from a previous run (same fixture content, but rebuild anyway).
    gribscan.tools.create_index.main(
        [*inputs, "-n", str(nprocs), "-f"], standalone_mode=False
    )


def _run_build_refs(index_files: list[str], refs_dir: str, prefix: str) -> None:
    import gribscan.tools

    gribscan.tools.build_dataset.main(
        [*index_files, "-o", refs_dir, "--prefix", prefix, "-m", "harmonie"],
        standalone_mode=False,
    )


def _download_with_hint(urls, settings, profile, anon) -> str:
    """Stage files, re-raising auth failures with remediation guidance."""
    assert settings.src_grib_temp_path is not None
    try:
        return storage.download_to_temp(
            urls, settings.src_grib_temp_path, profile, anon
        )
    except Exception as exc:
        hint = storage.auth_error_hint(exc, anon=anon)
        if hint is not None:
            raise RuntimeError(hint) from exc
        raise


def build_indexes_and_refs(
    t_analysis: datetime.datetime,
    settings: Settings,
) -> str:
    """Build indexes and refs for one analysis time; return the refs dir."""
    t_analysis = require_utc(t_analysis)
    profile = source_profile(settings)
    anon = settings.src_anon

    if _is_s3_uri(settings.src_grib_root_uri):
        logger.info(f"S3 source auth: {describe_source_auth(anon, profile)}")

    filenames = expected_grib_filenames(
        t_analysis, settings.max_hour, settings.member_id
    )
    urls = [storage.join(settings.src_grib_root_uri, name) for name in filenames]
    try:
        missing = storage.find_missing(
            settings.src_grib_root_uri, filenames, profile, anon
        )
    except Exception as exc:
        hint = storage.auth_error_hint(exc, anon=anon)
        if hint is not None:
            raise RuntimeError(hint) from exc
        raise
    if missing:
        raise FileNotFoundError(
            f"{len(missing)} of {len(filenames)} expected GRIB file(s) missing "
            f"from {settings.src_grib_root_uri} for analysis time "
            f"{t_analysis.isoformat()}: {missing[:5]}"
            + (" ..." if len(missing) > 5 else "")
        )

    if settings.src_grib_temp_path:
        logger.info(
            f"Staging GRIB files in {settings.src_grib_temp_path} before indexing"
        )
        src_dir = _download_with_hint(urls, settings, profile, anon)
    else:
        protocol, src_dir = split_protocol(settings.src_grib_root_uri)
        if protocol not in (None, "file", "local"):
            raise ValueError(
                f"SRC_GRIB_TEMP_PATH must be set to read from "
                f"{settings.src_grib_root_uri}: gribscan can only index files on "
                "the local filesystem."
            )
        logger.info(f"No staging path set, indexing directly from {src_dir}")

    refs_dir = refs_dir_for(t_analysis, settings)
    os.makedirs(refs_dir, exist_ok=True)

    set_local_eccodes_definitions_path()

    by_type: dict[str, list[str]] = {ft: [] for ft in FILE_TYPES}
    for name in filenames:
        file_type = name.rsplit("_", 1)[-1]
        by_type[file_type].append(name)

    for file_type in FILE_TYPES:
        names = by_type[file_type]
        if _is_s3_uri(src_dir):
            inputs = [storage.join(src_dir, name) for name in names]
        else:
            inputs = [os.path.join(src_dir, name) for name in names]
        logger.info(f"Indexing {file_type} files ({len(inputs)} files)")
        _run_index(inputs)
        index_files = [f"{path}.index" for path in inputs]
        logger.info(f"Building refs for {file_type} files")
        _run_build_refs(index_files, refs_dir, prefix=src_dir.rstrip("/") + "/")

    return refs_dir


def main(argv=None) -> str:
    """CLI: ``python -m zarr_creator.pipeline.index_refs --t-analysis ...``."""
    parser = argparse.ArgumentParser(description="Build GRIB indexes and refs")
    parser.add_argument(
        "--t-analysis",
        type=t_analysis_arg,
        default=LATEST,
        help=T_ANALYSIS_HELP + " (default: %(default)s)",
    )
    add_settings_arguments(parser)
    args = parser.parse_args(argv)
    settings = settings_from_args(args)
    return build_indexes_and_refs(args.t_analysis, settings)


if __name__ == "__main__":
    main()
