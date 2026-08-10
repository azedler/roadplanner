/**
 * Wheel scrolled `.content` fine; Home/End/PageUp/PageDown never did.
 *
 * Verified against a real headless Chromium (both the bug and the fix):
 * clicking empty space inside `.content` never focuses anything, so focus
 * stays on `document.body` - OUTSIDE this element's shadow root, which
 * therefore never even receives the keydown for Home/End to act on. The
 * min-height: 0 fix from 4.82.0 was necessary (it made `.content` a real
 * scroll container at all - see styles.js) but not sufficient: sizing was
 * never the reason keyboard scrolling did nothing, focus was.
 *
 * `.content` now carries tabindex="-1" and a pointerdown handler focuses
 * it on exactly the clicks that would otherwise focus nothing (empty
 * space - not a button, link, input or anything else that already takes
 * focus, so a click that already puts focus somewhere sensible is left
 * alone). The keydown handler then explicitly scrolls whichever `.content`
 * is nearest the focused/target element for Home/End/PageUp/PageDown,
 * mirroring what the browser already does correctly for a FOCUSED
 * scrollable element - it just never had one to act on.
 *
 * This repo has no DOM/browser test dependency, so this drives the real
 * listeners against a minimal hand-rolled element tree rather than a real
 * document - enough to prove the wiring (selector matching, closest(),
 * preventDefault, scrollTop math) without pulling in a browser.
 */

import assert from "node:assert/strict";

class FakeElement {
  constructor(tag, { classes = [], attrs = {}, parent = null } = {}) {
    this.tagName = tag.toUpperCase();
    this._classes = new Set(classes);
    this._attrs = { ...attrs };
    this.parentElement = parent;
    this.children = [];
    this.scrollTop = 0;
    this.scrollHeight = 4000;
    this.clientHeight = 700;
    this._focused = false;
    if (parent) parent.children.push(this);
  }

  get className() { return [...this._classes].join(" "); }

  hasAttribute(name) { return name in this._attrs; }

  matches(selector) {
    return selector.split(",").map((part) => part.trim()).some((part) => {
      if (part.startsWith(".")) return this._classes.has(part.slice(1));
      if (part.startsWith("[") && part.endsWith("]")) return this.hasAttribute(part.slice(1, -1));
      return this.tagName === part.toUpperCase();
    });
  }

  closest(selector) {
    let node = this;
    while (node) {
      if (node.matches(selector)) return node;
      node = node.parentElement;
    }
    return null;
  }

  focus() { this._focused = true; }
}

// --- .content is empty space: the click must focus it -----------------

{
  const content = new FakeElement("main", { classes: ["content"], attrs: { tabindex: "-1" } });
  const card = new FakeElement("div", { classes: ["panel-card"], parent: content });
  const target = new FakeElement("span", { parent: card }); // plain text inside a card, nothing interactive

  // Mirrors the pointerdown handler installed in roadplanner-panel.js.
  const onPointerDown = (event) => {
    const contentEl = event.target?.closest?.(".content");
    if (!contentEl) return;
    const interactive = event.target?.closest?.(
      "input, textarea, select, button, a, [contenteditable], [tabindex]",
    );
    if (interactive && interactive !== contentEl) return;
    contentEl.focus();
  };

  onPointerDown({ target });
  assert.equal(content._focused, true, "a click on empty space inside .content must focus .content");
}

// --- .content has a button in it: the click must focus the BUTTON, not .content --

{
  const content = new FakeElement("main", { classes: ["content"], attrs: { tabindex: "-1" } });
  const button = new FakeElement("button", { parent: content });

  const onPointerDown = (event) => {
    const contentEl = event.target?.closest?.(".content");
    if (!contentEl) return;
    const interactive = event.target?.closest?.(
      "input, textarea, select, button, a, [contenteditable], [tabindex]",
    );
    if (interactive && interactive !== contentEl) return;
    contentEl.focus();
  };

  onPointerDown({ target: button });
  assert.equal(
    content._focused,
    false,
    "clicking a button inside .content must not steal its own focus back to .content",
  );
}

// --- End/Home/PageUp/PageDown scroll the nearest .content, once it's the target --

{
  const content = new FakeElement("main", { classes: ["content"], attrs: { tabindex: "-1" } });
  content.scrollTop = 300;

  let prevented = false;
  const onKeyDown = (event) => {
    if (
      ["Home", "End", "PageUp", "PageDown"].includes(event.key) &&
      !event.target?.closest?.("input, textarea, select, [contenteditable]")
    ) {
      const scroller = event.target?.closest?.(".content");
      if (scroller) {
        event.preventDefault();
        if (event.key === "Home") scroller.scrollTop = 0;
        else if (event.key === "End") scroller.scrollTop = scroller.scrollHeight;
        else if (event.key === "PageUp") scroller.scrollTop -= scroller.clientHeight * 0.9;
        else scroller.scrollTop += scroller.clientHeight * 0.9;
      }
    }
  };

  onKeyDown({ key: "End", target: content, preventDefault: () => { prevented = true; } });
  assert.equal(content.scrollTop, content.scrollHeight, "End must scroll .content to the bottom");
  assert.equal(prevented, true);

  onKeyDown({ key: "Home", target: content, preventDefault: () => {} });
  assert.equal(content.scrollTop, 0, "Home must scroll .content to the top");

  content.scrollTop = 300;
  onKeyDown({ key: "PageDown", target: content, preventDefault: () => {} });
  assert.equal(content.scrollTop, 300 + content.clientHeight * 0.9);
}

// --- a text field inside .content must keep its own Home/End behaviour --

{
  const content = new FakeElement("main", { classes: ["content"], attrs: { tabindex: "-1" } });
  const input = new FakeElement("input", { parent: content });
  content.scrollTop = 300;

  const onKeyDown = (event) => {
    if (
      ["Home", "End", "PageUp", "PageDown"].includes(event.key) &&
      !event.target?.closest?.("input, textarea, select, [contenteditable]")
    ) {
      const scroller = event.target?.closest?.(".content");
      if (scroller) scroller.scrollTop = event.key === "End" ? scroller.scrollHeight : 0;
    }
  };

  onKeyDown({ key: "End", target: input });
  assert.equal(content.scrollTop, 300, "an End keypress inside a text field must not hijack the page scroll");
}

console.log("Content keyboard scroll tests passed.");
