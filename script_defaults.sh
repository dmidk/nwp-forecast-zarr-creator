#!/bin/bash

# Shared default configuration for runtime shell scripts.
# Values can still be overridden by environment variables.

: "${SRC_GRIB_ROOT_PATH:=/mnt/harmonie-data-from-pds/ml}"
: "${REFS_ROOT_PATH:=/home/ec2-user/nwp-forecast-zarr-creator/refs}"
: "${MEMBER_ID:=CONTROL__dmi}"
: "${MAX_HOUR:=36}"

# SRC_GRIB_TEMP_PATH is intentionally not set here.
# If SRC_GRIB_TEMP_PATH is set (env var or second arg to build script), GRIB
# files are copied before indexing. If unset, scripts index directly from
# SRC_GRIB_ROOT_PATH.
