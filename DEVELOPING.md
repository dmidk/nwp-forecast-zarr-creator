# Developing in VS Code (Dev Containers)

This repository includes a VS Code Dev Container setup so development runs in
the same Docker environment as the application.

## How code edits are used

When the Dev Container is running, `docker-compose.dev.yml` bind-mounts this
repository into the container at `/app` (`.:/app`). That means edits you make
in VS Code on your machine are immediately visible inside the container.

Although the `Dockerfile` also uses `COPY` to place application files in the
image at build time, the bind mount overrides those copied files at runtime for
development.

## Prerequisites

- Docker Desktop (or Docker Engine + Compose)
- VS Code
- VS Code extension: `Dev Containers`
- AWS credentials (`~/.aws`, via `SRC_AWS_PROFILE`/`DST_AWS_PROFILE` or
  `AWS_PROFILE`) if you read/write S3; the public test fixture needs none
  (`SRC_ANON=1`)

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

- mounts this repo (i.e. `./app`) to `/app`
- maps local input data (`./data/harmonie/ml`) to `/mnt/harmonie-data-from-pds/ml`
- maps local `./tmp` to `/tmp`

We default to building the container for `linux/amd64` platform to ensure compatibility with the `eccodeslib` dependency which does not have pre-built wheels for Apple Silicon (arm64). This allows Apple Silicon users to develop without needing to build `eccodeslib` from source. If you want to build the container for your native platform instead, you can set `DEV_CONTAINER_PLATFORM` to your desired platform before reopening the container, e.g.:

```bash
export DEV_CONTAINER_PLATFORM=linux/x86_64
```

**note**: `SRC_GRIB_TEMP_PATH` is not set by default in the dev container, so
indexing happens in place from `SRC_GRIB_ROOT_URI` rather than staging to a
temp path inside the container. This is to avoid unnecessary copying of
large GRIB files during development.

To use the dev container without VS Code, start it and open a shell from your
host terminal:

```bash
docker compose -f docker-compose.dev.yml up -d --build
docker compose -f docker-compose.dev.yml exec app bash
uv sync --dev   # first time only (VS Code does this for you)
```

**note**: the repo is bind-mounted at `/app`, so the container's `uv sync`
replaces your host `.venv` with a Linux one (and vice versa). Use one or the
other for a given checkout, or re-run `uv sync` after switching.

## 2. Prepare input data locally

The production system reads GRIBs from `SRC_GRIB_ROOT_URI` (a local path or
`s3://bucket/prefix`, dispatched via fsspec). For local development you have
two options:

### Option A: read the public S3 test fixture (no download needed)

```bash
export SRC_GRIB_ROOT_URI="s3://uwcw-sample-grib2zarr-conversion-datasets/<YYYY-MM-DDTHHMMZ>/ml"
export MAX_HOUR=2
export SRC_ANON=1
```

See §2b for how the fixture is created and for the frozen coordinates.

### Option B: stage operational files to `./data/harmonie/ml`

`./data/harmonie/ml` is mounted inside the container as
`/mnt/harmonie-data-from-pds/ml`. Stage one analysis time there (existing
files are skipped, so the directory acts as a cache):

```bash
uv run python -m zarr_creator.create_test_fixture --analysis-time 2025-03-02T00:00:00Z --dest-dir ./data/harmonie/ml
```

If you omit `analysis_time`, the most recent complete 3-hour analysis
interval is used (older 3-hour intervals are retried automatically):

```bash
uv run python -m zarr_creator.create_test_fixture --dest-dir ./data/harmonie/ml
```

Source selection and credentials:

```bash
# different source bucket/prefix
uv run python -m zarr_creator.create_test_fixture --source s3://harmonie-data/ml --dest-dir ./data/harmonie/ml
# separate AWS profiles per side (endpoint/keys/region from ~/.aws)
export SRC_AWS_PROFILE=my-source-profile
```

This stages files from `s3://harmonie-data/ml` (operational bucket, retains
~2 weeks) to `./data/harmonie/ml`.

Optional environment overrides (also accepted as flags, e.g. `--max-hour`):

- `MAX_HOUR` (default: `2` for fixtures; pipeline default is `36`)
- `MEMBER_ID` (default: `CONTROL__dmi`)
- `FILE_TYPES` (default: `sf pl`)

Example:

```bash
SRC_AWS_PROFILE=my-profile MAX_HOUR=12 uv run python -m zarr_creator.create_test_fixture --analysis-time 2025-03-02T00:00:00Z --dest-dir ./data/harmonie/ml
```

## 2b. Create an S3 test fixture (frozen GRIB sample)

The operational bucket only retains ~2 weeks of data, so CI and reproducible
local runs use a frozen trimmed copy in a fixture bucket (default
`uwcw-sample-grib2zarr-conversion-datasets`, override with `--fixture-bucket`
/ `$FIXTURE_BUCKET`). The prefix contains the suite and analysis time so the
origin is self-describing, with a trailing `/ml` mirroring the operational
layout. DINI and IG read different operational prefixes
(`s3://harmonie-data/ml` vs `s3://harmonie-data/ig`), so each suite gets its
own fixture namespace:

```text
s3://<fixture-bucket>/<suite>/<YYYY-MM-DDTHHMMZ>/ml/<grib files>
s3://<fixture-bucket>/<suite>/<YYYY-MM-DDTHHMMZ>/README.md
s3://<fixture-bucket>/<suite>/<YYYY-MM-DDTHHMMZ>/manifest.json
```

Create one (explicit time is recommended for reproducibility; the default
source is the DINI path, pass `--source s3://harmonie-data/ig` for IG):

```bash
uv run python -m zarr_creator.create_test_fixture --suite-name dini --analysis-time 2025-03-02T00:00:00Z --max-hour 2 --dry-run
uv run python -m zarr_creator.create_test_fixture --suite-name dini --analysis-time 2025-03-02T00:00:00Z --max-hour 2
uv run python -m zarr_creator.create_test_fixture --suite-name ig --source s3://harmonie-data/ig --analysis-time 2025-03-02T00:00:00Z --max-hour 2
```

Flags: `--source`, `--fixture-bucket`, `--suite-name`, `--member-id`, `--max-hour`,
`--file-types`, `--dry-run`, `--overwrite` (default refuses when the prefix
exists — fixtures are immutable, snapshot a new analysis time instead).

Source and destination may live on different S3 hosts: use
`--source-profile` (`SRC_AWS_PROFILE`) and `--dest-profile`
(`DST_AWS_PROFILE`), each falling back to `AWS_PROFILE`. Endpoint, keys, and
region resolve from `~/.aws` via the named profile:

```bash
SOURCE_AWS_PROFILE=oper DEST_AWS_PROFILE=fixtures \
  uv run python -m zarr_creator.create_test_fixture --analysis-time 2025-03-02T00:00:00Z --max-hour 2
```

After upload the script verifies the destination and prints the CI env block
(`SRC_GRIB_ROOT_URI=...`, `SUITE_NAME=...`, `MAX_HOUR=...`). `README.md` (origin, retention
warning, layout, usage) and `manifest.json` (suite, sizes, sha256, git sha, eccodes
version) travel with the data.

### S3 auth troubleshooting

Every S3-reading command logs its auth mode at startup, e.g.
`S3 source auth: unsigned (SRC_ANON=1)` or
`S3 source auth: signed (profile 'my-profile')`. On 403/AccessDenied
failures it also prints how to resolve the mismatch:

- unsigned reads (`SRC_ANON=1`) against a **private** bucket → unset
  `SRC_ANON` and set `SRC_AWS_PROFILE` (or `AWS_PROFILE`) so requests are
  signed via `~/.aws`;
- signed reads with bad/missing credentials → check the profile can access
  the bucket (or the container IAM role); for a **public** bucket set
  `SRC_ANON=1` instead.

## 3. Run the pipeline manually in dev

Inside the Dev Container terminal:

```bash
SRC_GRIB_TEMP_PATH=/tmp/nwp-forecast-zarr-creator uv run python -m zarr_creator.pipeline.index_refs --t-analysis 2025-03-02T00:00:00Z
uv run python -m zarr_creator --t_analysis 2025-03-02T00:00:00Z
```

Or the full one-shot runner (index + convert) / watcher:

```bash
uv run python -m zarr_creator run --t-analysis 2025-03-02T00:00:00Z
uv run python -m zarr_creator run --watch
```

Generated refs are written to `./refs` in your repo (when `REFS_ROOT_PATH`
points there). `run` deletes an analysis time's refs after a successful
conversion, leaving a `.done` marker; to keep them (and the staged GRIB
files) pass `--no-cleanup`, or run `pipeline.index_refs` on its own. Zarr output goes to `DST_ZARR_OUTPUT_PATH`
(`docker-compose.dev.yml` defaults it to local
`file:///tmp/nwp-zarr-output/...`, so no S3 writes happen in dev unless you
override it).

## 4. Run tests

Inside the Dev Container terminal:

```bash
uv run pytest
```

Unit tests run unconditionally; integration tests are gated:

```bash
# S3 fixture end-to-end (needs the frozen bucket from §2b)
# S3 fixture end-to-end (needs the frozen bucket from §2b; skips when unset).
# Analysis time, max hour, and suite come from the fixture's manifest.json.
FIXTURE_SRC_URI=s3://<bucket>/<suite>/<analysis>/ml SRC_ANON=1 \
  uv run pytest -m integration
```

This needs eccodes and the DMI definitions, so run it in the Dev Container (or
the Docker image). CI runs it in the image against the fixture pinned as
`FIXTURE_SRC_URI` in `.github/workflows/ci-tests.yml`; update that value if
the fixture is regenerated at a new analysis time.

## Notes on configuration

All runtime options live in `zarr_creator/settings.py`, each mapped 1:1 to an
environment variable. Precedence: explicit CLI flag > environment variable >
built-in default — the same flags exist on `run`, `pipeline.index_refs`, and
`create_test_fixture` (e.g. `--max-hour` overrides `MAX_HOUR`):

- `SRC_GRIB_ROOT_URI`
- `REFS_ROOT_PATH`
- `SRC_GRIB_TEMP_PATH`
- `DST_ZARR_OUTPUT_PATH`
- `MEMBER_ID`, `MAX_HOUR`, `SUITE_NAME`
- `SRC_AWS_PROFILE` / `DST_AWS_PROFILE` (fallback: `AWS_PROFILE`; details
  from `~/.aws`), `SRC_ANON`

This allows the same code to run in both production and local dev.
