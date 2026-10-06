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


def test_download_failure_raises_original_error(tmp_path):
    fs, _ = storage.resolve_fs("memory://test-partial/")
    fs.pipe("memory://test-partial/good", b"x")
    stage = str(tmp_path / "stage")
    with pytest.raises(FileNotFoundError, match="bad"):
        storage.download_to_temp(
            ["memory://test-partial/good", "memory://test-partial/bad"], stage
        )
    # Nothing is left under a final name, so a retry fetches everything again.
    assert [f for f in os.listdir(stage) if not f.endswith(".part")] == []
    fs.pipe("memory://test-partial/bad", b"y")
    storage.download_to_temp(
        ["memory://test-partial/good", "memory://test-partial/bad"], stage
    )
    assert sorted(os.listdir(stage)) == ["bad", "good"]


def test_interrupted_download_is_not_reused(tmp_path, monkeypatch):
    """A file cut off mid-transfer must be fetched again, not skipped."""
    from fsspec.implementations.memory import MemoryFileSystem

    fs, _ = storage.resolve_fs("memory://test-interrupt/")
    fs.pipe("memory://test-interrupt/f", b"complete")
    stage = str(tmp_path / "stage")
    real_get_file = MemoryFileSystem.get_file

    def get_half_then_fail(self, rpath, lpath, **kwargs):
        with open(lpath, "wb") as f:
            f.write(b"comp")
        raise ConnectionResetError("connection reset")

    monkeypatch.setattr(MemoryFileSystem, "get_file", get_half_then_fail)
    with pytest.raises(ConnectionResetError):
        storage.download_to_temp(["memory://test-interrupt/f"], stage)
    monkeypatch.setattr(MemoryFileSystem, "get_file", real_get_file)

    storage.download_to_temp(["memory://test-interrupt/f"], stage)
    with open(os.path.join(stage, "f"), "rb") as f:
        assert f.read() == b"complete"


def test_download_rejects_mixed_filesystems(tmp_path):
    with pytest.raises(ValueError, match="one filesystem"):
        storage.download_to_temp(
            ["memory://test-mixed/a", str(tmp_path / "b")], str(tmp_path / "stage")
        )


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


class _Fake403(Exception):
    def __init__(self):
        super().__init__("An error occurred (403) when calling HeadObject: Forbidden")
        self.response = {"Error": {"Code": "403"}}


def test_auth_error_hint_anon_points_to_signing():
    hint = storage.auth_error_hint(_Fake403(), anon=True)
    assert hint is not None
    assert "SRC_ANON" in hint
    assert "SRC_AWS_PROFILE" in hint


def test_auth_error_hint_signed_points_to_anon():
    hint = storage.auth_error_hint(_Fake403(), anon=False)
    assert hint is not None
    assert "SRC_ANON=1" in hint


def test_auth_error_hint_ignores_non_auth_errors():
    assert storage.auth_error_hint(RuntimeError("boom"), anon=True) is None
    assert storage.auth_error_hint(RuntimeError("boom"), anon=False) is None
    assert storage.auth_error_hint(FileNotFoundError("missing"), anon=True) is None


def test_upload_tree_object_store_uses_one_put_and_no_makedirs(tmp_path, monkeypatch):
    """Upload in one ``put`` call; never ``makedirs`` on an object store.

    s3fs' ``makedirs`` tries to create the bucket when it can't see it.
    """
    src = tmp_path / "src"
    src.mkdir()
    (src / "b.bin").write_bytes(b"b")
    (src / "a.bin").write_bytes(b"a")
    puts = []

    class FakeS3:
        protocol = "s3"

        def exists(self, path):
            return False

        def makedirs(self, path, exist_ok=False):
            raise AssertionError("makedirs must not be called on an object store")

        def put(self, lpaths, rpaths, callback=None):
            puts.append((lpaths, rpaths))

    monkeypatch.setattr(
        storage,
        "resolve_fs",
        lambda url, profile=None, anon=False: (FakeS3(), "b/dest"),
    )
    uploaded = storage.upload_tree(str(src), "s3://b/dest/")
    assert uploaded == ["s3://b/dest/a.bin", "s3://b/dest/b.bin"]
    assert puts == [
        ([str(src / "a.bin"), str(src / "b.bin")], ["b/dest/a.bin", "b/dest/b.bin"])
    ]


def test_upload_tree_file_url_creates_directory(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.txt").write_text("a")
    dest = tmp_path / "new" / "dst"
    uploaded = storage.upload_tree(str(src), f"file://{dest}")
    assert uploaded == [f"file://{dest}/a.txt"]
    assert (dest / "a.txt").read_text() == "a"
