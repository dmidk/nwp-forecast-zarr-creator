#!/bin/bash

# Create indexes and refs for a single set of Harmonie forecast files.
# This should eventually be triggered when the last file of a forecast has been
# uploaded to PDS to the S3 bucket. And it should probably be rewritten in
# python too.
#
# Usage: ./build_indexes_and_refs.sh <analysis_time>
#   analysis_time: analysis time in ISO 8601 format (YYYY-MM-DDTHH:MM:SS)
#
# NB: for now we only process the first 12 hrs
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
# └── fc2025030200_CONTROL__dmi.jsons
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
TEMP_ROOT="$HOME/tmp"
REFS_ROOT_PATH="refs"
MEMBER_ID="CONTROL__dmi"
COPY_GRIB_BEFORE_INDEXING=1

if [ -z "$ANALYSIS_TIME" ]; then
    echo "usage: $0 <analysis_time>"
    echo "  analysis_time: analysis time in ISO 8601 format (YYYY-MM-DDTHH:MM:SSZ)"
    exit 1
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

# check that the 60th file exists
for type in sf pl; do
    if [ ! -f "$ROOT_PATH/fc${ANALYSIS_TIME_STR}+012${MEMBER_ID}_${type}" ]; then
        echo "File $ROOT_PATH/fc${ANALYSIS_TIME_STR}+012${MEMBER_ID}_${type} does not exist"
        exit 1
    fi
done

mkdir -p $TEMP_ROOT
for type in sf pl; do
    SRC_PATH=""
    if [ $COPY_GRIB_BEFORE_INDEXING -eq 1 ]; then
        echo "Copying from $ROOT_PATH to $TEMP_ROOT"
        # cp $ROOT_PATH/fc${ANALYSIS_TIME_STR}+0{00..01}${MEMBER_ID}_${type} $TEMP_ROOT
        # use rsync with --progress to show progress
        rsync -av --progress $ROOT_PATH/fc${ANALYSIS_TIME_STR}+0{00..12}${MEMBER_ID}_${type} $TEMP_ROOT
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
    gribscan-index $SRC_PATH/fc${ANALYSIS_TIME_STR}+0{00..12}${MEMBER_ID}_${type} -n 2

    echo "Building refs for $type files"
    gribscan-build $SRC_PATH/fc${ANALYSIS_TIME_STR}+???${MEMBER_ID}_${type}.index \
        -o ${REFS_ROOT_PATH}/${MEMBER_ID}/${ANALYSIS_TIME//:/}.jsons\
        --prefix $SRC_PATH/ \
        -m harmonie
done
