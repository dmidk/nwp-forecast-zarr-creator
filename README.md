# NWP Forecast in zarr format

This repository contains the code to process DINI GRIB files into zarr format.


## Usage

```bash
pdm run python -m zarr-creator --output single_levels.zarr --pds_receive_path /mnt/zarr-from-dini/fc2025022715__dmi_sf/ --analysis_time 2025-02-27T15:00:00Z
