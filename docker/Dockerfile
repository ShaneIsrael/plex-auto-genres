# syntax=docker/dockerfile:1

# ---------------------------------------------------------------------------
# Builder
#
# v1 ran `apk add gcc g++ musl-dev` -- ~86 MB compressed -- for exactly one
# reason: numpy has no musl wheel, and numpy was imported solely to concatenate
# a nine-element list of strings. With numpy gone, every dependency is pure
# Python or ships a musllinux wheel for both amd64 and arm64.
#
# --only-binary=:all: is the guard that keeps it that way: if a dependency ever
# stops shipping a wheel, the build fails loudly here instead of quietly
# reintroducing a toolchain.
# ---------------------------------------------------------------------------
FROM python:3.12-alpine AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build
COPY requirements.txt pyproject.toml README.md ./
COPY plex_auto_genres/ ./plex_auto_genres/

RUN pip install --prefix=/install --no-compile --only-binary=:all: -r requirements.txt \
 && pip install --prefix=/install --no-compile --no-deps .

# ---------------------------------------------------------------------------
# Runtime
# ---------------------------------------------------------------------------
FROM python:3.12-alpine

LABEL org.opencontainers.image.title="plex-auto-genres" \
      org.opencontainers.image.description="Tag Plex media with genres from TMDB, MyAnimeList or AniList" \
      org.opencontainers.image.source="https://github.com/Dim145/plex-auto-genres" \
      org.opencontainers.image.licenses="MIT"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PAG_CONFIG=/config/config.json \
    PAG_DB=/logs/plex-auto-genres.db \
    PAG_POSTERS=/posters \
    CRON_SCHEDULE="0 1 * * *" \
    RUN_ON_START=true \
    TZ=UTC

# tzdata is what makes the TZ variable mean anything on Alpine.
RUN apk add --no-cache tzdata

COPY --from=builder /install /usr/local
COPY docker/entrypoint.sh /entrypoint.sh

# Run as a non-root user; the mounted volumes are the only writable paths.
RUN adduser -S -u 1000 -h /home/pag pag \
 && chmod +x /entrypoint.sh \
 && mkdir -p /config /logs /posters \
 && chown -R 1000 /config /logs /posters

USER pag
WORKDIR /

HEALTHCHECK --interval=5m --timeout=20s --start-period=30s --retries=3 \
    CMD plex-auto-genres --config "$PAG_CONFIG" --db "$PAG_DB" doctor >/dev/null 2>&1 || exit 1

ENTRYPOINT ["/entrypoint.sh"]
