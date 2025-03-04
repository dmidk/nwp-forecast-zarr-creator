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
    adjusted_time=$((now - 3600))
    # Calculate the nearest past 3-hour interval
    rounded_time=$((adjusted_time / 10800 * 10800))
    # Convert back to human-readable format
    rounded_time_str=$(date -d "@$rounded_time" +"%H:%M")
    # format the time in iso8601 format in utc
    analysis_time=$(date -d "$rounded_time_str" -u +"%Y-%m-%dT%H:%M:%SZ")

    # check if refs have already been written for this analysis time
    refs_path="${REFS_ROOT_PATH}/${analysis_time}"

    if [ -d "$refs_path" ]; then
        echo "Refs already exist for analysis time $analysis_time"
        # sleep three hours
        sleep 10800
        continue
    fi

    echo "Running zarr conversion for analysis time $analysis_time"

    while true; do
        ./build_indexes_and_refs.sh $analysis_time $TEMP_ROOT

        # check if the script was successful with the exit code
        if [ $? -eq 0 ]; then
            break
        else
            echo "Failed to build indexes and refs, retrying in 5 minutes"
            sleep 300
	    continue
        fi
    done

    while true; do
        pdm run python -m zarr_creator --t_analysis "$analysis_time"
        # check if the script was successful with the exit code
        if [ $? -eq 0 ]; then
            break
        else
            echo "Failed to build zarr, retrying in 5 minutes"
            sleep 300
        fi
    done

    # delete temporary storage if it was used
    if [ -d "$TEMP_ROOT" ]; then
        rm -rf $TEMP_ROOT
    fi

    # sleep three hours
    sleep 10800
done
