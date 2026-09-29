"""Create a frozen GRIB test fixture from the operational source.

Pulls a trimmed sample (default ``MAX_HOUR=2``, ``sf``+``pl``) for one
analysis time from the operational bucket (which only retains ~2 weeks)
and uploads it to a fixture bucket under a suite + analysis-time prefix::

    s3://<fixture-bucket>/<suite>/<YYYY-MM-DDTHHMMZ>/ml/<grib files>
    s3://<fixture-bucket>/<suite>/<YYYY-MM-DDTHHMMZ>/README.md
    s3://<fixture-bucket>/<suite>/<YYYY-MM-DDTHHMMZ>/manifest.json

DINI and IG read different operational prefixes (``s3://harmonie-data/ml``
vs ``s3://harmonie-data/ig``), so each suite gets its own fixture. The
default source is the DINI path; pass ``--source`` explicitly for IG.

Source and destination may live on different S3 hosts: select profiles
with ``--source-profile`` (``SRC_AWS_PROFILE``) and ``--dest-profile``
(``DST_AWS_PROFILE``), each falling back to ``AWS_PROFILE``. Endpoint,
keys, and region resolve from ``~/.aws`` via the named profile.

Staging is always ``source -> local tmp -> dest`` (no S3-to-S3 across
hosts). Fixtures are immutable: re-running without ``--overwrite``
refuses when the destination prefix already exists.

Pass ``--dest-dir`` to only stage files locally (replaces
``scripts/download_harmonie_data.sh`` for local development) without
uploading anything.
"""

import argparse
import datetime
import hashlib
import json
import os
import subprocess
import tempfile

import isodate
from loguru import logger

from . import storage
from .settings import (
    describe_source_auth,
    expected_grib_filenames,
    refs_dir_name,
    require_utc,
)

DEFAULT_SOURCE_URI = "s3://harmonie-data/ml"  # DINI path; IG uses s3://harmonie-data/ig
DEFAULT_SUITE_NAME = "dini"
VALID_SUITES = ("dini", "ig")
DEFAULT_FIXTURE_BUCKET = "uwcw-sample-grib2zarr-conversion-datasets"
DEFAULT_MAX_HOUR = 2
DEFAULT_MEMBER_ID = "CONTROL__dmi"
DEFAULT_FILE_TYPES = ("sf", "pl")
AUTO_LAG_HOURS = 3
AUTO_LAG_STEP_HOURS = 3
MAX_AUTO_ATTEMPTS = 8


def _parse_t_analysis(value: str | None) -> datetime.datetime | None:
    if value is None:
        return None
    return require_utc(isodate.parse_datetime(value))


def candidate_times(
    now: datetime.datetime,
    attempts: int = MAX_AUTO_ATTEMPTS,
) -> list[datetime.datetime]:
    """Candidate analysis times, newest first (mirrors download script)."""
    if now.tzinfo is None:
        now = now.replace(tzinfo=datetime.timezone.utc)
    out = []
    for attempt in range(attempts):
        lag = datetime.timedelta(hours=AUTO_LAG_HOURS + attempt * AUTO_LAG_STEP_HOURS)
        epoch = int((now - lag).timestamp())
        rounded = epoch // (3 * 3600) * (3 * 3600)
        out.append(datetime.datetime.fromtimestamp(rounded, tz=datetime.timezone.utc))
    return out


def is_complete(
    t_analysis: datetime.datetime,
    source_uri: str,
    max_hour: int,
    member_id: str,
    file_types: tuple[str, ...],
    profile: str | None,
    anon: bool = False,
) -> bool:
    """Check that all expected GRIB files exist for one analysis time."""
    urls = [
        storage.join(source_uri, name)
        for name in expected_grib_filenames(t_analysis, max_hour, member_id, file_types)
    ]
    return not storage.find_missing(urls, profile, anon)


def resolve_analysis_time(
    explicit: datetime.datetime | None,
    source_uri: str,
    max_hour: int,
    member_id: str,
    file_types: tuple[str, ...],
    profile: str | None,
    now: datetime.datetime | None = None,
    anon: bool = False,
) -> datetime.datetime:
    """Return the analysis time to snapshot (explicit or auto-found)."""
    if explicit is not None:
        if not is_complete(
            explicit, source_uri, max_hour, member_id, file_types, profile, anon
        ):
            raise FileNotFoundError(
                f"Incomplete GRIB set for analysis time {explicit.isoformat()} "
                f"in {source_uri}"
            )
        return explicit
    now = now or datetime.datetime.now(datetime.timezone.utc)
    for candidate in candidate_times(now):
        logger.info(f"Trying {candidate.isoformat()}")
        if is_complete(
            candidate, source_uri, max_hour, member_id, file_types, profile, anon
        ):
            return candidate
    raise FileNotFoundError(
        f"No complete analysis found in {source_uri} "
        f"after {MAX_AUTO_ATTEMPTS} attempts"
    )


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _git_sha() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except Exception:
        return "unknown"


def _eccodes_version() -> str:
    try:
        import importlib.metadata

        return importlib.metadata.version("eccodes")
    except Exception:
        return "unknown"


def dest_prefix(
    fixture_bucket: str, suite_name: str, t_analysis: datetime.datetime
) -> str:
    """Destination prefix URI: ``<bucket>/<suite>/<YYYY-MM-DDTHHMMZ>``."""
    root = fixture_bucket if "://" in fixture_bucket else f"s3://{fixture_bucket}"
    return f"{root.rstrip('/')}/{suite_name}/{refs_dir_name(t_analysis)}"


def build_manifest(
    *,
    t_analysis: datetime.datetime,
    source_uri: str,
    suite_name: str,
    member_id: str,
    max_hour: int,
    file_types: tuple[str, ...],
    staged_files: list[str],
    source_profile: str | None,
    dest_profile: str | None,
) -> dict:
    """Machine-readable provenance for the fixture."""
    files = []
    for path in sorted(staged_files):
        files.append(
            {
                "name": os.path.basename(path),
                "size": os.path.getsize(path),
                "sha256": _sha256(path),
            }
        )
    return {
        "fixture_version": 1,
        "analysis_time": require_utc(t_analysis).isoformat(),
        "source_uri": source_uri,
        "suite_name": suite_name,
        "source_profile": source_profile,
        "dest_profile": dest_profile,
        "member_id": member_id,
        "max_hour": max_hour,
        "file_types": list(file_types),
        "files": files,
        "created_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "git_sha": _git_sha(),
        "eccodes_version": _eccodes_version(),
        "retention_note": (
            "Source is the operational bucket, which retains ~2 weeks. "
            "This fixture is a frozen copy; old analysis times cannot be re-pulled."
        ),
    }


def build_readme(manifest: dict, fixture_prefix: str) -> str:
    """Human-readable fixture description (uploaded alongside the data)."""
    file_list = "\n".join(
        f"- `{f['name']}` ({f['size']} bytes, sha256 `{f['sha256'][:12]}…`)"
        for f in manifest["files"]
    )
    return f"""# GRIB test fixture for nwp-forecast-zarr-creator

Frozen sample of operational HARMONIE GRIB files for CI and local testing.

- **Analysis time:** `{manifest["analysis_time"]}`
- **Suite:** `{manifest["suite_name"]}`
- **Source:** `{manifest["source_uri"]}` (operational bucket, retains ~2 weeks —
  this is a frozen copy, old analysis times cannot be re-pulled)
- **Member:** `{manifest["member_id"]}`, **max hour:** `{manifest["max_hour"]}`,
  **types:** `{", ".join(manifest["file_types"])}`
- **Created:** `{manifest["created_utc"]}` (git `{manifest["git_sha"]}`,
  eccodes `{manifest["eccodes_version"]}`)

## Layout

```text
{fixture_prefix}/ml/<grib files>
{fixture_prefix}/README.md
{fixture_prefix}/manifest.json
```

The trailing `/ml` mirrors the operational prefix so the pipeline only
needs its source root swapped.

## Use in CI / locally

```bash
export SRC_GRIB_ROOT_URI="{fixture_prefix}/ml"
export SUITE_NAME={manifest["suite_name"]}
export MAX_HOUR={manifest["max_hour"]}
uv run pytest -m integration
```

## Files

{file_list}

## Regenerate

Fixtures are immutable — create a new `<analysis-time>/` prefix instead of
overwriting:

```bash
python -m zarr_creator.create_test_fixture --suite-name {manifest["suite_name"]} --analysis-time <ISO-Z> --max-hour {manifest["max_hour"]}
```
"""


def create_test_fixture(
    *,
    t_analysis: datetime.datetime | None = None,
    source_uri: str = DEFAULT_SOURCE_URI,
    fixture_bucket: str = DEFAULT_FIXTURE_BUCKET,
    suite_name: str = DEFAULT_SUITE_NAME,
    member_id: str = DEFAULT_MEMBER_ID,
    max_hour: int = DEFAULT_MAX_HOUR,
    file_types: tuple[str, ...] = DEFAULT_FILE_TYPES,
    source_profile: str | None = None,
    dest_profile: str | None = None,
    src_anon: bool = False,
    dry_run: bool = False,
    overwrite: bool = False,
    now: datetime.datetime | None = None,
    dest_dir: str | None = None,
) -> str:
    """Create the fixture; return the destination prefix URI (or ``dest_dir``)."""
    if suite_name not in VALID_SUITES:
        raise ValueError(
            f"suite_name must be one of {VALID_SUITES}, got: {suite_name!r}"
        )
    if source_uri.startswith("s3://"):
        logger.info(f"S3 source auth: {describe_source_auth(src_anon, source_profile)}")
    try:
        t_analysis = resolve_analysis_time(
            t_analysis,
            source_uri,
            max_hour,
            member_id,
            file_types,
            source_profile,
            now,
            src_anon,
        )
    except Exception as exc:
        hint = storage.auth_error_hint(exc, anon=src_anon)
        if hint is not None:
            raise RuntimeError(hint) from exc
        raise
    names = expected_grib_filenames(t_analysis, max_hour, member_id, file_types)
    src_urls = [storage.join(source_uri, name) for name in names]

    if dest_dir is not None:
        # Stage-only mode for local development: persist files in dest_dir
        # (existing files are skipped) and upload nothing.
        logger.info(f"Staging {t_analysis.isoformat()} from {source_uri} to {dest_dir}")
        storage.download_to_temp(src_urls, dest_dir, source_profile, src_anon)
        logger.info(
            "Staged. Point the pipeline at it with:\n"
            f"  export SRC_GRIB_ROOT_URI={dest_dir}\n"
            f"  export MAX_HOUR={max_hour}"
        )
        return dest_dir

    prefix = dest_prefix(fixture_bucket, suite_name, t_analysis)
    dest_ml = f"{prefix}/ml"
    logger.info(
        f"Snapshotting {suite_name} {t_analysis.isoformat()} "
        f"from {source_uri} to {prefix}"
    )

    dest_urls = [f"{dest_ml}/{name}" for name in names] + [
        f"{prefix}/README.md",
        f"{prefix}/manifest.json",
    ]
    existing = [u for u in dest_urls if storage.exists(u, dest_profile)]
    if existing and not overwrite:
        raise FileExistsError(
            f"Destination prefix {prefix} already exists "
            f"({len(existing)} file(s)); pass --overwrite to replace, "
            "or snapshot a different analysis time."
        )

    with tempfile.TemporaryDirectory(prefix="nwp-fixture-") as tmpdir:
        grib_dir = os.path.join(tmpdir, "ml")
        try:
            staged_dir = storage.download_to_temp(
                src_urls, grib_dir, source_profile, src_anon
            )
        except Exception as exc:
            hint = storage.auth_error_hint(exc, anon=src_anon)
            if hint is not None:
                raise RuntimeError(hint) from exc
            raise
        staged_files = [
            os.path.join(staged_dir, name) for name in sorted(os.listdir(staged_dir))
        ]
        manifest = build_manifest(
            t_analysis=t_analysis,
            source_uri=source_uri,
            suite_name=suite_name,
            member_id=member_id,
            max_hour=max_hour,
            file_types=file_types,
            staged_files=staged_files,
            source_profile=source_profile,
            dest_profile=dest_profile,
        )
        meta_dir = os.path.join(tmpdir, "meta")
        os.makedirs(meta_dir, exist_ok=True)
        readme = build_readme(manifest, prefix)
        with open(os.path.join(meta_dir, "README.md"), "w") as f:
            f.write(readme)
        with open(os.path.join(meta_dir, "manifest.json"), "w") as f:
            json.dump(manifest, f, indent=2)

        if dry_run:
            total = sum(os.path.getsize(p) for p in staged_files)
            logger.info(
                f"DRY RUN: would upload {len(staged_files)} GRIB files "
                f"({total} bytes) to {dest_ml} plus README.md/manifest.json "
                f"to {prefix} "
                f"(source_profile={source_profile}, dest_profile={dest_profile})"
            )
            return prefix

        storage.upload_tree(grib_dir, dest_ml, dest_profile, overwrite=overwrite)
        storage.upload_tree(meta_dir, prefix, dest_profile, overwrite=overwrite)

    still_missing = storage.find_missing(dest_urls, dest_profile)
    if still_missing:
        raise RuntimeError(f"Upload verification failed, missing: {still_missing}")
    logger.info(f"Fixture ready at {prefix}")
    logger.info(
        "CI usage:\n"
        f"  export SRC_GRIB_ROOT_URI={dest_ml}\n"
        f"  export SUITE_NAME={suite_name}\n"
        f"  export MAX_HOUR={max_hour}"
    )
    return prefix


def main(argv=None) -> str:
    """CLI: ``python -m zarr_creator.create_test_fixture [...]``."""
    parser = argparse.ArgumentParser(description="Create a frozen GRIB test fixture")
    parser.add_argument(
        "--analysis-time",
        default=None,
        help="Analysis time as an ISO8601 UTC string. Default: the most recent "
        "complete 3-hourly analysis time, retrying older ones if it is incomplete",
    )
    parser.add_argument(
        "--source",
        default=None,
        help="Source GRIB location, local path or s3://bucket/prefix "
        f"(env: FIXTURE_SOURCE_URI, default: {DEFAULT_SOURCE_URI})",
    )
    parser.add_argument(
        "--fixture-bucket",
        default=None,
        help="Bucket the fixture is uploaded to "
        f"(env: FIXTURE_BUCKET, default: {DEFAULT_FIXTURE_BUCKET})",
    )
    parser.add_argument(
        "--suite-name",
        default=None,
        help="Suite name, used in the fixture prefix "
        f"(env: SUITE_NAME, default: {DEFAULT_SUITE_NAME})",
    )
    parser.add_argument(
        "--member-id",
        default=None,
        help="Ensemble member id in the GRIB file names "
        f"(env: MEMBER_ID, default: {DEFAULT_MEMBER_ID})",
    )
    parser.add_argument(
        "--max-hour",
        type=int,
        default=None,
        help="Last forecast hour to include "
        f"(env: MAX_HOUR, default: {DEFAULT_MAX_HOUR})",
    )
    parser.add_argument(
        "--file-types",
        default=None,
        help="Space-separated GRIB file types to include "
        f"(env: FILE_TYPES, default: {' '.join(DEFAULT_FILE_TYPES)!r})",
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
        help="AWS profile for writing the fixture, resolved from ~/.aws "
        "(env: DST_AWS_PROFILE, falls back to AWS_PROFILE)",
    )
    parser.add_argument(
        "--src-anon",
        action="store_true",
        default=None,
        help="Unsigned S3 source reads (public buckets).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List what would be copied without downloading or uploading anything",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace an existing fixture at the destination prefix "
        "(default: refuse)",
    )
    parser.add_argument(
        "--dest-dir",
        default=None,
        help="Stage-only mode: download files to this local directory "
        "and skip the fixture upload (replaces download_harmonie_data.sh).",
    )
    parser.add_argument(
        "--log-level", default="INFO", help="Log level (default: %(default)s)"
    )
    args = parser.parse_args(argv)

    import sys

    logger.remove()
    logger.add(sys.stderr, level=args.log_level.upper())

    source_uri = args.source or os.environ.get("FIXTURE_SOURCE_URI", DEFAULT_SOURCE_URI)
    fixture_bucket = args.fixture_bucket or os.environ.get(
        "FIXTURE_BUCKET", DEFAULT_FIXTURE_BUCKET
    )
    member_id = args.member_id or os.environ.get("MEMBER_ID", DEFAULT_MEMBER_ID)
    suite_name = args.suite_name or os.environ.get("SUITE_NAME", DEFAULT_SUITE_NAME)
    max_hour = args.max_hour
    if max_hour is None:
        max_hour = int(os.environ.get("MAX_HOUR", str(DEFAULT_MAX_HOUR)))
    file_types = (
        tuple(args.file_types.split())
        if args.file_types
        else tuple(os.environ.get("FILE_TYPES", " ".join(DEFAULT_FILE_TYPES)).split())
    )
    source_profile = args.source_profile or os.environ.get(
        "SRC_AWS_PROFILE", os.environ.get("AWS_PROFILE")
    )
    dest_profile = args.dest_profile or os.environ.get(
        "DST_AWS_PROFILE", os.environ.get("AWS_PROFILE")
    )
    if args.src_anon is not None:
        src_anon = args.src_anon
    else:
        src_anon = os.environ.get("SRC_ANON", "").lower() in {"1", "true", "yes"}

    return create_test_fixture(
        t_analysis=_parse_t_analysis(args.analysis_time),
        source_uri=source_uri,
        fixture_bucket=fixture_bucket,
        suite_name=suite_name,
        member_id=member_id,
        max_hour=max_hour,
        file_types=file_types,
        source_profile=source_profile,
        dest_profile=dest_profile,
        src_anon=src_anon,
        dry_run=args.dry_run,
        overwrite=args.overwrite,
        dest_dir=args.dest_dir,
    )


if __name__ == "__main__":
    main()
