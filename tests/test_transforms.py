"""Tests for zarr_creator.transforms.derive_orography_from_geopotential.

Verifies scaling, time-dimension handling, attribute assignment, and
grid_mapping passthrough.
"""

import numpy as np
import xarray as xr

from zarr_creator.transforms import derive_orography_from_geopotential


def _make_geopotential(values, time_steps=2, grid_mapping=None):
    """Build a synthetic geopotential DataArray with (time, x, y) dims."""
    da = xr.DataArray(
        np.broadcast_to(values, (time_steps, *np.shape(values))).copy(),
        dims=("time", "x", "y"),
        coords={"time": list(range(time_steps))},
    )
    if grid_mapping is not None:
        da.attrs["grid_mapping"] = grid_mapping
    return da


def test_scales_by_inverse_gravity_and_drops_time():
    """Output values equal input / 9.82 and the time dimension is removed."""
    geopotential = _make_geopotential([[9.82, 19.64], [29.46, 39.28]])
    result = derive_orography_from_geopotential(geopotential)

    assert "time" not in result.dims
    np.testing.assert_allclose(result.values, [[1.0, 2.0], [3.0, 4.0]])


def test_uses_first_timestep():
    """Only the first timestep is used; later timesteps are ignored."""
    data = np.array([[1.0, 2.0]])
    da = xr.DataArray(
        np.stack([data * 9.82, data * 9.82 * 99]),
        dims=("time", "x", "y"),
        coords={"time": [0, 1]},
    )
    result = derive_orography_from_geopotential(da)

    np.testing.assert_allclose(result.values, [[1.0, 2.0]])


def test_sets_expected_attrs():
    """Result carries the standard orography CF attributes."""
    result = derive_orography_from_geopotential(_make_geopotential([[0.0]]))

    assert result.attrs["units"] == "m"
    assert result.attrs["standard_name"] == "surface_altitude"
    assert result.attrs["cfName"] == "surface_altitude"
    assert result.attrs["long_name"] == "Surface altitude (orography)"
    assert result.attrs["name"] == "Surface altitude (orography)"


def test_preserves_grid_mapping_when_present():
    """grid_mapping attr is forwarded from the input to the result."""
    da = _make_geopotential([[0.0]], grid_mapping="lambert_conformal_conic")
    result = derive_orography_from_geopotential(da)

    assert result.attrs["grid_mapping"] == "lambert_conformal_conic"


def test_omits_grid_mapping_when_absent():
    """grid_mapping is not added to the result if absent from the input."""
    result = derive_orography_from_geopotential(_make_geopotential([[0.0]]))

    assert "grid_mapping" not in result.attrs
