FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# pyogrio and pyproj wheels bundle GDAL and PROJ, so no system GIS packages are needed.
COPY pyproject.toml README.md ./
COPY app ./app
RUN pip install ".[postgres]"

# Compose mounts a volume at /app/data/uploads; it must exist here or Docker creates it root-owned.
RUN useradd --create-home appuser \
    && mkdir -p /app/data/uploads \
    && chown -R appuser /app/data
USER appuser

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
