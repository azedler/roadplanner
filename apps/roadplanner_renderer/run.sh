#!/usr/bin/env sh
# Start the worker as PID 1's direct replacement.
#
# `exec` matters: without it this shell stays the process Home Assistant
# signals, and the Node worker would never see SIGTERM. A container that
# ignores SIGTERM gets killed after the grace period, which would look like
# a crash in exactly the restart tests this PoC has to pass.
set -eu

# The version is read from the file the build wrote, so what the heartbeat
# reports is what was actually built - not what a Dockerfile substitution
# happened to produce.
if [ -z "${ROADPLANNER_APP_VERSION:-}" ] && [ -r /opt/roadplanner-renderer/VERSION ]; then
  ROADPLANNER_APP_VERSION="$(cat /opt/roadplanner-renderer/VERSION)"
fi
export ROADPLANNER_APP_VERSION="${ROADPLANNER_APP_VERSION:-0.0.0-unknown}"

EXCHANGE_DIR="${ROADPLANNER_EXCHANGE_DIR:-/share/roadplanner-renderer/poc-v1}"
export ROADPLANNER_EXCHANGE_DIR="${EXCHANGE_DIR}"

# Where the build actually put things.
#
# The Dockerfile declares these as ENV too, but the Home Assistant base
# image starts services through s6-overlay, which does not hand the
# container environment to them by default. That cost a CI round: the
# worker fell back to its own default of /usr/bin/chromium and reported
# "browser could not be started" for a browser that was present all along.
# Every other variable happened to have a correct fallback, so the loss was
# invisible until the browser moved out of the default location.
#
# Setting them here, in the process that actually execs the worker, does
# not depend on any of that. `${VAR:-default}` keeps a deliberate override
# working if one ever does arrive.
export ROADPLANNER_BROWSER="${ROADPLANNER_BROWSER:-/opt/roadplanner-renderer/browser/chrome-headless-shell}"
export ROADPLANNER_FFPROBE="${ROADPLANNER_FFPROBE:-/opt/ffprobe/bin/ffprobe}"
export ROADPLANNER_BUNDLE_DIR="${ROADPLANNER_BUNDLE_DIR:-/opt/roadplanner-renderer/bundle}"
# ffprobe was lifted out of the ffmpeg package with exactly its own
# libraries; without this it cannot find a single one of them.
export LD_LIBRARY_PATH="${LD_LIBRARY_PATH:-/opt/ffprobe/lib}"
# `${PATH}` unguarded would abort under `set -u` - and a stripped
# environment is exactly the situation this block exists for.
export PATH="/opt/node/bin:/opt/ffprobe/bin:${PATH:-/usr/local/bin:/usr/bin:/bin}"
export NODE_ENV="${NODE_ENV:-production}"
# Belt and braces: nothing may be downloaded on a user's machine, whatever
# else is or is not set.
export REMOTION_SKIP_BROWSER_DOWNLOAD=1
export REMOTION_DISABLE_TELEMETRY=1

# Fail at startup, not at the first render. A missing binary here is a
# broken image, and saying so now is far cheaper than a job that dies
# seconds in with a message about a path nobody chose.
for tool in "${ROADPLANNER_BROWSER}" "${ROADPLANNER_FFPROBE}"; do
  if [ ! -x "${tool}" ]; then
    echo "Erwartetes Programm fehlt oder ist nicht ausführbar: ${tool}" >&2
    exit 1
  fi
done
if [ ! -d "${ROADPLANNER_BUNDLE_DIR}" ]; then
  echo "Das Remotion-Bundle fehlt: ${ROADPLANNER_BUNDLE_DIR}" >&2
  exit 1
fi

if ! mkdir -p "${EXCHANGE_DIR}"; then
  echo "Austauschordner ${EXCHANGE_DIR} kann nicht angelegt werden." >&2
  echo "Ist die share-Freigabe für diese App aktiviert?" >&2
  exit 1
fi

exec /opt/node/bin/node /opt/roadplanner-renderer/src/index.mjs
