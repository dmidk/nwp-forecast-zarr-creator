#!/usr/bin/env python
# -*- coding: utf-8 -*-
import os
import sys
import datetime
from loguru import logger

import dmidc.harmonie
from dmidc.utils import normalise_duration

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
            storage_platform="scale_grib",
            level_type="heightAboveGround",
            forecast_duration=normalise_duration(f"PT{54}H"),
        )
    else:
        logger.error(f"Unknown source {source}")
        sys.exit(1)

    return ds
