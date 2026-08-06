# The renderer PoC image.
#
# Pinned by digest-free tag plus an explicit Node version: the point of the
# spike is reproducibility, and a floating base would make a later "it
# worked yesterday" impossible to investigate.
#
# Dependencies are installed at BUILD time with `npm ci`. Nothing is ever
# installed when the container starts - a user's system must not have work
# done on it at runtime. As it happens the app has zero runtime
# dependencies, so the install is a no-op that exists to keep the lockfile
# honest if that ever changes.
ARG BUILD_FROM=ghcr.io/home-assistant/amd64-base:3.22
FROM ${BUILD_FROM}

ARG APP_VERSION=0.0.0-dev
ENV ROADPLANNER_APP_VERSION=${APP_VERSION} \
    NODE_ENV=production \
    NPM_CONFIG_UPDATE_NOTIFIER=false \
    NPM_CONFIG_FUND=false

# nodejs and npm come from the Alpine base's pinned repository set.
RUN apk add --no-cache nodejs npm

WORKDIR /opt/roadplanner-renderer

COPY package.json package-lock.json ./
RUN npm ci --omit=dev --ignore-scripts

COPY src/ ./src/
COPY run.sh /run.sh
RUN chmod a+x /run.sh

LABEL \
  io.hass.name="Roadplanner Renderer (PoC)" \
  io.hass.description="Experimenteller Machbarkeitsnachweis ohne Remotion" \
  io.hass.type="addon" \
  io.hass.version="${APP_VERSION}" \
  org.opencontainers.image.source="https://github.com/azedler/roadplanner"

CMD ["/run.sh"]
