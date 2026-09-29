#!/usr/bin/env python
# -*- coding: utf-8 -*-
import warnings

import xarray as xr
from loguru import logger

from . import storage


def write_output_zarrs(
    ds: xr.Dataset,
    output_path: str,
    rechunk_to: dict,
    profile: str | None = None,
):
    """
    Write an xarray dataset to a single zarr destination (local path or
    ``s3://`` URI, dispatched via fsspec).

    Parameters
    ----------
    ds : xarray.Dataset
        The dataset to write.
    output_path : str
        Fully formatted destination, e.g.
        ``file:///tmp/dini-recent/single_levels.zarr`` or
        ``s3://harmonie-zarr/dini/control/2025-03-02T060000Z/single_levels.zarr``.
        S3 authentication uses ``profile`` (endpoint/keys/region resolve
        from ``~/.aws`` via botocore/s3fs).
    rechunk_to : dict
        A dictionary specifying the target chunk size for each dimension.
        Only the dimensions that are present in the dataset will be used, and
        the size limited to the size of the dimension (if the chunk size
        provided is larger).
    profile : str, optional
        AWS profile for S3 destinations.
    """
    for d in ds.dims:
        dim_len = len(ds[d])
        if d in rechunk_to and rechunk_to[d] > dim_len:
            warnings.warn(
                f"Requested chunksize for dim `{d}` is larger than then dimension"
                f" size ({rechunk_to[d]} > {dim_len}). Reducing to dimension size."
            )
            rechunk_to[d] = dim_len

    target_chunks = {}
    for d in ds.dims:
        target_chunks[d] = [rechunk_to.get(d, ds[d].size)]
    for c in ds.coords:
        # target_chunks[c] = {d: target_chunks[d] for d in ds[c].dims}
        target_chunks[c] = {d: rechunk_to.get(d, ds[d].size) for d in ds[c].dims}
    for v in ds.data_vars:
        # target_chunks[v] = {d: target_chunks[d] for d in ds[v].dims}
        target_chunks[v] = {d: rechunk_to.get(d, ds[d].size) for d in ds[v].dims}

    # reset the encoding so that the zarr dataset that is written isn't written
    # with an encoding that is reliant on the gribscan package's decoding
    # functions
    ds.encoding = {}
    for var_name in ds.data_vars:
        ds[var_name].encoding = {}

    logger.info(f"Writing to {output_path}")
    fs, path = storage.resolve_fs(output_path, profile)
    target = fs.get_mapper(path, create=True)
    ds.to_zarr(target, mode="w", compute=True, consolidated=True)

    logger.info("done!")

    return
