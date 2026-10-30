# Backend image only. Build the Vue/Nginx image from frontend/Dockerfile.
FROM debian:bookworm-slim AS cpp-builder
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential cmake \
    && rm -rf /var/lib/apt/lists/*

RUN g++ --version && cmake --version
WORKDIR /src/cpp-engine
COPY cpp-engine/ ./
COPY data/networks/synthetic-preview.json /src/data/networks/synthetic-preview.json
RUN cmake -S . -B build -DCMAKE_BUILD_TYPE=Release -DBUILD_TESTING=OFF \
    && cmake --build build --parallel

FROM python:3.12-slim-bookworm AS runtime
ARG WITH_MYSQL=false
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    APP_ENV=production \
    APP_HOST=0.0.0.0 \
    APP_PORT=8000 \
    CPP_ENGINE_PATH=/app/bin/isochrone_engine \
    ANALYSIS_CACHE_DIR=/app/data/cache \
    REGION_ID=shanghai-new-jiangwan
WORKDIR /app
RUN apt-get update \
    && apt-get install -y --no-install-recommends libstdc++6 \
    && rm -rf /var/lib/apt/lists/*
COPY backend/requirements.txt ./requirements.txt
COPY backend/requirements-mysql.txt ./requirements-mysql.txt
RUN pip install --no-cache-dir -r requirements.txt
RUN if [ "$WITH_MYSQL" = "true" ]; then pip install --no-cache-dir -r requirements-mysql.txt; fi
COPY backend/app/ ./app/
COPY backend/sql/ ./sql/
COPY data/networks/ ./data/networks/
COPY data/regions/ ./data/regions/
COPY contracts/engine-input.example.json ./contracts/engine-input.example.json
COPY --from=cpp-builder /src/cpp-engine/build/isochrone_engine ./bin/isochrone_engine
RUN mkdir -p /app/data/cache
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD ["python", "-m", "app.deployment_health"]
CMD ["sh", "-c", "exec uvicorn app.main:app --host ${APP_HOST} --port ${APP_PORT}"]
