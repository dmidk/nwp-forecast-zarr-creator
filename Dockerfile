# --- builder ---
FROM ubuntu:24.04
WORKDIR /app
# Install Git to ensure versioning works
RUN apt-get update && apt-get install -y git curl libaec0 libaec-dev tree
COPY pyproject.toml README.md ./
COPY zarr_creator ./zarr_creator
# copy over git metadata so that pdm-scm can detect version
COPY .git ./.git

RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:$PATH"
ENV REFS_ROOT_PATH="/app/refs"
ENV SRC_GRIB_TEMP_PATH="/tmp/nwp-forecast-zarr-creator"
ENV DST_ZARR_OUTPUT_PATH="s3://harmonie-zarr/{suite_name}/{member}/{t_analysis}/{dataset_id}.zarr"
RUN uv venv -p 3.12
# clean the uv cache in the same layer so it isn't stored in the image (~1 GB)
RUN uv sync && uv cache clean

# Check that version is set correctly from git (i.e. not the default "0.0.0")
RUN uv run python -c "import zarr_creator; assert zarr_creator.__version__ != '0.0.0'"

# --watch is the default argument (not part of the entrypoint) so that passing
# arguments, e.g. `--t-analysis 2025-02-27T15:00:00Z`, runs a single analysis
# time and exits instead of watching.
ENTRYPOINT ["uv", "run", "python", "-m", "zarr_creator", "run"]
CMD ["--watch"]
