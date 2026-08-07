#!/usr/bin/env sh
# Start the worker as PID 1's direct replacement.
#
# `exec` matters: without it this shell stays the process Home Assistant
# signals, and the Node worker would never see SIGTERM. A container that
# ignores SIGTERM gets killed after the grace period, which would look like
# a crash in exactly the restart tests this PoC has to pass.
set -eu

EXCHANGE_DIR="${ROADPLANNER_EXCHANGE_DIR:-/share/roadplanner-renderer/poc-v1}"
export ROADPLANNER_EXCHANGE_DIR="${EXCHANGE_DIR}"

if ! mkdir -p "${EXCHANGE_DIR}"; then
  echo "Austauschordner ${EXCHANGE_DIR} kann nicht angelegt werden." >&2
  echo "Ist die share-Freigabe für diese App aktiviert?" >&2
  exit 1
fi

exec /opt/node/bin/node /opt/roadplanner-renderer/src/index.mjs
