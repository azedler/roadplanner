export const escapeHtml = (value) => String(value ?? "")
  .replaceAll("&", "&amp;")
  .replaceAll("<", "&lt;")
  .replaceAll(">", "&gt;")
  .replaceAll('"', "&quot;")
  .replaceAll("'", "&#039;");

export const cleanText = (value) => String(value ?? "").trim();

export const newClientRequestId = () => {
  try {
    if (globalThis.crypto?.randomUUID) return `assistant-${globalThis.crypto.randomUUID()}`;
  } catch (_error) {
    // Fall back to a timestamp plus random material below.
  }
  return `assistant-${Date.now()}-${Math.random().toString(36).slice(2, 12)}`;
};

export const nullableNumber = (value, integer = false) => {
  const text = cleanText(value);
  if (!text) return null;
  const parsed = Number(text);
  if (!Number.isFinite(parsed)) return null;
  return integer && !Number.isInteger(parsed) ? null : parsed;
};

export const formatFileSize = (sizeBytes) => {
  const bytes = Number(sizeBytes);
  if (!Number.isFinite(bytes) || bytes < 0) return "";
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
};

export const cloneObject = (value) => {
  if (!value || typeof value !== "object" || Array.isArray(value)) return {};
  try {
    return structuredClone(value);
  } catch (_error) {
    return JSON.parse(JSON.stringify(value));
  }
};
