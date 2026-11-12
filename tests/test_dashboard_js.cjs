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

function createHarness({olderTrackedJob = false, jobsOverride = null, jobsPage = null, workspaceRead = null, legacyImportAvailable = false, totalJobs = 2, exportError = false, pdfError = false, discovery = () => Promise.resolve(ok({found: 3, new: 2, duplicates: 1})), outcomes = {
  submitted: 0, interviewed: 0, offered: 0, accepted: 0, interview_rate: null, offer_rate: null
}} = {}) {
  const elements = new Map();
  const get = (id) => {
    if (!elements.has(id)) elements.set(id, new Element(id));
    return elements.get(id);
  };
  get("app").dataset.session = "testing-only";
  for (const id of ["discover-site-indeed", "discover-site-linkedin", "discover-site-zip"]) get(id).checked = true;
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
  const discoveryRequests = [];
  const chatRequests = [];
  const downloads = [];
  const fetch = (url, options = {}) => {
    if (url === '/api/jobs/' + ID_A + '/possible-duplicates') return Promise.resolve(ok({
      job_id: ID_A, reason: 'Matching details can still represent different requisitions.',
      results_truncated: true, scan_limited: true,
      jobs: [{job_id: ID_B, company: 'Example Corp', title: 'Role Alpha', location: 'Remote USA',
        source: '<fictional source>', url: 'https://example.invalid/other', submission_recorded: true}]
    }));
    if (url === '/api/jobs/' + ID_A + '/fit-evidence') return Promise.resolve(ok({
      job_id: ID_A, description_revision: 0, workspace_revision: 0,
      notice: 'Keyword links need review', truncated: false,
      requirements: [{category: 'required', quote: '- Python <script>unsafe</script>',
        suggested_facts: [{id: 'fact-01', statement: 'Verified fictional Python work', matched_keywords: ['Python']}]},
        {category: 'preferred', quote: '- Rust', suggested_facts: []},
        {category: 'unspecified', source_heading: '**Qualifications**', quote: '- SQL', suggested_facts: []},
        {category: 'candidate_context', source_heading: '**About You**', quote: '- Leadership', suggested_facts: []}]
    }));
    if (url === "/api/ai/routes") return Promise.resolve(ok({routes: [{route: "local", provider: "ollama", model: "fictional", destination: "loopback", route_fingerprint: "a".repeat(64)}]}));
    if (url === "/api/workspace/chat") {
      chatRequests.push(JSON.parse(options.body));
      return Promise.resolve(ok({answer: "Fictional suggestion", provider: "ollama", model: "fictional"}));
    }
    if (url === "/api/crm/export") {
      assert.equal(options.headers["X-Session-Token"], "testing-only");
      assert.equal(options.cache, "no-store");
      assert.equal(options.credentials, "same-origin");
      return Promise.resolve(exportError
        ? {ok: false, json: async () => ({detail: "Export unavailable"})}
        : {ok: true, blob: async () => ({})});
    }
    if (url.includes("/resume.pdf?")) {
      assert.equal(options.headers["X-Session-Token"], "testing-only");
      return Promise.resolve(pdfError
        ? {ok: false, json: async () => ({detail: "Install PDF renderer"})}
        : {ok: true, blob: async () => ({})});
    }
    if (url.includes("/resume.docx?")) {
      assert.equal(options.headers["X-Session-Token"], "testing-only");
      return Promise.resolve({ok: true, blob: async () => ({})});
    }
    if (url.includes("/cover-note.txt?")) {
      assert.equal(options.headers["X-Session-Token"], "testing-only");
      assert.equal(options.cache, "no-store");
      assert.equal(options.credentials, "same-origin");
      return Promise.resolve({ok: true, blob: async () => ({})});
    }
    if (url === "/api/discover") {
      discoveryRequests.push(JSON.parse(options.body));
      return discovery();
    }
    if (url === "/api/crm") return Promise.resolve(ok({
      outcomes,
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
      return workspaceRead ? workspaceRead(workspace) : Promise.resolve(ok({workspace}));
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
    if (url === "/api/jobs?limit=100" || url.startsWith("/api/jobs?limit=100&before=")) return jobsPage ? jobsPage(url) : Promise.resolve(ok({
      jobs: jobsOverride || (olderTrackedJob ? [fakeJob(ID_B, "Role Beta")]
        : [fakeJob(ID_A, "Role Alpha"), fakeJob(ID_B, "Role Beta")]),
    }));
    if (url === "/api/overview") return Promise.resolve(ok({
      total_jobs: totalJobs, submitted_recorded: 0, staged: 0, review_events_recent: 0,
    }));
    if (url === "/api/settings") return Promise.resolve(ok({
      profile_configured: true, facts_configured: true, legacy_import_available: legacyImportAvailable,
    }));
    if (url === "/api/reviews") return Promise.resolve(ok({reviews: []}));
    if (url.endsWith("/packet")) {
      const id = url.split("/")[3];
      return new Promise((resolve) => {waiting.set(id, resolve);});
    }
    throw new Error("Unexpected API request: " + url);
  };
  const document = {
    body: new Element("body"),
    getElementById: get,
    createElement: (tag) => {
      const element = new Element(tag);
      if (tag === "a") element.handlers.click = () => downloads.push(element.download);
      return element;
    },
    createTextNode: (text) => {
      const el = new Element("text");
      el.textContent = String(text);
      return el;
    },
  };
  const context = {
    document, fetch, URL: class extends URL {
      static createObjectURL() { return "blob:local-pdf"; }
      static revokeObjectURL() {}
    },
    setTimeout: (callback, delay) => delay > 1000 ? 1 : callback(),
    clearTimeout: () => {},
    navigator: {clipboard: {writeText: async () => {}}},
    FormData: class {},
    window: {confirm: () => true},
    crypto: {randomUUID: () => "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"},
    console,
  };
  vm.runInNewContext(SOURCE, context, {filename: "app.js", timeout: 1500});
  return {get, waiting, downloads, discoveryRequests, chatRequests, getWorkspace: () => workspace, getCRM: () => ({stage: crmStage, contact: crmContact, task: crmTask})};
}

const flush = () => new Promise((resolve) => setImmediate(resolve));

test('possible duplicates show source and recorded submission without merging', async () => {
  const h = createHarness(); await flush();
  h.get('job-list').children[0].click();
  const find = (el) => el.tagName === 'button' && el.textContent === 'Find possible duplicates'
    ? el : el.children.map(find).find(Boolean);
  await find(h.get('detail-panel')).click();
  const output = h.get('detail-panel').textContent;
  assert.match(output, /different requisitions/);
  assert.match(output, /<fictional source>/);
  assert.match(output, /Submission recorded/);
  assert.match(output, /Showing the first 10/);
  assert.match(output, /10,000 most recent/);
});

test('discovery history is visible without claiming a job is current', async () => {
  const job = {...fakeJob(ID_A, 'Role Alpha'), discovery_observations: [{
    source: '<fictional source>', first_seen: '2026-01-01T00:00:00Z',
    last_seen: '2026-01-02T00:00:00Z', observation_count: 2,
    description_comparison: 'differs', description_observed_at: '2026-01-02T00:00:00Z',
    reported_posted_date: '2025-12-31', posted_date_status: 'partial', posted_date_observed_at: '2026-01-02T00:00:00Z'
  }, {source: 'other', first_seen: '2026-01-01Z', last_seen: '2026-01-02Z', observation_count: 1,
    reported_posted_date: '9999-01-01', posted_date_status: 'reported', posted_date_observed_at: '2026-01-02T00:00:00Z'}]};
  const h = createHarness({jobsOverride: [job]}); await flush();
  assert.match(h.get('job-list').textContent, /Source text differs/);
  h.get('job-list').children[0].click();
  assert.match(h.get('detail-panel').textContent, /<fictional source>/);
  assert.match(h.get('detail-panel').textContent, /last returned 2026-01-02/);
  assert.match(h.get('detail-panel').textContent, /not proof that the posting is open/);
  assert.match(h.get('detail-panel').textContent, /differs from the saved description/);
  assert.match(h.get('detail-panel').textContent, /may be partial or outdated/);
  assert.match(h.get('detail-panel').textContent, /Source-reported posting date: 2025-12-31/);
  assert.match(h.get('detail-panel').textContent, /Other returned rows did not provide a consistent date/);
  assert.match(h.get('detail-panel').textContent, /does not establish that the role is open/);
  assert.match(h.get('detail-panel').textContent, /reported date is in the future/);
});

test('hard constraint warnings appear separately from the score', async () => {
  const job = {...fakeJob(ID_A, 'Role Alpha'), constraint_evidence: [
    {constraint: 'Excluded employer names', status: 'flagged', detail: 'Exact name match'},
    {constraint: 'Fully remote required', status: 'unknown', detail: 'Remote status not confirmed'}
  ]};
  const h = createHarness({jobsOverride: [job]}); await flush();
  assert.match(h.get('job-list').textContent, /Constraint flagged/);
  h.get('job-list').children[0].click();
  assert.match(h.get('detail-panel').textContent, /Excluded employer names — Flagged/);
  assert.match(h.get('detail-panel').textContent, /Fully remote required — Unknown/);
  assert.match(h.get('detail-panel').textContent, /separate from the preference score/);
});

test('title-level preference displays literal evidence and unknown without score claims', async () => {
  const job = {...fakeJob(ID_A, 'Role Alpha'), title_level_evidence: {
    status: 'unknown', title: '<Fictional VP>', matched_phrases: [],
    notice: 'No match does not establish seniority or suitability.'
  }};
  const h = createHarness({jobsOverride: [job]}); await flush();
  h.get('job-list').children[0].click();
  assert.match(h.get('detail-panel').textContent, /<Fictional VP>/);
  assert.match(h.get('detail-panel').textContent, /level remains unknown/);
  assert.match(h.get('detail-panel').textContent, /does not establish seniority/);
});

test('provider test needs explicit consent and sends no private data', async () => {
  const elements = new Map();
  const get = (id) => { if (!elements.has(id)) elements.set(id, new Element('div')); return elements.get(id); };
  get('app').dataset.session = 'fictional-session';
  const calls = [];
  const fetch = async (url, options) => {
    calls.push({url, options});
    const payload = url === '/api/ai/routes' ? {routes: [{route: 'local', provider: 'ollama', model: 'fictional', destination: 'loopback', route_fingerprint: 'a'.repeat(64), max_tokens: 1500, sdk_installed: true, credential_status: 'not_required'}]}
      : url.startsWith('/api/jobs?') ? {jobs: []}
      : url === '/api/settings' ? {facts_configured: false}
      : {notice: 'Provider responded to synthetic test.'};
    return {ok: true, json: async () => payload};
  };
  const document = {getElementById: get, createElement: (tag) => new Element(tag)};
  const source = fs.readFileSync(path.join(__dirname, '..', 'src', 'career_agent', 'web', 'ai.js'), 'utf8');
  vm.runInNewContext(source, {document, fetch}, {timeout: 1500});
  await get('ai-load').click();
  get('ai-route').value = 'local';
  get('ai-route').handlers.change();
  await get('ai-test-route').click();
  assert.equal(calls.filter((call) => call.url === '/api/ai/test-route').length, 0);
  get('ai-test-consent').checked = true;
  await get('ai-test-route').click();
  const request = calls.find((call) => call.url === '/api/ai/test-route');
  assert.deepEqual(JSON.parse(request.options.body), {route: 'local', route_fingerprint: 'a'.repeat(64), acknowledged: true});
  assert.match(get('ai-test-status').textContent, /synthetic test/);
  assert.equal(get('ai-test-consent').checked, false);
});

test('fit evidence presents untrusted excerpts and unknowns without claiming qualifications', async () => {
  const h = createHarness(); await flush();
  h.get('job-list').children[0].click();
  const find = (el) => el.tagName === 'button' && el.textContent === 'Compare requirements'
    ? el : el.children.map(find).find(Boolean);
  await find(h.get('detail-panel')).click();
  const output = h.get('detail-panel').textContent;
  assert.match(output, /Python <script>unsafe<\/script>/);
  assert.match(output, /Potentially relevant verified fact fact-01/);
  assert.match(output, /evidence remains unknown/);
  assert.match(output, /Qualification level unspecified/);
  assert.match(output, /Section heading: \*\*Qualifications\*\*/);
  assert.match(output, /Candidate-related section; level unspecified/);
  assert.match(output, /Section heading: \*\*About You\*\*/);
});

test("reviewed Word download appears only for an approved current packet", async () => {
  const find = (el) => el.tagName === "button" && el.textContent === "Download reviewed resume Word"
    ? el : el.children.map(find).find(Boolean);
  for (const scenario of [{approved: false}, {approved: true, stale: true}, {approved: true}]) {
    const h = createHarness(); await flush();
    h.get("job-list").children[0].click();
    const packet = fakePacket(ID_A, "Fictional resume");
    packet.stale = scenario.stale;
    packet.review = scenario.approved ? {decision: "approved_for_manual_submission"} : null;
    h.waiting.get(ID_A)(ok(packet)); await flush();
    const control = find(h.get("detail-panel"));
    if (!scenario.approved || scenario.stale) assert.equal(control, undefined);
    else {
      assert.ok(control);
      await control.click();
      assert.equal(h.downloads[0], "resume-" + ID_A + "-" + "a".repeat(12) + ".docx");
      assert.match(h.get("notice").textContent, /not tracked/);
    }
  }
});

test("cover note download requires a current approved packet", async () => {
  const find = (el) => el.tagName === "button" && el.textContent === "Download reviewed cover note"
    ? el : el.children.map(find).find(Boolean);
  for (const scenario of [{approved: false}, {approved: true, stale: true}, {approved: true}]) {
    const h = createHarness(); await flush();
    h.get("job-list").children[0].click();
    const packet = fakePacket(ID_A, "Fictional resume");
    packet.stale = scenario.stale;
    packet.review = scenario.approved ? {decision: "approved_for_manual_submission"} : null;
    h.waiting.get(ID_A)(ok(packet)); await flush();
    const control = find(h.get("detail-panel"));
    if (!scenario.approved || scenario.stale) assert.equal(control, undefined);
    else {
      assert.ok(control); await control.click();
      assert.equal(h.downloads[0], "cover-note-" + ID_A + "-" + "a".repeat(12) + ".txt");
      assert.match(h.get("notice").textContent, /not tracked/);
    }
  }
});

test("overlapping profile loads retain the newest revision and keep the editor locked", async () => {
  for (const olderFirst of [true, false]) {
    const pending = [];
    const h = createHarness({workspaceRead: () => new Promise((resolve) => pending.push(resolve))});
    await flush();
    const first = h.get("nav-profile").click();
    h.get("nav-jobs").click();
    const second = h.get("nav-profile").click();
    const base = {profile: {name: "Candidate", headline: "", target_titles: [], skills: [], preferred_locations: [], allow_remote: true}, facts: []};
    const old = ok({workspace: {...base, revision: 1}});
    const current = ok({workspace: {...base, revision: 2}});
    if (olderFirst) {
      pending[0](old); await first;
      assert.equal(h.get("profile-screen").inert, true);
      pending[1](current); await second;
    } else {
      pending[1](current); await second;
      pending[0](old); await first;
    }
    assert.equal(h.get("profile-screen").inert, false);
    assert.match(h.get("profile-revision").textContent, /Revision 2/);
  }
});

test("a board refresh started before profile navigation does not start a competing profile load", async () => {
  let finishBoard;
  const pending = [];
  const h = createHarness({
    jobsPage: () => new Promise(resolve => { finishBoard = resolve; }),
    workspaceRead: () => new Promise(resolve => pending.push(resolve)),
  });
  await flush();
  const opening = h.get("nav-profile").click();
  await flush();
  assert.equal(pending.length, 1);
  assert.equal(h.get("profile-screen").inert, true);
  finishBoard(ok({jobs: [fakeJob(ID_A, "Role")], next_before: null}));
  await flush(); await flush();
  assert.equal(pending.length, 1, "the older board refresh must not load the newly opened profile");
  assert.equal(h.get("profile-screen").inert, true);
  pending[0](ok({workspace: {revision: 1, profile: {name: "Candidate", headline: "", target_titles: [],
    skills: [], preferred_locations: [], allow_remote: true}, facts: []}}));
  await opening;
  assert.equal(h.get("profile-screen").inert, false);
  h.get("career-resume-contact").value = "fictional@example.invalid";
  await flush();
  assert.equal(h.get("career-resume-contact").value, "fictional@example.invalid");
});

test("late settings reveal legacy import without replacing profile edits", async () => {
  let finishBoard, profileReads = 0;
  const h = createHarness({
    legacyImportAvailable: true,
    jobsPage: () => new Promise(resolve => { finishBoard = resolve; }),
    workspaceRead: workspace => { profileReads++; return Promise.resolve(ok({workspace: {...workspace, revision: 0}})); },
  });
  await flush(); await h.get("nav-profile").click();
  h.get("career-resume-contact").value = "fictional@example.invalid";
  assert.equal(h.get("legacy-import").hidden, true);
  finishBoard(ok({jobs: [fakeJob(ID_A, "Role")], next_before: null}));
  await flush(); await flush();
  assert.equal(h.get("legacy-import").hidden, false);
  assert.equal(profileReads, 1);
  assert.equal(h.get("career-resume-contact").value, "fictional@example.invalid");
});

test("an older server snapshot cannot replace an already loaded profile revision", async () => {
  let read = 0;
  const h = createHarness({workspaceRead: (workspace) => Promise.resolve(ok({workspace: {...workspace, revision: ++read === 1 ? 2 : 1}}))});
  await flush(); await h.get("nav-profile").click();
  assert.match(h.get("profile-revision").textContent, /Revision 2/);
  h.get("nav-jobs").click(); await h.get("nav-profile").click();
  assert.match(h.get("profile-revision").textContent, /Revision 2/);
});

test("packet review shows saved source facts and omissions without interpreting markup", async () => {
  const h = createHarness(); await flush();
  h.get("job-list").children[0].click();
  const packet = fakePacket(ID_A, "Fictional resume");
  packet.packet.fact_evidence = [{id: "fact-01", section: "experience", statement: "Built <fictional> tools", matched_keywords: ["Python"]}];
  packet.packet.omitted_fact_ids = ["fact-02"];
  h.waiting.get(ID_A)(ok(packet)); await flush();
  const detail = h.get("detail-panel").textContent;
  assert.match(detail, /Review source facts and omissions/);
  assert.match(detail, /Built <fictional> tools/);
  assert.match(detail, /Posting keyword matches: Python/);
  assert.match(detail, /Verified facts not included in this draft: fact-02/);
  assert.doesNotMatch(detail, /Compare verified facts and tailored draft/);
});

test("tailored comparison shows ordered baseline and omission without interpreting markup", async () => {
  const h = createHarness(); await flush();
  h.get("job-list").children[0].click();
  const packet = fakePacket(ID_A, "Fictional resume");
  packet.packet.fact_evidence = [{id: "fact-02", section: "projects", statement: "Selected <example>", matched_keywords: []}];
  packet.packet.omitted_fact_ids = ["fact-01"];
  packet.packet.baseline_fact_evidence = [
    {id: "fact-01", section: "experience", statement: "Omitted <script>"},
    {id: "fact-02", section: "projects", statement: "Selected <example>"}
  ];
  h.waiting.get(ID_A)(ok(packet)); await flush();
  const detail = h.get("detail-panel").textContent;
  assert.match(detail, /Compare verified facts and tailored draft/);
  assert.match(detail, /not your uploaded résumé/);
  assert.match(detail, /fact-01 · experience · Omitted/);
  assert.match(detail, /Omitted <script>/);
  assert.match(detail, /fact-02 · projects · Included/);
});

test("expired session explains how to recover without discarding input", async () => {
  const h = createHarness({discovery: () => Promise.resolve({
    ok: false, status: 403, json: async () => ({detail: "Session token required"})
  })});
  await flush();
  h.get("discover-consent").checked = true;
  h.get("discover-query").value = "Fictional role";
  h.get("discover-location").value = "United States";
  h.get("discover-results").value = "5";
  await h.get("discover-form").handlers.submit({preventDefault() {}});
  assert.match(h.get("discover-status").textContent, /session expired.*unsaved changes.*refresh/i);
  assert.equal(h.get("discover-query").value, "Fictional role");
});

test("board loads older pages before applying filters", async () => {
  const calls = [];
  const h = createHarness({jobsPage: (url) => {
    calls.push(url);
    return Promise.resolve(ok(url.includes("before=")
      ? {jobs: [fakeJob(ID_B, "Older target")], next_before: null}
      : {jobs: [fakeJob(ID_A, "Recent role")], next_before: 900}));
  }});
  await flush();
  assert.deepEqual(calls, ["/api/jobs?limit=100", "/api/jobs?limit=100&before=900"]);
  h.get("search").value = "Older target";
  h.get("search").handlers.input({target: h.get("search")});
  assert.match(h.get("job-list").textContent, /Older target/);
  assert.doesNotMatch(h.get("job-list").textContent, /Recent role/);
});

test("older board refresh cannot overwrite a newer refresh", async () => {
  let firstResolve, calls = 0;
  const h = createHarness({jobsPage: () => {
    calls++;
    if (calls === 1) return new Promise((resolve) => {firstResolve = resolve;});
    return Promise.resolve(ok({jobs: [fakeJob(ID_B, "Newer response")], next_before: null}));
  }});
  await h.get("refresh").click();
  firstResolve(ok({jobs: [fakeJob(ID_A, "Outdated response")], next_before: null}));
  await flush();
  assert.match(h.get("job-list").textContent, /Newer response/);
  assert.doesNotMatch(h.get("job-list").textContent, /Outdated response/);
});

test("profile edits typed during a slow board refresh survive", async () => {
  let finish, calls = 0;
  const h = createHarness({jobsPage: () => {
    calls++;
    if (calls === 2) return new Promise((resolve) => {finish = resolve;});
    return Promise.resolve(ok({jobs: [fakeJob(ID_A, "Role")], next_before: null}));
  }});
  await flush();
  await h.get("nav-profile").click();
  const refreshing = h.get("refresh").click();
  h.get("career-headline").value = "Unfinished fictional edit";
  h.get("fact-statement").value = "A draft being typed";
  finish(ok({jobs: [fakeJob(ID_A, "Role")], next_before: null}));
  await refreshing;
  assert.equal(h.get("career-headline").value, "Unfinished fictional edit");
  assert.equal(h.get("fact-statement").value, "A draft being typed");
});

test("nondecreasing board cursor fails rather than looping or hiding data", async () => {
  const h = createHarness({jobsPage: () => Promise.resolve(ok({jobs: [fakeJob(ID_A, "Role")], next_before: 1}))});
  await flush();
  assert.match(h.get("notice").textContent, /Invalid opportunity cursor/);
});

test("board explicitly discloses its memory limit", async () => {
  let page = 0;
  const jobs = Array.from({length: 100}, (_, i) => fakeJob(String(i).padStart(16, "0"), "Role"));
  const h = createHarness({totalJobs: 10000, jobsPage: () => Promise.resolve(ok({jobs, next_before: 10000 - ++page}))});
  await flush();
  assert.equal(page, 100);
  assert.equal(h.get("board-limit").hidden, false);
  assert.match(h.get("board-limit").textContent, /10000 loaded opportunities; older jobs are not included/);
});

test("career assistant shares session context only after consent and can clear it", async () => {
  const h = createHarness(); await flush();
  await h.get("profile-chat-load").click();
  h.get("profile-chat-route").value = "local";
  const ask = async (question) => {
    h.get("profile-chat-question").value = question;
    await h.get("profile-chat-form").handlers.submit({preventDefault() {}});
  };
  await ask("First question");
  assert.equal(h.chatRequests.length, 0);
  h.get("profile-chat-consent").checked = true; await ask("First question");
  assert.deepEqual(h.chatRequests[0].history, []);
  h.get("profile-chat-consent").checked = true; await ask("Follow up");
  assert.deepEqual(h.chatRequests[1].history, [
    {role: "user", content: "First question"}, {role: "assistant", content: "Fictional suggestion"}]);
  h.get("profile-chat-clear").click();
  assert.equal(h.get("profile-chat-answer").textContent, "");
  h.get("profile-chat-consent").checked = true; await ask("New question");
  assert.deepEqual(h.chatRequests[2].history, []);
});

test("empty board guides discovery; filtered empty state can restore results", async () => {
  const empty = createHarness({jobsOverride: [], totalJobs: 0});
  await flush();
  assert.match(empty.get("job-list").textContent, /Your opportunity board is empty/);
  const discoveryButton = empty.get("job-list").children[0].children.find((child) => child.textContent === "Find jobs");
  discoveryButton.click();
  assert.equal(empty.get("discovery-panel").open, true);
  assert.equal(empty.get("discover-query").focused, true);

  const filtered = createHarness();
  await flush();
  filtered.get("search").value = "no such fictional role";
  filtered.get("search").handlers.input({target: filtered.get("search")});
  assert.match(filtered.get("job-list").textContent, /No opportunities match these filters/);
  filtered.get("job-list").children[0].children.find((child) => child.textContent === "Clear filters").click();
  assert.equal(filtered.get("search").value, "");
  assert.match(filtered.get("job-list").textContent, /Role Alpha/);
});

test("board explicitly warns when older jobs are outside its filter scope", async () => {
  const limited = createHarness({totalJobs: 501});
  await flush();
  assert.equal(limited.get("board-limit").hidden, false);
  assert.match(limited.get("board-limit").textContent, /changed while loading/);
  const complete = createHarness();
  await flush();
  assert.equal(complete.get("board-limit").hidden, true);
});

test("opportunity filters combine and old observations are never shown as listed", async () => {
  const fresh = {...fakeJob(ID_A, "Role Alpha"), location: "Austin, TX", source: "LinkedIn",
    sponsorship_status: "posting_claims_sponsorship",
    live_check: {availability: "listed", checked_at: new Date().toISOString(), reason: "Found"}};
  const stale = {...fakeJob(ID_B, "Role Beta"), location: "Austin, TX", source: "LinkedIn",
    live_check: {availability: "listed", checked_at: new Date(Date.now() - 172800000).toISOString()}};
  const harness = createHarness({jobsOverride: [fresh, stale]});
  await flush();
  assert.match(harness.get("job-list").textContent, /Check is stale/);
  for (const [id, value, event] of [["location-filter", "austin", "input"],
    ["source-filter", "linked", "input"], ["remote-filter", "remote", "change"],
    ["sponsor-filter", "posting_claims_sponsorship", "change"], ["freshness-filter", "listed", "change"]]) {
    const control = harness.get(id); control.value = value; control.handlers[event]();
  }
  assert.match(harness.get("job-list").textContent, /Role Alpha/);
  assert.doesNotMatch(harness.get("job-list").textContent, /Role Beta/);
  harness.get("sponsor-filter").value = "all"; harness.get("sponsor-filter").handlers.change();
  harness.get("freshness-filter").value = "stale"; harness.get("freshness-filter").handlers.change();
  assert.match(harness.get("job-list").textContent, /Role Beta/);
  assert.doesNotMatch(harness.get("job-list").textContent, /Listed at check/);
  harness.get("clear-board-filters").click();
  assert.match(harness.get("job-list").textContent, /Role Alpha/);
  assert.match(harness.get("job-list").textContent, /Role Beta/);
});

test("a passed or invalid provider deadline is never displayed as currently listed", async () => {
  const base = {availability: "listed", checked_at: new Date().toISOString(), reason: "Listed at check"};
  const past = {...fakeJob(ID_A, "Role Alpha"), live_check: {...base, application_deadline: new Date(Date.now() - 1000).toISOString()}};
  const invalid = {...fakeJob(ID_B, "Role Beta"), live_check: {...base, application_deadline: "2026-12-01T12:00:00"}};
  const harness = createHarness({jobsOverride: [past, invalid]}); await flush();
  assert.match(harness.get("job-list").textContent, /Deadline passed/);
  assert.match(harness.get("job-list").textContent, /Availability unknown/);
  assert.doesNotMatch(harness.get("job-list").textContent, /Listed at check/);
  harness.get("job-list").children[0].click();
  assert.match(harness.get("detail-panel").textContent, /Current availability is unknown/);
});

test("tracker history download ignores filters and recovers from errors", async () => {
  for (const exportError of [false, true]) {
    const harness = createHarness({exportError});
    await flush();
    harness.get("crm-search").value = "no matches";
    const control = harness.get("crm-export");
    const pending = control.click();
    assert.equal(control.disabled, true);
    await pending;
    assert.equal(control.disabled, false);
    assert.equal(harness.get("crm-search").value, "no matches");
    if (exportError) {
      assert.equal(harness.downloads.length, 0);
      assert.match(harness.get("notice").textContent, /Export unavailable/);
    } else {
      assert.equal(harness.downloads[0], "career-tracker-history.json");
    }
  }
});

test("PDF download is limited to reviewed current packets and reports renderer errors", async () => {
  const findButton = (element) => element.textContent === "Download reviewed resume PDF" && element.tagName === "button"
    ? element : element.children.map(findButton).find(Boolean);
  for (const scenario of [{approved: false}, {approved: true, stale: true}, {approved: true}, {approved: true, pdfError: true}]) {
    const harness = createHarness({pdfError: scenario.pdfError});
    await flush();
    harness.get("job-list").children[0].click();
    const packet = fakePacket(ID_A, "Fictional resume");
    packet.stale = scenario.stale;
    packet.review = scenario.approved ? {decision: "approved_for_manual_submission"} : null;
    harness.waiting.get(ID_A)(ok(packet));
    await flush();
    const download = findButton(harness.get("detail-panel"));
    if (!scenario.approved || scenario.stale) {
      assert.equal(download, undefined);
    } else {
      assert.ok(download);
      await download.click();
      assert.equal(download.disabled, false);
      if (scenario.pdfError) {
        assert.equal(harness.downloads.length, 0);
        assert.match(harness.get("notice").textContent, /Install PDF renderer/);
      } else {
        assert.equal(harness.downloads[0], "resume-" + ID_A + "-" + "a".repeat(12) + ".pdf");
      }
    }
  }
});

test("JobSpy search requires consent, blocks duplicate clicks and reports imports", async () => {
  let finish;
  const harness = createHarness({discovery: () => new Promise((resolve) => {finish = resolve;})});
  await flush();
  const submit = () => harness.get("discover-form").handlers.submit({preventDefault() {}});
  await submit();
  assert.equal(harness.discoveryRequests.length, 0);
  harness.get("discover-consent").checked = true;
  harness.get("discover-query").value = "AI Lead";
  harness.get("discover-location").value = "United States";
  harness.get("discover-results").value = "5";
  const running = submit();
  assert.equal(harness.get("discover-submit").disabled, true);
  await submit();
  assert.equal(harness.discoveryRequests.length, 1);
  assert.deepEqual(JSON.parse(JSON.stringify(harness.discoveryRequests[0])), {
    query: "AI Lead", location: "United States", results: 5, sites: ["indeed", "linkedin", "zip_recruiter"], acknowledged: true
  });
  await harness.get("nav-profile").click();
  harness.get("career-headline").value = "Unsaved headline during search";
  harness.get("fact-statement").value = "Unsaved fictional career fact";
  finish(ok({found: 3, new: 2, duplicates: 1}));
  await running;
  assert.equal(harness.get("career-headline").value, "Unsaved headline during search");
  assert.equal(harness.get("fact-statement").value, "Unsaved fictional career fact");
  assert.match(harness.get("discover-status").textContent, /3 returned · 2 added · 1 duplicates/);
  assert.equal(harness.get("discover-submit").disabled, false);
  assert.equal(harness.get("discover-consent").checked, false);
});

test("partial discovery shows source errors and inconclusive empty sources", async () => {
  const h = createHarness({discovery: () => Promise.resolve(ok({found: 1, new: 1, duplicates: 0,
    source_status: {indeed: {state: "returned", returned: 1}, linkedin: {state: "reported_error", returned: 0},
      zip_recruiter: {state: "inconclusive", returned: 0}}}))});
  await flush();
  h.get("discover-consent").checked = true;
  h.get("discover-query").value = "Fictional role";
  h.get("discover-location").value = "USA";
  h.get("discover-results").value = "3";
  await h.get("discover-form").handlers.submit({preventDefault() {}});
  assert.match(h.get("discover-status").textContent, /linkedin: reported an error/);
  assert.match(h.get("discover-status").textContent, /zip_recruiter: no usable rows; inconclusive/);
});

test("manual discovery binds consent to selected sources and inputs", async () => {
  let finish;
  const h = createHarness({discovery: () => new Promise(resolve => { finish = resolve; })});
  await flush();
  h.get('discover-query').value = 'Engineer'; h.get('discover-location').value = 'USA'; h.get('discover-results').value = '5';
  h.get('discover-consent').checked = true;
  h.get('discover-site-linkedin').checked = false; h.get('discover-site-linkedin').handlers.change();
  assert.equal(h.get('discover-consent').checked, false);
  h.get('discover-site-zip').checked = false; h.get('discover-site-zip').handlers.change();
  h.get('discover-consent').checked = true;
  const running = h.get('discover-form').handlers.submit({preventDefault() {}});
  assert.deepEqual(JSON.parse(JSON.stringify(h.discoveryRequests[0].sites)), ['indeed']);
  assert.equal(h.get('discover-query').disabled, true);
  assert.equal(h.get('discover-site-indeed').disabled, true);
  finish(ok({found: 0, new: 0, duplicates: 0})); await running;
  assert.equal(h.get('discover-query').disabled, false);
  assert.equal(h.get('discover-consent').checked, false);
  h.get('discover-site-indeed').checked = false;
  h.get('discover-consent').checked = true;
  await h.get('discover-form').handlers.submit({preventDefault() {}});
  assert.equal(h.discoveryRequests.length, 1);
  assert.match(h.get('discover-status').textContent, /Choose at least one/);
});

test("failed discovery preserves search input and enables retry", async () => {
  const harness = createHarness({discovery: () => Promise.resolve({
    ok: false, status: 503, json: async () => ({detail: "Install the jobspy extra"})
  })});
  await flush();
  harness.get("discover-consent").checked = true;
  harness.get("discover-query").value = "AI Lead";
  harness.get("discover-location").value = "USA";
  harness.get("discover-results").value = "5";
  await harness.get("discover-form").handlers.submit({preventDefault() {}});
  assert.match(harness.get("discover-status").textContent, /Install the jobspy extra/);
  assert.equal(harness.get("discover-submit").disabled, false);
  assert.equal(harness.get("discover-query").value, "AI Lead");
  assert.equal(harness.get("discover-form").attributes["aria-busy"], "false");
});

test("outcome metrics explain empty and recorded submission denominators", async () => {
  const empty = createHarness();
  await flush();
  await empty.get("nav-tracker").click();
  assert.match(empty.get("crm-outcomes").textContent, /Drafts and approvals are excluded/);
  assert.doesNotMatch(empty.get("crm-outcomes").textContent, /null%|NaN|Infinity/);
  const recorded = createHarness({outcomes: {
    submitted: 4, interviewed: 2, offered: 1, accepted: 0, interview_rate: 50, offer_rate: 25
  }});
  await flush();
  await recorded.get("nav-tracker").click();
  const content = recorded.get("crm-outcomes").textContent;
  assert.match(content, /Interview rate: 50%/);
  assert.match(content, /Offer rate: 25%/);
  assert.match(content, /Denominator: 4 attested submissions/);
  assert.match(content, /skipped stages are not inferred/);
});

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


test("pasted resume lines enter the editor without saving or verifying", async () => {
  const harness = createHarness();
  await flush();
  await harness.get("nav-profile").click();
  harness.get("resume-text").value = "A fictional achievement for review.\nAnother example achievement.";
  harness.get("resume-extract").click();
  assert.equal(harness.get("resume-lines").children.length, 2);
  harness.get("resume-lines").value = harness.get("resume-lines").children[0].value;
  harness.get("resume-use-line").click();
  assert.equal(harness.get("fact-statement").value, "A fictional achievement for review.");
  assert.equal(harness.getWorkspace().facts.length, 0);
  harness.get("resume-text").handlers.input();
  assert.equal(harness.get("resume-use-line").disabled, true);
});

test("resume suggestions use explicit headings, preserve duplicate lines, and require manual save", async () => {
  const h = createHarness();
  await flush();
  await h.get("nav-profile").click();
  h.get("resume-text").value = "EXPERIENCE:\nBuilt a fictional dashboard.\nSKILLS\nPython\nEDUCATION\nFictional degree\nSUMMARY\nBuilt a fictional dashboard.";
  h.get("resume-extract").click();
  const options = h.get("resume-lines").children;
  assert.equal(options.length, 4);
  assert.match(options[1].textContent, /^Suggested skills/);
  assert.match(options[3].textContent, /^Unclassified/);
  h.get("resume-lines").value = options[1].value;
  h.get("resume-use-line").click();
  assert.equal(h.get("fact-category").value, "skills");
  assert.equal(h.get("fact-statement").value, "Python");
  assert.equal(h.getWorkspace().facts.length, 0);
  h.get("resume-lines").value = options[3].value;
  h.get("resume-use-line").click();
  assert.match(h.get("resume-status").textContent, /No category was inferred/);
  assert.equal(h.get("fact-category").value, "experience");
  assert.equal(h.getWorkspace().facts.length, 0);
  h.get("resume-text").handlers.input();
  h.get("resume-use-line").click();
  assert.match(h.get("resume-status").textContent, /Choose a suggested statement/);
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

test("salary minimum saves and can be cleared as an optional constraint", async () => {
  const h = createHarness(); await flush();
  await h.get("nav-profile").click(); await flush();
  h.get("career-minimum-base").value = "150000";
  await h.get("career-profile-form").handlers.submit({preventDefault() {}}); await flush();
  assert.equal(h.getWorkspace().profile.minimum_annual_base_usd, 150000);
  h.get("career-minimum-base").value = "";
  await h.get("career-profile-form").handlers.submit({preventDefault() {}}); await flush();
  assert.equal(h.getWorkspace().profile.minimum_annual_base_usd, null);
});

test("title-level phrases persist as literal preferences and can be cleared", async () => {
  const h = createHarness(); await flush();
  await h.get("nav-profile").click(); await flush();
  h.get("career-title-levels").value = "Director\nVP\nHead of";
  await h.get("career-profile-form").handlers.submit({preventDefault() {}}); await flush();
  assert.deepEqual(Array.from(h.getWorkspace().profile.preferred_title_levels), ['Director', 'VP', 'Head of']);
  h.get("career-title-levels").value = "";
  await h.get("career-profile-form").handlers.submit({preventDefault() {}}); await flush();
  assert.deepEqual(Array.from(h.getWorkspace().profile.preferred_title_levels), []);
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
