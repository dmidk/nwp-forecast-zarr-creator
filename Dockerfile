FROM ubuntu:24.04

# set working directory
WORKDIR /app

COPY pyproject.toml .
COPY README.md .
COPY zarr_creator ./zarr_creator
COPY build_indexes_and_refs.sh .
COPY run.sh .

RUN apt-get update
RUN apt-get install -y curl git rsync
RUN apt-get install -y libaec0 libaec-dev

# Install uv
RUN curl -LsSf https://astral.sh/uv/install.sh | sh

# Setup up PATH for uv
ENV PATH="/root/.local/bin:$PATH"

# Create venv with python3.12 (3.13 didn't work with some deps)
RUN uv venv -p 3.12
RUN uv sync

ENTRYPOINT ["./run.sh"]
