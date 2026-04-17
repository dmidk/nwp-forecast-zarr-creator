import numpy as np
import xarray as xr

from zarr_creator.transforms import (
    apply_variable_transforms,
    resolve_variable_transforms,
)


def test_apply_variable_transforms_renames_and_drops_time_dimension():
    ds = xr.Dataset(
        data_vars={
            "z0m": (("time", "x", "y"), np.arange(2 * 3 * 4).reshape(2, 3, 4)),
            "t2m": (("time", "x", "y"), np.ones((2, 3, 4))),
        },
        coords={
            "time": [0, 1],
            "x": [0, 1, 2],
            "y": [0, 1, 2, 3],
        },
    )

    transformed = apply_variable_transforms(
        ds,
        scale_map={"z0m": 1.0 / 9.82},
        rename_map={"z0m": "orography"},
        attrs_map={
            "orography": {
                "units": "m",
                "standard_name": "surface_altitude",
                "cfName": "surface_altitude",
                "long_name": "Surface altitude (orography)",
                "name": "Surface altitude (orography)",
            }
        },
        drop_time_dimension_for=["orography"],
    )

    assert "z0m" not in transformed.data_vars
    assert "orography" in transformed.data_vars
    assert transformed["orography"].dims == ("x", "y")
    assert transformed["t2m"].dims == ("time", "x", "y")
    np.testing.assert_allclose(
        transformed["orography"].values,
        ds["z0m"].isel(time=0).values / 9.82,
    )
    assert transformed["orography"].attrs["units"] == "m"
    assert transformed["orography"].attrs["standard_name"] == "surface_altitude"
    assert transformed["orography"].attrs["cfName"] == "surface_altitude"
    assert transformed["orography"].attrs["name"] == "Surface altitude (orography)"


def test_apply_variable_transforms_raises_on_rename_collision():
    ds = xr.Dataset(
        data_vars={
            "a": (("x",), [1, 2]),
            "b": (("x",), [3, 4]),
        },
        coords={"x": [0, 1]},
    )

    try:
        apply_variable_transforms(ds, rename_map={"a": "b"})
    except ValueError as exc:
        assert "already exists" in str(exc)
    else:
        raise AssertionError("Expected ValueError for rename collision")


def test_resolve_variable_transforms_ignores_missing_variables_and_keeps_renamed_drop_targets():
    ds_without_target = xr.Dataset(
        data_vars={
            "t2m": (("time", "x", "y"), np.ones((2, 3, 4))),
        },
        coords={"time": [0, 1], "x": [0, 1, 2], "y": [0, 1, 2, 3]},
    )

    rename_map, scale_map, attrs_map, drop_time_dimension_for = (
        resolve_variable_transforms(
            ds_without_target,
            rename_map={"z0m": "orography"},
            scale_map={"z0m": 1.0 / 9.82},
            attrs_map={"z0m": {"units": "m"}},
            drop_time_dimension_for=["orography"],
        )
    )

    assert rename_map == {}
    assert scale_map == {}
    assert attrs_map == {}
    assert drop_time_dimension_for == []

    ds_with_target = xr.Dataset(
        data_vars={
            "z0m": (("time", "x", "y"), np.arange(2 * 3 * 4).reshape(2, 3, 4)),
        },
        coords={"time": [0, 1], "x": [0, 1, 2], "y": [0, 1, 2, 3]},
    )

    rename_map, scale_map, attrs_map, drop_time_dimension_for = (
        resolve_variable_transforms(
            ds_with_target,
            rename_map={"z0m": "orography"},
            scale_map={"z0m": 1.0 / 9.82},
            attrs_map={"z0m": {"units": "m"}},
            drop_time_dimension_for=["orography"],
        )
    )

    assert rename_map == {"z0m": "orography"}
    assert scale_map == {"z0m": 1.0 / 9.82}
    assert attrs_map == {"orography": {"units": "m"}}
    assert drop_time_dimension_for == ["orography"]

    transformed = apply_variable_transforms(
        ds_with_target,
        rename_map=rename_map,
        scale_map=scale_map,
        attrs_map=attrs_map,
        drop_time_dimension_for=drop_time_dimension_for,
    )

    assert "orography" in transformed.data_vars
    assert transformed["orography"].dims == ("x", "y")
    assert transformed["orography"].attrs["units"] == "m"


def test_apply_variable_transforms_scales_variable():
    ds = xr.Dataset(
        data_vars={
            "z0m": (("x", "y"), np.array([[9.82, 19.64], [29.46, 39.28]])),
        },
        coords={"x": [0, 1], "y": [0, 1]},
    )

    transformed = apply_variable_transforms(ds, scale_map={"z0m": 1.0 / 9.82})
    np.testing.assert_allclose(
        transformed["z0m"].values,
        np.array([[1.0, 2.0], [3.0, 4.0]]),
    )
