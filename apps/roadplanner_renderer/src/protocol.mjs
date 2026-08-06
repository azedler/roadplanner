/**
 * The app's own implementation of the shared contract.
 *
 * It deliberately does NOT import anything from Roadplanner's Python side:
 * the two implementations are independent on purpose, so a change on one
 * side that breaks the agreement shows up as a failing test rather than
 * being silently absorbed by shared code. The contract is the JSON, not a
 * library.
 *
 * Node built-ins only — the app ships with zero npm dependencies, which is
 * the cheapest possible supply chain for something that runs beside a
 * user's Home Assistant.
 */
import { createHash } from "node:crypto";

export const SCHEMA_VERSION = 1;
export const PROTOCOL_VERSION = 1;
export const RENDERER_NAME = "roadplanner-renderer-poc";

export const APP_STATES = ["starting", "ready", "degraded", "stopping"];
export const JOB_STATES = [
  "queued",
  "claimed",
  "running",
  "completed",
  "failed",
  "expired",
];
export const TERMINAL_JOB_STATES = new Set(["completed", "failed", "expired"]);

export const ACTIONS = ["ping", "create_test_artifact"];
export const ARTIFACT_TEXT = "roadplanner-renderer-poc.txt";
export const ARTIFACT_IMAGE = "roadplanner-renderer-poc.svg";

export const MAX_JSON_BYTES = 64 * 1024;
export const MAX_MESSAGE_LENGTH = 120;

export const ERROR_INVALID_JOB = "INVALID_JOB";
export const ERROR_UNSUPPORTED_PROTOCOL = "UNSUPPORTED_PROTOCOL";
export const ERROR_EXPIRED = "EXPIRED";
export const ERROR_INTERNAL = "INTERNAL";

const JOB_ID_RE =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

export class ProtocolError extends Error {
  constructor(code, message) {
    super(message);
    this.code = code;
    this.retryable = false;
  }
}

export const formatTime = (date) =>
  `${new Date(date).toISOString().slice(0, 19)}Z`;

export const parseTime = (value) => {
  const text = String(value ?? "").trim();
  if (!text) return null;
  const parsed = Date.parse(text);
  return Number.isNaN(parsed) ? null : parsed;
};

export const cleanText = (value, limit = MAX_MESSAGE_LENGTH) =>
  String(value ?? "")
    .split(/\s+/)
    .filter(Boolean)
    .join(" ")
    .slice(0, limit);

export const sha256 = (data) => createHash("sha256").update(data).digest("hex");

export const isJobId = (value) => JOB_ID_RE.test(String(value ?? ""));

/**
 * Parse a job file. Every rejection carries a code, because "the app
 * ignored my job" is the one failure mode a file channel cannot explain by
 * itself — there is no connection to refuse.
 */
export function parseJob(raw, { now }) {
  if (Buffer.byteLength(raw) > MAX_JSON_BYTES) {
    throw new ProtocolError(ERROR_INVALID_JOB, "Auftrag überschreitet die Größengrenze.");
  }
  let payload;
  try {
    payload = JSON.parse(raw);
  } catch {
    throw new ProtocolError(ERROR_INVALID_JOB, "Auftrag ist kein gültiges JSON.");
  }
  if (payload === null || typeof payload !== "object" || Array.isArray(payload)) {
    throw new ProtocolError(ERROR_INVALID_JOB, "Auftrag ist kein Objekt.");
  }
  // Version first: an unknown protocol must be refused, not interpreted.
  if (
    payload.schema_version !== SCHEMA_VERSION ||
    payload.protocol_version !== PROTOCOL_VERSION
  ) {
    throw new ProtocolError(
      ERROR_UNSUPPORTED_PROTOCOL,
      "Der Auftrag verwendet eine nicht unterstützte Protokollversion.",
    );
  }
  if (!isJobId(payload.job_id)) {
    throw new ProtocolError(ERROR_INVALID_JOB, "Ungültige Job-ID.");
  }
  if (!ACTIONS.includes(payload.action)) {
    throw new ProtocolError(ERROR_INVALID_JOB, "Unbekannte Aktion.");
  }
  const message = cleanText(payload?.input?.message);
  if (!message) {
    throw new ProtocolError(ERROR_INVALID_JOB, "input.message fehlt.");
  }
  const expires = parseTime(payload.expires_at);
  if (expires === null) {
    throw new ProtocolError(ERROR_INVALID_JOB, "expires_at fehlt oder ist ungültig.");
  }
  if (expires <= now) {
    throw new ProtocolError(ERROR_EXPIRED, "Der Auftrag ist abgelaufen.");
  }
  return { jobId: payload.job_id, action: payload.action, message, expires };
}

export const envelope = (extra) => ({
  schema_version: SCHEMA_VERSION,
  protocol_version: PROTOCOL_VERSION,
  ...extra,
});

export function buildStatus({ jobId, state, progress, updatedAt, error }) {
  if (!JOB_STATES.includes(state)) {
    throw new ProtocolError(ERROR_INTERNAL, `Unbekannter Jobzustand: ${state}`);
  }
  const status = envelope({
    job_id: jobId,
    state,
    updated_at: formatTime(updatedAt),
  });
  if (typeof progress === "number") {
    status.progress = Math.max(0, Math.min(1, progress));
  }
  if (error) {
    status.error = {
      code: error.code || ERROR_INTERNAL,
      message: cleanText(error.message, 300),
      retryable: Boolean(error.retryable),
    };
  }
  return status;
}

/**
 * A deterministic test image. No external assets, no fonts to resolve, no
 * browser — the point is to prove that a *renderable* artefact survives the
 * channel, not to render anything difficult.
 */
export function buildSvg({ jobId, message, timestamp }) {
  const shortId = String(jobId).slice(0, 8);
  const escape = (value) =>
    String(value).replace(
      /[<>&"]/g,
      (char) => ({ "<": "&lt;", ">": "&gt;", "&": "&amp;", '"': "&quot;" })[char],
    );
  return `<svg xmlns="http://www.w3.org/2000/svg" width="640" height="360" viewBox="0 0 640 360" role="img" aria-label="Roadplanner Renderer App PoC">
  <rect width="640" height="360" fill="#101725"/>
  <rect x="24" y="24" width="592" height="312" fill="none" stroke="#3d7dca" stroke-width="2"/>
  <text x="56" y="120" fill="#f5f7fa" font-family="sans-serif" font-size="30">Roadplanner Renderer App PoC</text>
  <text x="56" y="176" fill="#9fb3c8" font-family="sans-serif" font-size="19">${escape(message)}</text>
  <text x="56" y="228" fill="#9fb3c8" font-family="monospace" font-size="17">Job ${escape(shortId)}</text>
  <text x="56" y="268" fill="#9fb3c8" font-family="monospace" font-size="17">${escape(timestamp)}</text>
</svg>
`;
}

export function buildText({ jobId, message, timestamp }) {
  return [
    "Roadplanner Renderer App PoC",
    `Job:       ${jobId}`,
    `Nachricht: ${message}`,
    `Erzeugt:   ${timestamp}`,
    `Renderer:  ${RENDERER_NAME}`,
    "",
    "Dieses Artefakt beweist ausschliesslich den Weg Auftrag -> App -> Ergebnis.",
    "Es enthaelt kein Remotion, keinen Browser und keine Reisedaten.",
    "",
  ].join("\n");
}

export function buildResult({ jobId, completedAt, artifacts }) {
  return envelope({
    job_id: jobId,
    state: "completed",
    completed_at: formatTime(completedAt),
    artifacts,
  });
}

export function buildHeartbeat({ state, startedAt, now, appVersion }) {
  if (!APP_STATES.includes(state)) {
    throw new ProtocolError(ERROR_INTERNAL, `Unbekannter App-Zustand: ${state}`);
  }
  return envelope({
    renderer: RENDERER_NAME,
    app_version: appVersion,
    state,
    started_at: formatTime(startedAt),
    heartbeat_at: formatTime(now),
    capabilities: [...ACTIONS],
  });
}
