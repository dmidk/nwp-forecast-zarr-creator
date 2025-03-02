#!/usr/bin/env python
# coding: utf-8

import datetime
import warnings

import isodate
import xarray as xr

from .write_zarr import write_zarr_to_s3

warnings.simplefilter("error", UserWarning)

DINI_CRS_WKT = """
"""


DINI_CRS_ATTRS = {
    "crs_wkt": "".join(DINI_CRS_WKT.splitlines()),
}


def _add_projection_info_to_all_variables(ds):
    """
    Add CF-compliant (http://cfconventions.org/cf-conventions/cf-conventions.html#appendix-grid-mappings)
    projection info by adding a new variable that holds the projection attributes and setting on each variable
    that this projection applies.

    NOTE: currently gribscan doesn't return the projection information attributes when parsing
    Harmonie GRIB files. For that reason the projection parameters are hardcoded here. They
    should be moved in future so that this information is returned by gribscan and set in dmidc

    Parameters
    ----------
    ds: xr.Dataset
        the dataset to add projection info to

    Returns
    -------
    Nothing
    """
    raise NotImplementedError("This function is not implemented yet")
    PROJECTION_IDENTIFIER = "dini_projection"
    ds[PROJECTION_IDENTIFIER] = xr.DataArray()
    ds[PROJECTION_IDENTIFIER].attrs.update(DINI_CRS_ATTRS)

    for var_name in ds.data_vars:
        ds[var_name].attrs["grid_mapping"] = PROJECTION_IDENTIFIER


def main(t_analysis: datetime.datetime):
    t_str = isodate.datetime_isoformat(t_analysis).replace(":", "")
    assert t_str.endswith("Z")
    fp = f"/home/ec2-user/nwp-forecast-zarr-creator/refs/CONTROL__dmi/{t_str}.jsons/isobaricInhPa.json"
    ds = xr.open_zarr(f"reference::{fp}")

    # copy over cf standard-names where eccodes provides them
    for var_name in ds.data_vars:
        if "cfName" in ds[var_name].attrs:
            ds[var_name].attrs["standard_name"] = ds[var_name].attrs["cfName"]

    # _add_projection_info_to_all_variables(ds)

    rechunk_to = dict(time=1, x=ds.x.size // 2, y=ds.y.size // 2)
    # check that with the chunking provided that the arrays exactly fit into the chunks
    for dim in rechunk_to:
        assert ds[dim].size % rechunk_to[dim] == 0

    write_zarr_to_s3(
        ds=ds,
        dataset_id="pressure_levels",
        rechunk_to=rechunk_to,
        member="control",
        t_analysis=t_analysis,
    )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--t_analysis", type=isodate.parse_datetime, required=True)
    args = parser.parse_args()
    main(t_analysis=args.t_analysis)
