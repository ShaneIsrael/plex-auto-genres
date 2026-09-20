#!/bin/sh
set -eu

# One foreground process. PAG_MODE picks which:
#   serve     web UI + API on PAG_WEB_PORT, with the scheduler in-process
#   schedule  headless scheduler only, as v1 behaved
# Unset, the mode follows the environment: serve when a login is configured
# (PAG_WEB_PASSWORD or PAG_WEB_INSECURE), otherwise schedule -- so a container
# carried over from v1 keeps doing its nightly pass instead of refusing to
# expose an open API.
# Any argument instead turns the container into a one-shot CLI invocation:
#   docker run --rm ... plex-auto-genres doctor
#
# The container starts as root only to hand the mounted volumes to PUID:PGID
# (v1 ran as root and left root-owned files), then runs everything as that
# user. PUID=0 keeps root, as v1 was.

if [ ! -f "$PAG_CONFIG" ]; then
    cat >&2 <<MSG
No config file at $PAG_CONFIG.

Mount a directory containing config.json:
    -v /path/to/config:/config

A starting point is config/config.json.example in the repository.
MSG
    exit 1
fi

RUN_AS=""
if [ "$(id -u)" = "0" ] && [ "${PUID:-1000}" != "0" ]; then
    RUN_AS="${PUID:-1000}:${PGID:-1000}"
    for dir in "$(dirname "$PAG_CONFIG")" "$(dirname "$PAG_DB")" "$PAG_POSTERS"; do
        [ -d "$dir" ] || continue
        if find "$dir" ! -user "${PUID:-1000}" -print -quit 2>/dev/null | grep -q .; then
            echo "Handing $dir to uid ${PUID:-1000} (files left by a previous version were owned by someone else)" >&2
            chown -R "$RUN_AS" "$dir" \
                || echo "warning: could not change the owner of $dir; if it must stay as is, run with PUID=0" >&2
        fi
    done
fi

# Run a command as the runtime user (or as we are, when RUN_AS is empty).
as_user() {
    if [ -n "$RUN_AS" ]; then
        su-exec "$RUN_AS" "$@"
    else
        "$@"
    fi
}
exec_as_user() {
    if [ -n "$RUN_AS" ]; then
        exec su-exec "$RUN_AS" "$@"
    else
        exec "$@"
    fi
}

if [ "$#" -gt 0 ]; then
    exec_as_user plex-auto-genres --config "$PAG_CONFIG" --db "$PAG_DB" "$@"
fi

MODE="${PAG_MODE:-}"
if [ -z "$MODE" ]; then
    if [ -n "${PAG_WEB_PASSWORD:-}" ] || [ -n "${PAG_WEB_INSECURE:-}" ]; then
        MODE=serve
    else
        MODE=schedule
        echo "PAG_MODE is unset and no PAG_WEB_PASSWORD is configured: running headless." >&2
        echo "Set PAG_WEB_PASSWORD (or PAG_MODE=serve with PAG_WEB_INSECURE=1) for the web UI." >&2
    fi
fi

echo "plex-auto-genres $(plex-auto-genres --version | awk '{print $2}') — mode=$MODE TZ=$TZ user=${RUN_AS:-$(id -u):$(id -g)}"
as_user plex-auto-genres --config "$PAG_CONFIG" --db "$PAG_DB" doctor --offline \
    || echo "(doctor reported issues; continuing anyway)"

# The optional --now flag travels as a positional parameter, never word-split.
set --
if [ "${RUN_ON_START}" = "true" ]; then
    set -- --now
fi

case "$MODE" in
    serve)
        exec_as_user plex-auto-genres --config "$PAG_CONFIG" --db "$PAG_DB" serve \
            --host 0.0.0.0 --port "$PAG_WEB_PORT" \
            --cron "$CRON_SCHEDULE" \
            --posters-dir "$PAG_POSTERS" \
            "$@"
        ;;
    schedule)
        exec_as_user plex-auto-genres --config "$PAG_CONFIG" --db "$PAG_DB" schedule \
            --cron "$CRON_SCHEDULE" \
            --posters-dir "$PAG_POSTERS" \
            "$@"
        ;;
    *)
        echo "Unknown PAG_MODE='$MODE' (expected serve or schedule)" >&2
        exit 2
        ;;
esac
