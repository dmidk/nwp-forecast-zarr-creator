# every hour run the script

while true; do
    # find the nearest three hour interval to the current time, e.g. 00:00,
    # 03:00, 06:00, etc. get the current time in utc
    #
    # Get the current time in seconds since epoch
    now=$(date +%s)
    # Subtract 2.5 hours (9000 seconds) to ensure we're in the past
    adjusted_time=$((now - 9000))
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

    pdm run python -m zarr_creator.pressure_levels --t_analysis "$analysis_time"
    sleep 3600
done
