"""Tests for zarr_creator.storage (local + memory filesystems, no network)."""

import os

import pytest

from zarr_creator import storage


def _write(path, content="data"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(content)


def test_resolve_local_and_memory():
    fs, path = storage.resolve_fs("/tmp/some/file")
    assert path == "/tmp/some/file"
    fs_m, path_m = storage.resolve_fs("memory://bucket/file")
    assert path_m == "/bucket/file"


@pytest.mark.parametrize(
    "value, verify", [(None, True), ("1", True), ("0", False), ("false", False)]
)
def test_s3_verify_ssl_env(monkeypatch, value, verify):
    if value is None:
        monkeypatch.delenv("S3_VERIFY_SSL", raising=False)
    else:
        monkeypatch.setenv("S3_VERIFY_SSL", value)
    assert storage.s3_verify_ssl() is verify
    fs, _ = storage.resolve_fs("s3://bucket/key", anon=True)
    assert fs.client_kwargs.get("verify", True) is verify


@pytest.mark.parametrize(
    "uri, local",
    [
        ("/mnt/data/ml", True),
        ("relative/ml", True),
        ("file:///mnt/data/ml", True),
        ("local:///mnt/data/ml", True),
        ("s3://bucket/ml", False),
        ("memory://bucket/ml", False),
    ],
)
def test_is_local_uri(uri, local):
    assert storage.is_local_uri(uri) is local


def test_exists_and_find_missing_local(tmp_path):
    a = str(tmp_path / "a")
    _write(a)
    assert storage.exists(a)
    assert not storage.exists(str(tmp_path / "nope"))
    assert storage.find_missing(str(tmp_path), ["a", "nope"]) == ["nope"]
    assert storage.find_missing(str(tmp_path), []) == []
    # a missing root means everything is missing
    assert storage.find_missing(str(tmp_path / "gone"), ["a"]) == ["a"]


def test_exists_memory():
    fs, _ = storage.resolve_fs("memory://test-exists/")
    fs.pipe("memory://test-exists/f", b"x")
    assert storage.exists("memory://test-exists/f")
    assert not storage.exists("memory://test-exists/missing")
    assert storage.find_missing("memory://test-exists", ["f", "missing"]) == ["missing"]
    # names may include subdirectories of the root
    fs.pipe("memory://test-exists/sub/g", b"x")
    assert storage.find_missing("memory://test-exists", ["sub/g", "sub/h"]) == ["sub/h"]


def test_find_missing_lists_once_with_common_prefix(monkeypatch):
    calls = []

    class FakeFS:
        def find(self, path, **kwargs):
            calls.append((path, kwargs))
            return [f"{path}/fc2025030206+000x_sf"]

    monkeypatch.setattr(
        storage, "resolve_fs", lambda url, profile=None, anon=False: (FakeFS(), "b/ml")
    )
    names = ["fc2025030206+000x_sf", "fc2025030206+001x_sf"]
    assert storage.find_missing("s3://b/ml", names) == ["fc2025030206+001x_sf"]
    assert calls == [("b/ml", {"prefix": "fc2025030206+00"})]


def test_find_missing_propagates_auth_errors(monkeypatch):
    class DeniedFS:
        def find(self, path, **kwargs):
            raise PermissionError("Access Denied")

    monkeypatch.setattr(
        storage, "resolve_fs", lambda url, profile=None, anon=False: (DeniedFS(), "b")
    )
    with pytest.raises(PermissionError):
        storage.find_missing("s3://b", ["x"])


def test_download_memory_to_temp(tmp_path):
    fs, _ = storage.resolve_fs("memory://test-dl/")
    fs.pipe("memory://test-dl/fc2025030200+000CONTROL__dmi_sf", b"grib-bytes")
    staged = storage.download_to_temp(
        ["memory://test-dl/fc2025030206+000CONTROL__dmi_sf".replace("0206", "0200")],
        str(tmp_path / "stage"),
    )
    dst = os.path.join(staged, "fc2025030200+000CONTROL__dmi_sf")
    assert os.path.exists(dst)
    # second call skips existing
    storage.download_to_temp(
        ["memory://test-dl/fc2025030200+000CONTROL__dmi_sf"],
        str(tmp_path / "stage"),
    )


def test_download_partial_failure_cleans_up(tmp_path):
    fs, _ = storage.resolve_fs("memory://test-partial/")
    fs.pipe("memory://test-partial/good", b"x")
    with pytest.raises(RuntimeError, match="Failed to download 1"):
        storage.download_to_temp(
            ["memory://test-partial/good", "memory://test-partial/bad"],
            str(tmp_path / "stage"),
        )
    assert os.listdir(str(tmp_path / "stage")) == []


def test_upload_tree_local(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "b.txt").write_text("b")
    (src / "a.txt").write_text("a")
    uploaded = storage.upload_tree(str(src), str(tmp_path / "dst"))
    assert uploaded == [
        str(tmp_path / "dst" / "a.txt"),
        str(tmp_path / "dst" / "b.txt"),
    ]
    with pytest.raises(FileExistsError):
        storage.upload_tree(str(src), str(tmp_path / "dst"))


def test_upload_tree_memory(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.txt").write_text("a")
    uploaded = storage.upload_tree(str(src), "memory://test-upload/dest")
    assert uploaded == ["memory://test-upload/dest/a.txt"]
    assert storage.exists("memory://test-upload/dest/a.txt")


def test_cleanup_temp(tmp_path):
    d = tmp_path / "stage"
    d.mkdir()
    (d / "f").write_text("x")
    storage.cleanup_temp(str(d))
    assert not d.exists()
    storage.cleanup_temp(str(tmp_path / "does-not-exist"))  # no error


def test_join():
    assert storage.join("s3://b/prefix", "a", "b") == "s3://b/prefix/a/b"
    assert storage.join("/mnt/root/", "f") == "/mnt/root/f"


def _forbidden():
    # What s3fs raises for a 403 / AccessDenied response.
    return PermissionError("Forbidden")


def test_auth_error_hint_anon_points_to_signing():
    hint = storage.auth_error_hint(_forbidden(), anon=True)
    assert hint is not None
    assert "SRC_ANON" in hint
    assert "SRC_AWS_PROFILE" in hint


def test_auth_error_hint_signed_points_to_anon():
    hint = storage.auth_error_hint(_forbidden(), anon=False)
    assert hint is not None
    assert "SRC_ANON=1" in hint


def test_auth_error_hint_detects_missing_and_chained_credentials_errors():
    from botocore.exceptions import NoCredentialsError

    assert storage.auth_error_hint(NoCredentialsError(), anon=False) is not None
    try:
        try:
            raise _forbidden()
        except PermissionError as exc:
            raise RuntimeError("listing failed") from exc
    except RuntimeError as wrapped:
        assert storage.auth_error_hint(wrapped, anon=False) is not None


def test_auth_error_hint_ignores_non_auth_errors():
    assert storage.auth_error_hint(RuntimeError("boom"), anon=True) is None
    assert storage.auth_error_hint(RuntimeError("boom"), anon=False) is None
    assert storage.auth_error_hint(FileNotFoundError("missing"), anon=True) is None
    # Analysis times like 2026-04-03 contain "403"; that is not an auth error.
    exc = OSError("Failed to fetch s3://b/ml/fc2026040300+012CONTROL__dmi_sf")
    assert storage.auth_error_hint(exc, anon=False) is None


def test_show_file_bar_only_for_remote():
    s3fs = pytest.importorskip("s3fs")
    fs_s3 = s3fs.S3FileSystem(anon=True)
    assert storage._show_file_bar(fs_s3)
    fs_local, _ = storage.resolve_fs("/tmp/whatever")
    assert not storage._show_file_bar(fs_local)
    fs_mem, _ = storage.resolve_fs("memory://whatever")
    assert not storage._show_file_bar(fs_mem)


def test_upload_to_s3_uses_small_chunks_for_progress(tmp_path, monkeypatch):
    """s3fs' 50MiB default parts stall the per-file bar; assert override."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.bin").write_bytes(b"x" * 100)

    seen = {}

    class FakeS3:
        protocol = "s3"

        def exists(self, path):
            return False

        def put_file(self, src, dst, callback=None, **kwargs):
            seen.update(kwargs)
            seen["callback"] = callback

    monkeypatch.setattr(
        storage, "resolve_fs", lambda url, profile=None, anon=False: (FakeS3(), "dest")
    )
    uploaded = storage.upload_tree(str(src), "s3://bucket/dest")
    assert uploaded == ["s3://bucket/dest/a.bin"]
    assert seen["chunksize"] == storage._S3_PUT_CHUNKSIZE
    assert seen["callback"] is not None
