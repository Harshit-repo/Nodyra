#!/bin/sh
set -eu

NOODLE_USER="${NOODLE_USER:-noodle}"
ENVS_DIR="${ENVS_DIR:-/app/envs}"
ARTIFACTS_DIR="${ARTIFACTS_DIR:-/app/artifacts}"

fix_writable_dir() {
    dir="$1"
    [ -n "$dir" ] || return 0

    mkdir -p "$dir"
    if ! chown -R "$NOODLE_USER:$NOODLE_USER" "$dir" 2>/dev/null; then
        echo "warning: could not chown $dir; checking write access as $NOODLE_USER" >&2
    fi
    if ! gosu "$NOODLE_USER" sh -c 'test -w "$1"' sh "$dir"; then
        echo "error: $dir is not writable by $NOODLE_USER" >&2
        echo "fix the host bind mount permissions or recreate the Docker volume" >&2
        exit 1
    fi
}

if [ "$(id -u)" = "0" ]; then
    fix_writable_dir "$ENVS_DIR"
    fix_writable_dir "$ARTIFACTS_DIR"

    # Docker Compose string commands arrive as one argv item; run them through
    # a shell for compatibility. Exec-form commands pass through unchanged.
    if [ "$#" -eq 1 ]; then
        exec gosu "$NOODLE_USER" sh -c "$1"
    fi
    exec gosu "$NOODLE_USER" "$@"
fi

exec "$@"
