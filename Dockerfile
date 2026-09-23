FROM node:22-bookworm-slim AS frontend-builder
WORKDIR /src/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM debian:bookworm-slim AS cpp-builder
RUN apt-get update \
    && apt-get install -y --no-install-recommends cmake g++ \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /src/cpp-engine
COPY cpp-engine/ ./
RUN cmake -S . -B build -DCMAKE_BUILD_TYPE=Release \
    && cmake --build build --parallel \
    && ctest --test-dir build --output-on-failure

FROM python:3.12-slim-bookworm AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    APP_HOST=0.0.0.0 \
    APP_PORT=8000 \
    STATIC_DIR=/app/static \
    CPP_ENGINE_PATH=/app/bin/isochrone_engine \
    ANALYSIS_CACHE_DIR=/app/data/cache
WORKDIR /app
COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/app/ ./app/
COPY data/networks/ ./data/networks/
COPY contracts/engine-input.example.json ./contracts/engine-input.example.json
COPY --from=frontend-builder /src/frontend/dist/ ./static/
COPY --from=cpp-builder /src/cpp-engine/build/isochrone_engine ./bin/isochrone_engine
RUN mkdir -p /app/data/cache
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=3)"
CMD ["sh", "-c", "uvicorn app.main:app --host ${APP_HOST} --port ${APP_PORT}"]
