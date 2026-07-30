// Park4Night reference handling for display purposes.
//
// Mirrors the Python regex in destination_intelligence.py: users (and the
// assistant) write Park4Night IDs in many shapes - "p4n 506374",
// "(p4n #506374)", "Park4Night: 506374" - and those IDs regularly end up
// glued to the stop name. The roadbook keeps whatever was written (name
// cleanup stays part of the reviewed enrichment flow); these helpers only
// clean what the PANEL shows and turn the reference into a proper link.

// The optional ".com/<path>/" branch also matches real page links like
// https://park4night.com/lieu/506374/ or /de/place/506374 - the path class
// deliberately contains no digits, so the place ID is never split.
const P4N_RE = /(?:\(\s*)?(?:p4n|park\s*4\s*night)(?:\.com\/[a-z\/_\-]*)?[\s#:.\-–—]*(?:id|nr\.?|platz)?[\s#:.\-–—]*(\d{3,12})(?:\s*\))?/giu;

export const park4nightReference = (...texts) => {
  for (const text of texts) {
    if (typeof text !== "string" || !text) continue;
    P4N_RE.lastIndex = 0;
    const match = P4N_RE.exec(text);
    if (match) {
      const id = match[1];
      return { id, url: `https://park4night.com/lieu/${id}/` };
    }
  }
  return null;
};

// Guarantee a Park4Night reference survives in the notes. The stop form's
// lookup overwrites a name that carries the reference with the clean place
// name - without this, the very first successful lookup DESTROYED the only
// copy of the reference and every later lookup failed with "Kein
// Park4Night-Verweis gefunden".
export const withPark4nightReference = (notes, reference) => {
  const text = typeof notes === "string" ? notes : "";
  if (!reference || !reference.id || text.includes(reference.id)) return text;
  const line = `Park4Night: ${reference.url}`;
  const trimmed = text.replace(/\s+$/u, "");
  return trimmed ? `${trimmed}\n${line}` : line;
};

export const cleanPlaceName = (name) => {
  if (typeof name !== "string" || !name) return name || "";
  P4N_RE.lastIndex = 0;
  const cleaned = name
    .replace(P4N_RE, " ")
    .replace(/\s+/g, " ")
    .replace(/^[\s\-–—,;:/]+|[\s\-–—,;:/]+$/g, "")
    .trim();
  // A name that IS only the reference keeps its original text - an empty
  // heading would be worse than a technical one.
  return cleaned || name;
};
