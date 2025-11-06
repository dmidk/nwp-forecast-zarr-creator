# --- builder ---
FROM python:3.12-slim AS builder
WORKDIR /src
# Install Git to ensure versioning works
RUN apt-get update && apt-get install -y git
COPY pyproject.toml README.md ./
COPY zarr_creator ./zarr_creator
# copy over git metadata so that pdm-scm can detect version
COPY .git ./.git
RUN pip install pdm-backend build
RUN python -m build --wheel

# --- runtime ---
FROM ubuntu:24.04
WORKDIR /app
RUN apt-get update && apt-get install -y curl libaec0 libaec-dev rsync
# Install uv
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:$PATH"
RUN uv venv -p 3.12

# Copy entrypoint script and runtime script
COPY build_indexes_and_refs.sh .
COPY run.sh .
# Copy wheel only (no .git)
COPY --from=builder /src/dist/*.whl /app/dist/
# Install the wheel and deps
RUN uv pip install /app/dist/*.whl

# Check that version is set correctly from git (i.e. not the default "0.0.0")
RUN uv run python -c "import zarr_creator; assert zarr_creator.__version__ != '0.0.0'"

ENTRYPOINT ["./run.sh"]
