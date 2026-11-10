"use strict";

/* Local-first review workspace. Dynamic job data is inserted only with textContent,
 * never innerHTML. No third-party scripts, telemetry or application submission.
 */
(function () {
  const root = document.getElementById("app");
  const token = root.dataset.session;
  const state = {
    jobs: [], overview: {}, settings: {}, reviews: [], selectedId: null,
    packet: null, packetRequestSequence: 0, filter: "all", search: "", screen: "jobs",
    workspace: null, editingFactId: null
  };

  const byId = (id) => document.getElementById(id);
  const node = (tag, cls, content) => {
    const el = document.createElement(tag);
    if (cls) el.className = cls;
    if (content !== undefined && content !== null) el.textContent = String(content);
    return el;
  };
  const button = (label, cls, action) => {
    const el = node("button", cls || "button", label);
    el.type = "button";
    if (action) el.addEventListener("click", action);
    return el;
  };

  async function api(url, method, body) {
    const headers = {"X-Session-Token": token};
    const options = {method: method || "GET", headers, cache: "no-store", credentials: "same-origin"};
    if (body !== undefined) {
      headers["Content-Type"] = "application/json";
      options.body = JSON.stringify(body);
    }
    const response = await fetch(url, options);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      const error = new Error(data.detail || "Request failed");
      error.status = response.status;
      throw error;
    }
    return data;
  }

  function notice(message, error) {
    const el = byId("notice");
    el.hidden = false;
    el.classList.toggle("error", Boolean(error));
    el.textContent = message;
  }

  function safeLink(text, rawUrl) {
    const link = node("a", "", text);
    try {
      const url = new URL(rawUrl);
      if (url.protocol !== "http:" && url.protocol !== "https:") throw new Error("Unsafe URL");
      link.href = url.href;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
    } catch (_error) {
      link.removeAttribute("href");
      link.textContent = "Invalid job URL";
    }
    return link;
  }

  function chip(label, tone) {
    return node("span", "chip" + (tone ? " " + tone : ""), label);
  }

  function sponsorshipLabel(status) {
    const map = {
      "posting_claims_sponsorship": ["Posting mentions sponsorship", "teal"],
      "historical_only": ["Employer filing history", "amber"],
      "explicit_exclusion": ["Posting excludes sponsorship", "red"],
      "unknown": ["Sponsorship unknown", ""]
    };
    return map[status] || ["Unverified", ""];
  }

  function showScreen(screen) {
    state.screen = screen;
    const screens = {jobs: "Opportunity board", activity: "Review history", profile: "Career profile"};
    for (const name of Object.keys(screens)) {
      byId(name + "-screen").hidden = screen !== name;
      byId("nav-" + name).classList.toggle("active", screen === name);
      if (screen === name) byId("nav-" + name).setAttribute("aria-current", "page");
      else byId("nav-" + name).removeAttribute("aria-current");
    }
    byId("section-name").textContent = screens[screen];
  }

  function renderMetrics() {
    byId("count-jobs").textContent = state.overview.total_jobs ?? 0;
    byId("count-staged").textContent = state.overview.staged ?? 0;
    byId("count-submitted").textContent = state.overview.submitted_recorded ?? 0;
    byId("count-reviews").textContent = state.overview.review_events_recent ?? 0;
    const s = state.settings;
    const el = byId("configuration-status");
    const ready = s.profile_configured && s.facts_configured;
    el.textContent = ready ? "Candidate profile configured" : "Profile / facts not configured";
    el.classList.toggle("warn", !ready);
  }

  function filteredJobs() {
    const needle = state.search.trim().toLocaleLowerCase();
    return state.jobs.filter((job) => {
      const searchable = [job.company, job.title, job.location, job.description].join(" ").toLocaleLowerCase();
      if (needle && !searchable.includes(needle)) return false;
      if (state.filter === "priority") return job.fit_score !== null && job.fit_score >= 70;
      if (state.filter === "sponsorship") return job.sponsorship_status === "posting_claims_sponsorship" || job.sponsorship_status === "historical_only";
      if (state.filter === "review") return job.packet_available && !job.review_decision;
      if (state.filter === "approved") return job.review_decision === "approved_for_manual_submission";
      if (state.filter === "submitted") return job.application_stage === "submitted";
      return true;
    }).sort((a, b) => (b.fit_score ?? -1) - (a.fit_score ?? -1));
  }

  function renderJobs() {
    const jobs = filteredJobs();
    byId("visible-count").textContent = String(jobs.length) + (jobs.length === 1 ? " opportunity" : " opportunities");
    const list = byId("job-list");
    list.replaceChildren();
    if (!jobs.length) {
      list.append(node("p", "loading", "No matching opportunities. Add a job or change filters."));
      return;
    }
    for (const job of jobs) {
      const item = button("", "job-item" + (state.selectedId === job.id ? " selected" : ""), () => selectJob(job.id));
      item.setAttribute("aria-label", "View " + job.title + " at " + job.company);
      item.setAttribute("aria-pressed", String(state.selectedId === job.id));
      const top = node("div", "job-item-top");
      const left = node("div");
      left.append(node("h3", "", job.title), node("small", "", job.company + " · " + (job.location || "Location unspecified")));
      const mark = node("div", "company-letter", (job.company || "?").charAt(0).toUpperCase());
      top.append(left, mark);
      const lower = node("div", "job-item-bottom");
      const signal = sponsorshipLabel(job.sponsorship_status);
      lower.append(chip(signal[0], signal[1]));
      if (job.packet_available) lower.append(chip(job.review_decision ? "Reviewed" : "Draft ready", job.review_decision ? "teal" : "amber"));
      if (job.application_stage === "submitted") lower.append(chip("Submitted", "teal"));
      if (job.fit_score !== null) lower.append(node("span", "score", String(job.fit_score) + "/100"));
      item.append(top, lower);
      list.append(item);
    }
  }

  async function selectJob(id) {
    state.selectedId = id;
    state.packet = null;
    renderJobs();
    renderDetail();
    await loadPacket(id);
    if (state.selectedId === id) renderDetail();
  }

  async function loadPacket(requestedJobId) {
    const jobId = requestedJobId || state.selectedId;
    if (!jobId) return;
    const sequence = ++state.packetRequestSequence;
    try {
      const response = await api("/api/jobs/" + jobId + "/packet");
      // An older request must not render a previous job's draft under a
      // newly selected job, or overwrite a newer same-job packet response.
      if (state.selectedId !== jobId || sequence !== state.packetRequestSequence) return;
      if (response.packet && response.packet.job_id !== jobId) {
        notice("Packet belongs to a different job. Refusing to display.", true);
        state.packet = null;
        return;
      }
      state.packet = response;
    } catch (error) {
      if (state.selectedId !== jobId || sequence !== state.packetRequestSequence) return;
      if (error.status !== 404) notice(error.message, true);
      state.packet = null;
    }
  }

  function section(heading) {
    const box = node("section", "detail-section");
    box.append(node("h4", "", heading));
    return box;
  }

  function renderDetail() {
    const detail = byId("detail-panel");
    detail.replaceChildren();
    const job = state.jobs.find((value) => value.id === state.selectedId);
    if (!job) {
      const empty = node("div", "empty-detail");
      empty.append(node("span", "empty-icon", "◎"), node("h3", "", "Select an opportunity"), node("p", "", "Inspect its details and prepare your review."));
      detail.append(empty);
      return;
    }
    const header = node("div", "detail-top");
    const heading = node("div");
    heading.append(node("span", "eyebrow", "OPPORTUNITY DETAILS"), node("h3", "", job.title), node("p", "", job.company));
    header.append(heading, node("div", "detail-badge", job.fit_score === null ? "Unscored" : job.fit_score + "/100"));
    detail.append(header);
    const info = node("div", "info-grid");
    const facts = [
      ["LOCATION", job.location || "Not specified"],
      ["WORK ARRANGEMENT", job.remote ? "Remote" : "Not confirmed remote"],
      ["TRACKING", job.application_stage],
      ["SOURCE", job.source || "Manual"],
    ];
    for (const pair of facts) {
      const cell = node("div");
      cell.append(node("small", "", pair[0]), node("strong", "", pair[1]));
      info.append(cell);
    }
    detail.append(info);
    const actions = node("div", "button-row");
    actions.append(safeLink("View original posting", job.url));
    detail.append(actions);

    const sponsor = section("Sponsorship assessment");
    const sponsorBox = node("div", "sponsorship-box");
    const label = sponsorshipLabel(job.sponsorship_status);
    sponsorBox.append(chip(label[0], label[1]), node("p", "", job.sponsorship_reason));
    sponsor.append(sponsorBox);
    detail.append(sponsor);

    if (job.fit_reasons && job.fit_reasons.length) {
      const fit = section("Why this role matched");
      const ul = node("ul");
      for (const reason of job.fit_reasons) ul.append(node("li", "", reason));
      fit.append(ul);
      detail.append(fit);
    }

    if (job.description) {
      const desc = section("Job description");
      desc.append(node("p", "description", job.description));
      detail.append(desc);
    }

    const drafts = section("Application preparation");
    const p = node("p", "", "Drafts copy only candidate-confirmed career facts and cannot send an application.");
    drafts.append(p);
    if (job.application_stage === "submitted") {
      drafts.append(node("p", "", "This submitted application's reviewed packet is locked. Draft regeneration is disabled."));
    } else {
      drafts.append(button(job.packet_available ? "Regenerate draft" : "Create application draft", "button button-primary", createDraft));
    }
    detail.append(drafts);
    if (state.packet) renderPacket(detail, job, state.packet);
  }

  function renderPacket(container, job, record) {
    const packet = record.packet;
    const review = record.review;
    const card = node("section", "review-card");
    card.append(node("h4", "", "Application packet"), node("p", "", "Review each statement before using it. Packet fingerprint: " + packet.packet_sha256.slice(0, 12) + "…"));
    const resume = node("details");
    const resumeTitle = node("summary", "", "Resume draft");
    const resumeText = node("pre", "", packet.resume_markdown);
    resume.append(resumeTitle, resumeText);
    const letter = node("details");
    letter.append(node("summary", "", "Cover-note draft"), node("pre", "", packet.cover_note));
    card.append(resume, letter);

    const tools = node("div", "button-row");
    tools.append(button("Copy resume text", "button", () => copyText(packet.resume_markdown)));
    tools.append(button("Copy cover note", "button", () => copyText(packet.cover_note)));
    card.append(tools);

    const questions = node("details");
    questions.append(node("summary", "", "Required review checklist"));
    const checklist = node("ul");
    for (const question of packet.review_questions) checklist.append(node("li", "", question));
    questions.append(checklist);
    card.append(questions);

    if (review) {
      const result = node("p", "", "Latest decision: " + review.decision.replaceAll("_", " ") + ".");
      card.append(result);
    }
    if (job.application_stage !== "submitted") {
      const attest = node("label", "check-label");
      const check = document.createElement("input");
      check.type = "checkbox";
      attest.append(check, document.createTextNode("I have personally reviewed this draft and its claims."));
      card.append(attest);
      const decisionButtons = node("div", "button-row");
      const approve = button("Approve for manual submission", "button button-primary", () => decide(true, packet.packet_sha256));
      const reject = button("Reject this draft", "button button-danger", () => decide(false, packet.packet_sha256));
      approve.disabled = true;
      reject.disabled = true;
      check.addEventListener("change", () => {approve.disabled = !check.checked; reject.disabled = !check.checked;});
      decisionButtons.append(approve, reject);
      card.append(decisionButtons);
    }
    if (review && review.decision === "approved_for_manual_submission" && job.application_stage !== "submitted") {
      const submitted = node("div", "detail-section");
      submitted.append(node("h4", "", "Already applied on employer site?"));
      submitted.append(node("p", "", "Only record submission after you have submitted it yourself and received confirmation."));
      const receiptLabel = node("label", "field", "Employer confirmation reference");
      const receipt = document.createElement("input");
      receipt.type = "text"; receipt.minLength = 4; receipt.maxLength = 200;
      receipt.placeholder = "Reference from employer application";
      receiptLabel.append(receipt);
      submitted.append(receiptLabel);
      const consent = node("label", "check-label");
      const consentBox = document.createElement("input");
      consentBox.type = "checkbox";
      consent.append(consentBox, document.createTextNode("I personally submitted this application externally."));
      submitted.append(consent);
      submitted.append(button("Record confirmed submission", "button", () => markSubmitted(packet.packet_sha256, receipt.value, consentBox.checked)));
      card.append(submitted);
    }
    if (job.application_stage === "submitted") card.append(node("p", "", "Submission recorded from your attestation. Nothing was sent by this app."));
    container.append(card);
  }

  async function copyText(content) {
    try {
      await navigator.clipboard.writeText(content);
      notice("Copied draft text to clipboard.");
    } catch (_error) {
      notice("Clipboard access unavailable. Expand the draft and copy manually.", true);
    }
  }

  async function createDraft() {
    try {
      await api("/api/jobs/" + state.selectedId + "/draft", "POST", {});
      notice("Application draft created locally. Review it before using.");
      await refresh(state.selectedId);
    } catch (error) {notice(error.message, true);}
  }

  async function decide(approved, fingerprint) {
    try {
      await api("/api/jobs/" + state.selectedId + "/decision", "POST", {
        approved: approved, acknowledged: true, packet_sha256: fingerprint
      });
      notice(approved ? "Draft approved for manual submission. No application has been sent." : "Draft rejected and recorded locally.");
      await refresh(state.selectedId);
    } catch (error) {notice(error.message, true);}
  }

  async function markSubmitted(fingerprint, receipt, acknowledged) {
    if (!acknowledged) return notice("Confirm that you submitted the application on the employer's site.", true);
    if (receipt.trim().length < 4) return notice("Provide a valid employer confirmation reference.", true);
    try {
      await api("/api/jobs/" + state.selectedId + "/submitted", "POST", {
        packet_sha256: fingerprint, receipt: receipt, attest_confirmed: true
      });
      notice("External submission recorded locally. The dashboard did not submit the application.");
      await refresh(state.selectedId);
    } catch (error) {notice(error.message, true);}
  }

  function renderActivity() {
    const list = byId("activity-list");
    list.replaceChildren();
    if (!state.reviews.length) {
      list.append(node("p", "muted", "No review decisions yet. Review a draft to create an audit entry."));
      return;
    }
    for (const review of state.reviews) {
      const entry = node("div", "activity-row");
      entry.append(
        node("strong", "", review.title + " · " + review.company),
        node("p", "", review.decision.replaceAll("_", " ")),
        node("small", "", "Recorded: " + review.decided_at + " · Packet " + review.packet_sha256.slice(0, 12))
      );
      entry.append(button("View opportunity", "plain-button", async () => {
        showScreen("jobs");
        await selectJob(review.job_id);
      }));
      list.append(entry);
    }
  }

  function linesFromText(value) {
    return value.split("\n").map((line) => line.trim()).filter(Boolean);
  }

  function readCareerPreferences() {
    return {
      name: byId("career-name").value.trim(),
      headline: byId("career-headline").value.trim(),
      target_titles: linesFromText(byId("career-targets").value),
      skills: linesFromText(byId("career-skills").value),
      preferred_locations: linesFromText(byId("career-locations").value),
      allow_remote: byId("career-remote").checked
    };
  }

  function resetFactEditor() {
    state.editingFactId = null;
    byId("fact-form-heading").textContent = "Add career fact";
    byId("fact-category").value = "experience";
    byId("fact-statement").value = "";
    byId("fact-keywords").value = "";
    byId("fact-source").value = "";
  }

  function editFact(fact) {
    state.editingFactId = fact.id;
    byId("fact-form-heading").textContent = "Edit fact " + fact.id;
    byId("fact-category").value = fact.section;
    byId("fact-statement").value = fact.statement;
    byId("fact-keywords").value = fact.keywords.join("\n");
    byId("fact-source").value = fact.source_note;
    byId("career-fact-form").scrollIntoView?.({behavior: "smooth", block: "center"});
  }

  async function loadWorkspace() {
    try {
      const [data, history] = await Promise.all([
        api("/api/workspace"), api("/api/workspace/history")
      ]);
      if (state.screen !== "profile") return;
      state.workspace = data.workspace;
      renderWorkspace(history.revisions);
    } catch (error) {
      notice("Unable to load private career workspace: " + error.message, true);
    }
  }

  function renderWorkspace(revisions) {
    const workspace = state.workspace;
    if (!workspace) return;
    const profile = workspace.profile;
    byId("profile-revision").textContent = "Revision " + workspace.revision;
    byId("career-name").value = profile.name;
    byId("career-headline").value = profile.headline;
    byId("career-targets").value = profile.target_titles.join("\n");
    byId("career-skills").value = profile.skills.join("\n");
    byId("career-locations").value = profile.preferred_locations.join("\n");
    byId("career-remote").checked = profile.allow_remote;
    byId("legacy-import").hidden = workspace.revision !== 0 || !state.settings.legacy_import_available;
    const list = byId("career-facts-list");
    list.replaceChildren();
    if (!workspace.facts.length) {
      list.append(node("p", "muted", "No career facts yet. Save a statement and explicitly verify it before drafting."));
    }
    for (const fact of workspace.facts) {
      const card = node("article", "career-fact");
      const heading = node("div", "career-fact-heading");
      heading.append(
        node("strong", "", fact.section + " · " + fact.id),
        chip(fact.verified_by_candidate ? "Candidate verified" : "Not verified", fact.verified_by_candidate ? "teal" : "amber")
      );
      card.append(heading, node("p", "", fact.statement));
      card.append(node("small", "muted", "Private evidence: " + fact.source_note));
      const actions = node("div", "button-row");
      actions.append(button("Edit", "button", () => editFact(fact)));
      actions.append(button(fact.verified_by_candidate ? "Revoke verification" : "Verify this fact", fact.verified_by_candidate ? "button" : "button button-primary", () => toggleFactVerification(fact)));
      actions.append(button("Delete", "button button-danger", () => deleteFact(fact.id)));
      card.append(actions);
      list.append(card);
    }
    const history = byId("career-revision-list");
    history.replaceChildren();
    if (!revisions.length) history.append(node("p", "muted", "Your first saved change will appear here."));
    for (const revision of revisions) {
      const row = node("div", "career-history-row");
      row.append(node("strong", "", "Revision " + revision.revision + " · " + revision.change_type), node("small", "muted", revision.created_at));
      history.append(row);
    }
  }

  async function saveCareerWorkspace(profile, facts, actionLabel) {
    if (!state.workspace) return notice("Open the career profile first.", true);
    try {
      const response = await api("/api/workspace", "PUT", {
        expected_revision: state.workspace.revision, profile, facts
      });
      state.workspace = response.workspace;
      resetFactEditor();
      notice(actionLabel + " saved locally. Any added or changed career fact must be verified separately.");
      await refresh();
    } catch (error) {
      notice(error.status === 409 ? "Another edit was saved. Reload the profile before editing." : error.message, true);
    }
  }

  async function toggleFactVerification(fact) {
    if (!state.workspace) return;
    if (!fact.verified_by_candidate && !window.confirm(
      "Confirm this career statement is accurate and supported by your private evidence?"
    )) return;
    try {
      const result = await api("/api/workspace/facts/" + encodeURIComponent(fact.id) + "/verification", "POST", {
        expected_revision: state.workspace.revision,
        verified: !fact.verified_by_candidate,
        acknowledged: true
      });
      state.workspace = result.workspace;
      notice(fact.verified_by_candidate ? "Fact verification revoked." : "Fact verified. It can now appear in drafts.");
      await refresh();
    } catch (error) {notice(error.message, true);}
  }

  async function deleteFact(factId) {
    if (!state.workspace || !window.confirm("Delete this fact from the current career profile? Its historical revisions remain stored locally.")) return;
    await saveCareerWorkspace(state.workspace.profile, state.workspace.facts.filter((fact) => fact.id !== factId), "Fact");
  }

  async function importConfiguredProfile() {
    if (!window.confirm("Copy your configured profile and verified facts into this private local workspace? Your original files will remain unchanged.")) return;
    try {
      const response = await api("/api/workspace/import", "POST", {acknowledged: true});
      state.workspace = response.workspace;
      resetFactEditor();
      notice("Existing career files imported into your local workspace.");
      await refresh();
    } catch (error) {notice(error.message, true);}
  }

  async function refresh(keepSelected) {
    try {
      const results = await Promise.all([
        api("/api/jobs"), api("/api/overview"), api("/api/settings"), api("/api/reviews")
      ]);
      state.jobs = results[0].jobs;
      state.overview = results[1];
      state.settings = results[2];
      state.reviews = results[3].reviews;
      const previous = keepSelected || state.selectedId;
      state.selectedId = state.jobs.some((job) => job.id === previous) ? previous : null;
      renderMetrics();
      renderJobs();
      renderActivity();
      const selectedId = state.selectedId;
      if (selectedId) await loadPacket(selectedId); else state.packet = null;
      if (state.selectedId === selectedId) renderDetail();
      if (state.screen === "profile") await loadWorkspace();
    } catch (error) {
      notice("Unable to load local workspace: " + error.message, true);
    }
  }

  byId("nav-jobs").addEventListener("click", () => showScreen("jobs"));
  byId("nav-activity").addEventListener("click", () => showScreen("activity"));
  byId("nav-profile").addEventListener("click", async () => {showScreen("profile"); await loadWorkspace();});
  byId("import-career-data").addEventListener("click", importConfiguredProfile);
  byId("career-profile-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!state.workspace) return;
    await saveCareerWorkspace(readCareerPreferences(), state.workspace.facts, "Preferences");
  });
  byId("career-fact-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!state.workspace) return;
    const existing = state.workspace.facts.find((fact) => fact.id === state.editingFactId);
    const id = existing ? existing.id : "fact-" + crypto.randomUUID().replaceAll("-", "").slice(0, 12);
    const updated = {
      id,
      section: byId("fact-category").value,
      statement: byId("fact-statement").value.trim(),
      keywords: linesFromText(byId("fact-keywords").value),
      source_note: byId("fact-source").value.trim(),
      verified_by_candidate: existing ? existing.verified_by_candidate : false
    };
    const facts = existing
      ? state.workspace.facts.map((fact) => fact.id === id ? updated : fact)
      : [...state.workspace.facts, updated];
    await saveCareerWorkspace(state.workspace.profile, facts, "Career fact");
  });
  byId("fact-reset").addEventListener("click", resetFactEditor);
  byId("refresh").addEventListener("click", () => refresh());
  byId("search").addEventListener("input", (event) => {state.search = event.target.value; renderJobs();});
  byId("filter").addEventListener("change", (event) => {state.filter = event.target.value; renderJobs();});
  byId("show-add-job").addEventListener("click", () => {byId("add-job-form").hidden = false;});
  byId("close-add-job").addEventListener("click", () => {byId("add-job-form").hidden = true;});
  byId("add-job-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const fields = new FormData(form);
    const data = {
      company: String(fields.get("company") || ""),
      title: String(fields.get("title") || ""),
      location: String(fields.get("location") || ""),
      job_url: String(fields.get("job_url") || ""),
      description: String(fields.get("description") || ""),
      is_remote: fields.get("is_remote") === "on"
    };
    try {
      const result = await api("/api/jobs", "POST", data);
      form.reset();
      form.hidden = true;
      notice(result.created ? "Opportunity added." : "This opportunity was already tracked.");
      await refresh(result.job_id);
    } catch (error) {notice(error.message, true);}
  });

  refresh();
})();
