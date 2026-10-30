FROM node:22-bookworm-slim AS builder
ARG VITE_BAIDU_BROWSER_AK=""
WORKDIR /src
COPY package.json package-lock.json ./
RUN npm ci
COPY . ./
RUN VITE_BAIDU_BROWSER_AK="$VITE_BAIDU_BROWSER_AK" npm run build

FROM nginx:1.28-alpine AS runtime
# The upstream is a runtime setting, not part of the Vue bundle.
ENV API_UPSTREAM=http://backend:8000 \
    NGINX_ENVSUBST_FILTER=^API_UPSTREAM$
COPY nginx/default.conf.template /etc/nginx/templates/default.conf.template
COPY nginx/api-proxy.conf /etc/nginx/api-proxy.conf
COPY --from=builder /src/dist/ /usr/share/nginx/html/
EXPOSE 80
# Frontend readiness is independent of backend/engine readiness.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD wget -q -O /dev/null http://127.0.0.1/ || exit 1
