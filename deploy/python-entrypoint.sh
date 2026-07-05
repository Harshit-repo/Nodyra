#!/bin/sh
set -eu

NODYRA_USER="${NODYRA_USER:-nodyra}"
ENVS_DIR="${ENVS_DIR:-/app/envs}"
ARTIFACTS_DIR="${ARTIFACTS_DIR:-/app/artifacts}"

fix_writable_dir() {
    dir="$1"
    [ -n "$dir" ] || return 0

    mkdir -p "$dir"
    if ! chown -R "$NODYRA_USER:$NODYRA_USER" "$dir" 2>/dev/null; then
        echo "warning: could not chown $dir; checking write access as $NODYRA_USER" >&2
    fi
    if ! gosu "$NODYRA_USER" sh -c 'test -w "$1"' sh "$dir"; then
        echo "error: $dir is not writable by $NODYRA_USER" >&2
        echo "fix the host bind mount permissions or recreate the Docker volume" >&2
        exit 1
    fi
}

configure_docker_socket_group() {
    sock="${DOCKER_HOST_UNIX_SOCKET:-/var/run/docker.sock}"
    [ "$NODYRA_USER" != "root" ] || return 0
    [ -S "$sock" ] || return 0

    gid="$(stat -c '%g' "$sock" 2>/dev/null || true)"
    [ -n "$gid" ] || return 0

    if ! getent group "$gid" >/dev/null 2>&1; then
        groupadd --gid "$gid" dockerhost >/dev/null 2>&1 || true
    fi
    if ! id -nG "$NODYRA_USER" | tr ' ' '\n' | grep -qx "$(getent group "$gid" | cut -d: -f1)"; then
        usermod -aG "$gid" "$NODYRA_USER" >/dev/null 2>&1 || true
    fi
}

if [ "$(id -u)" = "0" ]; then
    configure_docker_socket_group
    fix_writable_dir "$ENVS_DIR"
    fix_writable_dir "$ARTIFACTS_DIR"

    # Docker Compose string commands arrive as one argv item; run them through
    # a shell for compatibility. Exec-form commands pass through unchanged.
    if [ "$#" -eq 1 ]; then
        exec gosu "$NODYRA_USER" sh -c "$1"
    fi
    exec gosu "$NODYRA_USER" "$@"
fi

exec "$@"
