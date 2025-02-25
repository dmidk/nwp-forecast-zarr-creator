#!/usr/bin/env python
# -*- coding: utf-8 -*-
import os
import sys
from loguru import logger
from pathlib import Path
from .read_source import read_source
from .write_zarr import write_zarr

import numpy as np

def run(args):
    logger.info("Creating zarr dataset from source: {}".format(args.source))

    ds = read_source(args.source)

    write_zarr(ds, Path(args.output))