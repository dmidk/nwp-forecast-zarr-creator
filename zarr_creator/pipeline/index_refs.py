"""Build GRIB indexes and gribscan refs (port of ``build_indexes_and_refs.sh``).

Flow per analysis time, for each file group of the suite (``sf``/``pl`` for
HARMONIE, see ``zarr_creator.suites``):

1. Enumerate the expected files for ``0..MAX_HOUR`` (e.g.
   ``fc<YYYYMMDDHH>+<HHH><member>_<type>`` for HARMONIE) and verify
   completeness (replaces the ``test -f`` loop).
2. If ``SRC_GRIB_TEMP_PATH`` is set, download/copy files there first and
   index the staged copy (replaces ``rsync``); otherwise index in place.
   S3 sources without a temp path log a warning — direct S3 reads by
   gribscan/eccodes are unverified — and are attempted in place anyway.
3. Run ``gribscan-index`` in-process (with the local DMI eccodes
   definitions path set), then assemble the refs with the suite's gribscan
   magician (what ``gribscan-build --prefix <src>/ -m <magician>`` does).
"""

import argparse
import datetime
import json
import os

from loguru import logger

from .. import storage
from ..grib_definitions import set_local_eccodes_definitions_path
from ..settings import (
    LATEST,
    Settings,
    describe_source_auth,
    refs_dir_for,
    require_utc,
    resolve_t_analysis_for_suite,
    source_profile,
)
from ..suites import get_suite
from .cli_args import (
    T_ANALYSIS_HELP,
    add_settings_arguments,
    settings_from_args,
    t_analysis_arg,
)


def _is_s3_uri(uri: str) -> bool:
    return uri.startswith("s3://")


def _run_index(inputs: list[str], nprocs: int = 2, outdir: str | None = None) -> None:
    import gribscan.tools

    # -f: always rebuild; the staging dir may be a warm cache containing
    # indexes from a previous run (same fixture content, but rebuild anyway).
    args = [*inputs, "-n", str(nprocs), "-f"]
    if outdir is not None:
        args += ["-o", outdir]
    gribscan.tools.create_index.main(args, standalone_mode=False)


def _run_build_refs(
    index_files: list[str], refs_dir: str, prefix: str, magician
) -> None:
    import gribscan

    # In-process equivalent of `gribscan-build`, whose `-m` choice only
    # accepts gribscan's built-in magicians.
    refs = gribscan.grib_magic(index_files, magician=magician, global_prefix=prefix)
    os.makedirs(refs_dir, exist_ok=True)
    for dataset, ref in refs.items():
        with open(os.path.join(refs_dir, f"{dataset}.json"), "w") as f:
            json.dump(ref, f, indent=2)


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
    suite = get_suite(settings.suite_name)
    profile = source_profile(settings)
    anon = settings.src_anon

    if _is_s3_uri(settings.src_grib_root_uri):
        logger.info(f"S3 source auth: {describe_source_auth(anon, profile)}")

    file_groups = suite.grib_file_groups(
        t_analysis, settings.max_hour, settings.member_id
    )
    filenames = [name for names in file_groups.values() for name in names]
    urls = [storage.join(settings.src_grib_root_uri, name) for name in filenames]
    try:
        missing = storage.find_missing(urls, profile, anon)
    except Exception as exc:
        hint = storage.auth_error_hint(exc, anon=anon)
        if hint is not None:
            raise RuntimeError(hint) from exc
        raise
    if missing:
        raise FileNotFoundError(
            f"{len(missing)} expected GRIB file(s) missing for analysis time "
            f"{t_analysis.isoformat()}: {missing[:5]}"
            + (" ..." if len(missing) > 5 else "")
        )

    if settings.src_grib_temp_path:
        logger.info(
            f"Staging GRIB files in {settings.src_grib_temp_path} before indexing"
        )
        src_dir = _download_with_hint(urls, settings, profile, anon)
    else:
        if _is_s3_uri(settings.src_grib_root_uri):
            logger.warning(
                "Source is S3 but SRC_GRIB_TEMP_PATH is not set; "
                "gribscan/eccodes likely cannot read directly from S3. "
                "Set SRC_GRIB_TEMP_PATH to stage files locally. "
                "Attempting in-place reads anyway."
            )
        logger.info(
            f"No staging path set, indexing directly from {settings.src_grib_root_uri}"
        )
        src_dir = settings.src_grib_root_uri

    refs_dir = refs_dir_for(t_analysis, settings)
    os.makedirs(refs_dir, exist_ok=True)

    set_local_eccodes_definitions_path()

    if suite.index_next_to_source:
        index_dir = None
    else:
        index_dir = os.path.join(refs_dir, "index")
        os.makedirs(index_dir, exist_ok=True)

    for group, names in file_groups.items():
        if _is_s3_uri(src_dir):
            inputs = [storage.join(src_dir, name) for name in names]
        else:
            inputs = [os.path.join(src_dir, name) for name in names]
        logger.info(f"Indexing {group} files ({len(inputs)} files)")
        _run_index(inputs, outdir=index_dir)
        if index_dir is None:
            index_files = [f"{path}.index" for path in inputs]
        else:
            index_files = [os.path.join(index_dir, f"{name}.index") for name in names]
        logger.info(f"Building refs for {group} files")
        _run_build_refs(
            index_files,
            refs_dir,
            prefix=src_dir.rstrip("/") + "/",
            magician=suite.make_magician(),
        )

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
    t_analysis = resolve_t_analysis_for_suite(args.t_analysis, settings.suite_name)
    return build_indexes_and_refs(t_analysis, settings)


if __name__ == "__main__":
    main()
