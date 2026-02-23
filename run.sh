#!/bin/bash
# every hour run the script

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/script_defaults.sh"

# print env vars for debugging
echo "SRC_GRIB_ROOT_PATH: ${SRC_GRIB_ROOT_PATH}"
echo "REFS_ROOT_PATH: ${REFS_ROOT_PATH}"
echo "SRC_GRIB_TEMP_PATH: ${SRC_GRIB_TEMP_PATH:-not set}"

while true; do
    # find the nearest three hour interval to the current time, e.g. 00:00,
    # 03:00, 06:00, etc. get the current time in utc
    #
    # Get the current time in seconds since epoch
    now=$(date +%s)
    # Subtract 2.0 hours (7200 seconds) to get the time of the previous 3-hour interval
    adjusted_time=$((now - 7200))
    # Calculate the nearest past 3-hour interval
    rounded_time=$((adjusted_time / 10800 * 10800))
    # Convert back to human-readable format
    rounded_time_str=$(date -d "@$rounded_time" +"%H:%M")
    # format the time in iso8601 format in utc
    analysis_time=$(date -d "$rounded_time_str" -u +"%Y-%m-%dT%H:%M:%SZ")

    # refs directory format is normalized to minute precision: YYYY-MM-DDTHHMMZ
    analysis_time_refs=$(date -d "$rounded_time_str" -u +"%Y-%m-%dT%H%MZ")
    refs_path="${REFS_ROOT_PATH}/CONTROL__dmi/${analysis_time_refs}.jsons/"

    if [ -d "$refs_path" ]; then
        echo "Refs already exist for analysis time $analysis_time ($refs_path)"
        echo "Sleeping for 20 min..."
        sleep 1200
        continue
    else
        echo "Creating indexes refs for analysis time $analysis_time"
        if [ -n "${SRC_GRIB_TEMP_PATH:-}" ]; then
            echo "(calling ./build_indexes_and_refs.sh $analysis_time $SRC_GRIB_TEMP_PATH)"
            ./build_indexes_and_refs.sh "$analysis_time" "$SRC_GRIB_TEMP_PATH"
        else
            echo "(calling ./build_indexes_and_refs.sh $analysis_time)"
            ./build_indexes_and_refs.sh "$analysis_time"
        fi

        # check if the script was successful with the exit code
        if [ $? -eq 0 ]; then
            echo "Indexes and refs built successfully for analysis time $analysis_time"
        else
            echo "Failed to build indexes and refs"
        fi
    fi

    if [ -d "$refs_path" ]; then
        echo "Running zarr conversion for analysis time $analysis_time"

        while true; do
            uv run python -m zarr_creator --t_analysis "$analysis_time"
            # check if the script was successful with the exit code
            if [ $? -eq 0 ]; then
                # delete temporary storage if it was used
                echo "Zarr conversion successful for analysis time $analysis_time"
                if [ -n "${SRC_GRIB_TEMP_PATH:-}" ] && [ -d "$SRC_GRIB_TEMP_PATH" ]; then
                    echo "Deleting temporary storage..."
                    rm -rf "$SRC_GRIB_TEMP_PATH"
                fi
                break
            else
                echo "Failed to build zarr, retrying..."
            fi
        done
    fi

    echo "Sleeping for 5 minutes..."
    sleep 300
done
