"""Tests for the single-destination zarr output (Step 5)."""

import numpy as np
import xarray as xr
import zarr

from zarr_creator.write_zarr import ZARR_FORMAT, write_output_zarrs


def _ds():
    return xr.Dataset(
        {"t": (("x", "y"), np.ones((4, 4)))},
        coords={"x": range(4), "y": range(4)},
    )


def test_write_local_file_url_roundtrip(tmp_path):
    out = f"file://{tmp_path}/single_levels.zarr"
    write_output_zarrs(_ds(), out, rechunk_to=dict(x=2, y=2))
    back = xr.open_zarr(out)
    assert back["t"].shape == (4, 4)


def test_write_plain_local_path(tmp_path):
    out = str(tmp_path / "nested" / "part.zarr")
    write_output_zarrs(_ds(), out, rechunk_to=dict(x=10, y=10))
    back = xr.open_zarr(out)
    assert back["t"].shape == (4, 4)


def test_write_uses_configured_zarr_format(tmp_path):
    out = str(tmp_path / "part.zarr")
    write_output_zarrs(_ds(), out, rechunk_to=dict(x=2, y=2))
    assert zarr.open_group(out, mode="r").metadata.zarr_format == ZARR_FORMAT


def test_write_stores_chunk_equal_to_default_fill(tmp_path):
    # xarray encodes a single time as [0] (days since itself); zarr-python 3
    # skips storing such a chunk by default, and zarr-python 2 readers then
    # can't decode the time
    ds = xr.Dataset(coords={"time": [np.datetime64("2026-11-18T00:00", "ns")]})
    out = str(tmp_path / "part.zarr")
    write_output_zarrs(ds, out, rechunk_to={})
    time = zarr.open_group(out, mode="r")["time"]
    assert time.nchunks_initialized == time.nchunks
