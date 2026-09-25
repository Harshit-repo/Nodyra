# Stage 1: Build the production bundle
FROM node:26.10.0-bookworm-slim AS builder
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm ci --ignore-scripts
COPY . .
RUN npm run build

# Stage 2: Serve the static bundle with nginx
FROM cgr.dev/chainguard/nginx:latest-dev@sha256:3313b8400eb5278c193ac72d28803eeb27631f7fc1a42c0841cf68d5685a0946 AS production
USER root
RUN apk add --no-cache gettext-envsubst \
    && mkdir -p /var/cache/nginx /var/run /etc/nginx/conf.d \
    && chown -R 65532:65532 /var/cache/nginx /var/run /etc/nginx/conf.d
COPY --from=builder /app/dist /usr/share/nginx/html
COPY nginx-main.conf /etc/nginx/nginx.conf
COPY nginx.conf /etc/nginx/templates/default.conf.template
COPY --chmod=755 15-nodyra-dns.envsh /docker-entrypoint.d/15-nodyra-dns.envsh
COPY --chmod=755 docker-entrypoint.sh /docker-entrypoint.sh
ENV NODYRA_API_UPSTREAM=api:8000 \
    NODYRA_WEB_PORT=5173 \
    NODYRA_MAX_BODY_SIZE=52m \
    NGINX_ENVSUBST_FILTER=^NODYRA_
EXPOSE 5173
USER 65532:65532
ENTRYPOINT ["/docker-entrypoint.sh"]
CMD ["nginx", "-g", "daemon off;"]
