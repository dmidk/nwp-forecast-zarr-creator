# Developing in VS Code (Dev Containers)

This repository includes a VS Code Dev Container setup so development runs in
the same Docker environment as the application.

## Prerequisites

- Docker Desktop (or Docker Engine + Compose)
- VS Code
- VS Code extension: `Dev Containers`
- AWS credentials configured if you need to download GRIB files from S3

## 1. Open in Dev Container

From the repository root in VS Code:

1. Run `Dev Containers: Reopen in Container`
2. VS Code builds from `Dockerfile` and starts `docker-compose.dev.yml`
3. `uv sync` runs automatically after container creation

On Apple Silicon Macs, the dev compose config defaults to
`DEV_CONTAINER_PLATFORM=linux/amd64` so dependency wheels for `eccodeslib` are
available. If needed, you can override this before reopening the container:

```bash
export DEV_CONTAINER_PLATFORM=linux/amd64
```

The development container:

- mounts this repo to `/app`
- maps local input data to `/mnt/harmonie-data-from-pds/ml`
- maps local `./tmp` to `/tmp`
- sets:
  - `ROOT_PATH=/mnt/harmonie-data-from-pds/ml`
  - `REFS_ROOT_PATH=/app/refs`
  - `TEMP_ROOT=/tmp/nwp-forecast-zarr-creator`

## 2. Prepare input data locally

The production system reads from `/mnt/harmonie-data-from-pds/ml` (S3FS mount).
For local development, populate `./data/harmonie/ml` instead.

Download files for one analysis time:

```bash
./scripts/download_harmonie_data.sh 2025-03-02T00:00:00Z
```

If you omit `analysis_time`, the script tries the most recent 3-hour analysis
interval and automatically retries older 3-hour intervals until it finds a
complete set of expected files:

```bash
./scripts/download_harmonie_data.sh
```

If you need a specific AWS credentials profile, set `AWS_PROFILE` when running
the script:

```bash
AWS_PROFILE=my-profile ./scripts/download_harmonie_data.sh 2025-03-02T00:00:00Z
```

This downloads files from `s3://harmonie-data/ml` to `./data/harmonie/ml`
which is mounted inside the container as `/mnt/harmonie-data-from-pds/ml`.

Optional environment overrides:

- `S3_BUCKET` (default: `harmonie-data`)
- `S3_PREFIX` (default: `ml`)
- `MEMBER_ID` (default: `CONTROL__dmi`)
- `MAX_HOUR` (default: `36`)
- `FILE_TYPES` (default: `sf pl`)

Example:

```bash
MAX_HOUR=12 ./scripts/download_harmonie_data.sh 2025-03-02T00:00:00Z
```

## 3. Run the pipeline manually in dev

Inside the Dev Container terminal:

```bash
./build_indexes_and_refs.sh 2025-03-02T00:00:00Z /tmp/nwp-forecast-zarr-creator
uv run python -m zarr_creator --t_analysis 2025-03-02T00:00:00Z
```

Generated refs are written to `./refs` in your repo.

## 4. Run tests

Inside the Dev Container terminal:

```bash
uv run pytest
```

## Notes on script paths

`run.sh` and `build_indexes_and_refs.sh` now support environment-variable
overrides while preserving existing server defaults:

- `ROOT_PATH`
- `REFS_ROOT_PATH`
- `TEMP_ROOT`
- `MEMBER_ID`

This allows the same scripts to run in both production and local dev.
