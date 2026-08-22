/**
 * Turning the archive's per-category totals into one readable cost split.
 *
 * Everything here is pure: the same payload always yields the same rows,
 * the same segment order and the same colours. No Date, no Math.random -
 * a chart that redraws differently on every render is unreadable and
 * untestable.
 *
 * Two facts shape the design:
 *
 *  - The archive sums expenses PER CURRENCY and never rewrites an
 *    original amount. A single circle needs a single scale, so a trip
 *    with more than one currency is converted to EUR with the ECB rates
 *    the panel already fetches - and the result is labelled approximate.
 *    A currency without a rate is reported, never silently dropped.
 *  - Colours are assigned by segment position, and the segments are
 *    ordered largest first. In a ring only NEIGHBOURS touch, and the
 *    palette's colour-blind separation is validated exactly for
 *    neighbouring slots in this order, so the ring is safe to read.
 *    Every segment additionally carries its label and value in the
 *    legend, which is what keeps the two lighter hues legible.
 */

/**
 * The categorical slots, in their fixed order. These are the palette's
 * dark-surface steps, which clear the lightness, chroma and colour-blind
 * gates on a light surface as well - so one palette serves both Home
 * Assistant themes, and the chart does not have to guess which one is
 * active (inside a shadow root it cannot reliably tell).
 */
export const COST_SERIES_COLORS = [
  "#3987e5",
  "#d95926",
  "#199e70",
  "#c98500",
  "#d55181",
  "#008300",
  "#9085e9",
  "#e66767",
];

/** The folded remainder never competes with a real category. */
export const COST_REST_COLOR = "#8a8a86";

export const COST_REST_KEY = "__rest__";

/**
 * How many slices the ring carries, the folded remainder included.
 *
 * Eight is the palette's own ceiling and it is what a road trip needs:
 * fuel, campsite, restaurant, ferry, groceries, toll, snack are all
 * categories worth seeing by name. Folding at six pushed the toll - a
 * category the request named explicitly - into an unnamed grey wedge.
 * Everything past the ceiling folds into one remainder that the legend
 * then breaks down by name.
 */
export const MAX_COST_SEGMENTS = 8;

/** Below this share a slice is thinner than its own gap. */
export const MIN_COST_SEGMENT_SHARE = 0.02;

const round2 = (value) => Math.round(value * 100) / 100;

/**
 * Convert one category's per-currency amounts into a single number.
 *
 * Returns the amount plus the currencies that could not be converted, so
 * the caller can say WHICH currency is missing instead of showing a
 * total that is quietly too small.
 */
export function convertAmounts(amounts, { currency = "EUR", rates = null } = {}) {
  let total = 0;
  const missing = [];
  for (const [rawCode, rawAmount] of Object.entries(amounts || {})) {
    const code = String(rawCode || "").toUpperCase();
    const amount = Number(rawAmount);
    if (!Number.isFinite(amount)) continue;
    if (code === currency) {
      total += amount;
      continue;
    }
    const rateTo = currency === "EUR" ? 1 : Number(rates?.[currency]);
    const rateFrom = Number(rates?.[code]);
    if (Number.isFinite(rateFrom) && rateFrom > 0 && Number.isFinite(rateTo) && rateTo > 0) {
      total += (amount / rateFrom) * rateTo;
    } else if (!missing.includes(code)) {
      missing.push(code);
    }
  }
  return { total: round2(total), missing: missing.sort() };
}

/** Every currency that appears anywhere in the category totals. */
export function currenciesUsed(categoryTotals) {
  const codes = new Set();
  for (const amounts of Object.values(categoryTotals || {})) {
    for (const code of Object.keys(amounts || {})) {
      const clean = String(code || "").toUpperCase();
      if (clean) codes.add(clean);
    }
  }
  return [...codes].sort();
}

/**
 * Build the full cost split.
 *
 * `ready` is false with a `reason` whenever no honest chart can be drawn -
 * no expenses at all, or several currencies without the rates to bring
 * them onto one scale. A reason is not an error message; the caller
 * decides what to show for each one.
 */
export function buildCostBreakdown({
  categoryTotals = {},
  labels = {},
  rates = null,
  fallbackLabel = "Sonstiges",
  restLabel = "Weitere",
} = {}) {
  const currencies = currenciesUsed(categoryTotals);
  if (!currencies.length) {
    return { ready: false, reason: "no_expenses", rows: [], segments: [], total: 0, currency: "EUR", approximate: false, missing: [] };
  }
  const currency = currencies.length === 1 ? currencies[0] : "EUR";
  const approximate = currencies.length > 1;
  if (currencies.length > 1 && !rates) {
    return { ready: false, reason: "rates_missing", rows: [], segments: [], total: 0, currency, approximate: true, missing: [] };
  }

  const rows = [];
  const missing = [];
  for (const [key, amounts] of Object.entries(categoryTotals)) {
    const converted = convertAmounts(amounts, { currency, rates });
    for (const code of converted.missing) if (!missing.includes(code)) missing.push(code);
    if (!(converted.total > 0)) continue;
    rows.push({
      key,
      label: labels[key] || key || fallbackLabel,
      value: converted.total,
      amounts: { ...amounts },
    });
  }
  // Largest first; the key breaks ties so the order never depends on
  // object insertion order, which differs between payloads.
  rows.sort((a, b) => b.value - a.value || a.key.localeCompare(b.key));
  const total = round2(rows.reduce((sum, row) => sum + row.value, 0));
  if (!(total > 0)) {
    return { ready: false, reason: "no_expenses", rows: [], segments: [], total: 0, currency, approximate, missing: missing.sort() };
  }
  for (const row of rows) row.share = row.value / total;

  let head = rows.filter((row) => row.share >= MIN_COST_SEGMENT_SHARE);
  // Something has to be folded - so the last slot belongs to the fold,
  // not to one more category.
  if (head.length < rows.length || head.length > MAX_COST_SEGMENTS) {
    head = head.slice(0, MAX_COST_SEGMENTS - 1);
  }
  const tail = rows.filter((row) => !head.includes(row));

  const segments = head.map((row, index) => ({
    key: row.key,
    label: row.label,
    value: row.value,
    share: row.share,
    color: COST_SERIES_COLORS[index % COST_SERIES_COLORS.length],
  }));
  if (tail.length) {
    const value = round2(tail.reduce((sum, row) => sum + row.value, 0));
    if (value > 0) {
      segments.push({
        key: COST_REST_KEY,
        label: tail.length === 1 ? tail[0].label : `${restLabel} (${tail.length})`,
        value,
        share: value / total,
        color: COST_REST_COLOR,
        folded: tail.map((row) => row.key),
      });
    }
  }
  const colorByKey = new Map(segments.map((segment) => [segment.key, segment.color]));
  for (const row of rows) row.color = colorByKey.get(row.key) || COST_REST_COLOR;

  return { ready: true, reason: "", rows, segments, total, currency, approximate, missing: missing.sort() };
}

/** The dash geometry of a donut whose circumference is exactly 100. */
export const DONUT_CIRCUMFERENCE = 100;

/**
 * Lay the segments out on that circle, starting at twelve o'clock.
 *
 * The gap between two slices is drawn as absence, not as a border: each
 * arc gives up `gap` units at its end. A slice never shrinks below
 * `minLength`, so a one-percent category stays visible instead of
 * disappearing into its own separator.
 */
export function donutDashes(segments, { gap = 0.7, minLength = 0.6 } = {}) {
  const list = (segments || []).filter((segment) => Number(segment?.share) > 0);
  const usableGap = list.length > 1 ? gap : 0;
  let offset = 0;
  return list.map((segment) => {
    const raw = Number(segment.share) * DONUT_CIRCUMFERENCE;
    const length = Math.max(minLength, raw - usableGap);
    const dash = {
      ...segment,
      length: Math.round(length * 1000) / 1000,
      // dashoffset counts backwards, and 25 units put the start at the
      // top of the circle instead of at three o'clock.
      dashOffset:
        Math.round((((DONUT_CIRCUMFERENCE - offset) % DONUT_CIRCUMFERENCE) + 25) * 1000) / 1000,
    };
    offset += raw;
    return dash;
  });
}
