// Park4Night reference handling for display purposes.
//
// Mirrors the Python regex in destination_intelligence.py: users (and the
// assistant) write Park4Night IDs in many shapes - "p4n 506374",
// "(p4n #506374)", "Park4Night: 506374" - and those IDs regularly end up
// glued to the stop name. The roadbook keeps whatever was written (name
// cleanup stays part of the reviewed enrichment flow); these helpers only
// clean what the PANEL shows and turn the reference into a proper link.

const P4N_RE = /(?:\(\s*)?(?:p4n|park\s*4\s*night)[\s#:.\-–—]*(?:id|nr\.?|platz)?[\s#:.\-–—]*(\d{3,12})(?:\s*\))?/giu;

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
