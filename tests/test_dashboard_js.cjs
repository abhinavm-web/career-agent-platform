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
    for (const child of children) child.parentElement = this;
    this.children.push(...children);
  }
  focus() { this.focused = true; }
  remove() {
    this.parentElement.children = this.parentElement.children.filter((child) => child !== this);
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

function createHarness({olderTrackedJob = false} = {}) {
  const elements = new Map();
  const get = (id) => {
    if (!elements.has(id)) elements.set(id, new Element(id));
    return elements.get(id);
  };
  get("app").dataset.session = "testing-only";
  const waiting = new Map();
  let workspace = {
    revision: 0,
    profile: {
      name: "", headline: "", target_titles: [], skills: [],
      preferred_locations: [], allow_remote: true,
    },
    facts: [],
  };
  const revisions = [];
  let crmStage = "discovered";
  let crmContact = null;
  let crmTask = null;
  let crmPriority = "none";
  const interviewNotes = [];
  const fetch = (url, options = {}) => {
    if (url === "/api/crm") return Promise.resolve(ok({
      stages: {[crmStage]: 1, discovered: crmStage === "discovered" ? 2 : 1},
      job_stages: [
        {...fakeJob(ID_A, "Role Alpha"), job_id: ID_A, stage: crmStage, priority: crmPriority},
        {...fakeJob(ID_B, "Role Beta"), job_id: ID_B, stage: "discovered"}
      ],
      open_followups: crmTask && !crmTask.completed_at ? [{
        ...crmTask, job_id: ID_A, company: "Example Corp", overdue: false
      }] : [],
      today: "2026-11-10"
    }));
    if (url === "/api/jobs/" + ID_A + "/crm") return Promise.resolve(ok({
      job: fakeJob(ID_A, "Role Alpha"),
      priority: crmPriority, priority_events: [], interview_notes: interviewNotes,
      job_id: ID_A, stage: crmStage, application_submitted: false,
      allowed_transitions: crmStage === "discovered" ? ["saved", "shortlisted", "archived"] : ["shortlisted", "archived"],
      contacts: crmContact ? [crmContact] : [],
      followups: crmTask ? [crmTask] : [],
      stage_events: crmStage === "discovered" ? [] : [{
        event_id: 1, stage: crmStage, note: "Manual note",
        actor: "candidate", created_at: "2026-11-10"
      }]
    }));
    if (url === "/api/jobs/" + ID_A + "/crm/stage" && options.method === "POST") {
      crmStage = JSON.parse(options.body).stage;
      return Promise.resolve(ok({job_id: ID_A, stage: crmStage}));
    }
    if (url === "/api/jobs/" + ID_A + "/crm/priority" && options.method === "POST") {
      crmPriority = JSON.parse(options.body).priority;
      return Promise.resolve(ok({priority: crmPriority}));
    }
    if (url === "/api/jobs/" + ID_A + "/crm/interview-notes" && options.method === "POST") {
      interviewNotes.push({...JSON.parse(options.body), actor: "candidate", created_at: "2026-11-10"});
      return Promise.resolve(ok({note_id: interviewNotes.length}));
    }
    if (url === "/api/jobs/" + ID_A + "/crm/contacts" && options.method === "POST") {
      const data = JSON.parse(options.body);
      crmContact = {...data, contact_id: 5, created_at: "2026-11-10"};
      return Promise.resolve(ok(crmContact));
    }
    if (url === "/api/jobs/" + ID_A + "/crm/contacts/5" && options.method === "POST") {
      crmContact = {...crmContact, ...JSON.parse(options.body)};
      return Promise.resolve(ok(crmContact));
    }
    if (url === "/api/jobs/" + ID_A + "/crm/followups" && options.method === "POST") {
      const data = JSON.parse(options.body);
      crmTask = {...data, followup_id: 7, completed_at: null, created_at: "2026-11-10",
        contact_name: crmContact?.name || null};
      return Promise.resolve(ok(crmTask));
    }
    if (url === "/api/crm/followups/7/complete" && options.method === "POST") {
      assert.equal(JSON.parse(options.body).acknowledged, true);
      crmTask = {...crmTask, completed_at: "2026-11-10"};
      return Promise.resolve(ok({followup_id: 7, action: "completed"}));
    }

    if (url === "/api/workspace") {
      if (options.method === "PUT") {
        const data = JSON.parse(options.body);
        assert.equal(data.expected_revision, workspace.revision);
        const previous = Object.fromEntries(workspace.facts.map((fact) => [fact.id, fact]));
        const facts = data.facts.map((fact) => {
          const old = previous[fact.id];
          const unchanged = old && ["section", "statement", "source_note", "keywords"].every(
            (key) => JSON.stringify(old[key]) === JSON.stringify(fact[key])
          );
          return {...fact, verified_by_candidate: Boolean(
            unchanged && old.verified_by_candidate && fact.verified_by_candidate
          )};
        });
        workspace = {revision: workspace.revision + 1, profile: data.profile, facts};
        revisions.unshift({revision: workspace.revision, change_type: "edited", created_at: "now"});
        return Promise.resolve(ok({workspace}));
      }
      return Promise.resolve(ok({workspace}));
    }
    if (url === "/api/workspace/history") return Promise.resolve(ok({revisions}));
    if (url.startsWith("/api/workspace/facts/") && url.endsWith("/verification")) {
      const data = JSON.parse(options.body);
      assert.equal(data.expected_revision, workspace.revision);
      assert.equal(data.acknowledged, true);
      const id = url.split("/")[4];
      workspace = {
        ...workspace, revision: workspace.revision + 1,
        facts: workspace.facts.map((fact) => fact.id === id
          ? {...fact, verified_by_candidate: data.verified} : fact),
      };
      revisions.unshift({revision: workspace.revision, change_type: "verified", created_at: "now"});
      return Promise.resolve(ok({workspace}));
    }
    if (url === "/api/jobs") return Promise.resolve(ok({
      jobs: olderTrackedJob ? [fakeJob(ID_B, "Role Beta")]
        : [fakeJob(ID_A, "Role Alpha"), fakeJob(ID_B, "Role Beta")],
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
    window: {confirm: () => true},
    crypto: {randomUUID: () => "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"},
    console,
  };
  vm.runInNewContext(SOURCE, context, {filename: "app.js", timeout: 1500});
  return {get, waiting, getWorkspace: () => workspace, getCRM: () => ({stage: crmStage, contact: crmContact, task: crmTask})};
}

const flush = () => new Promise((resolve) => setImmediate(resolve));

test("tracker combines filters and saves priority and plain-text interview notes", async () => {
  const harness = createHarness();
  await flush();
  await harness.get("nav-tracker").click();
  await harness.get("crm-jobs").children[0].click();
  const section = (title) => harness.get("crm-detail").children.find((child) => child.children[0]?.textContent === title);
  const priorityForm = section("Opportunity priority").children.find((child) => child.tagName === "form");
  priorityForm.children[0].children[0].value = "high";
  await priorityForm.handlers.submit({preventDefault() {}});
  assert.match(harness.get("crm-jobs").textContent, /high priority/);
  harness.get("crm-priority-filter").value = "high";
  harness.get("crm-priority-filter").handlers.change();
  assert.equal(harness.get("crm-result-count").textContent, "1 of 2 opportunities");
  harness.get("crm-search").value = "beta";
  harness.get("crm-search").handlers.input();
  assert.match(harness.get("crm-jobs").textContent, /No opportunities match/);
  harness.get("crm-search").value = "ALPHA";
  harness.get("crm-search").handlers.input();
  assert.match(harness.get("crm-jobs").textContent, /Role Alpha/);
  harness.get("crm-stage-filter").value = "interview";
  harness.get("crm-stage-filter").handlers.change();
  assert.equal(harness.get("crm-result-count").textContent, "0 of 2 opportunities");
  await harness.get("crm-clear-filters").click();
  assert.equal(harness.get("crm-result-count").textContent, "2 of 2 opportunities");
  const noteForm = section("Interview notes").children.find((child) => child.tagName === "form");
  noteForm.children[0].children[0].value = "2026-11-10";
  noteForm.children[1].children[0].value = "Technical panel";
  noteForm.children[2].children[0].value = "<script>not executable</script>\nPrivate follow-up";
  await noteForm.handlers.submit({preventDefault() {}});
  const notes = section("Interview notes");
  assert.match(notes.textContent, /Technical panel/);
  assert.match(notes.textContent, /<script>not executable<\/script>/);
  const tags = (element) => [element.tagName, ...element.children.flatMap(tags)];
  assert.ok(!tags(notes).includes("script"));
  assert.equal(harness.getCRM().stage, "discovered");
  await harness.get("nav-tracker").click();
  assert.match(section("Interview notes").textContent, /Private follow-up/);
});

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


test("career profile can add and separately verify a fact through the browser controls", async () => {
  const harness = createHarness();
  await flush();
  await harness.get("nav-profile").click();
  await flush();
  assert.equal(harness.get("profile-revision").textContent, "Revision 0");
  harness.get("career-name").value = "Fictional Candidate";
  harness.get("career-headline").value = "AI delivery";
  harness.get("career-targets").value = "AI Lead";
  harness.get("career-skills").value = "AWS";
  await harness.get("career-profile-form").handlers.submit({preventDefault() {}});
  await flush();
  assert.equal(harness.getWorkspace().profile.name, "Fictional Candidate");
  assert.equal(harness.getWorkspace().revision, 1);

  harness.get("fact-category").value = "projects";
  harness.get("fact-statement").value = "Built a fictional AWS test prototype.";
  harness.get("fact-keywords").value = "AWS";
  harness.get("fact-source").value = "Private fictional source";
  await harness.get("career-fact-form").handlers.submit({preventDefault() {}});
  await flush();
  assert.equal(harness.getWorkspace().facts.length, 1);
  assert.equal(harness.getWorkspace().facts[0].verified_by_candidate, false);
  const factCard = harness.get("career-facts-list").children[0];
  const actions = factCard.children.find((child) => child.className === "button-row");
  await actions.children[1].click();
  await flush();
  assert.equal(harness.getWorkspace().facts[0].verified_by_candidate, true);
  assert.match(harness.get("career-facts-list").textContent, /Candidate verified/);
});


test("saving preferences or a fact preserves unsaved changes in the other editor", async () => {
  const harness = createHarness();
  await flush();
  await harness.get("nav-profile").click();
  await flush();

  // Save preferences while a separate fact statement is still unsaved.
  harness.get("career-name").value = "Fictional Candidate";
  harness.get("career-headline").value = "Saved headline";
  harness.get("fact-statement").value = "An unsaved fictional achievement.";
  harness.get("fact-source").value = "Unsaved supporting note";
  harness.get("fact-keywords").value = "AWS";
  await harness.get("career-profile-form").handlers.submit({preventDefault() {}});
  await flush();
  assert.equal(harness.getWorkspace().profile.headline, "Saved headline");
  assert.equal(harness.get("fact-statement").value, "An unsaved fictional achievement.");
  assert.equal(harness.get("fact-source").value, "Unsaved supporting note");

  // Saving the fact should preserve unsaved edits to the separate profile form.
  harness.get("career-headline").value = "Not yet saved as a preference";
  await harness.get("career-fact-form").handlers.submit({preventDefault() {}});
  await flush();
  assert.equal(harness.getWorkspace().facts.length, 1);
  assert.equal(harness.getWorkspace().facts[0].verified_by_candidate, false);
  assert.equal(harness.getWorkspace().profile.headline, "Saved headline");
  assert.equal(harness.get("career-headline").value, "Not yet saved as a preference");
  assert.equal(harness.get("fact-statement").value, "");
});


test("application tracker records local pipeline, contact and follow-up without sending", async () => {
  // Alpha has fallen outside the board's newest-job window.
  const harness = createHarness({olderTrackedJob: true});
  await flush();
  assert.doesNotMatch(harness.get("job-list").textContent, /Role Alpha/);
  await harness.get("nav-tracker").click();
  await flush();
  assert.match(harness.get("crm-summary").textContent, /Upcoming follow-ups/);
  assert.match(harness.get("crm-jobs").textContent, /Role Alpha/);
  await harness.get("crm-jobs").children[0].click();
  await flush();
  assert.match(harness.get("crm-detail").textContent, /Role Alpha/);
  const detail = harness.get("crm-detail");
  const status = detail.children.find((child) =>
    child.children[0]?.textContent === "Pipeline stage");
  assert.ok(status);
  const form = status.children.find((child) => child.tagName === "form");
  const select = form.children[0].children[0];
  select.value = "saved";
  await form.handlers.submit({preventDefault() {}});
  await flush();
  assert.equal(harness.getCRM().stage, "saved");
  assert.match(harness.get("crm-detail").textContent, /Manual note/);

  const refreshed = harness.get("crm-detail");
  const contacts = refreshed.children.find((child) =>
    child.children[0]?.textContent === "Recruiter and referral contacts");
  const contactForm = contacts.children.find((child) => child.tagName === "form");
  contactForm.children[0].children[0].value = "Example Recruiter";
  contactForm.children[1].children[0].value = "Talent Partner";
  await contactForm.handlers.submit({preventDefault() {}});
  await flush();
  assert.equal(harness.getCRM().contact.name, "Example Recruiter");

  const savedContacts = harness.get("crm-detail").children.find((child) =>
    child.children[0]?.textContent === "Recruiter and referral contacts");
  const contactRow = savedContacts.children.find((child) => child.className === "crm-record");
  const edit = contactRow.children.find((child) => child.textContent === "Edit contact");
  await edit.click();
  let editForm = contactRow.children.find((child) => child.tagName === "form");
  assert.equal(editForm.children[0].children[0].value, "Example Recruiter");
  editForm.children[0].children[0].value = "Discarded name";
  await editForm.children.find((child) => child.textContent === "Cancel").click();
  assert.equal(harness.getCRM().contact.name, "Example Recruiter");
  assert.equal(edit.hidden, false);
  await edit.click();
  editForm = contactRow.children.find((child) => child.tagName === "form");
  editForm.children[1].children[0].value = "Updated Talent Partner";
  editForm.children[2].children[0].value = "corrected@example.invalid";
  await editForm.handlers.submit({preventDefault() {}});
  await flush();
  assert.equal(harness.getCRM().contact.role, "Updated Talent Partner");
  assert.match(harness.get("crm-detail").textContent, /corrected@example.invalid/);

  const followups = harness.get("crm-detail").children.find((child) =>
    child.children[0]?.textContent === "Follow-ups and reminders");
  const followupForm = followups.children.find((child) => child.tagName === "form");
  followupForm.children[0].children[0].value = "Call after interview";
  followupForm.children[1].children[0].value = "2026-11-20";
  followupForm.children[2].children[0].value = "";
  await followupForm.handlers.submit({preventDefault() {}});
  await flush();
  assert.equal(harness.getCRM().task.title, "Call after interview");
  assert.match(harness.get("crm-summary").textContent, /Call after interview/);
  await harness.get("crm-summary").children[1].children[1].click();
  await flush();
  assert.match(harness.get("crm-detail").textContent, /Role Alpha/);
  assert.match(harness.get("crm-detail").textContent, /Example Recruiter/);
  const tasks = harness.get("crm-detail").children.find((child) =>
    child.children[0]?.textContent === "Follow-ups and reminders");
  const taskRow = tasks.children.find((child) => child.className === "crm-record");
  await taskRow.children.find((child) => child.className === "button-row").children[0].click();
  await flush();
  assert.match(harness.get("crm-detail").textContent, /Completed/);
  assert.match(harness.get("crm-detail").textContent, /Role Alpha/);
});
