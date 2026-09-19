#!/bin/sh
set -eu

# v1 started crond in the background and ran a separate Python process per
# library, per action. v2 has its own scheduler, so the container is a single
# foreground process that signals cleanly and logs to stdout.

if [ ! -f "$PAG_CONFIG" ]; then
    cat >&2 <<MSG
No config file at $PAG_CONFIG.

Mount a directory containing config.json:
    -v /path/to/config:/config

A starting point is available at:
    https://github.com/Dim145/plex-auto-genres/blob/master/config/config.json.example
MSG
    exit 1
fi

COMMON="--config $PAG_CONFIG --db $PAG_DB"

# Any argument turns the container into a one-shot CLI invocation, e.g.
#   docker run --rm ... plex-auto-genres doctor
if [ "$#" -gt 0 ]; then
    exec plex-auto-genres $COMMON "$@"
fi

echo "plex-auto-genres $(plex-auto-genres --version | awk '{print $2}') — TZ=$TZ"
plex-auto-genres $COMMON doctor || echo "(doctor reported issues; continuing anyway)"

RUN_NOW=""
if [ "${RUN_ON_START}" = "true" ]; then
    RUN_NOW="--now"
fi

exec plex-auto-genres $COMMON schedule \
    --cron "$CRON_SCHEDULE" \
    --posters-dir "$PAG_POSTERS" \
    $RUN_NOW
