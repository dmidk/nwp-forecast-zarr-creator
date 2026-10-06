# Refactor Plan: Python-only + S3-capable NWP Forecast Zarr Creator

## Goals
1. Remove all bash orchestration (`run.sh`, `build_indexes_and_refs.sh`, `script_defaults.sh`, `scripts/download_harmonie_data.sh`) — Python only.
2. Read source GRIB files from S3 object store configured via environment variables, while keeping local filesystem support (auto-detect `s3://` vs local path).
3. Preserve current behavior: 3-hourly analysis times with 2h lag, `sf`/`pl` types, `MAX_HOUR` range, gribscan index + refs, retry loop, temp cleanup.
4. Support both one-shot (cron / K8s Job) and `--watch` daemon (current `run.sh` loop) modes.
5. Strategy for S3: download-to-temp then index (no streaming reads for eccodes/gribscan).

## Agreed decisions
- S3 source: standard `s3://bucket/prefix` URI + AWS profile env vars. Credentials, endpoint URLs, and regions come from `~/.aws/config` + `~/.aws/credentials` via the named profile (resolved by botocore/s3fs); code only selects the profile name.
- Dual profiles: `SRC_AWS_PROFILE` (source read) and `DST_AWS_PROFILE` (fixture-dest / output write), each falling back to standard `AWS_PROFILE`. Single-host case = set `AWS_PROFILE` once. Fixture creation uses two filesystems (`fs_src`, `fs_dst`); always stage `source -> local tmp -> dest` (no direct S3-to-S3 across hosts).
- Keep both local and S3 inputs, auto-detected.
- Runner supports one-shot + `--watch` loop.
- Indexing: download to temp dir first, then run eccodes/gribscan on local files.
- CI fixture: trimmed subset (`MAX_HOUR=2`), public-read bucket, full pipeline to zarr with `--skip-s3-bucket-upload`.
- Fixture bucket default `uwcw-sample-grib2zarr-conversion-datasets` (variable via `--fixture-bucket` / `$FIXTURE_BUCKET`); prefix contains analysis time: `s3://<bucket>/<YYYY-MM-DDTHHMMZ>/ml/...` plus `README.md` + `manifest.json` at the analysis prefix.

---

## Step 1: Create centralized settings module
**New file:** `zarr_creator/settings.py`

- [ ] Define settings dataclass (stdlib `dataclasses` + `os.environ`, no new dependency unless `pydantic-settings` is preferred).
- [ ] Fields + defaults (mirroring `script_defaults.sh`):
  - `SRC_GRIB_ROOT_URI: str` (replaces `SRC_GRIB_ROOT_PATH`; accept `/abs/path` or `s3://bucket/prefix`; `SRC_GRIB_ROOT` / `SRC_GRIB_ROOT_PATH` kept as deprecated aliases)
  - `REFS_ROOT_PATH` (default `/mnt/...` prod / `/app/refs` container)
  - `MEMBER_ID` (default `CONTROL__dmi`)
  - `MAX_HOUR: int` (default `36`)
  - `SUITE_NAME` (default `dini`, choices `dini|ig`)
  - `SRC_GRIB_TEMP_PATH: str | None` (default empty/unset = index in place, no copy; if set, download/copy GRIBs there first and index the staged copy. Local-only `_PATH`, never an S3 URI)
  - `DST_ZARR_OUTPUT_PATH: str` (full output-path format string; default `file:///tmp/{suite_name}-recent/{dataset_id}.zarr`; placeholders `{suite_name}`, `{member}`, `{t_analysis}`, `{dataset_id}`; must contain `{dataset_id}`)
  - Profile selection only (creds/endpoint/region resolve from `~/.aws/config` + `~/.aws/credentials` via botocore/s3fs):
    - source read: `SRC_AWS_PROFILE` -> else `AWS_PROFILE` -> else default chain
    - dest write (fixture creation / output bucket): `DST_AWS_PROFILE` -> else `AWS_PROFILE` -> else default chain
  - Builders: `source_profile()` and `dest_profile()`; log resolved profile name per side (no secrets). CLI flags mirror env (`--source-profile`, `--dest-profile`); explicit flags win over env.
- [ ] Keep old `SRC_GRIB_ROOT_PATH` as deprecated alias with warning.
- [ ] Helpers (pure path/time logic only — no filesystem branching; fsspec owns URL parsing/dispatch):
  - `analysis_time_str(dt) -> YYYYMMDDHH`
  - `refs_dir_name(dt) -> YYYY-MM-DDTHHMMZ`
  - `grib_filename(analysis_dt, hour, member, type) -> fc...+...`
  - `refs_dir_for(dt, settings) -> Path`
- [ ] Validate: `analysis_time` must be tz-aware UTC (`Z` suffix); `MAX_HOUR` int >= 0.
- [ ] Unit tests: parsing, defaults, env overrides, deprecated alias, helpers.

**Acceptance:** `from zarr_creator.settings import load_settings` works with and without env set; `pytest tests/test_settings.py` passes.

## Step 2: Create fsspec storage abstraction
**New file:** `zarr_creator/storage.py` — single place where URLs become filesystems; all callers pass opaque URLs and let fsspec dispatch (no `is_s3` branching outside).

- [ ] `resolve_fs(url, profile=None) -> (fs, path)` via `fsspec.url_to_fs(url, profile=...)`: `s3://` → s3fs (profile from per-side `SRC_`/`DST_` -> `AWS_PROFILE`; endpoint/keys/region from `~/.aws` via botocore; `anon=False` except anon public-fixture CI read), local paths → local filesystem. Callers pass per-side profile (source vs dest); never share one filesystem across hosts.
- [ ] Thin wrappers taking full URLs: `exists(url, ...)`, `find_missing(urls, ...)`, `download_to_temp(src_urls, tmpdir)` — skip-existing (mirror `rsync` / download-script behavior), progress logging, delete partial attempt on failure.
- [ ] `cleanup_temp(path)` via `shutil.rmtree` (replaces `rm -rf` in `run.sh`).
- [ ] Unit tests with `memory` + local filesystem mocks; no network.
- [ ] Profile fallback tests: only `AWS_PROFILE` set -> both sides inherit; `SRC_AWS_PROFILE` set -> source overrides, dest inherits; both `SRC_*`+`DST_*` set -> independent. Prove no profile bleed with two mocked filesystems.

**Acceptance:** listing + download works against local dir and mocked S3; partial-failure cleanup verified.

## Step 3: Port `build_indexes_and_refs.sh` to Python
**New file:** `zarr_creator/pipeline/index_refs.py` (keep `zarr_creator/build_indexes.py` as thin shim).

- [ ] Function `build_indexes_and_refs(t_analysis: datetime, settings) -> Path`:
  1. Validate UTC, compute `ANALYSIS_TIME_STR`, `ANALYSIS_REFS_DIR`.
  2. Enumerate `sf/pl × 0..MAX_HOUR` expected filenames.
  3. Completeness check via `storage.find_missing` (replaces `test -f` loop); raise with missing list instead of `exit 1`.
  4. Staging: if `SRC_GRIB_TEMP_PATH` is set, download/copy expected files there first and index the staged copy; if empty/unset, index in place from `SRC_GRIB_ROOT_URI`. If source is `s3://` and no temp path is set, log a warning that gribscan/eccodes likely cannot read directly from S3 (unverified — see risks) and recommend setting `SRC_GRIB_TEMP_PATH`, then attempt in place anyway.
  5. Per type: call `gribscan.tools.create_index()` in-process after `set_local_eccodes_definitions_path()` (replaces `uv run python -m zarr_creator.build_indexes ... -n 2`; preserve `-n 2` equivalent).
  6. Per type: call `gribscan-build` (library call if available, else subprocess) with `-o <refs>/<member>/<time>.jsons --prefix <local_src>/ -m harmonie`.
- [ ] Preserve `--prefix` semantics so `reference::` reads keep working.
- [ ] Add `python -m zarr_creator.pipeline.index_refs --t-analysis ...` CLI for manual runs (replaces `./build_indexes_and_refs.sh ...`).
- [ ] Side-by-side diff-test vs bash script on one local analysis time before deleting bash.

**Acceptance:** refs output tree identical to bash version for same input; `test_index_refs.py` covers missing-file error and filename enumeration.

## Step 4: Port `run.sh` to Python runner
**New file:** `zarr_creator/pipeline/runner.py`

- [ ] `compute_analysis_time(now_utc, lag_hours=2) -> datetime`: port `now-7200 → floor(/10800)` + ISO8601 UTC formatting. Extract as pure/testable function.
- [ ] `refs_exist(t_analysis, settings) -> bool`.
- [ ] `process_one(t_analysis, settings)`:
  - Skip if refs exist (log + return).
  - Else `build_indexes_and_refs()`, then call existing conversion (`zarr_creator.__main__.cli([...])`) directly in-process (no `uv run` subprocess).
  - Retry loop on conversion failure (configurable max retries / backoff; current behavior is infinite retry).
  - Delete temp dir on success.
- [ ] `watch_loop(settings, poll_interval=300, already_done_sleep=1200)`: port outer `while true` + sleeps.
- [ ] CLI `python -m zarr_creator run [--t-analysis X | --watch]` — every option maps 1:1 to an env var (Step 1); precedence is explicit flag > env > built-in default, container configured purely via env:
  - `--t-analysis X` (no env; runtime value) | default = one-shot most recent eligible; `--watch` = daemon mode
  - `--suite-name` (`SUITE_NAME`), `--max-hour` (`MAX_HOUR`), `--member-id` (`MEMBER_ID`)
  - `--src-grib-root-uri` (`SRC_GRIB_ROOT_URI`), `--src-grib-temp-path` (`SRC_GRIB_TEMP_PATH`), `--refs-root-path` (`REFS_ROOT_PATH`)
  - `--dst-zarr-output-path` (`DST_ZARR_OUTPUT_PATH`)
  - `--source-profile` (`SRC_AWS_PROFILE` → `AWS_PROFILE`), `--dest-profile` (`DST_AWS_PROFILE` → `AWS_PROFILE`)
  - (Removed: `--skip-s3-bucket-upload` — output is solely `DST_ZARR_OUTPUT_PATH`)
- [ ] Unit tests: time rounding (boundary 00/03/06...), skip-if-exists, retry-then-succeed, temp cleanup.

**Acceptance:** one-shot run converts one analysis time locally; `--watch` reproduces `run.sh` sleep/skip/retry behavior.

## Step 5: Loosen env handling in read/write paths
- [ ] `zarr_creator/read_source.py`: move `REFS_ROOT_PATH` check from import time to call time, source from `settings`; keep `reference::` open logic unchanged.
- [ ] `zarr_creator/write_zarr.py`: reuse resolved profile from settings for `fsspec.get_mapper("s3://harmonie-zarr/...")` (endpoint/keys/region via `~/.aws`).
- [ ] `zarr_creator/__main__.py`: keep `uv run python -m zarr_creator --t_analysis ...` working; wire new `run` subcommand or delegate to `pipeline.runner`.
- [ ] Tests: `--help` works without env set; read path resolves via settings.

## Step 6: Update packaging, Docker, dev compose
- [ ] `pyproject.toml`: confirm `fsspec/s3fs` cover S3 needs; optionally add `pydantic-settings`. Add `[project.scripts]` entry (e.g. `nwp-zarr = zarr_creator.pipeline.runner:main`) if wanted.
- [ ] `Dockerfile`: remove `COPY run.sh build_indexes_and_refs.sh script_defaults.sh`; change `ENTRYPOINT` to `["python","-m","zarr_creator","run","--watch"]`; keep `REFS_ROOT_PATH`/`SRC_GRIB_TEMP_PATH` defaults; drop `rsync` apt dep if nothing else needs it.
- [ ] `docker-compose.dev.yml`: add `SRC_GRIB_ROOT_URI`, `DST_ZARR_OUTPUT_PATH`, `AWS_PROFILE` examples (profiles resolve via mounted `~/.aws`); document S3 vs bind-mount usage.
- [ ] CI (`.github/workflows/`): add `ci-tests.yml` with `unit` (`pytest -m "not integration"`, no creds) + `s3-e2e` jobs; lint new modules via existing pre-commit.

**Acceptance:** `docker build` succeeds; container starts in `--watch` mode without bash.

## Step 7: Test-fixture creation script (frozen GRIB sample for CI)
**New file:** `zarr_creator/create_test_fixture.py` — Python only (reuses `settings.py` + `storage.py`, ports `scripts/download_harmonie_data.sh` logic; no `aws` CLI subprocess).

- [ ] CLI: `python -m zarr_creator.create_test_fixture --analysis-time <ISO-Z, optional> --source s3://harmonie-data/ml --fixture-bucket $FIXTURE_BUCKET --member-id CONTROL__dmi --max-hour 2 --file-types "sf pl" [--dry-run] [--overwrite]`, plus `--source-profile` (`SRC_AWS_PROFILE` -> `AWS_PROFILE`) and `--dest-profile` (`DST_AWS_PROFILE` -> `AWS_PROFILE`); explicit flags win over env. Endpoint/keys/region come from `~/.aws/config` + `~/.aws/credentials` for the selected profile.
- [ ] Resolve `analysis_time`: explicit value validated (`Z`-suffix UTC) or auto-find latest complete set (port lag=3h, step=3h, max 8 attempts, `floor((now-lag)/3h)` + `fs_src.exists` completeness check over `0..MAX_HOUR x sf/pl`, same as download script; discard partial attempts).
- [ ] Operational source note: `s3://harmonie-data/ml` retains ~2 weeks, so fixture is a frozen copy; record source URI + retention warning in provenance.
- [ ] Stage to local tmp via `storage.download_to_temp` (skip-existing, progress logging); abort with missing-file list if incomplete.
- [ ] Collect provenance per file (size + sha256) + `utcnow`, `git rev-parse HEAD`, eccodes version.
- [ ] Generate locally and upload to `s3://$FIXTURE_BUCKET/<YYYY-MM-DDTHHMMZ>/ml/<grib files>` + `s3://$FIXTURE_BUCKET/<YYYY-MM-DDTHHMMZ>/README.md` + `manifest.json`:
  - `README.md`: origin, retention warning, layout (`<analysis-time>/ml/` mirrors operational `ml/`), file count, CI usage (`SRC_GRIB_ROOT_URI=s3://<bucket>/<analysis>/ml`, `T_ANALYSIS`, `MAX_HOUR`), regen command.
  - `manifest.json`: machine-readable provenance (both endpoints incl. host/bucket/prefix, no secrets; profile names; file list).
- [ ] Guards: default `--no-overwrite` (fail if dest prefix exists); `--overwrite` required to replace; fixtures immutable — regenerate as new `<analysis-time>/` prefix. `--dry-run` prints planned keys/sizes + resolved profiles/endpoints (from `~/.aws`, no secrets) without touching either side.
- [ ] Verify: re-list dest prefix, compare counts/sizes (+ spot checksum where supported); print copy-paste CI env block. Fail fast with distinct source-auth vs dest-auth errors.
- [ ] Tests: unit with `memory://` (prefix building, README/manifest rendering, no-overwrite guard, dry-run, profile fallback `SRC_`/`DST_` -> `AWS_PROFILE`); manual `--max-hour 0` dry-run smoke (not CI).

**Acceptance:** one command produces `<analysis>/ml/* + README.md + manifest.json` in fixture bucket; re-run without `--overwrite` refuses; `--dry-run` touches nothing.

## Step 8: CI S3 end-to-end tests (public trimmed fixture, full to zarr)
- [ ] Fixture content (created once via Step 7): frozen `analysis_time` (e.g. `2025-03-02T00:00:00Z`), `MAX_HOUR=2`, `MEMBER_ID=CONTROL__dmi`, `sf+pl` (6 files) under `s3://uwcw-sample-grib2zarr-conversion-datasets/<YYYY-MM-DDTHHMMZ>/ml/`; public-read, `eu-central-1`; pin eccodes version note alongside fixture.
- [ ] New `tests/test_pipeline_s3.py` (`@pytest.mark.integration`):
  1. `load_settings(SRC_GRIB_ROOT_URI=s3://<bucket>/<analysis>/ml, REFS_ROOT_PATH=tmp, MAX_HOUR=2, DST_ZARR_OUTPUT_PATH=file:///tmp/ci-out/{dataset_id}.zarr)`; `find_missing()` empty or fail naming the fixture gap.
  2. `build_indexes_and_refs(t_analysis)` -> assert `<tmp>/<member>/<time>.jsons/*.json` exist.
  3. Conversion (output to local `DST_ZARR_OUTPUT_PATH`, `--suite-name dini`) -> assert `single_levels/pressure_levels/height_levels.zarr` written, openable via `xr.open_zarr` (dims `time==3`, expected vars present, no all-NaN vars).
- [ ] New `.github/workflows/ci-tests.yml`: `unit` job (`uv sync`, `pytest -m "not integration"`, no AWS) + `s3-e2e` job reading public fixture (`SRC_GRIB_ROOT_URI`, `T_ANALYSIS`, `MAX_HOUR=2`, local `DST_ZARR_OUTPUT_PATH`, `AWS_REGION=eu-central-1` for anon public read, `AWS_EC2_METADATA_DISABLED=true`). Prefer running `s3-e2e` inside the built container image (avoids installing eccodes C-lib on runner); alternative native runner with apt eccodes. Upload refs listing + xarray summary on failure; `timeout-minutes: ~20`. Keep `container-image.yml` build check; optionally `s3-e2e needs: build`.
- [ ] Manual validation: minio + real-bucket smoke; `--prefix` refs resolve; temp cleanup happens; profile chain (`SRC_AWS_PROFILE` / `DST_AWS_PROFILE` -> `AWS_PROFILE`, details from `~/.aws`) verified for non-public paths.

## Step 9: Remove bash + update docs
- [ ] Delete `run.sh`, `build_indexes_and_refs.sh`, `script_defaults.sh`, and `scripts/download_harmonie_data.sh` (replaced by `storage.py` + `create_test_fixture.py`).
- [ ] Update `README.md` (Usage, Runtime Defaults table → new env vars incl. `SRC_GRIB_ROOT_URI=s3://...`, `DST_ZARR_OUTPUT_PATH`, `SRC_AWS_PROFILE`/`DST_AWS_PROFILE`/`AWS_PROFILE` with `~/.aws` note), `CHANGELOG.md`.
  - [ ] State configuration precedence explicitly: **CLI flag > environment variable > built-in default**, with a worked example (`--max-hour 12` beats `MAX_HOUR=12` beats built-in `36`). Note the container is configured purely via environment (`ENTRYPOINT` takes no config args); flags are manual overrides.
- [ ] Update `DEVELOPING.md`:
  - [ ] Keep `§2 Prepare input data locally`; insert new `§2b Create an S3 test fixture (frozen GRIB sample)` before `§3`: what/why (operational ~2-week retention -> frozen copy), bucket var (`FIXTURE_BUCKET`, default `uwcw-sample-grib2zarr-conversion-datasets`), prefix scheme `<YYYY-MM-DDTHHMMZ>/ml/` + `README.md` + `manifest.json`, profile-only auth table (`SRC_AWS_PROFILE` vs `DST_AWS_PROFILE` -> `AWS_PROFILE`; endpoint/keys/region from `~/.aws/config` + `~/.aws/credentials`), explicit-time / auto-find / `--dry-run` / `--overwrite` command examples, result-layout block, verify + CI-consumption snippet (`SRC_GRIB_ROOT_URI=s3://...`, `T_ANALYSIS`, `MAX_HOUR=2`, `pytest -m integration`), immutable-regeneration policy.
  - [ ] Update `Prerequisites` (source + optional separate dest profile; `~/.aws` holds endpoint/creds) and `§3` manual pipeline commands (new Python CLI), `§4 Run tests` (unit vs S3 e2e pointer to §2b).
- [ ] Update mermaid data-flow diagrams: S3 bucket → temp download → index → refs → zarr → output bucket; add fixture-creation flow (`operational bucket -> tmp -> fixture bucket/<analysis>/ml + README/manifest`) and CI-consumption flow.
- [ ] Close out `README TODO` items about “rewrite in python”.

---

## Env var mapping (old → new)
| Old | New | Notes |
|---|---|---|
| `SRC_GRIB_ROOT_PATH` | `SRC_GRIB_ROOT_URI` | Root URI that GRIB filenames resolve relative to; `s3://bucket/prefix` or local path. `SRC_GRIB_ROOT` / `SRC_GRIB_ROOT_PATH` kept as deprecated aliases |
| `REFS_ROOT_PATH` | unchanged (`_PATH`: always local) | Dir where gribscan refs are written / read via `reference::` |
| `SRC_GRIB_TEMP_PATH` | unchanged (`_PATH`: always local) | Default empty = index in place, no copy; if set, stage GRIBs there first. S3 source + unset temp logs a warning (direct S3 reads by gribscan unverified) |
| `MEMBER_ID`, `MAX_HOUR`, `SUITE_NAME` | unchanged | Now centralized in `settings.py` |
| `S3_BUCKET` / `S3_PREFIX` (download script) | `--source s3://<bucket>/<prefix>` | Source expressed as URI; fixture dest via `--fixture-bucket` / `$FIXTURE_BUCKET` (`uwcw-sample-grib2zarr-conversion-datasets`), prefix `<YYYY-MM-DDTHHMMZ>/ml/` |
| `BUCKET_NAME`/`BUCKET_REGION` hardcoded + `--skip-s3-bucket-upload` + `LOCAL_COPY_STORAGE_PATH` | `DST_ZARR_OUTPUT_PATH` | Single full output-path format string, fsspec-dispatched. Default `file:///tmp/{suite_name}-recent/{dataset_id}.zarr` (local, no timestamp, overwrites). S3 override e.g. `s3://harmonie-zarr/{suite_name}/{member}/{t_analysis}/{dataset_id}.zarr`. Must contain `{dataset_id}`; missing `{t_analysis}` logs overwrite note. Skip flag removed |
| — | `AWS_PROFILE` | Default profile for both sides / single-host case (endpoint/keys/region from `~/.aws`) |
| — | `SRC_AWS_PROFILE` | Source-read profile override, else `AWS_PROFILE` |
| — | `DST_AWS_PROFILE` | Dest-write profile override (fixture upload + zarr output), else `AWS_PROFILE` |

## Risks / open checks
- `gribscan-build --prefix` embeds the local temp path in refs — must confirm reads still work after temp cleanup (current `run.sh` ordering already assumes conversion finishes before `rm -rf`; preserve this).
- `gribscan.tools.create_index` parallelism flag (`-n 2`) needs in-process equivalent.
- Unknown whether gribscan/eccodes can index directly from `s3://` URLs: default (temp unset) attempts in-place reads after warning; verify during Step 7 validation and record outcome here (if direct reads fail, make temp path required for S3 sources).
- Custom S3 hosts resolve via `~/.aws/config` per profile — verify s3fs/botocore picks up endpoint URL for both `SRC_` and `DST_` profiles (no endpoint/keys in code or env).
- Operational source retention (~2 weeks): frozen fixture + `manifest.json` provenance mitigate drift; old analysis times cannot be re-pulled.
- Fixture bucket public-read policy + immutable `<analysis-time>/` prefixes: avoid overwrites; CI pins a frozen analysis time.
