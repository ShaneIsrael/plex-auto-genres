#!/bin/sh
# In serve mode the API answering is the liveness signal; in headless
# schedule mode there is no HTTP, so fall back to the offline config checks
# (no network: a probe must not block on a metadata provider being slow).
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
    if [ -n "${PAG_WEB_PASSWORD:-}" ]; then
        wget -qO- --header="Authorization: Bearer ${PAG_WEB_PASSWORD}" \
            "http://127.0.0.1:${PAG_WEB_PORT:-8095}/api/v1/health" >/dev/null 2>&1
    else
        wget -qO- "http://127.0.0.1:${PAG_WEB_PORT:-8095}/api/v1/health" >/dev/null 2>&1
    fi
else
    plex-auto-genres --config "$PAG_CONFIG" --db "$PAG_DB" doctor --offline >/dev/null 2>&1
fi
