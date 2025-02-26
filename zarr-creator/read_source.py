#!/usr/bin/env python
# -*- coding: utf-8 -*-
import datetime
import sys

import dmidc.harmonie
import dmidc.utils
import gribscan
import xarray as xr
from dmidc.utils import normalise_duration
from loguru import logger

# set the eccodes definitions path, older versions of eccodes require this
gribscan.eccodes.codes_set_definitions_path("/usr/share/eccodes/definitions")


def read_source(
    source_name: str, t_analysis: datetime.datetime, forecast_duration: str
) -> xr.Dataset:
    """
    Read the source data for the given source name, analysis time and forecast duration.

    Parameters
    ----------
    source_name : str
        The name of the source to read, e.g. "dinisf".
    t_analysis : iso8601 datetime string
        The analysis time, e.g. "2025-02-17T01:00:00Z".
    forecast_duration : iso8601 duration string
        The forecast duration, e.g. "PT3H".
    """
    logger.info(f"Reading source {source_name} at {t_analysis}")

    if source_name == "dinisf":
        ds = dmidc.harmonie.load(
            suite_name="DINI",
            analysis_time=t_analysis,
            data_kind="sf",
            storage_platform="pds_grib",
            short_name="u",
            level=10,
            pds_receive_path="/mnt/zarr-from-dini/",
            level_type="heightAboveGround",
            forecast_duration=normalise_duration(forecast_duration),
        )
    else:
        logger.error(f"Unknown source {source_name}")
        sys.exit(1)

    return ds
