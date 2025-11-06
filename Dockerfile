# --- builder ---
FROM ubuntu:24.04
WORKDIR /app
# Install Git to ensure versioning works
RUN apt-get update && apt-get install -y git curl libaec0 libaec-dev rsync
COPY pyproject.toml README.md ./
COPY zarr_creator ./zarr_creator
# copy over git metadata so that pdm-scm can detect version
COPY .git ./.git
# copy over entrypoint and runtime scripts
COPY run.sh .
COPY build_indexes_and_refs.sh .

RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:$PATH"
RUN uv venv -p 3.12
RUN uv sync

# Check that version is set correctly from git (i.e. not the default "0.0.0")
RUN uv run python -c "import zarr_creator; assert zarr_creator.__version__ != '0.0.0'"

ENTRYPOINT ["./run.sh"]
