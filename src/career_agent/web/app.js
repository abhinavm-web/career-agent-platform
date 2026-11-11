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
    workspace: null, editingFactId: null,
    crmSummary: null, crmDetails: null, crmJobId: null, crmRequestSeq: 0, jobsPage: 0,
    boardFilters: {location: "", source: "", remote: "all", sponsor: "all", freshness: "all"}
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
      "conflicting": ["Conflicting sponsorship wording", "amber"],
      "conditional": ["Conditional sponsorship wording", "amber"],
      "unknown": ["Sponsorship unknown", ""]
    };
    return map[status] || ["Unverified", ""];
  }

  function showScreen(screen) {
    state.screen = screen;
    const screens = {jobs: "Opportunity board", activity: "Review history", profile: "Career profile", tracker: "Application tracker", h1b: "H-1B research", automation: "Saved searches", ai: "AI analysis"};
    for (const name of Object.keys(screens)) {
      byId(name + "-screen").hidden = screen !== name;
      byId("nav-" + name).classList.toggle("active", screen === name);
      if (screen === name) byId("nav-" + name).setAttribute("aria-current", "page");
      else byId("nav-" + name).removeAttribute("aria-current");
    }
    byId("section-name").textContent = screens[screen];
    window.scrollTo?.(0, 0);
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
    byId("profile-prompt").hidden = ready;
    byId("setup-progress").textContent = (s.profile_configured ? "Goals configured" : "Add your goals for matching") +
      " · " + (s.facts_configured ? "Career facts verified" : "Verify a career fact for AI analysis and drafts") + ".";
  }

  function postingObservation(job) {
    const observed = job.live_check;
    if (!observed) return {kind: "unknown", label: "Not checked", detail: "Availability has not been checked."};
    const timestamp = Date.parse(observed.checked_at);
    const age = Date.now() - timestamp;
    if (!Number.isFinite(timestamp) || age < 0 || age > 86400000 || observed.stale) {
      return {kind: "stale", label: "Check is stale", detail: "The previous observation is stale or has an invalid date; current availability is unknown."};
    }
    if (observed.availability === "listed") return {kind: "listed", label: "Listed at check", detail: observed.reason};
    if (observed.availability === "expired") return {kind: "expired", label: "Deadline passed", detail: observed.reason};
    return {kind: "unknown", label: "Availability unknown", detail: observed.reason || "The check was inconclusive."};
  }

  function filteredJobs() {
    const needle = state.search.trim().toLocaleLowerCase();
    const filters = state.boardFilters;
    return state.jobs.filter((job) => {
      const searchable = [job.company, job.title, job.location, job.description].join(" ").toLocaleLowerCase();
      if (needle && !searchable.includes(needle)) return false;
      if (filters.location && !(job.location || "").toLocaleLowerCase().includes(filters.location.trim().toLocaleLowerCase())) return false;
      if (filters.source && !(job.source || "").toLocaleLowerCase().includes(filters.source.trim().toLocaleLowerCase())) return false;
      if (filters.remote === "remote" && job.remote !== true) return false;
      if (filters.sponsor !== "all" && job.sponsorship_status !== filters.sponsor) return false;
      if (filters.freshness !== "all" && postingObservation(job).kind !== filters.freshness) return false;
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
    const truncated = (state.overview?.total_jobs ?? 0) > state.jobs.length;
    byId("board-limit").hidden = !truncated;
    byId("board-limit").textContent = truncated
      ? "Showing the most recent " + state.jobs.length + " opportunities. Board filters do not search older jobs."
      : "";
    byId("visible-count").textContent = String(jobs.length) + (jobs.length === 1 ? " opportunity" : " opportunities");
    byId("sort-label").textContent = jobs.some((job) => job.fit_score !== null) ? "Match score first" : "Recently added";
    const pageCount = Math.max(1, Math.ceil(jobs.length / 10));
    state.jobsPage = Math.min(state.jobsPage, pageCount - 1);
    byId("jobs-page").textContent = "Page " + (state.jobsPage + 1) + " of " + pageCount;
    byId("jobs-prev").disabled = state.jobsPage === 0;
    byId("jobs-next").disabled = state.jobsPage >= pageCount - 1;
    const list = byId("job-list");
    list.replaceChildren();
    if (!jobs.length) {
      const empty = node("div", "board-empty");
      if (state.jobs.length) {
        empty.append(node("strong", "", "No opportunities match these filters"),
          node("p", "", "Try a broader search or clear the filters to see your jobs."),
          button("Clear filters", "button", () => byId("clear-board-filters").click()));
      } else {
        empty.append(node("strong", "", "Your opportunity board is empty"),
          node("p", "", "Search public job sources or add a posting you found yourself."),
          button("Find jobs", "button button-primary", () => {
            byId("discovery-panel").open = true;
            byId("discover-query").focus();
          }),
          button("Add a posting", "button", () => {
            byId("show-add-job").click();
            byId("add-job-form").querySelector?.('[name="title"]')?.focus();
          }));
      }
      list.append(empty);
      return;
    }
    for (const job of jobs.slice(state.jobsPage * 10, (state.jobsPage + 1) * 10)) {
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
      if (job.source) lower.append(chip("Source: " + job.source));
      const observation = postingObservation(job);
      lower.append(chip(observation.label, observation.kind === "listed" ? "teal" : observation.kind === "expired" ? "red" : ""));
      if (job.packet_available) lower.append(chip(
        job.packet_stale ? "Draft outdated" : (job.review_decision ? "Reviewed" : "Draft ready"),
        job.review_decision && !job.packet_stale ? "teal" : "amber"
      ));
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
    actions.append(button("Analyze with AI", "button", () => {
      if (byId("ai-job").value !== job.id) {
        byId("ai-results").replaceChildren();
        byId("ai-consent").checked = false;
        byId("ai-status").textContent = "Loading selected opportunity…";
      }
      byId("ai-job").dataset.requestedJob = job.id;
      byId("nav-ai").click();
    }));
    detail.append(actions);

    const availability = section("Posting availability");
    const availabilityStatus = node("div", "availability-status");
    availabilityStatus.setAttribute("role", "status");
    const showAvailability = () => {
      const observation = postingObservation(job);
      availabilityStatus.replaceChildren(chip(observation.label, observation.kind === "listed" ? "teal" : observation.kind === "expired" ? "red" : ""),
        node("p", "", observation.detail));
      if (job.live_check && job.live_check.checked_at) availabilityStatus.append(node("p", "", "Checked: " + job.live_check.checked_at));
      if (job.live_check && job.live_check.source_url) availabilityStatus.append(safeLink("Observation source", job.live_check.source_url));
    };
    showAvailability();
    availability.append(availabilityStatus);
    if (job.live_check_supported) {
      const check = button("Check live availability", "button", async () => {
        if (check.disabled) return;
        check.disabled = true;
        availabilityStatus.textContent = "Checking the public posting…";
        try {
          job.live_check = await api("/api/jobs/" + job.id + "/live-check", "POST", {acknowledged: true});
          showAvailability();
          renderJobs();
        } catch (error) { availabilityStatus.textContent = "Check failed: " + error.message; }
        finally { check.disabled = false; }
      });
      availability.append(node("p", "", "Checks the public ATS posting. No candidate information is sent."), check);
    } else {
      availability.append(node("p", "", "Live checks support Greenhouse and Lever postings. Check the original posting manually."));
    }
    availability.append(node("p", "", "A listed posting is not a guarantee of current hiring or sponsorship."));
    detail.append(availability);

    const sponsor = section("Sponsorship assessment");
    const sponsorBox = node("div", "sponsorship-box");
    const label = sponsorshipLabel(job.sponsorship_status);
    sponsorBox.append(chip(label[0], label[1]), node("p", "", job.sponsorship_reason));
    sponsor.append(sponsorBox);
    sponsor.append(button("Research H-1B records and jobs", "button", () => {
      byId("h1b-query").value = job.company;
      byId("nav-h1b").click();
    }));
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
    const stale = Boolean(record.stale);
    const card = node("section", "review-card");
    card.append(node("h4", "", "Application packet"), node("p", "", "Review each statement before using it. Packet fingerprint: " + packet.packet_sha256.slice(0, 12) + "…"));
    if (stale) card.append(node("p", "stale-warning", "Career facts or preferences changed since this packet was drafted. Regenerate and review before using it."));
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
    if (!stale && review?.decision === "approved_for_manual_submission") {
      const download = button("Download reviewed resume PDF", "button button-primary", async () => {
        download.disabled = true;
        try {
          const response = await fetch("/api/jobs/" + job.id + "/resume.pdf?packet_sha256=" + packet.packet_sha256, {
            headers: {"X-Session-Token": token}, cache: "no-store", credentials: "same-origin"
          });
          if (!response.ok) {
            const error = await response.json().catch(() => ({}));
            throw new Error(error.detail || "PDF export failed");
          }
          const url = URL.createObjectURL(await response.blob());
          const link = document.createElement("a");
          link.href = url;
          link.download = "resume-" + job.id + "-" + packet.packet_sha256.slice(0, 12) + ".pdf";
          document.body.append(link);
          link.click();
          link.remove();
          setTimeout(() => URL.revokeObjectURL(url), 1000);
          notice("Reviewed resume downloaded. Inspect the PDF before using it; no application was sent.");
        } catch (error) {
          notice(error.message, true);
        } finally {
          download.disabled = false;
        }
      });
      tools.append(download);
    } else {
      tools.append(node("span", "muted", "Approve the current packet to download a resume PDF."));
    }
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
    if (job.application_stage !== "submitted" && !stale) {
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
    if (!stale && review && review.decision === "approved_for_manual_submission" && job.application_stage !== "submitted") {
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


  function snapshotEditorInputs() {
    return {
      profile: readCareerPreferences(),
      editingFactId: state.editingFactId,
      category: byId("fact-category").value,
      statement: byId("fact-statement").value,
      keywords: byId("fact-keywords").value,
      source: byId("fact-source").value
    };
  }

  function restoreEditorInputs(draft) {
    const p = draft.profile;
    byId("career-name").value = p.name;
    byId("career-headline").value = p.headline;
    byId("career-targets").value = p.target_titles.join("\n");
    byId("career-skills").value = p.skills.join("\n");
    byId("career-locations").value = p.preferred_locations.join("\n");
    byId("career-remote").checked = p.allow_remote;
    state.editingFactId = draft.editingFactId;
    byId("fact-form-heading").textContent = draft.editingFactId
      ? "Edit fact " + draft.editingFactId : "Add career fact";
    byId("fact-category").value = draft.category;
    byId("fact-statement").value = draft.statement;
    byId("fact-keywords").value = draft.keywords;
    byId("fact-source").value = draft.source;
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
      // A saved fact closes its editor. Saving preferences must not erase
      // a separate fact draft that the candidate was still typing.
      if (actionLabel !== "Preferences") resetFactEditor();
      notice(actionLabel + " saved locally. Any added or changed career fact must be verified separately.");
      await refresh(undefined, true);
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
      await refresh(undefined, true);
    } catch (error) {notice(error.message, true);}
  }

  async function deleteFact(factId) {
    if (!state.workspace || !window.confirm("Delete this fact from the current career profile? Its historical revisions remain stored locally.")) return;
    if (state.editingFactId === factId) resetFactEditor();
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


  function crmInput(label, name, type, required, maxlength) {
    const field = node("label", "field", label);
    const control = document.createElement("input");
    control.name = name;
    control.type = type;
    control.required = Boolean(required);
    if (maxlength) control.maxLength = maxlength;
    field.append(control);
    return {field, control};
  }

  function crmSection(title) {
    const section = node("section", "detail-section");
    section.append(node("h4", "", title));
    return section;
  }

  async function crmPost(url, data, message) {
    try {
      await api(url, "POST", data);
      notice(message);
      await loadCRM();
    } catch (error) {
      notice(error.message, true);
    }
  }

  function renderCRMSummary() {
    const root = byId("crm-summary");
    root.replaceChildren();
    const summary = state.crmSummary;
    if (!summary) return;
    const counts = node("div", "crm-stage-counts");
    for (const [status, count] of Object.entries(summary.stages)) {
      const pill = node("div", "crm-count");
      pill.append(node("strong", "", count), node("small", "", status.replaceAll("_", " ")));
      counts.append(pill);
    }
    if (!Object.keys(summary.stages).length) counts.append(node("p", "muted", "Track your first opportunity to see pipeline stages."));
    root.append(counts);
    const due = node("div", "crm-due-list");
    due.append(node("h3", "", "Upcoming follow-ups"));
    if (!summary.open_followups.length) due.append(node("p", "muted", "No pending follow-ups."));
    for (const task of summary.open_followups.slice(0, 6)) {
      const row = button("", "crm-due-item", () => selectCRMJob(task.job_id));
      row.append(node("strong", "", task.title), node("small", task.overdue ? "overdue" : "", task.due_date + " · " + task.company));
      due.append(row);
    }
    root.append(due);
    const outcomePanel = byId("crm-outcomes");
    outcomePanel.replaceChildren();
    outcomePanel.append(node("h3", "", "Recorded application outcomes"));
    const outcomes = summary.outcomes;
    if (!outcomes) return;
    const milestones = node("div", "crm-stage-counts");
    for (const [key, label] of [["submitted", "Attested submissions"], ["interviewed", "Interview recorded"],
      ["offered", "Offer recorded"], ["accepted", "Accepted offer"]]) {
      const metric = node("div", "crm-count");
      metric.append(node("strong", "", outcomes[key]), node("small", "", label));
      milestones.append(metric);
    }
    outcomePanel.append(milestones);
    if (outcomes.submitted) {
      outcomePanel.append(node("p", "muted", "Interview rate: " + outcomes.interview_rate + "% · Offer rate: " +
        outcomes.offer_rate + "% · Denominator: " + outcomes.submitted + " attested submissions."));
    } else {
      outcomePanel.append(node("p", "muted", "Rates appear after a manual submission is attested. Drafts and approvals are excluded."));
    }
    outcomePanel.append(node("p", "muted", "All-time candidate-recorded milestones, counted once per opportunity. Later outcomes retain earlier milestones; skipped stages are not inferred. These records are not independently verified or predictions."));
  }

  function renderCRMJobs() {
    const list = byId("crm-jobs");
    list.replaceChildren();
    const jobs = state.crmSummary?.job_stages || [];
    const search = byId("crm-search").value.trim().toLowerCase();
    const stage = byId("crm-stage-filter").value;
    const priority = byId("crm-priority-filter").value;
    const visible = jobs.filter((job) =>
      (!stage || job.stage === stage) && (!priority || (job.priority || "none") === priority) &&
      (!search || [job.title, job.company, job.location].join(" ").toLowerCase().includes(search))
    );
    byId("crm-result-count").textContent = visible.length + " of " + jobs.length + " opportunities";
    if (!jobs.length) {
      list.append(node("p", "loading", "No jobs yet. Add a role on the Opportunity board."));
      return;
    }
    if (!visible.length) list.append(node("p", "loading", "No opportunities match these filters."));
    for (const tracked of visible) {
      const job = {...tracked, id: tracked.job_id};
      const item = button("", "job-item" + (state.crmJobId === job.id ? " selected" : ""), () => selectCRMJob(job.id));
      item.setAttribute("aria-pressed", String(state.crmJobId === job.id));
      item.append(
        node("h3", "", job.title),
        node("small", "", job.company + " · " + (job.location || "Location not specified"))
      );
      const chips = node("div", "job-item-bottom");
      chips.append(chip(job.stage, "teal"));
      if (job.priority && job.priority !== "none") chips.append(chip(job.priority + " priority", ""));
      if (job.application_stage === "submitted") chips.append(chip("Manually submitted", ""));
      item.append(chips);
      list.append(item);
    }
  }

  async function selectCRMJob(jobId) {
    state.crmJobId = jobId;
    state.crmDetails = null;
    const requestNumber = ++state.crmRequestSeq;
    renderCRMJobs();
    renderCRMDetail();
    try {
      const details = await api("/api/jobs/" + jobId + "/crm");
      if (requestNumber !== state.crmRequestSeq || state.crmJobId !== jobId) return;
      state.crmDetails = details;
      renderCRMDetail();
    } catch (error) {
      if (requestNumber !== state.crmRequestSeq || state.crmJobId !== jobId) return;
      notice(error.message, true);
    }
  }

  function renderCRMDetail() {
    const panel = byId("crm-detail");
    panel.replaceChildren();
    const data = state.crmDetails;
    const item = data?.job;
    if (!item || data.job_id !== state.crmJobId || data.job_id !== item.id) {
      const empty = node("div", "empty-detail");
      empty.append(node("h3", "", "Select a tracked opportunity"), node("p", "", "Review its pipeline stage, contacts and follow-up tasks."));
      panel.append(empty);
      return;
    }

    const heading = node("div", "detail-top");
    const identity = node("div");
    identity.append(node("span", "eyebrow", "APPLICATION TRACKER"), node("h3", "", item.title), node("p", "", item.company));
    heading.append(identity, chip(data.stage, "teal"));
    panel.append(heading);
    panel.append(node("p", "muted", data.application_submitted
      ? "Submission recorded from your independent attestation. Pipeline updates do not change that receipt."
      : "This application has not been recorded as submitted. Advancing the hiring pipeline requires a separate manual application submission."));

    const statusSection = crmSection("Pipeline stage");
    if (data.allowed_transitions.length) {
      const form = node("form", "crm-form");
      const stageLabel = node("label", "field", "Next stage");
      const stageSelect = node("select");
      for (const stage of data.allowed_transitions) {
        const option = node("option", "", stage.replaceAll("_", " "));
        option.value = stage;
        stageSelect.append(option);
      }
      stageLabel.append(stageSelect);
      const noteField = crmInput("Optional outcome note (private)", "note", "text", false, 1000);
      const stageSubmit = button("Record stage", "button button-primary");
      stageSubmit.type = "submit";
      form.append(stageLabel, noteField.field, stageSubmit);
      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        await crmPost("/api/jobs/" + item.id + "/crm/stage", {
          stage: stageSelect.value, note: noteField.control.value
        }, "Pipeline stage recorded; no application was sent.");
      });
      statusSection.append(form);
    } else {
      statusSection.append(node("p", "muted", "This is a final pipeline outcome. No further transitions are available in the local MVP."));
    }
    panel.append(statusSection);

    const prioritySection = crmSection("Opportunity priority");
    const priorityForm = node("form", "crm-form");
    const priorityLabel = node("label", "field", "Priority");
    const prioritySelect = node("select");
    for (const value of ["none", "low", "medium", "high"]) {
      const option = node("option", "", value === "none" ? "No priority" : value);
      option.value = value;
      prioritySelect.append(option);
    }
    prioritySelect.value = data.priority || "none";
    priorityLabel.append(prioritySelect);
    const prioritySave = button("Save priority", "button");
    prioritySave.type = "submit";
    priorityForm.append(priorityLabel, prioritySave);
    priorityForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      await crmPost("/api/jobs/" + item.id + "/crm/priority", {priority: prioritySelect.value}, "Priority saved.");
    });
    prioritySection.append(priorityForm);
    for (const event of data.priority_events || []) {
      prioritySection.append(node("small", "muted", event.created_at + " · " + event.actor + " · " + event.priority));
    }
    panel.append(prioritySection);

    const interviews = crmSection("Interview notes");
    interviews.append(node("p", "muted", "Private notes stay on this device. Saved notes are retained; add a new note for corrections. Notes do not change the application stage."));
    for (const note of data.interview_notes || []) {
      const row = node("div", "crm-record");
      row.append(node("strong", "", note.round), node("small", "", note.interview_date),
        node("p", "interview-note-text", note.notes), node("small", "", "Recorded " + note.created_at + " · " + note.actor));
      interviews.append(row);
    }
    const interviewForm = node("form", "crm-form");
    const interviewDate = crmInput("Interview date", "interview_date", "date", true);
    const round = crmInput("Round / topic", "round", "text", true, 120);
    const noteLabel = node("label", "field", "Private interview notes");
    const noteText = node("textarea");
    noteText.required = true;
    noteText.maxLength = 4000;
    noteText.rows = 4;
    noteLabel.append(noteText);
    const noteSave = button("Save interview note", "button button-primary");
    noteSave.type = "submit";
    interviewForm.append(interviewDate.field, round.field, noteLabel, noteSave);
    interviewForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      await crmPost("/api/jobs/" + item.id + "/crm/interview-notes", {
        interview_date: interviewDate.control.value, round: round.control.value, notes: noteText.value
      }, "Interview note saved locally.");
    });
    interviews.append(interviewForm);
    panel.append(interviews);

    const contacts = crmSection("Recruiter and referral contacts");
    if (!data.contacts.length) contacts.append(node("p", "muted", "No contacts saved for this opportunity."));
    for (const contact of data.contacts) {
      const row = node("div", "crm-record");
      row.append(node("strong", "", contact.name + (contact.role ? " · " + contact.role : "")));
      if (contact.email) row.append(node("small", "", contact.email));
      if (contact.notes) row.append(node("p", "", contact.notes));
      const edit = button("Edit contact", "button", () => {
        edit.hidden = true;
        const form = node("form", "crm-form");
        const name = crmInput("Name", "name", "text", true, 120);
        const role = crmInput("Role / relationship", "role", "text", false, 120);
        const email = crmInput("Email (optional)", "email", "email", false, 254);
        const notes = crmInput("Context note (private)", "notes", "text", false, 1000);
        for (const field of [name, role, email, notes]) {
          field.control.value = contact[field.control.name];
          form.append(field.field);
        }
        const save = button("Save contact", "button button-primary");
        save.type = "submit";
        const cancel = button("Cancel", "button", () => {
          form.remove();
          edit.hidden = false;
          edit.focus();
        });
        form.append(save, cancel);
        form.addEventListener("submit", async (event) => {
          event.preventDefault();
          await crmPost("/api/jobs/" + item.id + "/crm/contacts/" + contact.contact_id, {
            name: name.control.value, role: role.control.value,
            email: email.control.value, notes: notes.control.value
          }, "Contact updated locally.");
        });
        row.append(form);
        name.control.focus();
      });
      row.append(edit);
      contacts.append(row);
    }
    const contactForm = node("form", "crm-form");
    const contactName = crmInput("Name", "name", "text", true, 120);
    const contactRole = crmInput("Role / relationship", "role", "text", false, 120);
    const contactEmail = crmInput("Email (optional)", "email", "email", false, 254);
    const contactNotes = crmInput("Context note (private)", "notes", "text", false, 1000);
    const contactSubmit = button("Add contact", "button button-primary");
    contactSubmit.type = "submit";
    contactForm.append(contactName.field, contactRole.field, contactEmail.field, contactNotes.field, contactSubmit);
    contactForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      await crmPost("/api/jobs/" + item.id + "/crm/contacts", {
        name: contactName.control.value, role: contactRole.control.value,
        email: contactEmail.control.value, notes: contactNotes.control.value
      }, "Contact saved locally. No message was sent.");
    });
    contacts.append(contactForm);
    panel.append(contacts);

    const tasks = crmSection("Follow-ups and reminders");
    tasks.append(node("p", "muted", "Tasks are local reminders. Nothing is scheduled or sent automatically."));
    if (!data.followups.length) tasks.append(node("p", "muted", "No follow-up tasks yet."));
    for (const followup of data.followups) {
      const row = node("div", "crm-record");
      row.append(node("strong", "", followup.title));
      row.append(node("small", "", followup.due_date + (followup.contact_name ? " · " + followup.contact_name : "")));
      if (followup.completed_at) {
        row.append(chip("Completed", "teal"));
      } else {
        const buttons = node("div", "button-row");
        buttons.append(button("Mark completed", "button", async () => {
          if (!window.confirm("Confirm this follow-up was completed? No email will be sent.")) return;
          await crmPost("/api/crm/followups/" + followup.followup_id + "/complete",
            {acknowledged: true}, "Follow-up marked completed.");
        }));
        const newDate = document.createElement("input");
        newDate.type = "date";
        newDate.value = followup.due_date;
        newDate.setAttribute("aria-label", "Reschedule " + followup.title);
        buttons.append(newDate, button("Reschedule", "button", () =>
          crmPost("/api/crm/followups/" + followup.followup_id + "/reschedule",
            {due_date: newDate.value}, "Follow-up rescheduled.")));
        row.append(buttons);
      }
      tasks.append(row);
    }
    const followupForm = node("form", "crm-form");
    const title = crmInput("Follow-up title", "title", "text", true, 200);
    const due = crmInput("Due date", "due_date", "date", true);
    const contactLabel = node("label", "field", "Associated contact (optional)");
    const contactSelect = document.createElement("select");
    const emptyOption = node("option", "", "No contact");
    emptyOption.value = "";
    contactSelect.append(emptyOption);
    for (const contact of data.contacts) {
      const option = node("option", "", contact.name);
      option.value = String(contact.contact_id);
      contactSelect.append(option);
    }
    contactLabel.append(contactSelect);
    const followupSubmit = button("Add follow-up", "button button-primary");
    followupSubmit.type = "submit";
    followupForm.append(title.field, due.field, contactLabel, followupSubmit);
    followupForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      await crmPost("/api/jobs/" + item.id + "/crm/followups", {
        title: title.control.value,
        due_date: due.control.value,
        contact_id: contactSelect.value ? Number(contactSelect.value) : null
      }, "Follow-up saved locally. No message was scheduled or sent.");
    });
    tasks.append(followupForm);
    panel.append(tasks);

    const history = crmSection("Pipeline history");
    if (!data.stage_events.length) history.append(node("p", "muted", "No stage changes recorded yet."));
    for (const event of data.stage_events) {
      const row = node("div", "crm-record");
      row.append(node("strong", "", event.stage), node("small", "", event.created_at + " · " + event.actor));
      if (event.note) row.append(node("p", "", event.note));
      history.append(row);
    }
    panel.append(history);
  }

  async function loadCRM() {
    try {
      state.crmSummary = await api("/api/crm");
      if (state.screen !== "tracker") return;
      renderCRMSummary();
      renderCRMJobs();
      if (state.crmJobId) {
        await selectCRMJob(state.crmJobId);
      } else {
        state.crmJobId = null;
        state.crmDetails = null;
        renderCRMDetail();
      }
    } catch (error) {
      notice("Unable to load application tracker: " + error.message, true);
    }
  }

  async function refresh(keepSelected, preserveEditor = false) {
    const draft = preserveEditor && state.screen === "profile" && state.workspace
      ? snapshotEditorInputs() : null;
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
      if (keepSelected && state.selectedId) {
        const index = filteredJobs().findIndex((job) => job.id === state.selectedId);
        if (index >= 0) state.jobsPage = Math.floor(index / 10);
      }
      renderMetrics();
      renderJobs();
      renderActivity();
      const selectedId = state.selectedId;
      if (selectedId) await loadPacket(selectedId); else state.packet = null;
      if (state.selectedId === selectedId) renderDetail();
      if (state.screen === "profile") {
        await loadWorkspace();
        if (draft) restoreEditorInputs(draft);
      }
      if (state.screen === "tracker") await loadCRM();
    } catch (error) {
      notice("Unable to load local workspace: " + error.message, true);
    }
  }

  byId("nav-jobs").addEventListener("click", () => showScreen("jobs"));
  byId("nav-h1b").addEventListener("click", () => showScreen("h1b"));
  byId("nav-automation").addEventListener("click", () => {showScreen("automation"); byId("automation-panel").open = true; byId("automation-refresh").click();});
  byId("nav-ai").addEventListener("click", () => {showScreen("ai"); byId("ai-panel").open = true; byId("ai-load").click();});
  byId("open-profile").addEventListener("click", () => byId("nav-profile").click());
  byId("ai-open-profile").addEventListener("click", () => byId("nav-profile").click());
  byId("discover-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const submit = byId("discover-submit");
    const status = byId("discover-status");
    if (submit.disabled) return;
    if (!byId("discover-consent").checked) {
      status.textContent = "Confirm sending this search to external job sources.";
      return;
    }
    submit.disabled = true;
    byId("discover-form").setAttribute("aria-busy", "true");
    status.textContent = "Searching job sources… This may take a few minutes.";
    try {
      const result = await api("/api/discover", "POST", {
        query: byId("discover-query").value, location: byId("discover-location").value,
        results: Number(byId("discover-results").value), acknowledged: true
      });
      status.textContent = result.found + " returned · " + result.new + " added · " + result.duplicates +
        " duplicates. Sources may return partial results.";
      byId("discover-consent").checked = false;
      await refresh(undefined, true);
    } catch (error) {
      status.textContent = error.message;
    } finally {
      submit.disabled = false;
      byId("discover-form").setAttribute("aria-busy", "false");
    }
  });
  byId("nav-activity").addEventListener("click", () => showScreen("activity"));
  byId("nav-profile").addEventListener("click", async () => {showScreen("profile"); await loadWorkspace();});
  byId("nav-tracker").addEventListener("click", async () => {showScreen("tracker"); await loadCRM();});
  byId("crm-export").addEventListener("click", async () => {
    const control = byId("crm-export");
    control.disabled = true;
    try {
      const response = await fetch("/api/crm/export", {
        headers: {"X-Session-Token": token}, cache: "no-store", credentials: "same-origin"
      });
      if (!response.ok) {
        const error = await response.json().catch(() => ({}));
        throw new Error(error.detail || "Tracker export failed");
      }
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement("a");
      link.href = url;
      link.download = "career-tracker-history.json";
      document.body.append(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      notice("Tracker history downloaded. Keep this file private.");
    } catch (error) {
      notice(error.message, true);
    } finally {
      control.disabled = false;
    }
  });
  byId("crm-search").addEventListener("input", renderCRMJobs);
  byId("crm-stage-filter").addEventListener("change", renderCRMJobs);
  byId("crm-priority-filter").addEventListener("change", renderCRMJobs);
  byId("crm-clear-filters").addEventListener("click", () => {
    for (const id of ["crm-search", "crm-stage-filter", "crm-priority-filter"]) byId(id).value = "";
    renderCRMJobs();
  });
  byId("import-career-data").addEventListener("click", importConfiguredProfile);
  let profileChatRoutes = [];
  let profileChatBusy = false;
  function updateProfileChatRoute() {
    byId("profile-chat-consent").checked = false;
    const route = profileChatRoutes.find((item) => item.route === byId("profile-chat-route").value);
    byId("profile-chat-destination").textContent = route
      ? `${route.provider} · ${route.model} · ${route.destination}` : "No model route selected.";
  }
  byId("profile-chat-route").addEventListener("change", updateProfileChatRoute);
  byId("profile-chat-facts").addEventListener("change", () => {byId("profile-chat-consent").checked = false;});
  byId("profile-chat-question").addEventListener("input", () => {byId("profile-chat-consent").checked = false;});
  byId("profile-chat-load").addEventListener("click", async () => {
    if (profileChatBusy) return;
    profileChatBusy = true;
    byId("profile-chat-load").disabled = true;
    byId("profile-chat-submit").disabled = true;
    try {
      const response = await api("/api/ai/routes");
      profileChatRoutes = response.routes;
      const select = byId("profile-chat-route");
      const previous = select.value;
      select.replaceChildren();
      for (const route of profileChatRoutes) {
        const option = node("option", "", `${route.route} (${route.provider})`);
        option.value = route.route;
        select.append(option);
      }
      if (profileChatRoutes.some((route) => route.route === previous)) select.value = previous;
      updateProfileChatRoute();
      byId("profile-chat-status").textContent = profileChatRoutes.length ? "Review the provider and confirm sharing before asking." : "No model routes configured.";
    } catch (error) {
      profileChatRoutes = [];
      byId("profile-chat-route").replaceChildren();
      updateProfileChatRoute();
      byId("profile-chat-status").textContent = error.message;
    } finally {
      profileChatBusy = false;
      byId("profile-chat-load").disabled = false;
      byId("profile-chat-submit").disabled = !profileChatRoutes.length;
    }
  });
  byId("profile-chat-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (profileChatBusy || !byId("profile-chat-consent").checked) return;
    profileChatBusy = true;
    const ids = ["profile-chat-load", "profile-chat-submit", "profile-chat-route", "profile-chat-question", "profile-chat-facts", "profile-chat-consent"];
    ids.forEach((id) => {byId(id).disabled = true;});
    byId("profile-chat-answer").replaceChildren();
    byId("profile-chat-status").textContent = "Waiting for the selected provider…";
    try {
      const response = await api("/api/workspace/chat", "POST", {
        route: byId("profile-chat-route").value,
        message: byId("profile-chat-question").value,
        include_verified_facts: byId("profile-chat-facts").checked,
        acknowledged: true
      });
      byId("profile-chat-answer").append(node("p", "", response.answer));
      byId("profile-chat-status").textContent = `Unverified suggestion · ${response.provider} · ${response.model}. No profile changes were made.`;
    } catch (error) {
      byId("profile-chat-status").textContent = error.message;
    } finally {
      profileChatBusy = false;
      ids.forEach((id) => {byId(id).disabled = false;});
      byId("profile-chat-consent").checked = false;
    }
  });
  byId("resume-file-extract").addEventListener("click", async () => {
    const control = byId("resume-file-extract"), file = byId("resume-file").files?.[0];
    const status = byId("resume-file-status"), field = byId("resume-text");
    if (!file || file.size > 2 * 1024 * 1024 || !file.size) {
      status.textContent = "Choose a nonempty PDF, DOCX or text file of at most 2 MB.";
      return;
    }
    if (field.value.trim()) {
      status.textContent = "Clear the résumé text field before extracting a file, so existing text is not overwritten.";
      return;
    }
    if (control.disabled) return;
    control.disabled = true;
    status.textContent = "Extracting text locally…";
    try {
      const content = await new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(String(reader.result).split(",", 2)[1]);
        reader.onerror = () => reject(new Error("Unable to read the selected file"));
        reader.readAsDataURL(file);
      });
      const result = await api("/api/workspace/resume-extract", "POST", {filename: file.name, content});
      if (field.value.trim() || byId("resume-file").files?.[0] !== file) {
        status.textContent = "Text or file selection changed during extraction. Existing text was preserved; extract again.";
        return;
      }
      field.value = result.text;
      byId("resume-lines").replaceChildren();
      byId("resume-use-line").disabled = true;
      status.textContent = "Text extracted locally. Review it, then extract lines to choose a fact.";
    } catch (error) { status.textContent = error.message; }
    finally { control.disabled = false; }
  });
  byId("resume-text").addEventListener("input", () => {
    byId("resume-lines").replaceChildren();
    byId("resume-use-line").disabled = true;
    byId("resume-status").textContent = "Extract lines again after editing the text.";
  });
  byId("resume-extract").addEventListener("click", () => {
    const lines = byId("resume-text").value.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
    const select = byId("resume-lines");
    select.replaceChildren();
    for (const line of lines.slice(0, 200)) {
      const option = node("option", "", line.length > 120 ? line.slice(0, 117) + "…" : line);
      option.value = line;
      select.append(option);
    }
    byId("resume-use-line").disabled = !lines.length;
    byId("resume-status").textContent = lines.length > 200 ? "Showing the first 200 lines." : `${lines.length} lines found. Review each before saving.`;
  });
  byId("resume-use-line").addEventListener("click", () => {
    const line = byId("resume-lines").value;
    if (line.length < 8 || line.length > 1000) {
      byId("resume-status").textContent = "Choose a line with 8–1,000 characters, or edit the pasted text first.";
      return;
    }
    if ((state.editingFactId || byId("fact-statement").value || byId("fact-source").value) &&
        !window.confirm("Replace the unsaved fact editor contents with this résumé line?")) return;
    resetFactEditor();
    byId("fact-statement").value = line;
    byId("fact-statement").focus();
    byId("resume-status").textContent = "Review the statement and add a private evidence note before saving it as an unverified fact.";
  });
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
  byId("search").addEventListener("input", (event) => {state.search = event.target.value; state.jobsPage = 0; renderJobs();});
  byId("filter").addEventListener("change", (event) => {state.filter = event.target.value; state.jobsPage = 0; renderJobs();});
  byId("jobs-prev").addEventListener("click", () => {state.jobsPage = Math.max(0, state.jobsPage - 1); renderJobs();});
  byId("jobs-next").addEventListener("click", () => {state.jobsPage += 1; renderJobs();});
  for (const key of ["location", "source", "remote", "sponsor", "freshness"]) {
    const control = byId(key + "-filter");
    control.addEventListener(key === "location" || key === "source" ? "input" : "change", () => {
      state.boardFilters[key] = control.value;
      state.jobsPage = 0;
      renderJobs();
    });
  }
  byId("clear-board-filters").addEventListener("click", () => {
    state.search = ""; state.filter = "all"; state.jobsPage = 0;
    byId("search").value = ""; byId("filter").value = "all";
    for (const key of ["location", "source", "remote", "sponsor", "freshness"]) {
      state.boardFilters[key] = key === "location" || key === "source" ? "" : "all";
      byId(key + "-filter").value = state.boardFilters[key];
    }
    renderJobs();
  });
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
