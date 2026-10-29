"""Build GRIB indexes and gribscan refs (port of ``build_indexes_and_refs.sh``).

Flow per analysis time, for each file group of the suite (``sf``/``pl`` for
HARMONIE, see ``zarr_creator.suites``):

1. Enumerate the expected files for ``0..MAX_HOUR`` (e.g.
   ``fc<YYYYMMDDHH>+<HHH><member>_<type>`` for HARMONIE) and verify
   completeness (replaces the ``test -f`` loop).
2. If ``SRC_GRIB_TEMP_PATH`` is set, download/copy files there first and
   index the staged copy (replaces ``rsync``); otherwise index in place.
   gribscan only reads plain local paths, so a source on any other
   filesystem (e.g. ``s3://``) requires ``SRC_GRIB_TEMP_PATH``.
3. Run ``gribscan-index`` in-process (with the local DMI eccodes
   definitions path set), then assemble the refs with the suite's gribscan
   magician (what ``gribscan-build --prefix <src>/ -m <magician>`` does).
"""

import datetime
import json
import os

from fsspec.core import split_protocol
from loguru import logger

from .. import storage
from ..grib_definitions import set_local_eccodes_definitions_path
from ..settings import Settings, describe_source_auth, make_refs_dir_path, require_utc
from ..suites import get_suite


def _run_index(inputs: list[str], nprocs: int = 2, outdir: str | None = None) -> None:
    """Create gribscan ``.index`` files by running ``gribscan-index`` in-process.

    Parameters
    ----------
    inputs : list[str]
        Local paths of the GRIB files to index.
    nprocs : int, optional
        Number of processes to index with.
    outdir : str, optional
        Directory to write the index files to; by default they are written
        next to the GRIB files.
    """
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
    """Build the refs from index files and write one JSON per dataset.

    Parameters
    ----------
    index_files : list[str]
        Paths of the gribscan ``.index`` files.
    refs_dir : str
        Directory to write the refs to (created if missing), one
        ``<dataset>.json`` per dataset of the magician.
    prefix : str
        Global prefix of the GRIB file paths in the refs (the source
        directory, with a trailing ``/``).
    magician : gribscan.magician.Magician
        The gribscan magician that assembles the datasets.
    """
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
        hint = storage.get_auth_error_hint(exc, anon=anon)
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
    profile = settings.src_aws_profile
    anon = settings.src_anon

    if not storage.is_local_uri(settings.src_grib_root_uri):
        logger.info(f"Remote source auth: {describe_source_auth(anon, profile)}")

    file_groups = suite.get_grib_file_groups(
        t_analysis, settings.max_hour, settings.member_id
    )
    filenames = [name for names in file_groups.values() for name in names]
    urls = [storage.join(settings.src_grib_root_uri, name) for name in filenames]
    try:
        missing = storage.find_missing(
            settings.src_grib_root_uri, filenames, profile, anon
        )
    except Exception as exc:
        hint = storage.get_auth_error_hint(exc, anon=anon)
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
        if not storage.is_local_uri(settings.src_grib_root_uri):
            raise ValueError(
                f"SRC_GRIB_TEMP_PATH must be set to read from "
                f"{settings.src_grib_root_uri}: gribscan can only index files on "
                "the local filesystem."
            )
        # gribscan needs a plain path, so drop any ``file://`` prefix.
        _, src_dir = split_protocol(settings.src_grib_root_uri)
        logger.info(f"No staging path set, indexing directly from {src_dir}")

    refs_dir = make_refs_dir_path(t_analysis, settings)
    os.makedirs(refs_dir, exist_ok=True)

    set_local_eccodes_definitions_path()

    if suite.index_next_to_source:
        index_dir = None
    else:
        index_dir = os.path.join(refs_dir, "index")
        os.makedirs(index_dir, exist_ok=True)

    for group, names in file_groups.items():
        # src_dir is always local here: non-local sources must be staged.
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
