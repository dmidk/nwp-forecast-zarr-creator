import xarray as xr


def derive_orography_from_geopotential(da: xr.DataArray) -> xr.DataArray:
    result = (da.isel(time=0, drop=True) * (1.0 / 9.82)).assign_attrs(
        {
            "units": "m",
            "standard_name": "surface_altitude",
            "cfName": "surface_altitude",
            "long_name": "Surface altitude (orography)",
            "name": "Surface altitude (orography)",
        }
    )
    if "grid_mapping" in da.attrs:
        result.attrs["grid_mapping"] = da.attrs["grid_mapping"]
    return result
