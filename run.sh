# every hour run the script
REFS_ROOT_PATH="/home/ec2-user/nwp-forecast-zarr-creator/refs"
TEMP_ROOT="$HOME/tmp"

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

    # the refs path uses this analysis time in iso8601 format but without colons, without seconds and ending in .json
    analysis_time_refs=$(date -d "$rounded_time_str" -u +"%Y-%m-%dT%H%M%SZ")
    refs_path="${REFS_ROOT_PATH}/CONTROL__dmi/${analysis_time_refs}.jsons/"

    if [ -d "$refs_path" ]; then
        echo "Refs already exist for analysis time $analysis_time ($refs_path)"
        echo "Sleeping for 20 min..."
        sleep 1200
        continue
    else
        echo "Creating indexes refs for analysis time $analysis_time"
        ./build_indexes_and_refs.sh $analysis_time $TEMP_ROOT

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
            pdm run python -m zarr_creator --t_analysis "$analysis_time"
            # check if the script was successful with the exit code
            if [ $? -eq 0 ]; then
                # delete temporary storage if it was used
                echo "Zarr conversion successful for analysis time $analysis_time"
                if [ -d "$TEMP_ROOT" ]; then
                    echo "Deleting temporary storage..."
                    rm -rf $TEMP_ROOT
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
