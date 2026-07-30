import assert from "node:assert/strict";

// Regression tests for three confirmed panel audit findings.
//
// 1. _runAction started with `if (this._busy) return null;` while the busy
//    state only changed the cursor - the UI stayed clickable, so a stop
//    save/move/delete issued while another action was running (multi-second
//    route calculation, quick double-click on the reorder arrows) was
//    silently discarded: no request, no toast, dialog already closed.
//    Actions are now serialized in a queue instead of dropped; blockUi:false
//    actions (video export) bypass the queue so they never stall it.
// 2. _runAction's finally block flushed a queued background refresh even
//    while a dialog was open. _loadData(force) re-renders the whole shadow
//    root, rebuilding the form from stale data: typed input erased, and the
//    Park4Night prefill then wrote into a detached form while the toast
//    claimed success. The flush now waits until the dialog closes.
// 3. The stop form closed BEFORE the save request ran, so any server-side
//    rejection (lone latitude, stale revision) discarded everything the
//    user typed. The dialog now closes only after a successful save.

class FakeShadowRoot {
  addEventListener() {}
  querySelector() { return null; }
}

class FakeHTMLElement {
  attachShadow() {
    this.shadowRoot = new FakeShadowRoot();
    return this.shadowRoot;
  }
}

const registry = new Map();
globalThis.HTMLElement = FakeHTMLElement;
globalThis.window = {
  location: { origin: "https://ha.example" },
  setTimeout,
  clearTimeout,
};
globalThis.document = {
  createElement() {
    return { setAttribute() {}, style: {}, select() {}, remove() {} };
  },
  body: { appendChild() {} },
  execCommand() { return true; },
};
Object.defineProperty(globalThis, "navigator", {
  value: { clipboard: { async writeText() {} } },
  configurable: true,
});
globalThis.customElements = {
  define(name, constructor) { registry.set(name, constructor); },
  get(name) { return registry.get(name); },
};

await import(
  new URL("../custom_components/roadplanner_mcp/frontend/roadplanner-panel.js", import.meta.url)
);

const Panel = registry.get("roadplanner-panel");

const makePanel = () => {
  const panel = new Panel();
  panel._render = () => {};
  panel._loadDataCalls = 0;
  panel._loadData = async () => { panel._loadDataCalls += 1; };
  panel._showToast = () => {};
  return panel;
};

const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

// --- 1a. concurrent actions are queued, not dropped ---------------------

{
  const panel = makePanel();
  const sent = [];
  panel._send = async (message) => {
    sent.push(message.action);
    await wait(30);
    return { ok: true, action: message.action };
  };
  const first = panel._runAction("calculate_day_route", {}, "", { refresh: false });
  const second = panel._runAction("update_stop", {}, "", { refresh: false });
  const [, secondResult] = await Promise.all([first, second]);
  assert.deepEqual(sent, ["calculate_day_route", "update_stop"],
    "the second action must be sent (in order), not silently discarded");
  assert.ok(secondResult, "the queued action's result must reach its caller");
}

// --- 1b. blockUi:false bypasses the queue -------------------------------

{
  const panel = makePanel();
  let slowDone = false;
  panel._send = async (message) => {
    if (message.action === "slow") { await wait(60); slowDone = true; return {}; }
    return { fast: true };
  };
  const slow = panel._runAction("slow", {}, "", { refresh: false });
  const fast = await panel._runAction("export_trip_video", {}, "", { refresh: false, blockUi: false });
  assert.ok(fast && !slowDone,
    "a blockUi:false action must complete without waiting for the queue");
  await slow;
}

// --- 2. queued refresh never flushes while a dialog is open -------------

{
  const panel = makePanel();
  panel._send = async () => ({ ok: true });
  panel._dialog = { type: "form", form: "stop" };
  panel._refreshQueued = true;
  await panel._runAction("park4night_lookup", {}, "", { refresh: false });
  assert.equal(panel._loadDataCalls, 0,
    "no forced reload may run while the form is open - it would rebuild the "
    + "form from stale data and erase typed input");
  assert.equal(panel._refreshQueued, true, "the refresh stays queued");
  panel._busy = false;
  panel._closeDialog();
  await wait(0);
  assert.equal(panel._loadDataCalls, 1, "closing the dialog flushes the queued refresh");
}

// --- 2b. a revision error with an open dialog queues instead of reloading

{
  const panel = makePanel();
  panel._send = async () => {
    const error = new Error("Revision veraltet");
    error.code = "revision_conflict";
    throw error;
  };
  panel._dialog = { type: "form", form: "stop" };
  const result = await panel._runAction("update_stop", {}, "", { refresh: false });
  assert.equal(result, null);
  assert.equal(panel._loadDataCalls, 0,
    "a revision error must not reload (and wipe) the open form");
  assert.equal(panel._refreshQueued, true);
}

// --- 3. stop form closes only after a successful save (source contract) --

{
  const { readFileSync } = await import("node:fs");
  const source = readFileSync(
    new URL("../custom_components/roadplanner_mcp/frontend/roadplanner-panel.js", import.meta.url),
    "utf-8",
  );
  const anchor = source.indexOf("this._expandedDays.add(common.day_id)");
  assert.ok(anchor > 0, "stop submit branch found");
  const stopBranch = source.slice(anchor);
  const beforeSave = source.slice(source.indexOf('stop_type: cleanText(values.stop_type) || "waypoint"'), anchor);
  assert.ok(!beforeSave.includes("_closeDialog"),
    "the stop form must not close before the save request runs");
  assert.ok(stopBranch.includes("if (result) this._closeDialog()"),
    "the stop form closes only when the server accepted the save");
}

console.log("Panel action race regression tests passed.");
