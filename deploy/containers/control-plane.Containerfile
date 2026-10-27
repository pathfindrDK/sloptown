# sloptown-cp and sloptown-egress (same image, different command).
# Build context: repo root.
#   cp:     uvicorn sloptown.app:app --host 0.0.0.0 --port 8000   (default CMD)
#   egress: python -m sloptown.egress   (:8200 ASGI + :8201 CONNECT proxy, single worker)
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.9.30
ARG PYTHON_IMAGE=docker.io/library/python:3.13-slim-bookworm

FROM ${UV_IMAGE} AS uv

FROM ${PYTHON_IMAGE} AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /src
COPY control-plane/pyproject.toml control-plane/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY control-plane/ ./
RUN uv sync --frozen --no-dev --no-editable

FROM ${PYTHON_IMAGE}
RUN apt-get update && apt-get upgrade -y && rm -rf /var/lib/apt/lists/* \
 && useradd --uid 1000 --create-home sloptown
COPY --from=build /opt/venv /opt/venv
COPY --chmod=0755 deploy/containers/healthcheck.py /usr/local/bin/sloptown-healthcheck
ENV PATH=/opt/venv/bin:$PATH PYTHONUNBUFFERED=1
USER sloptown
EXPOSE 8000 8200 8201
LABEL io.containers.autoupdate=registry
CMD ["uvicorn", "sloptown.app:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
