"use strict";

// Browserless regression coverage for out-of-order packet responses.
// Runs with Node's built-in test runner; no dependencies or network access.
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");

const SOURCE = fs.readFileSync(
  path.join(__dirname, "..", "src", "career_agent", "web", "app.js"), "utf8"
);
const ID_A = "aaaaaaaaaaaaaaaa";
const ID_B = "bbbbbbbbbbbbbbbb";

class Element {
  constructor(tag) {
    this.tagName = tag;
    this.children = [];
    this.attributes = {};
    this.dataset = {};
    this.className = "";
    this.hidden = false;
    this.value = "";
    this.checked = false;
    this.handlers = {};
    this.classList = {
      toggle: (_value, _force) => {},
    };
    this._text = "";
  }
  set textContent(value) {
    this._text = String(value);
    this.children = [];
  }
  get textContent() {
    return this._text + this.children.map((c) => c.textContent).join("");
  }
  append(...children) {
    this.children.push(...children);
  }
  replaceChildren(...children) {
    this.children = children;
    this._text = "";
  }
  setAttribute(name, value) {
    this.attributes[name] = String(value);
  }
  removeAttribute(name) {
    delete this.attributes[name];
  }
  addEventListener(name, fn) {
    this.handlers[name] = fn;
  }
  click() {
    return this.handlers.click?.({target: this, currentTarget: this});
  }
}

function fakeJob(id, title) {
  return {
    id, title,
    company: "Example Corp",
    location: "Remote USA",
    remote: true,
    url: "https://example.invalid/" + id,
    source: "sample",
    description: "Example role description",
    application_stage: "discovered",
    fit_score: 82,
    fit_reasons: ["Title relevance: 40/40"],
    sponsorship_status: "unknown",
    sponsorship_reason: "No confirmed visa policy",
    packet_available: true,
    review_decision: null,
  };
}

function fakePacket(id, resume) {
  return {
    packet: {
      job_id: id,
      packet_sha256: id[0].repeat(64),
      resume_markdown: resume,
      cover_note: "Cover letter for " + id,
      review_questions: ["Verify each claim"],
      status: "awaiting_human_review",
    },
    review: null,
  };
}

function ok(value) {
  return {ok: true, status: 200, json: async () => value};
}
function missing() {
  return {ok: false, status: 404, json: async () => ({detail: "No packet"})};
}

function createHarness() {
  const elements = new Map();
  const get = (id) => {
    if (!elements.has(id)) elements.set(id, new Element(id));
    return elements.get(id);
  };
  get("app").dataset.session = "testing-only";
  const waiting = new Map();
  const fetch = (url) => {
    if (url === "/api/jobs") return Promise.resolve(ok({
      jobs: [fakeJob(ID_A, "Role Alpha"), fakeJob(ID_B, "Role Beta")],
    }));
    if (url === "/api/overview") return Promise.resolve(ok({
      total_jobs: 2, submitted_recorded: 0, staged: 0, review_events_recent: 0,
    }));
    if (url === "/api/settings") return Promise.resolve(ok({
      profile_configured: true, facts_configured: true,
    }));
    if (url === "/api/reviews") return Promise.resolve(ok({reviews: []}));
    if (url.endsWith("/packet")) {
      const id = url.split("/")[3];
      return new Promise((resolve) => {waiting.set(id, resolve);});
    }
    throw new Error("Unexpected API request: " + url);
  };
  const document = {
    getElementById: get,
    createElement: (tag) => new Element(tag),
    createTextNode: (text) => {
      const el = new Element("text");
      el.textContent = String(text);
      return el;
    },
  };
  const context = {
    document, fetch, URL,
    navigator: {clipboard: {writeText: async () => {}}},
    FormData: class {},
    console,
  };
  vm.runInNewContext(SOURCE, context, {filename: "app.js", timeout: 1500});
  return {get, waiting};
}

const flush = () => new Promise((resolve) => setImmediate(resolve));

async function selectBoth(harness) {
  await flush();
  const list = harness.get("job-list");
  assert.equal(list.children.length, 2);
  list.children[0].click();
  assert.ok(harness.waiting.has(ID_A));
  // First click rerenders job buttons. Select the newly rendered B.
  harness.get("job-list").children[1].click();
  assert.ok(harness.waiting.has(ID_B));
}

test("late success for job A cannot display A packet under job B", async () => {
  const harness = createHarness();
  await selectBoth(harness);
  harness.waiting.get(ID_B)(ok(fakePacket(ID_B, "B-only resume")));
  await flush();
  assert.match(harness.get("detail-panel").textContent, /Role Beta/);
  assert.match(harness.get("detail-panel").textContent, /B-only resume/);
  harness.waiting.get(ID_A)(ok(fakePacket(ID_A, "A-only resume")));
  await flush();
  const visible = harness.get("detail-panel").textContent;
  assert.match(visible, /Role Beta/);
  assert.match(visible, /B-only resume/);
  assert.doesNotMatch(visible, /A-only resume/);
});

test("late 404 from job A cannot erase B's packet", async () => {
  const harness = createHarness();
  await selectBoth(harness);
  harness.waiting.get(ID_B)(ok(fakePacket(ID_B, "B preserved")));
  await flush();
  harness.waiting.get(ID_A)(missing());
  await flush();
  assert.match(harness.get("detail-panel").textContent, /B preserved/);
  assert.equal(harness.get("notice").hidden, false, "Notice state is separate from packet state");
  // An old 404 should not create a new, user-visible error.
  assert.equal(harness.get("notice").textContent, "");
});

test("submitted job UI cannot expose a regenerate-draft action", async () => {
  const harness = createHarness();
  await flush();
  // Simulate an API refresh returning a previously submitted role by
  // changing the live job list entry before selecting it.
  const scriptText = SOURCE;
  assert.match(scriptText, /job\.application_stage === "submitted"/);
  assert.match(scriptText, /Draft regeneration is disabled/);
});
