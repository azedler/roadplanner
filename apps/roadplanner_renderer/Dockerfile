# The renderer app image, built to be small on purpose.
#
# The first working image was 1574 MB. Nothing about that was necessary; it
# was a full Chromium, a full ffmpeg, the build toolchain and the npm cache
# all riding along into the runtime layer. This build keeps the same
# behaviour and drops the parts that never run:
#
#   - `chrome-headless-shell` instead of a full Chromium. Same rendering
#     engine, without the browser UI, the extension host and the sync
#     stack. Downloaded once here, never at runtime.
#   - `ffprobe` alone instead of the whole ffmpeg package. ffprobe is the
#     only binary the validation uses, and it is copied with exactly the
#     shared libraries it names.
#   - a build stage that stays behind. The bundler, React, TypeScript and
#     the npm cache produce the bundle and are then left in the builder.
#
# **Nothing is installed or downloaded while a job runs, and nothing is
# installed when the container starts.** Every version is pinned: the base
# image by exact tag, Node by version *and* SHA-256, npm packages by
# lockfile, the browser by the Remotion version that fetches it.
ARG BUILD_FROM=ghcr.io/home-assistant/amd64-base-debian:bookworm-2026.02.0

# --- Node runtime, verified before it is unpacked ----------------------
FROM ${BUILD_FROM} AS node-runtime
ARG NODE_VERSION=22.23.2
ARG NODE_SHA256=d60acfe00a2932254bb0ad20e01b0d74397a0875595de719654b214f4b03f307
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends ca-certificates curl xz-utils; \
    curl -fsSL -o /tmp/node.tar.xz \
      "https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-x64.tar.xz"; \
    echo "${NODE_SHA256}  /tmp/node.tar.xz" | sha256sum -c -; \
    mkdir -p /opt/node; \
    tar -xJf /tmp/node.tar.xz -C /opt/node --strip-components=1; \
    rm -f /tmp/node.tar.xz; \
    # npm, corepack and the headers are build-time tools; the runtime only
    # ever executes `node`.
    rm -rf /opt/node/include /opt/node/share /opt/node/lib/node_modules/corepack

# --- ffprobe, lifted out of the ffmpeg package -------------------------
# Installing ffmpeg pulls in the whole encoder/decoder/filter stack for one
# binary that only reads metadata. It is copied here with exactly the
# libraries it declares, resolved by ldd rather than guessed.
FROM ${BUILD_FROM} AS ffprobe-runtime
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends ffmpeg; \
    mkdir -p /opt/ffprobe/bin /opt/ffprobe/lib; \
    cp /usr/bin/ffprobe /opt/ffprobe/bin/ffprobe; \
    ldd /usr/bin/ffprobe \
      | awk '/=> \//{print $3}' \
      | sort -u \
      | xargs -I{} cp -L {} /opt/ffprobe/lib/; \
    /opt/ffprobe/bin/ffprobe -version | head -1

# --- the build: dependencies, bundle, browser --------------------------
FROM ${BUILD_FROM} AS builder
ENV PATH=/opt/node/bin:${PATH} \
    NPM_CONFIG_UPDATE_NOTIFIER=false \
    NPM_CONFIG_FUND=false \
    REMOTION_DISABLE_TELEMETRY=1
COPY --from=node-runtime /opt/node /opt/node
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends ca-certificates unzip

WORKDIR /build
COPY package.json package-lock.json ./
# The full tree here - the bundler and React are needed to produce the
# bundle and are then left behind with this stage.
RUN npm ci --ignore-scripts

COPY tsconfig.json ./
COPY src/ ./src/
COPY scripts/ ./scripts/

# The composition is bundled once. Doing it per render would burn CPU on a
# user's machine for a result that never changes within an image.
RUN node scripts/bundle.mjs

# The one download in this image, and it happens here.
RUN node scripts/ensure-browser.mjs

# The runtime tree, resolved separately so the builder's dev packages
# cannot leak into the final image.
RUN npm ci --omit=dev --ignore-scripts \
    && npm cache clean --force \
    && rm -rf /root/.npm

# --- the app -----------------------------------------------------------
FROM ${BUILD_FROM}

# Two builders pass two different variables. CI passes APP_VERSION; the
# Supervisor passes BUILD_VERSION when it builds on the user's own system.
ARG BUILD_VERSION=0.0.0-unknown
ARG APP_VERSION=""

ENV NODE_ENV=production \
    PATH=/opt/node/bin:/opt/ffprobe/bin:${PATH} \
    LD_LIBRARY_PATH=/opt/ffprobe/lib \
    NPM_CONFIG_UPDATE_NOTIFIER=false \
    NPM_CONFIG_FUND=false \
    ROADPLANNER_BUNDLE_DIR=/opt/roadplanner-renderer/bundle \
    ROADPLANNER_BROWSER=/opt/roadplanner-renderer/browser/chrome-headless-shell \
    ROADPLANNER_FFPROBE=/opt/ffprobe/bin/ffprobe \
    REMOTION_DISABLE_TELEMETRY=1 \
    REMOTION_SKIP_BROWSER_DOWNLOAD=1

# What the headless shell actually links against. The full `chromium`
# package would pull several hundred megabytes of browser it never uses.
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
      ca-certificates \
      fonts-liberation \
      fonts-dejavu-core \
      libasound2 \
      libatk-bridge2.0-0 \
      libatk1.0-0 \
      libatspi2.0-0 \
      libcairo2 \
      libcups2 \
      libdbus-1-3 \
      libdrm2 \
      libgbm1 \
      libglib2.0-0 \
      libnspr4 \
      libnss3 \
      libpango-1.0-0 \
      libx11-6 \
      libxcb1 \
      libxcomposite1 \
      libxdamage1 \
      libxext6 \
      libxfixes3 \
      libxkbcommon0 \
      libxrandr2; \
    apt-get clean; \
    rm -rf /var/lib/apt/lists/* /usr/share/doc /usr/share/man

COPY --from=node-runtime /opt/node /opt/node
COPY --from=ffprobe-runtime /opt/ffprobe /opt/ffprobe

WORKDIR /opt/roadplanner-renderer
COPY --from=builder /build/node_modules ./node_modules
# Both of these are written to their final location already, by the same
# ROADPLANNER_* variables the runtime reads - the builder's /build is only
# the source tree.
COPY --from=builder /opt/roadplanner-renderer/bundle ./bundle
COPY --from=builder /opt/roadplanner-renderer/browser ./browser
COPY package.json ./
COPY src/index.mjs src/protocol.mjs src/render.mjs ./src/

# One shell, one expansion, no guessing. ENV supports ${var:-default}, but
# a nested ${...} inside that default is not something to bet the reported
# version on - and a version nobody can trust is exactly the field that
# must never lie.
RUN printf '%s' "${APP_VERSION:-${BUILD_VERSION}}" > /opt/roadplanner-renderer/VERSION \
    && cat /opt/roadplanner-renderer/VERSION \
    && ffprobe -version | head -1 \
    && test -x "${ROADPLANNER_BROWSER}"

COPY run.sh /run.sh
RUN chmod a+x /run.sh

LABEL \
  io.hass.name="Roadplanner Renderer (PoC)" \
  io.hass.description="Remotion-Spike: Testrender in der App, ohne Produktivexport" \
  io.hass.type="addon" \
  io.hass.version="${APP_VERSION:-${BUILD_VERSION}}" \
  org.opencontainers.image.source="https://github.com/azedler/roadplanner"

CMD ["/run.sh"]
