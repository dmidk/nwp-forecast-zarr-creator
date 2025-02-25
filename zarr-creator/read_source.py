#!/usr/bin/env python
# -*- coding: utf-8 -*-
import os
import sys
import datetime
from loguru import logger
import shutil
from pathlib import Path

try:
    from collections.abc import Mapping
except ImportError:
    from collections import Mapping

import dmidc.harmonie
import dmidc.utils
from dmidc.utils import normalise_duration
import xarray as xr


def read_source(source):

    t_analysis = (datetime.datetime.utcnow() - datetime.timedelta(hours=6)).replace(
        minute=0, second=0, microsecond=0)
    t_analysis = t_analysis - datetime.timedelta(hours=t_analysis.hour % 6)

    logger.info(f"Reading source {source} at {t_analysis}")

    if source == "dinisf":
        ds = dmidc.harmonie.load(
            suite_name="DINI",
            analysis_time=t_analysis,
            data_kind="sf",
            storage_platform="pds_grib",
            pds_receive_path="/mnt/zarr-from-dini/",
            level_type="heightAboveGround",
            forecast_duration=normalise_duration(f"PT{54}H"),
        )
    else:
        logger.error(f"Unknown source {source}")
        sys.exit(1)

    return ds

# def _is_listlike(v):
#     return isinstance(v, list) or isinstance(v, tuple)


# def _ordered_set(x):
#     return sorted(set(x), key=x.index)


# def create_zarr_dataset(
#     fp_temp,
#     fp_out,
#     analysis_time,
#     rechunk_to,
#     level_type,
#     variables,
#     levels=None,
#     level_name_mapping=None,
#     max_memory="128MB",
# ):
#     """
#     Produce a zarr store with the given variables and levels, rechunked to the given chunk sizes.

#     Parameters
#     ----------
#     analysis_time : datetime.datetime or slice(datetime.datetime, datetime.datetime)
#         The analysis time or interval of analysis times to create a dataset for.
#         NB: when a slice is provided the end time will not be included which ensures
#         we can concatenate subsets over time without having duplicate time values
#     fp_out : str
#         File path to the output zarr store
#     rechunk_to : dict
#         Dictionary specifying the chunk sizes to rechunk to. Keys are dimension names, values are chunk sizes.
#         e.g. {'time': 4, 'x': 512, 'y': 512}
#     level_type : str
#         Level type to load, e.g. 'isobaricInhPa' or 'heightAboveGround'
#     variables : dict or str
#         Dictionary of variables to load, e.g. {'t': [850, 500]} or ['t', 'w']
#         if a dict, levels must be None
#     levels : list or None
#         List of levels to load, e.g. [850, 500]
#         if a list, variables must be a list of variable names
#     level_name_mapping : str or None
#         String to use to format the variable with a given level type
#         e.g. "{var_name}{level}hPa", "{var_name}{level}m" where var_name is the
#         variable name and level is the level value
#     """
#     # remove fp_out directory if it exists
#     if Path(fp_out).is_dir():
#         shutil.rmtree(fp_out)

#     def _get_levels_and_variables():
#         # need to match `dict` and `luigi.freezing.FrozenOrderedDict` which both subclass Mapping
#         if isinstance(variables, Mapping):
#             # e.g. {'t': [850, 500], 'w': [850, 500, 100]}
#             if levels is not None:
#                 raise ValueError("if variables is a dict, levels must be None")
#             levels_per_variable = variables

#             # first, check if all variables are to be extracted on the same levels, in which case we can load them all at once
#             levels_first_variable = _ordered_set(
#                 next(iter(levels_per_variable.values()))
#             )
#             if all(
#                 levels_first_variable == _ordered_set(var_levels)
#                 for var_levels in levels_per_variable.values()
#             ):
#                 yield list(levels_first_variable), list(variables.keys())
#                 return

#             # otherwise group the variables so that we can load all given on the same level at once
#             # e.g. {'t': [850, 500], 'w': [850, 500, 100]} -> {850: ['t', 'w'], 500: ['t', 'w'], 100: ['w']}

#             # first, get a list of all the levels
#             all_levels = []
#             for var_name, var_levels in levels_per_variable.items():
#                 all_levels.extend(var_levels)
#             all_levels = list(_ordered_set(all_levels))

#             # now, for each level, get the variables that are defined for that level
#             for level in all_levels:
#                 var_names = []
#                 for var_name, var_levels in levels_per_variable.items():
#                     if level in var_levels:
#                         var_names.append(var_name)
#                 yield level, var_names

#         elif _is_listlike(variables) and _is_listlike(levels):
#             for level in levels:
#                 yield level, sorted(variables)
#         else:
#             raise NotImplementedError(variables, levels)

#     datasets = []

#     def _flatten_time_dims(ds_, temp_dim_name="_time_"):
#         """
#         since we're only using analysis time, we can drop the `time` dimension,
#         but we need to take all the analysis times where the time dimension is
#         the same
#         """
#         ds_ = ds_.stack({temp_dim_name: ["time", "analysis_time"]})
#         ds_ = ds_.where(ds_.time == ds_.analysis_time, drop=True)
#         da_times = ds_.analysis_time
#         ds_ = (
#             ds_.reset_index(temp_dim_name)
#             .drop(["time", "analysis_time"])
#             .rename({temp_dim_name: "time"})
#         )
#         ds_["time"] = da_times.values
#         return ds_

#     if analysis_time.start != analysis_time.stop:
#         # when providing dmidc with a slice for the `analysis_time` argument it
#         # will return data for the entire time range (i.e from
#         # `analysis_time.start` to and including `analysis_time.stop`)
#         # However, for the concatenation to work without duplicating time-values
#         # further down the pipeline we don't want `analysis_time.stop` to be
#         # included. By subtracking a single second from the end time in the slice
#         # this is avoided
#         analysis_time_extraction = slice(
#             analysis_time.start, analysis_time.stop - datetime.timedelta(seconds=1)
#         )
#     else:
#         analysis_time_extraction = analysis_time

#     for level, var_names in _get_levels_and_variables():
#         logger.info(f"loading {var_names} on {level_type} level {level}")

#         if level_type == "CONSTANTS":
#             # this is a hack, but I've used the "level-type" called "CONSTANTS"
#             # here to indicate that I want to load a constant field
#             level_type = None
#             data_kind = "CONSTANTS"
#             time_range_indicator = None
#             # when reading constants dmidc doesn't allow for a list of levels currently
#             if len(level) == 1:
#                 level = level[0]
#         else:
#             data_kind = "ANALYSIS"
#             time_range_indicator = "instantaneous"

#         try:
#             ds = dmidc.harmonie.load(
#                 analysis_time=analysis_time_extraction,
#                 suite_name="DANRA",
#                 data_kind=data_kind,
#                 temp_filepath=fp_temp,
#                 short_name=var_names,
#                 level_type=level_type,
#                 level=level,
#                 time_range_type=time_range_indicator,
#             )
#         except Exception as ex:
#             logger.error(ex)
#             raise

#         if data_kind == "CONSTANTS":
#             # no mapping from levels to separate variables for the constant fields
#             # But we don't want to use `z` shortname since that often refers to
#             # just altitude, `orography` is clearer
#             if "z" in ds.data_vars:
#                 ds = ds.rename(dict(z="orography"))
#         elif level_name_mapping is None:
#             # this means we are not mapping level (coordinate) values to
#             # individual variables
#             if "level" not in ds.coords:
#                 raise NotADirectoryError(ds.coords)
#             # doing selection here ensures we get the same order as the levels
#             # were provided in with `level`
#             ds = ds.sel(level=level)
#         else:
#             if _is_listlike(level) and len(level) != 1:
#                 # for now I've only implemented turning a single level into a
#                 # variable
#                 raise NotImplementedError(level_name_mapping)

#             for var_name in var_names:
#                 da_var = ds[var_name]
#                 ds = ds.drop(var_name)
#                 da_var.attrs["level"] = level
#                 new_var_name = level_name_mapping.format(var_name=var_name, level=level)
#                 ds[new_var_name] = da_var

#         if all(d in ds.dims for d in ["time", "analysis_time"]):
#             ds = _flatten_time_dims(ds)

#         datasets.append(ds)

#     if data_kind == "CONSTANTS":
#         ds = datasets[0]
#     elif level_name_mapping is None:
#         ds = xr.concat(datasets, dim="level")
#     else:
#         ds = xr.merge(datasets)

#     # TODO: at some point `dmidc.harmonie.load` won't return this anymore
#     if "forecast_duration" in ds:
#         ds = ds.drop("forecast_duration")

#     if level_name_mapping is None:
#         # TODO: this should really be done in dmidc.harmonie.load
#         if level_type == "isobaricInhPa":
#             ds.coords["level"].attrs["units"] = "hPa"
#             ds = ds.rename(dict(level="pressure"))
#         elif level_type in ["heightAboveGround", "heightAboveSea"]:
#             ds.coords["level"].attrs["units"] = "m"
#             ds = ds.rename(dict(level="altitude"))
#         elif level_type in ["entireAtmosphere", "nominalTop", "surface"]:
#             pass
#         elif data_kind == "CONSTANTS" and level_type is None:
#             pass
#         else:
#             raise NotImplementedError(level_type)

#     if data_kind != "CONSTANTS":
#         # check that all time-steps have the same length, it appears that some
#         # output files are sometimes incomplete... we need to check that the source
#         # files have been fixed
#         da_dt = ds.time.diff(dim="time")
#         if not da_dt.min() == da_dt.max():
#             raise Exception(
#                 "Not all time increments are the same in the produced zarr dataset."
#                 " Maybe some of the loaded GRIB files were incomplete?"
#             )
    

#     ds_zarr = xr.open_zarr(ds)
#     logger.info(ds_zarr)

#     logger.info(f"{fp_out} done!", flush=True)

