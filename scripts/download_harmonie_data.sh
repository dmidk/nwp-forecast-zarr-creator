#!/bin/bash

set -u

usage() {
    cat <<USAGE
Usage: $0 [analysis_time] [destination_dir]
       $0 [destination_dir]

Download Harmonie GRIB files for one analysis time from S3.

Arguments:
  analysis_time     Optional ISO-8601 UTC timestamp ending in Z, e.g. 2025-03-02T00:00:00Z
  destination_dir   Local directory for downloaded files (default: ./data/harmonie/ml)

Environment variables:
  S3_BUCKET         S3 bucket name (default: harmonie-data)
  S3_PREFIX         Prefix in bucket (default: ml)
  MEMBER_ID         Member id in filename (default: CONTROL__dmi)
  MAX_HOUR          Max forecast hour to download (default: 36)
  FILE_TYPES        Space-separated file types (default: "sf pl")
USAGE
}

ANALYSIS_TIME_INPUT="${1:-}"
if [ -n "$ANALYSIS_TIME_INPUT" ] && [[ "$ANALYSIS_TIME_INPUT" =~ Z$ ]]; then
    ANALYSIS_TIME="$ANALYSIS_TIME_INPUT"
    DEST_DIR="${2:-./data/harmonie/ml}"
elif [ -n "$ANALYSIS_TIME_INPUT" ]; then
    ANALYSIS_TIME=""
    DEST_DIR="$ANALYSIS_TIME_INPUT"
else
    ANALYSIS_TIME=""
    DEST_DIR="./data/harmonie/ml"
fi

S3_BUCKET="${S3_BUCKET:-harmonie-data}"
S3_PREFIX="${S3_PREFIX:-ml}"
MEMBER_ID="${MEMBER_ID:-CONTROL__dmi}"
MAX_HOUR="${MAX_HOUR:-36}"
FILE_TYPES="${FILE_TYPES:-sf pl}"
INITIAL_AUTO_LAG_HOURS=3
AUTO_LAG_STEP_HOURS=3
MAX_AUTO_ATTEMPTS=8

if ! [[ "$MAX_HOUR" =~ ^[0-9]+$ ]]; then
    echo "MAX_HOUR must be an integer, got: $MAX_HOUR"
    exit 1
fi

if ! command -v aws >/dev/null 2>&1; then
    echo "aws CLI is required but was not found in PATH"
    exit 1
fi

compute_analysis_time_from_lag() {
    local lag_hours="$1"
    local now_epoch lag_seconds adjusted_epoch rounded_epoch computed_time
    now_epoch=$(date -u +%s)
    lag_seconds=$((lag_hours * 3600))
    adjusted_epoch=$((now_epoch - lag_seconds))
    rounded_epoch=$((adjusted_epoch / 10800 * 10800))

    if computed_time=$(date -u -d "@${rounded_epoch}" +"%Y-%m-%dT%H:%M:%SZ" 2>/dev/null); then
        :
    else
        computed_time=$(date -u -r "${rounded_epoch}" +"%Y-%m-%dT%H:%M:%SZ")
    fi
    echo "$computed_time"
}

download_one_analysis_time() {
    local analysis_time="$1"
    local analysis_time_str downloads failed type h hour file src dst
    local -a downloaded_this_attempt=()

    if [[ ! "$analysis_time" =~ Z$ ]]; then
        echo "analysis_time must end with Z (UTC), got: $analysis_time"
        return 3
    fi

    analysis_time_str=$(echo "$analysis_time" | sed -E 's/[-:]//g; s/T//; s/Z$//; s/^([0-9]{10}).*/\1/')
    if ! [[ "$analysis_time_str" =~ ^[0-9]{10}$ ]]; then
        echo "Could not parse analysis_time: $analysis_time"
        return 3
    fi

    downloads=0
    failed=0

    for type in $FILE_TYPES; do
        for h in $(seq 0 "$MAX_HOUR"); do
            hour=$(printf "%03d" "$h")
            file="fc${analysis_time_str}+${hour}${MEMBER_ID}_${type}"
            src="s3://${S3_BUCKET}/${S3_PREFIX}/${file}"
            dst="${DEST_DIR}/${file}"

            if [ -f "$dst" ]; then
                echo "Skipping existing: $dst"
                continue
            fi

            echo "Downloading: $src"
            if aws s3 cp "$src" "$dst"; then
                downloads=$((downloads + 1))
                downloaded_this_attempt+=("$dst")
            else
                echo "Failed: $src"
                failed=$((failed + 1))
            fi
        done
    done

    if [ "$failed" -eq 0 ]; then
        echo "Completed: downloaded=$downloads failed=$failed destination=$DEST_DIR analysis_time=$analysis_time"
        return 0
    fi

    echo "Completed: downloaded=$downloads failed=$failed destination=$DEST_DIR analysis_time=$analysis_time"
    if [ "${#downloaded_this_attempt[@]}" -gt 0 ]; then
        rm -f "${downloaded_this_attempt[@]}"
    fi
    return 2
}

mkdir -p "$DEST_DIR"

if [ -n "$ANALYSIS_TIME" ]; then
    download_one_analysis_time "$ANALYSIS_TIME"
    exit $?
fi

for attempt in $(seq 0 $((MAX_AUTO_ATTEMPTS - 1))); do
    lag_hours=$((INITIAL_AUTO_LAG_HOURS + attempt * AUTO_LAG_STEP_HOURS))
    candidate_analysis_time=$(compute_analysis_time_from_lag "$lag_hours")
    echo "No analysis_time provided; trying ${candidate_analysis_time} (lag=${lag_hours}h)"

    if download_one_analysis_time "$candidate_analysis_time"; then
        exit 0
    fi
done

echo "Failed to find a complete analysis after ${MAX_AUTO_ATTEMPTS} attempts."
exit 2
