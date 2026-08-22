/**
 * The cost split at the end of a trip - "wieviel für tanken, Essen,
 * Restaurant/Imbiss, Maut, Fähre" - as one ring plus a legend.
 *
 * The payload shapes here are the REAL ones: `stats.category_totals` is
 * category -> currency -> amount exactly as `travel_archive.panel_payload`
 * writes it, and the rates dict is what the `get_exchange_rates` action
 * returns. A test that invents its own shape covers the bug instead of
 * catching it.
 */
import assert from "node:assert/strict";

import {
  buildCostBreakdown,
  donutDashes,
  COST_SERIES_COLORS,
  COST_REST_COLOR,
  MAX_COST_SEGMENTS,
} from "../custom_components/roadplanner_mcp/frontend/lib/cost-breakdown.js";
import { archiveExpenseCategoryLabels } from "../custom_components/roadplanner_mcp/frontend/lib/constants.js";

class FakeShadowRoot { addEventListener() {} querySelector() { return null; } }
class FakeHTMLElement { attachShadow() { this.shadowRoot = new FakeShadowRoot(); return this.shadowRoot; } }
const registry = new Map();
globalThis.HTMLElement = FakeHTMLElement;
globalThis.window = { location: { origin: "https://ha.example" }, setTimeout, clearTimeout };
globalThis.document = {
  createElement() { return { setAttribute() {}, style: {}, remove() {} }; },
  body: { appendChild() {} },
};
globalThis.customElements = {
  define(name, constructor) { registry.set(name, constructor); },
  get(name) { return registry.get(name); },
};

await import(new URL("../custom_components/roadplanner_mcp/frontend/roadplanner-panel.js", import.meta.url));
const Panel = registry.get("roadplanner-panel");

const labels = archiveExpenseCategoryLabels;

function panelWith(categoryTotals, { trip = "trip-a", rates = null, ratesTrip = "trip-a", frozen = false } = {}) {
  const panel = new Panel();
  panel._render = () => {};
  panel._selectedTripId = trip;
  panel._data = {
    capabilities: { can_edit: false },
    selected_is_active: true,
    travel_archive: {
      documents: [], expenses: [], todos: [], by_day: {}, by_stop: {},
      stats: { category_totals: categoryTotals, totals_by_currency: {} },
    },
  };
  panel._runAction = async () => { throw new Error("no action expected"); };
  panel._exchangeRates = rates;
  panel._exchangeRatesTripId = ratesTrip;
  panel._exchangeRatesFrozen = frozen;
  return panel;
}

function verify_one_currency_needs_no_conversion() {
  const breakdown = buildCostBreakdown({
    categoryTotals: { fuel: { EUR: 400 }, restaurant: { EUR: 300 }, toll: { EUR: 300 } },
    labels,
  });
  assert.equal(breakdown.ready, true);
  assert.equal(breakdown.currency, "EUR");
  assert.equal(breakdown.approximate, false, "one currency is exact, never 'approximate'");
  assert.equal(breakdown.total, 1000);
  // Equal amounts are ordered by their category key, so the same book
  // always draws the same ring.
  assert.deepEqual(breakdown.rows.map((row) => row.label), ["Tanken", "Restaurant", "Maut"]);
  assert.equal(breakdown.rows[0].share, 0.4);
  const shares = breakdown.segments.reduce((sum, segment) => sum + segment.share, 0);
  assert.ok(Math.abs(shares - 1) < 1e-9, "the ring covers the whole circle");
}

function verify_several_currencies_are_converted_and_labelled() {
  const breakdown = buildCostBreakdown({
    categoryTotals: { fuel: { EUR: 100, SEK: 1130 }, ferry: { EUR: 50 } },
    labels,
    rates: { SEK: 11.3, PLN: 4.25 },
  });
  assert.equal(breakdown.ready, true);
  assert.equal(breakdown.currency, "EUR");
  assert.equal(breakdown.approximate, true, "a converted total must say so");
  assert.equal(breakdown.rows[0].value, 200, "1130 SEK at 11.3 is 100 EUR on top of the 100 EUR");
  assert.deepEqual(breakdown.missing, []);
}

function verify_a_currency_without_a_rate_is_named_not_dropped() {
  const breakdown = buildCostBreakdown({
    categoryTotals: { fuel: { EUR: 100, NOK: 500 } },
    labels,
    rates: { SEK: 11.3 },
  });
  assert.deepEqual(breakdown.missing, ["NOK"], "the missing currency is reported by name");
  assert.equal(breakdown.rows[0].value, 100, "an unconvertible amount is not guessed at");
}

function verify_no_rates_means_no_chart_rather_than_a_wrong_one() {
  const breakdown = buildCostBreakdown({
    categoryTotals: { fuel: { EUR: 100 }, ferry: { SEK: 1000 } },
    labels,
    rates: null,
  });
  assert.equal(breakdown.ready, false);
  assert.equal(breakdown.reason, "rates_missing", "the reason is named, not left blank");
  assert.deepEqual(breakdown.segments, []);
}

function verify_an_empty_book_draws_nothing() {
  const breakdown = buildCostBreakdown({ categoryTotals: {}, labels });
  assert.equal(breakdown.ready, false);
  assert.equal(breakdown.reason, "no_expenses");
}

function verify_cancelled_zero_categories_never_become_a_slice() {
  const breakdown = buildCostBreakdown({
    categoryTotals: { fuel: { EUR: 100 }, parking: { EUR: 0 } },
    labels,
  });
  assert.deepEqual(breakdown.rows.map((row) => row.key), ["fuel"], "a zero category is not a slice");
}

function verify_the_tail_folds_into_one_remainder() {
  const categoryTotals = {
    fuel: { EUR: 500 }, restaurant: { EUR: 300 }, campsite: { EUR: 200 },
    ferry: { EUR: 150 }, toll: { EUR: 100 }, snack: { EUR: 80 },
    groceries: { EUR: 60 }, parking: { EUR: 40 }, charging: { EUR: 30 },
  };
  const breakdown = buildCostBreakdown({ categoryTotals, labels });
  assert.ok(breakdown.segments.length <= MAX_COST_SEGMENTS, "a ring stays readable");
  const rest = breakdown.segments[breakdown.segments.length - 1];
  assert.ok(rest.label.startsWith("Weitere ("), `expected a folded remainder, got ${rest.label}`);
  assert.equal(rest.color, COST_REST_COLOR);
  assert.equal(breakdown.rows.length, 9, "the legend still lists every single category");
  const segmentSum = breakdown.segments.reduce((sum, segment) => sum + segment.value, 0);
  assert.ok(Math.abs(segmentSum - breakdown.total) < 0.01, "the fold loses no money");
}

function verify_a_tiny_category_is_folded_rather_than_drawn_invisible() {
  const breakdown = buildCostBreakdown({
    categoryTotals: { fuel: { EUR: 1000 }, parking: { EUR: 1 } },
    labels,
  });
  assert.equal(breakdown.segments.length, 2);
  assert.equal(breakdown.segments[1].label, "Parken", "a single folded category keeps its own name");
  assert.equal(breakdown.segments[1].color, COST_REST_COLOR);
}

function verify_the_order_does_not_depend_on_the_payloads_key_order() {
  const first = buildCostBreakdown({ categoryTotals: { fuel: { EUR: 100 }, ferry: { EUR: 100 } }, labels });
  const second = buildCostBreakdown({ categoryTotals: { ferry: { EUR: 100 }, fuel: { EUR: 100 } }, labels });
  assert.deepEqual(
    first.segments.map((segment) => segment.key),
    second.segments.map((segment) => segment.key),
    "equal amounts must not draw differently depending on insertion order",
  );
}

function verify_colours_come_from_the_validated_palette_in_order() {
  const breakdown = buildCostBreakdown({
    categoryTotals: { fuel: { EUR: 400 }, restaurant: { EUR: 300 }, toll: { EUR: 200 } },
    labels,
  });
  assert.deepEqual(
    breakdown.segments.map((segment) => segment.color),
    COST_SERIES_COLORS.slice(0, 3),
    "neighbouring slices must be the palette's validated neighbours",
  );
}

function verify_the_ring_starts_at_the_top_and_leaves_gaps() {
  const breakdown = buildCostBreakdown({
    categoryTotals: { fuel: { EUR: 500 }, ferry: { EUR: 500 } },
    labels,
  });
  const dashes = donutDashes(breakdown.segments);
  assert.equal(dashes[0].dashOffset, 25, "the first slice begins at twelve o'clock");
  assert.ok(dashes[0].length < 50, "every slice gives up a sliver so the slices do not touch");
  assert.ok(dashes[0].length > 48, "the gap is a sliver, not a wedge");
}

function verify_a_single_slice_has_no_gap_to_leave() {
  const dashes = donutDashes([{ key: "fuel", share: 1 }]);
  assert.equal(dashes[0].length, 100, "one category fills the whole ring");
}

function verify_the_panel_draws_the_ring_and_names_every_number() {
  const panel = panelWith({
    fuel: { EUR: 400 }, restaurant: { EUR: 300 }, toll: { EUR: 200 }, ferry: { EUR: 100 },
  });
  const html = panel._renderArchiveCostSplit();
  const arcs = html.match(/class="cost-donut-arc"/g) || [];
  assert.equal(arcs.length, 4, "one arc per category");
  for (const label of ["Tanken", "Restaurant", "Maut", "Fähre"]) {
    assert.ok(html.includes(label), `the legend must name ${label}`);
  }
  assert.ok(html.includes("40 %"), "the legend carries the share");
  assert.ok(/aria-label="Kostenverteilung: [^"]+"/.test(html), "the ring is readable without seeing it");
  assert.ok(!html.includes("≈ gesamt"), "a single-currency total is exact, not approximate");
}

function verify_the_grey_wedge_is_named_in_the_legend() {
  const panel = panelWith({
    fuel: { EUR: 500 }, campsite: { EUR: 400 }, restaurant: { EUR: 300 },
    ferry: { EUR: 250 }, groceries: { EUR: 200 }, toll: { EUR: 150 },
    snack: { EUR: 100 }, parking: { EUR: 30 }, charging: { EUR: 12 },
  });
  const html = panel._renderArchiveCostSplit();
  assert.ok(html.includes("Weitere (2)"), "the grey wedge says what it stands for");
  assert.ok(html.includes("Maut"), "a category the trip actually paid keeps its own colour and name");
  const folded = html.match(/cost-legend-folded/g) || [];
  assert.equal(folded.length, 2, "both folded categories stay readable inside the remainder");
}

function verify_the_panel_says_nothing_when_there_is_nothing_to_say() {
  assert.equal(panelWith({})._renderArchiveCostSplit(), "", "no expenses, no chart, no empty box");
}

function verify_rates_of_another_trip_are_never_reused() {
  const panel = panelWith({ fuel: { EUR: 100 }, ferry: { SEK: 1000 } }, {
    trip: "trip-b",
    rates: { date: "2026-08-20", rates: { SEK: 11.3 } },
    ratesTrip: "trip-a",
  });
  assert.equal(panel._archiveRates(), null, "a frozen rate belongs to the trip it was frozen for");
  let requested = null;
  panel._runAction = async (action, payload) => {
    requested = { action, payload };
    return null;
  };
  const html = panel._renderArchiveCostSplit();
  assert.ok(html.includes("wird vorbereitet"), "the panel waits instead of converting at foreign rates");
  assert.equal(requested?.action, "get_exchange_rates");
  assert.equal(requested?.payload?.trip_id, "trip-b", "the rate request names its own trip");
}

function verify_a_frozen_rate_is_labelled_as_frozen() {
  const panel = panelWith({ fuel: { EUR: 100 }, ferry: { SEK: 1130 } }, {
    rates: { date: "2026-08-20", rates: { SEK: 11.3 } },
    frozen: true,
  });
  const html = panel._renderArchiveCostSplit();
  assert.ok(html.includes("≈ gesamt"), "a converted total is marked approximate");
  assert.ok(
    html.includes("EZB-Kurse vom 20.08.2026, für diese Reise eingefroren"),
    `expected the frozen rate date, got: ${html}`,
  );
}

function verify_the_todo_section_stands_above_the_costs() {
  const panel = panelWith({ fuel: { EUR: 100 } });
  panel._data.travel_archive.todos = [];
  const html = panel._renderArchive();
  // The toolbar headline names all three record types, so anchor on the
  // section heading itself rather than on the word.
  const todoAt = html.indexOf("<h2>Tagesaufgaben</h2>");
  const costAt = html.indexOf("Reisekosten");
  assert.ok(todoAt > -1 && costAt > -1, "both sections are rendered");
  assert.ok(todoAt < costAt, "what is still to do comes before what was already spent");
  assert.equal(
    html.split("<h2>Tagesaufgaben</h2>").length - 1,
    1,
    "the section moved, it was not duplicated",
  );
}

const checks = Object.entries({
  verify_one_currency_needs_no_conversion,
  verify_several_currencies_are_converted_and_labelled,
  verify_a_currency_without_a_rate_is_named_not_dropped,
  verify_no_rates_means_no_chart_rather_than_a_wrong_one,
  verify_an_empty_book_draws_nothing,
  verify_cancelled_zero_categories_never_become_a_slice,
  verify_the_tail_folds_into_one_remainder,
  verify_a_tiny_category_is_folded_rather_than_drawn_invisible,
  verify_the_order_does_not_depend_on_the_payloads_key_order,
  verify_colours_come_from_the_validated_palette_in_order,
  verify_the_ring_starts_at_the_top_and_leaves_gaps,
  verify_a_single_slice_has_no_gap_to_leave,
  verify_the_panel_draws_the_ring_and_names_every_number,
  verify_the_grey_wedge_is_named_in_the_legend,
  verify_the_panel_says_nothing_when_there_is_nothing_to_say,
  verify_rates_of_another_trip_are_never_reused,
  verify_a_frozen_rate_is_labelled_as_frozen,
  verify_the_todo_section_stands_above_the_costs,
});

for (const [name, check] of checks) {
  await check();
  console.log(`ok - ${name}`);
}
console.log(`\n${checks.length} checks passed`);
