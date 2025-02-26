#!/usr/bin/env python
# -*- coding: utf-8 -*-
import os
import sys
from loguru import logger

from .arguments import arguments
from .run import run

def cli():
    """
    Run zarr creator
    """

    modargs = arguments()
    args = modargs.get_args(sys.argv)

    logger.remove()
    logger.add(sys.stderr, level=args.log_level.upper())

    logger.debug("Arguments: {}".format(args))

    if args.source not in ["dinisf"]:
        raise ValueError("Invalid data-catalog source: {}".format(args.source))

    run(args)
    

if __name__ == "__main__":
    import ipdb
    with ipdb.launch_ipdb_on_exception():
        with logger.catch(reraise=True):
            cli()