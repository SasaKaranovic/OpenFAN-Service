# OpenFAN Service
FROM python:3.13-slim

LABEL org.opencontainers.image.title="OpenFAN Service" \
      org.opencontainers.image.description="Temperature-driven fan control for OpenFAN controllers, with a web UI" \
      org.opencontainers.image.source="https://github.com/SasaKaranovic/OpenFan-Service"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    OPENFAN_CONFIG_DIR=/config \
    OPENFAN_PORT=5340 \
    PUID=99 \
    PGID=100 \
    UMASK=022

# tini: proper signal handling (fans are set to a safe speed on stop)
# gosu: drop root after fixing /config permissions
RUN apt-get update \
 && apt-get install -y --no-install-recommends tini gosu \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY openfan_service ./openfan_service

# Version shown in the web UI footer and the API: build date (UTC, yyyy-mm-dd) + git hash.
# Passed in by scripts/docker-build.* and the GitHub workflow; plain "docker build" gives dev/unknown.
ARG OPENFAN_VERSION=dev
ARG OPENFAN_GIT_HASH=unknown
ENV OPENFAN_BUILD_VERSION=$OPENFAN_VERSION \
    OPENFAN_BUILD_HASH=$OPENFAN_GIT_HASH
LABEL org.opencontainers.image.version=$OPENFAN_VERSION \
      org.opencontainers.image.revision=$OPENFAN_GIT_HASH
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod 755 /entrypoint.sh

EXPOSE 5340
VOLUME ["/config"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/api/health' % os.environ.get('OPENFAN_PORT','5340'), timeout=4)" || exit 1

STOPSIGNAL SIGTERM
ENTRYPOINT ["/usr/bin/tini", "--", "/entrypoint.sh"]
CMD ["python", "-m", "openfan_service"]
