#!/usr/bin/env python
# -*- coding: utf-8 -*-
import datetime
import shutil
import warnings
from pathlib import Path

import fsspec
import xarray as xr
from loguru import logger

BUCKET_NAME = "harmonie-zarr"
BUCKET_REGION = "eu-central-1"
OUTPUT_PREFIX_FORMAT = "dini/{member}/{t_analysis_formatted}/{dataset_id}.zarr"


def write_zarr_to_s3(
    ds: xr.Dataset,
    dataset_id: str,
    rechunk_to: dict,
    member: str,
    t_analysis: datetime.datetime,
    local_copy_path: str = None,
):
    """
    Write a xarray dataset to a zarr store.

    Parameters
    ----------
    ds : xarray.Dataset
        The dataset to write.
    dataset_id: str
        The dataset id, e.g. "single_levels" or "pressure_levels"
    rechunk_to : dict
        A dictionary specifying the target chunk size for each dimension.
        Only the dimensions that are present in the dataset will be used, and
        the size limited to the size of the dimension (if the chunk size
        provided is larger).
    member : str
        The forecast member name, e.g. "control"
    t_analysis : datetime.datetime
        The analysis time of the forecast.
    local_copy_path : str, optional
        If provided, a local copy of the zarr dataset will also be saved to
        this path, but without the timestamp with the filename `{part_id}.zarr`.
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

    t_analysis_formatted = t_analysis.isoformat().replace(":", "").replace("+0000", "Z")
    prefix = OUTPUT_PREFIX_FORMAT.format(
        member=member, t_analysis_formatted=t_analysis_formatted, dataset_id=dataset_id
    )

    fn_local = f"{dataset_id}.zarr"
    if local_copy_path is not None:
        Path(local_copy_path).mkdir(parents=True, exist_ok=True)
        fp_local = Path(local_copy_path) / fn_local
        if fp_local.exists():
            logger.warning(f"Local copy path {fp_local} already exists, overwriting")
            shutil.rmtree(fp_local)

        logger.info(f"Writing local copy to {fp_local}")
        ds.to_zarr(fp_local, mode="w")

    path_out = f"s3://{BUCKET_NAME}/{prefix}"
    logger.info(f"Writing to {path_out}", flush=True)
    target = fsspec.get_mapper(
        path_out,
        client_kwargs={"region_name": BUCKET_REGION},
    )
    ds.to_zarr(target, mode="w", compute=True, consolidated=True)

    logger.info("done!", flush=True)

    return
