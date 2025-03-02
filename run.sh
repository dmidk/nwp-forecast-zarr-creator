# every hour run the script

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

    echo "Running zarr conversion for analysis time $analysis_time"

    while true; do
        ./build_indexes_and_refs.sh $analysis_time
        # check if the script was successful with the exit code
        if [ $? -eq 0 ]; then
            break
        else
            echo "Failed to build indexes and refs, retrying in 5 minutes"
            sleep 300
        fi
    done

    pdm run python -m zarr_creator --t_analysis "$analysis_time"
    # sleep three hours
    sleep 10800
done
