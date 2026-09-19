#!/bin/sh
set -eu

# One foreground process. PAG_MODE picks which:
#   serve     web UI + API on PAG_WEB_PORT, with the scheduler in-process (default)
#   schedule  headless scheduler only, as v1 behaved
# Any argument instead turns the container into a one-shot CLI invocation:
#   docker run --rm ... plex-auto-genres doctor

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

if [ "$#" -gt 0 ]; then
    exec plex-auto-genres $COMMON "$@"
fi

echo "plex-auto-genres $(plex-auto-genres --version | awk '{print $2}') — mode=$PAG_MODE TZ=$TZ"
plex-auto-genres $COMMON doctor || echo "(doctor reported issues; continuing anyway)"

RUN_NOW=""
if [ "${RUN_ON_START}" = "true" ]; then
    RUN_NOW="--now"
fi

case "$PAG_MODE" in
    serve)
        exec plex-auto-genres $COMMON serve \
            --host 0.0.0.0 --port "$PAG_WEB_PORT" \
            --cron "$CRON_SCHEDULE" \
            --posters-dir "$PAG_POSTERS" \
            $RUN_NOW
        ;;
    schedule)
        exec plex-auto-genres $COMMON schedule \
            --cron "$CRON_SCHEDULE" \
            --posters-dir "$PAG_POSTERS" \
            $RUN_NOW
        ;;
    *)
        echo "Unknown PAG_MODE='$PAG_MODE' (expected serve or schedule)" >&2
        exit 2
        ;;
esac
