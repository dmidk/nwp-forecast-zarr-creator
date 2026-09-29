"""fsspec-based storage abstraction.

Single place where URLs become filesystems. All callers pass opaque URLs
(``s3://bucket/prefix/...`` or local paths) and let fsspec dispatch —
no ``is_s3`` branching outside this module.

S3 authentication is profile-only: pass ``profile=<name>`` and endpoint,
keys, and region resolve from ``~/.aws/config`` + ``~/.aws/credentials``
via botocore/s3fs.
"""

import os
import shutil
import sys

import fsspec
from fsspec.callbacks import TqdmCallback
from loguru import logger
from tqdm import tqdm


def _progress(desc: str, total: int):
    """File-count progress bar (suppressed when stderr is not a TTY)."""
    return tqdm(desc=desc, total=total, unit="files", disable=not sys.stderr.isatty())


# s3fs uploads in 50MiB parts by default, so the per-file bar would sit at
# 0% for minutes on slow links. Smaller parts stream progress more often;
# S3 requires >=5MiB per part (except the last).
_S3_PUT_CHUNKSIZE = 8 * 1024 * 1024


def _protocols(fs) -> set:
    protocol = fs.protocol
    return set(protocol) if isinstance(protocol, (tuple, list)) else {protocol}


def _is_local_copy(fs) -> bool:
    """Whether to copy via the local filesystem (vs fsspec put/get)."""
    return bool(_protocols(fs) & {"file", "local"})


def _show_file_bar(fs) -> bool:
    """Whether a per-file byte bar is useful (remote transfers only).

    Local and memory transfers don't report byte progress, so a bar would
    sit at 0% — the outer file-count bar already covers those.
    """
    return not bool(_protocols(fs) & {"file", "local", "memory"})


def _file_bar(desc: str):
    """Per-file byte progress bar (suppressed when stderr is not a TTY)."""
    return TqdmCallback(
        tqdm_kwargs={
            "desc": desc,
            "unit": "B",
            "unit_scale": True,
            "unit_divisor": 1024,
            "leave": False,
            "disable": not sys.stderr.isatty(),
        }
    )


def resolve_fs(url: str, profile: str | None = None, anon: bool = False):
    """Resolve ``(filesystem, path)`` for any URL via fsspec.

    ``profile``/``anon`` are only forwarded for ``s3://`` URLs. ``anon``
    enables unsigned reads of public buckets (CI fixture consumption).
    """
    if url.startswith("s3://"):
        kwargs: dict = {"anon": anon}
        if profile is not None:
            kwargs["profile"] = profile
        return fsspec.url_to_fs(url, **kwargs)
    return fsspec.url_to_fs(url)


def exists(url: str, profile: str | None = None, anon: bool = False) -> bool:
    """Check existence of a single URL."""
    fs, path = resolve_fs(url, profile, anon)
    return fs.exists(path)


def find_missing(
    urls: list[str], profile: str | None = None, anon: bool = False
) -> list[str]:
    """Return the subset of ``urls`` that do not exist."""
    if not urls:
        return []
    # Re-resolve per URL so mixed-protocol lists still work.
    missing = []
    for url in urls:
        fs_u, path_u = resolve_fs(url, profile, anon)
        if not fs_u.exists(path_u):
            missing.append(url)
    return missing


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
    ``download_harmonie_data.sh`` behavior). On partial failure, files
    downloaded in this call are deleted and the error re-raised.
    """
    os.makedirs(tmpdir, exist_ok=True)
    downloaded_this_attempt: list[str] = []
    failed: list[str] = []
    skipped = 0
    with _progress(f"Downloading to {tmpdir}", total=len(src_urls)) as bar:
        for url in src_urls:
            fs, src_path = resolve_fs(url, profile, anon)
            dst = os.path.join(tmpdir, os.path.basename(src_path.rstrip("/")))
            if os.path.exists(dst):
                logger.debug(f"Skipping existing: {dst}")
                skipped += 1
                bar.update(1)
                continue
            try:
                if _show_file_bar(fs):
                    callback = _file_bar(f"↓ {os.path.basename(dst)}")
                    try:
                        fs.get_file(src_path, dst, callback=callback)
                    finally:
                        callback.close()
                else:
                    fs.get_file(src_path, dst)
                downloaded_this_attempt.append(dst)
            except Exception:
                logger.error(f"Failed: {url}")
                failed.append(url)
            bar.update(1)
    if skipped:
        logger.info(f"Skipped {skipped} existing file(s) in {tmpdir}")
    if failed:
        for dst in downloaded_this_attempt:
            try:
                os.remove(dst)
            except OSError:
                pass
        raise RuntimeError(f"Failed to download {len(failed)} file(s): {failed}")
    return tmpdir


def upload_tree(
    local_dir: str,
    dest_root_uri: str,
    profile: str | None = None,
    overwrite: bool = False,
) -> list[str]:
    """Upload all files under ``local_dir`` (flat) to ``dest_root_uri``."""
    fs, dest_path = resolve_fs(dest_root_uri, profile)
    uploaded = []
    is_local = _is_local_copy(fs)
    show_bar = _show_file_bar(fs)
    # Smaller parts than s3fs' 50MiB default so the per-file bar advances
    # steadily; only s3fs understands this kwarg.
    put_kwargs = {"chunksize": _S3_PUT_CHUNKSIZE} if "s3" in _protocols(fs) else {}
    if is_local:
        os.makedirs(dest_path, exist_ok=True)
    names = sorted(
        name
        for name in os.listdir(local_dir)
        if os.path.isfile(os.path.join(local_dir, name))
    )
    with _progress(f"Uploading to {dest_root_uri}", total=len(names)) as bar:
        for name in names:
            src = os.path.join(local_dir, name)
            if is_local:
                dst = os.path.join(dest_path, name)
                if not overwrite and os.path.exists(dst):
                    raise FileExistsError(f"Destination already exists: {dst}")
                shutil.copy2(src, dst)
                uploaded.append(dst)
            else:
                dst_path = dest_path.rstrip("/") + "/" + name
                if not overwrite and fs.exists(dst_path):
                    raise FileExistsError(
                        f"Destination already exists: {dest_root_uri}/{name}"
                    )
                if show_bar:
                    callback = _file_bar(f"↑ {name}")
                    try:
                        fs.put_file(src, dst_path, callback=callback, **put_kwargs)
                    finally:
                        callback.close()
                else:
                    fs.put_file(src, dst_path, **put_kwargs)
                uploaded.append(f"{dest_root_uri.rstrip('/')}/{name}")
            bar.update(1)
    return uploaded


def cleanup_temp(path: str) -> None:
    """Remove a staging directory (replaces ``rm -rf`` in ``run.sh``)."""
    if path and os.path.isdir(path):
        logger.info(f"Deleting temporary storage {path}")
        shutil.rmtree(path)


_AUTH_ERROR_MARKERS = (
    "403",
    "forbidden",
    "accessdenied",
    "invalidaccesskeyid",
    "signaturedoesnotmatch",
    "nocredentials",
    "missingcredentials",
    "expiredtoken",
    "invalidclienttokenid",
)


def auth_error_hint(exc: Exception, *, anon: bool) -> str | None:
    """Remediation guidance if ``exc`` looks like an S3 auth failure.

    Returns None for non-auth errors. Catches the classic 403 confusion in
    both directions: unsigned reads against a private bucket, and signed
    reads with bad/missing credentials (or against a public bucket that
    needs no signing).
    """
    code = ""
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        code = str(response.get("Error", {}).get("Code", ""))
    text = f"{code} {exc}".lower()
    if not any(marker in text for marker in _AUTH_ERROR_MARKERS):
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
