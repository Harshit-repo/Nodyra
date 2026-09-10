# Stage 1: Build the production bundle
FROM node:22.23.1-bookworm-slim AS builder
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm ci --ignore-scripts
COPY . .
RUN npm run build

# Stage 2: Serve the static bundle with nginx
FROM nginx:1.30.4-alpine3.24 AS production
# The nginx image can lag Alpine security updates between image rebuilds.
RUN apk add --no-cache 'libuuid>=2.42.3-r1'
COPY --from=builder /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/templates/default.conf.template
COPY --chmod=755 15-nodyra-dns.envsh /docker-entrypoint.d/15-nodyra-dns.envsh
ENV NODYRA_API_UPSTREAM=api:8000 \
    NODYRA_WEB_PORT=5173 \
    NODYRA_MAX_BODY_SIZE=52m \
    NGINX_ENVSUBST_FILTER=^NODYRA_
EXPOSE 5173
CMD ["nginx", "-g", "daemon off;"]
