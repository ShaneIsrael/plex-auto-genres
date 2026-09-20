#!/bin/sh
set -eu

# One foreground process. PAG_MODE picks which:
#   serve     web UI + API on PAG_WEB_PORT, with the scheduler in-process
#   schedule  headless scheduler only, as v1 behaved
# Unset, the mode follows the environment: serve when a login is configured
# (PAG_WEB_PASSWORD or PAG_WEB_INSECURE), otherwise schedule -- so a container
# carried over from v1 keeps doing its nightly pass instead of refusing to
# expose an open API.
#
# Any argument instead turns the container into a one-shot CLI invocation:
#   docker run --rm ... plex-auto-genres doctor
#   docker run --rm --user 0 ... fix-permissions     (see below)
#
# Ownership. v1 ran as root, so an upgraded install's /config and /logs belong
# to root while this image runs the app as PUID:PGID (default 1000:1000).
# When the container starts as root it hands those two directories over before
# dropping privileges; when it starts as someone else (compose `user:`,
# Kubernetes runAsUser) it cannot, so it says exactly what to run instead.
# PUID=0 keeps everything as root, as v1 did.

die() {
    for line in "$@"; do
        echo "$line" >&2
    done
    exit 1
}

case "${PUID:-1000}" in *[!0-9]*) die "PUID must be a number, not '${PUID}'." ;; esac
case "${PGID:-1000}" in *[!0-9]*) die "PGID must be a number, not '${PGID}'." ;; esac
# Arithmetic, so "00" and "0" are the same request: stay root.
APP_UID=$((${PUID:-1000}))
APP_GID=$((${PGID:-1000}))

I_AM=$(id -u)
RUN_AS=""
if [ "$I_AM" -eq 0 ] && [ "$APP_UID" -ne 0 ]; then
    RUN_AS="$APP_UID:$APP_GID"
fi

as_user() {
    if [ -n "$RUN_AS" ]; then
        su-exec "$RUN_AS" "$@"
    else
        "$@"
    fi
}

# Can the account the app will run as write here?
writable() {
    as_user test -w "$1"
}

# Hand one directory over: the directory itself and the files directly in it.
# Never recursive, never through a symlink -- /config and /logs hold a handful
# of flat files, and `chown -R` on a volume the host shares would be a way to
# rewrite the ownership of anything a symlink there points at.
hand_over() {
    chown "$APP_UID:$APP_GID" "$1" 2>/dev/null || true
    find "$1" -maxdepth 1 -type f -exec chown "$APP_UID:$APP_GID" {} + 2>/dev/null || true
}

CONFIG_DIR=$(dirname "$PAG_CONFIG")
LOG_DIR=$(dirname "$PAG_DB")

fix_permissions() {
    [ "$I_AM" -eq 0 ] || die "fix-permissions needs root: add --user 0 to this one command."
    for dir in "$CONFIG_DIR" "$LOG_DIR" "$PAG_POSTERS"; do
        [ -d "$dir" ] || continue
        echo "Handing $dir to $APP_UID:$APP_GID"
        hand_over "$dir"
    done
    echo "Done. Start the container normally again."
}

if [ "${1:-}" = "fix-permissions" ]; then
    fix_permissions
    exit 0
fi

# /posters is only ever read, so it is left alone; these two are written.
for dir in "$CONFIG_DIR" "$LOG_DIR"; do
    [ -d "$dir" ] || die "$dir does not exist. Mount it: -v /path/on/host:$dir"
    writable "$dir" && continue
    if [ -n "$RUN_AS" ]; then
        echo "Handing $dir to $APP_UID:$APP_GID (it belongs to the user an older version ran as)" >&2
        hand_over "$dir"
        writable "$dir" || die \
            "$dir is still not writable by $APP_UID:$APP_GID." \
            "If it is a network share that refuses chown, mount it with uid=$APP_UID," \
            "or set PUID/PGID to the owner it already has."
    else
        die "$dir is not writable by uid $I_AM:$(id -g)." \
            "This container was started with a fixed user, so it cannot fix that itself." \
            "Either hand the volumes over once:" \
            "    docker compose run --rm --user 0 plex-auto-genres fix-permissions" \
            "or set PUID/PGID to the uid that owns them and start without 'user:'."
    fi
done

if [ ! -f "$PAG_CONFIG" ]; then
    cat >&2 <<MSG
No config file at $PAG_CONFIG.

Mount a directory containing config.json:
    -v /path/to/config:/config

A starting point is config/config.json.example in the repository.
MSG
    exit 1
fi

# su-exec keeps the environment, so HOME would still say /root; anything that
# writes under ~ (a provider client's cache) must land somewhere writable.
if [ -n "$RUN_AS" ]; then
    HOME=/tmp
    export HOME
fi

if [ "$#" -gt 0 ]; then
    if [ -n "$RUN_AS" ]; then
        exec su-exec "$RUN_AS" plex-auto-genres --config "$PAG_CONFIG" --db "$PAG_DB" "$@"
    fi
    exec plex-auto-genres --config "$PAG_CONFIG" --db "$PAG_DB" "$@"
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

echo "plex-auto-genres $(plex-auto-genres --version | awk '{print $2}') — mode=$MODE TZ=$TZ user=${RUN_AS:-$I_AM:$(id -g)}"
as_user plex-auto-genres --config "$PAG_CONFIG" --db "$PAG_DB" doctor --offline \
    || echo "(doctor reported issues; continuing anyway)"

# The optional --now flag travels as a positional parameter, never word-split.
set --
if [ "${RUN_ON_START}" = "true" ]; then
    set -- --now
fi

case "$MODE" in
    serve)
        set -- serve --host 0.0.0.0 --port "$PAG_WEB_PORT" --cron "$CRON_SCHEDULE" \
            --posters-dir "$PAG_POSTERS" "$@"
        ;;
    schedule)
        set -- schedule --cron "$CRON_SCHEDULE" --posters-dir "$PAG_POSTERS" "$@"
        ;;
    *)
        die "Unknown PAG_MODE='$MODE' (expected serve or schedule)"
        ;;
esac

if [ -n "$RUN_AS" ]; then
    exec su-exec "$RUN_AS" plex-auto-genres --config "$PAG_CONFIG" --db "$PAG_DB" "$@"
fi
exec plex-auto-genres --config "$PAG_CONFIG" --db "$PAG_DB" "$@"
