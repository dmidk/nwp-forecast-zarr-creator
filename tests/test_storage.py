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


def test_exists_and_find_missing_local(tmp_path):
    a = str(tmp_path / "a")
    _write(a)
    assert storage.exists(a)
    assert not storage.exists(str(tmp_path / "nope"))
    missing = storage.find_missing([a, str(tmp_path / "nope")])
    assert missing == [str(tmp_path / "nope")]
    assert storage.find_missing([]) == []


def test_exists_memory():
    fs, _ = storage.resolve_fs("memory://test-exists/")
    fs.pipe("memory://test-exists/f", b"x")
    assert storage.exists("memory://test-exists/f")
    assert not storage.exists("memory://test-exists/missing")
    assert storage.find_missing(
        ["memory://test-exists/f", "memory://test-exists/missing"]
    ) == ["memory://test-exists/missing"]


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
