#!/usr/bin/env python
# -*- coding: utf-8 -*-
import shutil
import tempfile
import warnings
from pathlib import Path

import fsspec
from loguru import logger


def write_zarr(ds, fp_out, rechunk_to, overwrite=True, temp_dir=None):
    """
    Write a xarray dataset to a zarr store.

    Parameters
    ----------
    ds : xarray.Dataset
        The dataset to write.
    fp_out : Path
        The path to the zarr store to create.
    rechunk_to : dict
        A dictionary specifying the target chunk size for each dimension.
        Only the dimensions that are present in the dataset will be used, and
        the size limited to the size of the dimension (if the chunk size
        provided is larger).
    overwrite : bool
        Whether to overwrite the zarr store if it already exists.
    temp_dir : Path
        The temporary directory to use for intermediate files. If None, a
        temporary directory will be created.
    """
    fp_out = Path(fp_out)

    if not fp_out.name.endswith(".zarr"):
        raise Exception(f"fp_out must end with .zarr, got {fp_out}")

    fp_out.parent.mkdir(parents=True, exist_ok=True)

    if Path(fp_out).exists():
        if overwrite:
            shutil.rmtree(fp_out)
        else:
            logger.error(f"{fp_out} already exists. Set overwrite=True to overwrite.")

    fs = fsspec.filesystem("file")
    mapper = fs.get_mapper(fp_out)

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

    target_store = mapper

    if temp_dir is None:
        temp_dir = Path(tempfile.TemporaryDirectory().name)

    # reset the encoding so that the zarr dataset that is written isn't written
    # with an encoding that is reliant on the gribscan package's decoding
    # functions
    ds.encoding = {}
    for var_name in ds.data_vars:
        ds[var_name].encoding = {}
    ds.to_zarr(target_store, mode="w", compute=True, consolidated=True)

    logger.info("done!", flush=True)

    return
