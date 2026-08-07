# The renderer app image, now with Remotion.
#
# Everything the renderer needs is baked in here: the Node runtime, the
# pinned npm tree, a Chromium, the fonts it draws with, ffprobe, and the
# pre-built Remotion bundle. **Nothing is installed or downloaded while a
# job runs, and nothing is installed when the container starts.** A renderer
# that fetches a browser onto someone's Home Assistant mid-render is not
# something to ship.
#
# Everything is pinned: the base image by exact tag, Node by version *and*
# SHA-256, npm packages by lockfile. The point of a spike is that a later
# "it worked yesterday" can actually be investigated.
#
# Debian rather than Alpine on purpose. Remotion publishes a musl compositor
# and Alpine would produce a smaller image, but Chromium on glibc is the
# far better-trodden path for headless rendering, and a spike should fail
# for interesting reasons rather than for the base distribution.
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
    rm -f /tmp/node.tar.xz

# --- the app -----------------------------------------------------------
FROM ${BUILD_FROM}

# Two builders pass two different variables. CI passes APP_VERSION; the
# Supervisor, when it builds the app on the user's own system, passes
# BUILD_VERSION taken from config.yaml. Reading only one of them meant a
# locally built image reported a version that does not exist.
ARG BUILD_VERSION=0.0.0-unknown
ARG APP_VERSION=""
ENV ROADPLANNER_APP_VERSION=${APP_VERSION:-${BUILD_VERSION}} \
    NODE_ENV=production \
    PATH=/opt/node/bin:${PATH} \
    NPM_CONFIG_UPDATE_NOTIFIER=false \
    NPM_CONFIG_FUND=false \
    ROADPLANNER_BUNDLE_DIR=/opt/roadplanner-renderer/bundle \
    ROADPLANNER_BROWSER=/usr/bin/chromium \
    # Remotion phones home unless told not to.
    REMOTION_DISABLE_TELEMETRY=1 \
    # The renderer must never fetch its own browser.
    REMOTION_SKIP_BROWSER_DOWNLOAD=1

COPY --from=node-runtime /opt/node /opt/node

# chromium: what Remotion drives. fonts-*: without them every glyph renders
# as a box, which looks like a broken composition rather than a missing
# package. ffmpeg: brings ffprobe, which is what turns "Remotion exited 0"
# into "a valid H.264 file exists".
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
      chromium \
      ffmpeg \
      fonts-liberation \
      fonts-dejavu-core \
      ca-certificates; \
    apt-get clean; \
    rm -rf /var/lib/apt/lists/*

WORKDIR /opt/roadplanner-renderer

# Dependencies at BUILD time, from the lockfile, with no lifecycle scripts.
COPY package.json package-lock.json ./
RUN npm ci --omit=dev --ignore-scripts

COPY tsconfig.json ./
COPY src/ ./src/
COPY scripts/ ./scripts/

# Bundle the composition once, here. Doing it per render would burn CPU on
# a user's machine for a result that never changes within an image - and it
# belongs where it can fail loudly in CI.
RUN node scripts/bundle.mjs

COPY run.sh /run.sh
RUN chmod a+x /run.sh

LABEL \
  io.hass.name="Roadplanner Renderer (PoC)" \
  io.hass.description="Remotion-Spike: Testrender in der App, ohne Produktivexport" \
  io.hass.type="addon" \
  io.hass.version="${ROADPLANNER_APP_VERSION}" \
  org.opencontainers.image.source="https://github.com/azedler/roadplanner"

CMD ["/run.sh"]
