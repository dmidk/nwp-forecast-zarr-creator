#!/bin/bash

# Create indexes and refs for a single set of Harmonie forecast files.
# This should eventually be triggered when the last file of a forecast has been
# uploaded to PDS to the S3 bucket. And it should probably be rewritten in
# python too.
#
# Usage: ./build_indexes_and_refs.sh <analysis_time>
#   analysis_time: analysis time in ISO 8601 format (YYYY-MM-DDTHH:MM:SS)
#
# NB: for now we only process the first 36 hours
#
# Running the script as for example:
#   ./build_indexes_and_refs.sh 2025-03-02T00:00
#
# Will result in the following GRIB files being indexed:
#   /mnt/harmonie-data-from-pds/ml/fc2025030200+000CONTROL__dmi_sf
#   /mnt/harmonie-data-from-pds/ml/fc2025030200+000CONTROL__dmi_pl
#   ...
#   /mnt/harmonie-data-from-pds/ml/fc2025030200+012CONTROL__dmi_sf
#   /,nt/harmonie-data-from-pds/ml/fc2025030200+012CONTROL__dmi_pl
#
# With the refs written as:
# refs/
# └── control/2025-03-02T0000Z.jsons
#     ├── adiabaticCondensation.json
#     ├── cloudTop.json
#     ├── entireAtmosphere.json
#     ├── freeConvection.json
#     ├── heightAboveGround.json
#     ├── heightAboveSea.json
#     ├── hybrid.json
#     ├── isobaricInhPa.json
#     ├── isothermal.json
#     ├── isothermZero.json
#     ├── neutralBuoyancy.json
#     ├── nominalTop.json
#     └── surface.json
#
# i.e. the "sf" and "pl" files are indexed and ref'ed into a single output directory

# fail if any variables used are not defined, this ensures we don't try copying
# to SRC_GRIB_TEMP_PATH if it's not set
set -u
# fail if any command has non-zero exit code
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/script_defaults.sh"

ANALYSIS_TIME="${1:-}"

if [ -z "$ANALYSIS_TIME" ]; then
    echo "usage: $0 <analysis_time> [temp_root]"
    echo "  analysis_time: analysis time in ISO 8601 format (YYYY-MM-DDTHH:MM:SSZ)"
    echo "  MAX_HOUR env var controls max forecast hour (default: ${MAX_HOUR})"
    echo "  temp_root or SRC_GRIB_TEMP_PATH env var enables copy-before-indexing"
    exit 1
fi

# set the temp root if it's provided as second arg
if [ $# -ge 2 ] && [ -n "$2" ]; then
    SRC_GRIB_TEMP_PATH="$2"
fi

if [ -n "${SRC_GRIB_TEMP_PATH:-}" ]; then
    COPY_GRIB_BEFORE_INDEXING=1
else
    COPY_GRIB_BEFORE_INDEXING=0
fi

# check that the analysis_time ends with Z so that we're sure it's UTC
if [[ ! $ANALYSIS_TIME =~ Z$ ]]; then
    echo "analysis_time must end with Z"
    exit 1
fi

# there are two types of files, ending with _sf and _pl, we need to index both
# files are stored as the following example:
# fc2025030206+042CONTROL__dmi_pl, i.e. the format is
# fc<analysis_time>+<forecast_hour><member_id>_<type>

# use `date` to format the analysis time to the format used in the file names
ANALYSIS_TIME_STR=$(date -d "$ANALYSIS_TIME" +%Y%m%d%H)
# use a normalized minute-resolution refs directory name everywhere
ANALYSIS_REFS_DIR=$(date -d "$ANALYSIS_TIME" -u +"%Y-%m-%dT%H%MZ")

# check that the necessary GRIB files exist
# the files don't always arrive in order, so we need to check all of them
forecast_hours=()
for hour in $(seq 0 "$MAX_HOUR"); do
    forecast_hours+=("$(printf "%03d" "$hour")")
done

for type in sf pl; do
    for hour in "${forecast_hours[@]}"; do
        if [ ! -f "$SRC_GRIB_ROOT_PATH/fc${ANALYSIS_TIME_STR}+${hour}${MEMBER_ID}_${type}" ]; then
            echo "File $SRC_GRIB_ROOT_PATH/fc${ANALYSIS_TIME_STR}+${hour}${MEMBER_ID}_${type} does not exist"
            exit 1
        fi
    done
done

if [ "$COPY_GRIB_BEFORE_INDEXING" -eq 1 ]; then
    echo "Using temporary root $SRC_GRIB_TEMP_PATH and copying GRIB files before indexing"
    mkdir -p "$SRC_GRIB_TEMP_PATH"
else
    echo "No temporary root provided, will index GRIB files directly from $SRC_GRIB_ROOT_PATH"
fi

for type in sf pl; do
    SRC_PATH=""
    source_files=()
    for hour in "${forecast_hours[@]}"; do
        source_files+=("$SRC_GRIB_ROOT_PATH/fc${ANALYSIS_TIME_STR}+${hour}${MEMBER_ID}_${type}")
    done

    if [ "$COPY_GRIB_BEFORE_INDEXING" -eq 1 ]; then
        echo "Copying from $SRC_GRIB_ROOT_PATH to $SRC_GRIB_TEMP_PATH"
        rsync -av --progress "${source_files[@]}" "$SRC_GRIB_TEMP_PATH"
        # check exist code and exit if not 0
        if [ $? -ne 0 ]; then
            echo "Failed to copy files"
            exit 1
        fi
        SRC_PATH=$SRC_GRIB_TEMP_PATH
    else
        SRC_PATH=$SRC_GRIB_ROOT_PATH
    fi

    echo "Indexing $type files"
    # we can't just call the `gribscan-index` command line tool here because we
    # need to set the local GRIB2 defininitions path and that is only possible
    # with the eccodes python packge with the call
    # `eccodes.codes_set_definitions_path(...)`. Unfortunately using the
    # `ECCODES_DEFINITION_PATH` doesn't work with the python package, and so we
    # must wrap the `gribscan-index` call.`
    index_inputs=()
    for hour in "${forecast_hours[@]}"; do
        index_inputs+=("$SRC_PATH/fc${ANALYSIS_TIME_STR}+${hour}${MEMBER_ID}_${type}")
    done
    uv run python -m zarr_creator.build_indexes "${index_inputs[@]}" -n 2

    echo "Building refs for $type files"
    index_files=()
    for hour in "${forecast_hours[@]}"; do
        index_files+=("$SRC_PATH/fc${ANALYSIS_TIME_STR}+${hour}${MEMBER_ID}_${type}.index")
    done
    uv run gribscan-build "${index_files[@]}" \
        -o ${REFS_ROOT_PATH}/${MEMBER_ID}/${ANALYSIS_REFS_DIR}.jsons\
        --prefix "$SRC_PATH"/ \
        -m harmonie
done
