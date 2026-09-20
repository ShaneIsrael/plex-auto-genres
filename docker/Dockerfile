# syntax=docker/dockerfile:1

# ---------------------------------------------------------------------------
# UI: built once here; node never enters the runtime image.
# ---------------------------------------------------------------------------
FROM node:22-alpine AS ui

WORKDIR /ui
RUN corepack enable
COPY ui/package.json ui/pnpm-lock.yaml ui/pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile
COPY ui/ ./
# Emit into this stage rather than into the Python package path the dev
# config uses, so the runtime stage can COPY a single directory.
RUN PAG_UI_OUT=/ui/dist pnpm build

# ---------------------------------------------------------------------------
# Python dependencies
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
      org.opencontainers.image.licenses="MIT"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PAG_CONFIG=/config/config.json \
    PAG_DB=/logs/plex-auto-genres.db \
    PAG_POSTERS=/posters \
    PAG_STATIC_DIR=/app/static \
    PAG_WEB_PORT=8095 \
    CRON_SCHEDULE="0 1 * * *" \
    RUN_ON_START=true \
    TZ=UTC

# tzdata is what makes the TZ variable mean anything on Alpine. Its version
# tracks the Alpine release of the base image; pinning it would break every
# rebuild after the next tzdata update for no security gain.
# hadolint ignore=DL3018
RUN apk add --no-cache tzdata

COPY --from=builder /install /usr/local
COPY --from=ui /ui/dist /app/static
COPY docker/entrypoint.sh /entrypoint.sh
COPY docker/healthcheck.sh /healthcheck.sh

# Run as a non-root user; the mounted volumes are the only writable paths.
RUN adduser -S -u 1000 -h /home/pag pag \
 && chmod +x /entrypoint.sh /healthcheck.sh \
 && mkdir -p /config /logs /posters \
 && chown -R 1000 /config /logs /posters

# Numeric, so the host (and Kubernetes' runAsNonRoot) can verify it.
USER 1000
WORKDIR /
EXPOSE 8095

HEALTHCHECK --interval=1m --timeout=15s --start-period=30s --retries=3 CMD ["/healthcheck.sh"]

ENTRYPOINT ["/entrypoint.sh"]
