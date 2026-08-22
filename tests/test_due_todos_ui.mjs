/**
 * What is due stands at the top - on the dashboard and in the archive.
 *
 * The tasks were the last section of the archive tab, below documents and
 * the expense book, and the dashboard mentioned them only as a number in
 * a stat tile. A task nobody scrolls to is a task nobody does.
 *
 * The dashboard card is deliberately conditional: it appears only while
 * something is overdue, due today, or due within the day. A card that is
 * always there stops being read.
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

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

const DAY = 86400000;
const at = (offsetMs) => new Date(Date.now() + offsetMs).toISOString();

function panelWithTodos(todos) {
  const panel = new Panel();
  panel._render = () => {};
  panel._selectedTripId = "trip-a";
  panel._data = {
    capabilities: { can_edit: true },
    selected_is_active: true,
    travel_archive: {
      documents: [], expenses: [], todos, by_day: {}, by_stop: {},
      stats: { category_totals: {}, totals_by_currency: {} },
    },
  };
  panel._runAction = async () => null;
  return panel;
}

const todo = (id, title, dueAt, status = "open") => ({
  id, title, status, due_at: dueAt, priority: "normal", notes: "", day_id: "", stop_id: "",
});

function verify_nothing_due_means_no_card() {
  const panel = panelWithTodos([
    todo("todo-1", "Fähre buchen", at(30 * DAY)),
    todo("todo-2", "Schon erledigt", at(-2 * DAY), "done"),
  ]);
  assert.equal(panel._renderDueTodoCard(), "", "a card that is always there stops being read");
}

function verify_the_card_appears_for_what_is_actually_due() {
  const panel = panelWithTodos([
    todo("todo-1", "Vignette kaufen", at(-2 * DAY)),
    todo("todo-2", "Fähre buchen", at(30 * DAY)),
  ]);
  const html = panel._renderDueTodoCard();
  assert.ok(html.includes("Vignette kaufen"), "the overdue task is on the card");
  assert.ok(!html.includes("Fähre buchen"), "next month is not 'jetzt dran'");
  assert.ok(html.includes("1 überfällig"), `expected the count in the headline, got: ${html}`);
}

function verify_overdue_comes_before_today() {
  const panel = panelWithTodos([
    todo("todo-today", "Heute", at(2 * 3600 * 1000)),
    todo("todo-late", "Längst fällig", at(-5 * DAY)),
  ]);
  const html = panel._renderDueTodoCard();
  assert.ok(html.indexOf("Längst fällig") < html.indexOf("Heute"), "the most pressing task is first");
  assert.ok(html.includes("1 überfällig, 1 heute"), `expected both counts, got: ${html}`);
}

function verify_a_long_list_is_cut_and_says_so() {
  const panel = panelWithTodos(
    Array.from({ length: 7 }, (_, index) => todo(`todo-${index}`, `Aufgabe ${index}`, at(-(index + 1) * DAY))),
  );
  const html = panel._renderDueTodoCard();
  const rows = html.match(/archive-todo-row/g) || [];
  assert.equal(rows.length, 4, "the dashboard shows a handful, not a backlog");
  assert.ok(html.includes("und 3 weitere fällige Aufgaben"), `the rest is counted, not hidden: ${html}`);
}

function verify_one_hidden_task_is_named_in_the_singular() {
  const panel = panelWithTodos(
    Array.from({ length: 5 }, (_, index) => todo(`todo-${index}`, `Aufgabe ${index}`, at(-(index + 1) * DAY))),
  );
  assert.ok(panel._renderDueTodoCard().includes("und 1 weitere fällige Aufgabe"));
}

function verify_the_order_is_stable_for_identical_deadlines() {
  const due = at(-DAY);
  const first = panelWithTodos([todo("todo-b", "B", due), todo("todo-a", "A", due)])._renderDueTodoCard();
  const second = panelWithTodos([todo("todo-a", "A", due), todo("todo-b", "B", due)])._renderDueTodoCard();
  assert.equal(first, second, "the same tasks must not shuffle between renders");
}

function verify_the_card_stands_above_the_planning_numbers() {
  // The overview needs a whole trip payload to render, so this pins the
  // ORDER at its source: the due card is emitted before the stat grid.
  const source = readFileSync(
    new URL("../custom_components/roadplanner_mcp/frontend/features/trip-day-stop.js", import.meta.url),
    "utf-8",
  );
  const overview = source.slice(source.indexOf("_renderOverview()"));
  const cardAt = overview.indexOf("_renderDueTodoCard()");
  const statsAt = overview.indexOf('class="stat-grid planning-stats"');
  assert.ok(cardAt > -1, "the dashboard renders the due card at all");
  assert.ok(statsAt > -1 && cardAt < statsAt, "what is due comes before the planning numbers");
}

const checks = Object.entries({
  verify_nothing_due_means_no_card,
  verify_the_card_appears_for_what_is_actually_due,
  verify_overdue_comes_before_today,
  verify_a_long_list_is_cut_and_says_so,
  verify_one_hidden_task_is_named_in_the_singular,
  verify_the_order_is_stable_for_identical_deadlines,
  verify_the_card_stands_above_the_planning_numbers,
});

for (const [name, check] of checks) {
  await check();
  console.log(`ok - ${name}`);
}
console.log(`\n${checks.length} checks passed`);
