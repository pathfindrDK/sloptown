# sloptown-fake-miira: dev-only Miira stand-in. Build context: repo root.
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.9.30
ARG PYTHON_IMAGE=docker.io/library/python:3.13-slim-bookworm

FROM ${UV_IMAGE} AS uv

FROM ${PYTHON_IMAGE} AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /src
COPY fake-miira/pyproject.toml fake-miira/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY fake-miira/ ./
RUN uv sync --frozen --no-dev --no-editable

FROM ${PYTHON_IMAGE}
RUN useradd --uid 1000 --create-home miira \
    && mkdir -p /var/lib/fake-miira && chown miira /var/lib/fake-miira  # optional state volume
COPY --from=build /opt/venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH PYTHONUNBUFFERED=1
USER miira
EXPOSE 8100
CMD ["uvicorn", "fake_miira.app:app", "--host", "0.0.0.0", "--port", "8100"]
