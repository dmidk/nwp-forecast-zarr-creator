#!/usr/bin/env python
# -*- coding: utf-8 -*-
import os
import warnings
import sys
from loguru import logger
from pathlib import Path
import fsspec
import rechunker
from zarr.convenience import consolidate_metadata
import xarray as xr
import shutil
import tempfile


def write_zarr(ds, fp_out, overwrite=True, temp_dir=None):
    """
    Write a xarray dataset to a zarr store.

    Parameters
    ----------
    ds : xarray.Dataset
        The dataset to write.

    fp_out : Path
        The path to the zarr store to create.
    """

    if not fp_out.name.endswith(".zarr"):
        raise Exception(f"fp_out must end with .zarr, got {fp_out}")

    zarr_filepath = fp_out
    zarr_filepath.parent.mkdir(parents=True, exist_ok=True)
    
    if Path(fp_out).exists():
        if overwrite:
            shutil.rmtree(fp_out)
        else:
            logger.error(f"{fp_out} already exists. Set overwrite=True to overwrite.")



    fs = fsspec.filesystem("file")
    mapper = fs.get_mapper(fp_out)


    rechunk_to = dict(time=54, x=300, y=260)

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

    # print(ds)
    # print(rechunk_to)
    print(target_chunks)

    # remove chunks encoding
    for v in ds.data_vars:
        if "chunks" in ds[v].encoding:
            del ds[v].encoding["chunks"]
            # del ds[v].encoding["compressor"]
            # del ds[v].encoding["preferred_chunks"]
            # del ds[v].encoding["filters"]
            print(ds[v].encoding)
            print(f"removed chunks encoding for {v}")
   
    del ds.x.encoding["chunks"]
    del ds.y.encoding["chunks"]
    del ds.time.encoding["chunks"]
    del ds.level.encoding["chunks"]
    del ds.lat.encoding["chunks"]
    del ds.lon.encoding["chunks"]

    # del ds.x.encoding["preferred_chunks"]
    # del ds.y.encoding["preferred_chunks"]
    # del ds.time.encoding["preferred_chunks"]
    # del ds.level.encoding["preferred_chunks"]
    # del ds.lat.encoding["preferred_chunks"]
    # del ds.lon.encoding["preferred_chunks"]

    # del ds.x.encoding["compressor"]
    # del ds.y.encoding["compressor"]
    # del ds.time.encoding["compressor"]
    # del ds.level.encoding["compressor"]
    # del ds.lat.encoding["compressor"]
    # del ds.lon.encoding["compressor"]

    # del ds.x.encoding["filters"]
    # del ds.y.encoding["filters"]
    # del ds.time.encoding["filters"]
    # del ds.level.encoding["filters"] 
    # del ds.lat.encoding["filters"]
    # del ds.lon.encoding["filters"]

    print(ds)
    print(ds.x.encoding)
    print(f"Type of ds: {type(ds)}")  # Should be <xarray.Dataset>
    print(f"Type of fp_out: {type(fp_out)}")  # Should be <pathlib.Path>
    print(f"fp_out: {fp_out}")  # Check if it’s a valid path
    print(f"Target Chunks: {target_chunks}")  # Debug output

    print(f"Type of target_store: {type(target_store)}")
    
    ds.encoding = {}
    ds.to_zarr(target_store, mode="w", compute=True, consolidated=True)


    # try:
    #     r = rechunker.rechunk(
    #         ds,
    #         target_chunks=target_chunks,
    #         max_mem="1GB",
    #         target_store=target_store, # open("/dmidata/scratch/10day/kah/dini2.zarr","wb"),
    #         temp_store=temp_dir,
    #     )

    #     logger.info(f"writing to {fp_out}")
    #     print(f"Type of ds: {type(ds)}")  # Should be <xarray.Dataset>
    #     print(f"Type of target_store: {type(target_store)}")  # Should be a Zarr store, not a string
    #     print(f"Type of temp_store: {type(temp_dir)}")  # Should be <pathlib.Path>
    #     print(f"Target store path: {target_store}")  # Check if it's a valid Zarr store
    #     print(f"Temp store path: {temp_dir}")  # Check if temp directory exists

    #     r.execute()
    #     logger.info(r)
    # finally:
    #     shutil.rmtree(temp_dir)

    # consolidate_metadata(mapper)

    logger.info("done!", flush=True)

    return
