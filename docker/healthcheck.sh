#!/bin/sh
# In serve mode the API answering is the liveness signal: /auth/status is
# public, so the probe needs no credential (a password on a wget command line
# would sit in /proc/<pid>/cmdline every minute). In headless schedule mode
# there is no HTTP, so fall back to the offline config checks -- offline
# because a probe must not block on a metadata provider being slow.
MODE="${PAG_MODE:-}"
if [ -z "$MODE" ]; then
    # Same inference as the entrypoint.
    if [ -n "${PAG_WEB_PASSWORD:-}" ] || [ -n "${PAG_WEB_INSECURE:-}" ]; then
        MODE=serve
    else
        MODE=schedule
    fi
fi

if [ "$MODE" = "serve" ]; then
    wget -qO- "http://127.0.0.1:${PAG_WEB_PORT:-8095}/api/v1/auth/status" >/dev/null 2>&1
elif [ "$(id -u)" = "0" ] && [ "${PUID:-1000}" != "0" ]; then
    # As the runtime user: a root-owned WAL file would lock the app out of its database.
    su-exec "$((${PUID:-1000})):$((${PGID:-1000}))" \
        plex-auto-genres --config "$PAG_CONFIG" --db "$PAG_DB" doctor --offline >/dev/null 2>&1
else
    plex-auto-genres --config "$PAG_CONFIG" --db "$PAG_DB" doctor --offline >/dev/null 2>&1
fi
