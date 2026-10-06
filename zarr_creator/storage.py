"""fsspec-based storage abstraction.

Single place where URLs become filesystems. All callers pass opaque URLs
(``s3://bucket/prefix/...`` or local paths) and let fsspec dispatch —
no ``is_s3`` branching outside this module (use :func:`is_local_uri`).

S3 authentication is profile-only: pass ``profile=<name>`` and endpoint,
keys, and region resolve from ``~/.aws/config`` + ``~/.aws/credentials``
via botocore/s3fs.
"""

import os
import posixpath
import shutil
import sys

import fsspec
from botocore.exceptions import NoCredentialsError
from fsspec.callbacks import TqdmCallback
from fsspec.core import strip_protocol
from fsspec.implementations.local import LocalFileSystem
from fsspec.utils import get_protocol
from loguru import logger


def _progress_callback(desc: str) -> TqdmCallback:
    """File-count progress callback for fsspec ``get``/``put``.

    Suppressed when stderr is not a TTY (e.g. container logs).
    """
    return TqdmCallback(
        tqdm_kwargs={"desc": desc, "unit": "files", "disable": not sys.stderr.isatty()}
    )


def s3_verify_ssl() -> bool:
    """Whether to verify S3 TLS certificates (env: ``S3_VERIFY_SSL``).

    Defaults to true; set ``S3_VERIFY_SSL=0`` for endpoints whose CA is not
    in the container's trust store (like ``aws s3 --no-verify-ssl``).
    """
    return os.environ.get("S3_VERIFY_SSL", "").lower() not in {"0", "false", "no"}


def storage_options(url: str, profile: str | None = None, anon: bool = False) -> dict:
    """fsspec filesystem kwargs for ``url``.

    ``profile``/``anon`` are only forwarded for ``s3://`` URLs. ``anon``
    enables unsigned reads of public buckets (CI fixture consumption).
    Local filesystems create parent directories on write, which zarr 3's
    fsspec store relies on.
    """
    if url.startswith("s3://"):
        kwargs: dict = {"anon": anon}
        if profile is not None:
            kwargs["profile"] = profile
        if not s3_verify_ssl():
            kwargs["client_kwargs"] = {"verify": False}
        return kwargs
    return {"auto_mkdir": True}


def resolve_fs(url: str, profile: str | None = None, anon: bool = False):
    """Resolve ``(filesystem, path)`` for any URL via fsspec."""
    return fsspec.url_to_fs(url, **storage_options(url, profile, anon))


def is_local_uri(uri: str) -> bool:
    """Whether ``uri`` is on the local filesystem (plain path or ``file://``)."""
    return get_protocol(uri) in ("file", "local")


def exists(url: str, profile: str | None = None, anon: bool = False) -> bool:
    """Check existence of a single URL."""
    fs, path = resolve_fs(url, profile, anon)
    return fs.exists(path)


def find_missing(
    root_uri: str,
    names: list[str],
    profile: str | None = None,
    anon: bool = False,
) -> list[str]:
    """Return the ``names`` (relative to ``root_uri``) that do not exist.

    Lists ``root_uri`` once instead of checking every name: s3fs sends the
    common prefix of ``names`` as a single, uncached LIST request (so new
    files show up in long-running watch loops); other filesystems ignore
    it. A missing root means all names are missing; auth errors propagate.
    """
    if not names:
        return []
    fs, root = resolve_fs(root_uri, profile, anon)
    try:
        found = fs.find(root, prefix=os.path.commonprefix(names))
    except FileNotFoundError:
        found = []
    present = {posixpath.relpath(path, root) for path in found}
    return [name for name in names if name not in present]


def join(root_uri: str, *parts: str) -> str:
    """Join names onto a root URI (works for ``s3://`` and local paths)."""
    root_uri = root_uri.rstrip("/")
    return root_uri + "/" + "/".join(p.strip("/") for p in parts)


def download_to_temp(
    src_urls: list[str],
    tmpdir: str,
    profile: str | None = None,
    anon: bool = False,
) -> str:
    """Download ``src_urls`` into ``tmpdir`` (flat layout), return ``tmpdir``.

    Skips files that already exist locally (mirrors ``rsync`` /
    ``download_harmonie_data.sh`` behavior). Files are fetched under a
    ``.part`` name and renamed once complete, so an interrupted download is
    never mistaken for a finished one on the next call.
    """
    os.makedirs(tmpdir, exist_ok=True)
    if not src_urls:
        return tmpdir
    protocols = {get_protocol(url) for url in src_urls}
    if len(protocols) > 1:
        raise ValueError(
            f"Source URLs must all be on one filesystem, got: {sorted(protocols)}"
        )
    fs, _ = resolve_fs(src_urls[0], profile, anon)
    todo = []
    for url in src_urls:
        src_path = strip_protocol(url)
        dst = os.path.join(tmpdir, os.path.basename(src_path.rstrip("/")))
        if os.path.exists(dst):
            logger.debug(f"Skipping existing: {dst}")
        else:
            todo.append((src_path, dst))
    if len(todo) < len(src_urls):
        logger.info(f"Skipped {len(src_urls) - len(todo)} existing file(s) in {tmpdir}")
    if not todo:
        return tmpdir
    # A single call lets s3fs fetch the files concurrently.
    try:
        with _progress_callback(f"Downloading to {tmpdir}") as callback:
            fs.get(
                [src for src, _ in todo],
                [dst + ".part" for _, dst in todo],
                callback=callback,
            )
    except Exception as exc:
        logger.error(f"Failed to download {len(todo)} file(s) to {tmpdir}: {exc!r}")
        raise
    for _, dst in todo:
        os.replace(dst + ".part", dst)
    return tmpdir


def upload_tree(
    local_dir: str,
    dest_root_uri: str,
    profile: str | None = None,
    overwrite: bool = False,
) -> list[str]:
    """Upload all files under ``local_dir`` (flat) to ``dest_root_uri``."""
    fs, dest_path = resolve_fs(dest_root_uri, profile)
    dest_root_uri = dest_root_uri.rstrip("/")
    names = sorted(
        name
        for name in os.listdir(local_dir)
        if os.path.isfile(os.path.join(local_dir, name))
    )
    dst_paths = [dest_path.rstrip("/") + "/" + name for name in names]
    if not overwrite:
        for name, dst_path in zip(names, dst_paths):
            if fs.exists(dst_path):
                raise FileExistsError(
                    f"Destination already exists: {dest_root_uri}/{name}"
                )
    if isinstance(fs, LocalFileSystem):
        # Object stores need no directories, and s3fs' makedirs would try to
        # create the bucket if it can't see it.
        fs.makedirs(dest_path, exist_ok=True)
    with _progress_callback(f"Uploading to {dest_root_uri}") as callback:
        fs.put(
            [os.path.join(local_dir, name) for name in names],
            dst_paths,
            callback=callback,
        )
    return [f"{dest_root_uri}/{name}" for name in names]


def cleanup_temp(path: str) -> None:
    """Remove a staging directory (replaces ``rm -rf`` in ``run.sh``)."""
    if path and os.path.isdir(path):
        logger.info(f"Deleting temporary storage {path}")
        shutil.rmtree(path)


# s3fs maps S3's AccessDenied, InvalidAccessKeyId, ExpiredToken,
# SignatureDoesNotMatch etc. (and bare 403s) to PermissionError; missing
# credentials surface as botocore's NoCredentialsError.
_AUTH_ERRORS = (PermissionError, NoCredentialsError)


def _is_auth_error(exc: BaseException | None) -> bool:
    """Whether ``exc``, or an exception it was raised from, is an auth failure."""
    seen = set()
    while exc is not None and id(exc) not in seen:
        if isinstance(exc, _AUTH_ERRORS):
            return True
        seen.add(id(exc))
        exc = exc.__cause__ or exc.__context__
    return False


def auth_error_hint(exc: Exception, *, anon: bool) -> str | None:
    """Remediation guidance if ``exc`` is an S3 auth failure.

    Returns None for non-auth errors. Catches the classic 403 confusion in
    both directions: unsigned reads against a private bucket, and signed
    reads with bad/missing credentials (or against a public bucket that
    needs no signing).
    """
    if not _is_auth_error(exc):
        return None
    if anon:
        return (
            f"S3 auth error ({exc}): reads are unsigned (SRC_ANON=1), "
            "so the bucket is likely private. Unset SRC_ANON and set "
            "SRC_AWS_PROFILE (or AWS_PROFILE) so requests are signed via ~/.aws."
        )
    return (
        f"S3 auth error ({exc}): signed reads failed. Check the credentials "
        "can access the bucket (SRC_AWS_PROFILE/AWS_PROFILE via ~/.aws, or the "
        "container IAM role); for a public bucket set SRC_ANON=1 to use "
        "unsigned reads instead."
    )
