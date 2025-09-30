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


ANALYSIS_TIME=$1
ROOT_PATH="/mnt/harmonie-data-from-pds/ml"
REFS_ROOT_PATH="/home/ec2-user/nwp-forecast-zarr-creator/refs"
MEMBER_ID="CONTROL__dmi"

if [ -z "$ANALYSIS_TIME" ]; then
    echo "usage: $0 <analysis_time> [temp_root]"
    echo "  analysis_time: analysis time in ISO 8601 format (YYYY-MM-DDTHH:MM:SSZ)"
    exit 1
fi

# set the temp root if it's provided
if [ -n "$2" ]; then
    TEMP_ROOT=$2
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
ANALYSIS_TIME_STR=$(date -d $ANALYSIS_TIME +%Y%m%d%H)

# check that the necessary GRIB files exist
# the files don't always arrive in order, so we need to check all of them
for type in sf pl; do
    for i in {00..36}; do
        if [ ! -f "$ROOT_PATH/fc${ANALYSIS_TIME_STR}+0${i}${MEMBER_ID}_${type}" ]; then
            echo "File $ROOT_PATH/fc${ANALYSIS_TIME_STR}+0${i}${MEMBER_ID}_${type} does not exist"
            exit 1
        fi
    done
done

mkdir -p $TEMP_ROOT
for type in sf pl; do
    SRC_PATH=""
    if [ $COPY_GRIB_BEFORE_INDEXING -eq 1 ]; then
        echo "Copying from $ROOT_PATH to $TEMP_ROOT"
        # cp $ROOT_PATH/fc${ANALYSIS_TIME_STR}+0{00..01}${MEMBER_ID}_${type} $TEMP_ROOT
        # use rsync with --progress to show progress
        rsync -av --progress $ROOT_PATH/fc${ANALYSIS_TIME_STR}+0{00..36}${MEMBER_ID}_${type} $TEMP_ROOT
        # check exist code and exit if not 0
        if [ $? -ne 0 ]; then
            echo "Failed to copy files"
            exit 1
        fi
        SRC_PATH=$TEMP_ROOT
    else
        SRC_PATH=$ROOT_PATH
    fi

    echo "Indexing $type files"
    # we can't just call the `gribscan-index` command line tool here because we
    # need to set the local GRIB2 defininitions path and that is only possible
    # with the eccodes python packge with the call
    # `eccodes.codes_set_definitions_path(...)`. Unfortunately using the
    # `ECCODES_DEFINITION_PATH` doesn't work with the python package, and so we
    # must wrap the `gribscan-index` call.`
    uv run python -m zarr_creator.build_indexes $SRC_PATH/fc${ANALYSIS_TIME_STR}+0{00..36}${MEMBER_ID}_${type} -n 2

    echo "Building refs for $type files"
    gribscan-build $SRC_PATH/fc${ANALYSIS_TIME_STR}+???${MEMBER_ID}_${type}.index \
        -o ${REFS_ROOT_PATH}/${MEMBER_ID}/${ANALYSIS_TIME//:/}.jsons\
        --prefix $SRC_PATH/ \
        -m harmonie
done
