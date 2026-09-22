# FireAI Pro 2.0 — reproducible runtime/test image.
#
# Kept under docker/ (not the repo root) so it does not change how Railway
# currently builds the service. Build from the repo root:
#   docker build -f docker/Dockerfile -t fireai:dev .
#   docker run --rm fireai:dev pytest -q
#
# DWG conversion: no converter is installed by default, so DWG uploads fail with
# DWG_CONVERSION_UNAVAILABLE. To enable, install ODA File Converter (license
# terms apply; needs xvfb) or LibreDWG's dwg2dxf and set FIREAI_DWG_CONVERTER.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 FIREAI_DATA_DIR=/data
WORKDIR /app
# Fonts are required: without them ezdxf cannot render drawing text in the
# verification images (text would silently disappear from source.png).
RUN apt-get update && apt-get install -y --no-install-recommends fonts-dejavu-core fontconfig \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt requirements-dev.txt ./
RUN pip install --no-cache-dir -r requirements-dev.txt
COPY . .
RUN useradd --create-home fireai && mkdir -p /data && chown fireai /data
USER fireai
EXPOSE 8000
CMD ["sh", "-c", "uvicorn api.app:app --host 0.0.0.0 --port ${PORT:-8000}"]
