# FireAI Pro 2.0 — reproducible runtime/test image.
#
# Kept under docker/ (not the repo root) so it does not change how Railway
# currently builds the service. Build from the repo root:
#   docker build -f docker/Dockerfile -t fireai:dev .
#   docker run --rm fireai:dev pytest -q
#
# DWG conversion: GNU LibreDWG `dwg2dxf`, built from a pinned, checksum-verified
# release tarball and invoked as a separate process (see fireai/ingest/dwg.py).
# ODA File Converter is NOT used (non-commercial-only terms for non-members).

# ── Stage 1: build LibreDWG ───────────────────────────────────────────────────
FROM python:3.12-slim AS libredwg
ARG LIBREDWG_VERSION=0.14.8597
ARG LIBREDWG_COMMIT=34f02f54b9aacb5708c1d3d2070efb3e4b2d8c43
ARG LIBREDWG_SHA256=af2646681858a78d756cfb9e0eeb6901f61be3d09ae8f56a98e94b1490dec4ed
RUN apt-get update && apt-get install -y --no-install-recommends build-essential curl ca-certificates xz-utils \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /build
RUN curl -fsSL -o libredwg.tar.xz \
      "https://github.com/LibreDWG/libredwg/releases/download/${LIBREDWG_VERSION}/libredwg-${LIBREDWG_VERSION}.tar.xz" \
    && echo "${LIBREDWG_SHA256}  libredwg.tar.xz" | sha256sum -c - \
    && tar xf libredwg.tar.xz
RUN cd libredwg-${LIBREDWG_VERSION} \
    && ./configure --prefix=/opt/libredwg --disable-bindings --disable-shared --enable-static \
    && make -j"$(nproc)" \
    && make install \
    && printf 'version=%s\ncommit=%s\nsha256=%s\n' "$LIBREDWG_VERSION" "$LIBREDWG_COMMIT" "$LIBREDWG_SHA256" \
       > /opt/libredwg/BUILD_INFO

# ── Stage 2: runtime ──────────────────────────────────────────────────────────
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 FIREAI_DATA_DIR=/data \
    PATH="/opt/libredwg/bin:${PATH}" FIREAI_DWG_CONVERTER=libredwg
WORKDIR /app
# Fonts are required: without them ezdxf cannot render drawing text in the
# verification images (text would silently disappear from source.png).
RUN apt-get update && apt-get install -y --no-install-recommends fonts-dejavu-core fontconfig \
    && rm -rf /var/lib/apt/lists/*
COPY --from=libredwg /opt/libredwg /opt/libredwg
COPY requirements.txt requirements-dev.txt ./
RUN pip install --no-cache-dir -r requirements-dev.txt
COPY . .
RUN useradd --create-home fireai && mkdir -p /data && chown fireai /data
USER fireai
EXPOSE 8000
CMD ["sh", "-c", "uvicorn api.app:app --host 0.0.0.0 --port ${PORT:-8000}"]
