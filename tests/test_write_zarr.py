"""Tests for the single-destination zarr output (Step 5)."""

import numpy as np
import xarray as xr

from zarr_creator.write_zarr import write_output_zarrs


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
