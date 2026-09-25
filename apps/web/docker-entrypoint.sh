#!/bin/sh
set -eu

. /docker-entrypoint.d/15-nodyra-dns.envsh

# Substitute only deployment settings; preserve nginx variables such as $host,
# $scheme, and $request_uri, and never interpolate unrelated environment data.
envsubst '${NODYRA_API_UPSTREAM} ${NODYRA_WEB_PORT} ${NODYRA_MAX_BODY_SIZE} ${NODYRA_DNS_RESOLVER}' \
    < /etc/nginx/templates/default.conf.template \
    > /etc/nginx/conf.d/default.conf

exec "$@"
