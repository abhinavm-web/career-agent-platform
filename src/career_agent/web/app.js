"use strict";

/* Local-first review workspace. Dynamic job data is inserted only with textContent,
 * never innerHTML. No third-party scripts, telemetry or application submission.
 */
(function () {
  const root = document.getElementById("app");
  const token = root.dataset.session;
  const state = {
    jobs: [], overview: {}, settings: {}, reviews: [], selectedId: null, detailTab: "overview", studioOpen: false,
    packet: null, packetRequestSequence: 0, filter: "all", search: "", sort: "score", screen: "home",
    workspace: null, editingFactId: null,
    crmSummary: null, crmDetails: null, crmJobId: null, crmRequestSeq: 0, jobsPage: 0, refreshSequence: 0, boardTruncated: false,
    boardFilters: {location: "", source: "", remote: "all", sponsor: "all", freshness: "all"}
  };
  let freshnessTimer = null;
  let selectedAvailabilityUpdater = null;
  const availabilityInteractions = new Map();
  let workspaceMutationBusy = false;
  let workspaceLoadSequence = 0;
  let profileNavigationSequence = 0;

  const byId = (id) => document.getElementById(id);
  const modelInfo = (result) => ` Configured model: ${result.model}. Provider-reported model: ${result.reported_model || "not available"} (unverified).`;
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
      const message = response.status === 403 && data.detail === "Session token required"
        ? "Your local session expired after the server restarted. Copy any unsaved changes, refresh this page, then try again."
        : data.detail || "Request failed";
      const error = new Error(message);
      error.status = response.status;
      throw error;
    }
    return data;
  }

  const applicationAnswers = window.applicationAnswers({api, node, button});
  byId("application-answer-bank").append(applicationAnswers());

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
    const screens = {home: "Home", settings: "Settings & data", jobs: "Opportunity board", activity: "Review history", profile: "Career profile", tracker: "Application tracker", h1b: "H-1B research", automation: "Saved searches", ai: "AI analysis"};
    for (const name of Object.keys(screens)) {
      byId(name + "-screen").hidden = screen !== name;
      byId("nav-" + name).classList.toggle("active", screen === name);
      if (screen === name) byId("nav-" + name).setAttribute("aria-current", "page");
      else byId("nav-" + name).removeAttribute("aria-current");
    }
    byId("section-name").textContent = screens[screen];
    window.scrollTo?.(0, 0);
    const heading = byId(screen + "-screen").querySelector?.("h2");
    if (heading) { heading.setAttribute("tabindex", "-1"); heading.focus(); }
  }

  function renderMetrics() {
    byId("count-jobs").textContent = state.overview.total_jobs ?? 0;
    byId("count-staged").textContent = state.overview.staged ?? 0;
    byId("count-submitted").textContent = state.overview.submitted_recorded ?? 0;
    byId("count-reviews").textContent = state.overview.review_events_recent ?? 0;
    const s = state.settings;
    const el = byId("configuration-status");
    const targets = s.target_titles_configured !== false, skills = s.skills_configured !== false;
    const ready = s.profile_configured && s.facts_configured && targets && skills;
    el.textContent = ready ? "Candidate profile configured" : "Profile setup incomplete";
    el.classList.toggle("warn", !ready);
    const hasJobs = (state.overview.total_jobs ?? 0) > 0;
    byId("profile-prompt").hidden = ready && hasJobs;
    byId("setup-progress").textContent = (s.profile_configured ? "Profile name saved" : "Add your name") +
      " · " + (targets ? "Target roles saved" : "Add target roles for matching") +
      " · " + (skills ? "Skills saved" : "Add skills for matching") +
      " · " + (s.facts_configured ? "Career facts verified" : "Verify a career fact for AI analysis and drafts") + ".";
    byId("setup-jobs-progress").textContent = hasJobs ? "A saved opportunity is available to review." :
      "Find or add a first opportunity. External searches require your consent.";
    byId("open-profile").hidden = ready;
    byId("setup-find-jobs").hidden = hasJobs;
    byId("setup-add-job").hidden = hasJobs;
    byId("home-jobs-count").textContent = state.overview.total_jobs ?? 0;
    byId("home-submitted-count").textContent = state.overview.submitted_recorded ?? 0;
    byId("home-next-title").textContent = ready ? (hasJobs ? "Review your opportunities" : "Find your first opportunity") : "Build your career profile";
    byId("home-next-description").textContent = ready ? (hasJobs ? "Compare saved roles and prepare your next application." : "Search job sources or add a role you found.") : "Add your goals and verify your experience to prepare application drafts.";
    byId("home-next").textContent = ready ? (hasJobs ? "Review saved roles" : "Find opportunities") : "Open career profile";
    byId("home-next").dataset.destination = ready ? "jobs" : "profile";
    const recommendations = byId("home-recommendations");
    recommendations.replaceChildren();
    const roles = state.jobs.filter((job) => !job.archive && job.application_stage !== "submitted");
    if (ready) roles.sort((a, b) => (b.fit_score ?? -1) - (a.fit_score ?? -1));
    for (const job of roles.slice(0, 3)) {
      const item = button("", "home-followup", () => { showScreen("jobs"); selectJob(job.id); });
      item.append(node("strong", "", job.title + " · " + job.company), node("small", "", ready && job.fit_score != null ? (!job.description?.trim() ? "Incomplete keyword score " : "Keyword score ") + job.fit_score + "/100 · review fit" : "Saved role · review details"));
      recommendations.append(item);
    }
    if (!roles.length) recommendations.append(node("p", "muted", "No unsubmitted roles to review. Search job sources or add an opportunity to begin."));
  }

  function postingDeadline(value) {
    if (typeof value !== "string" || !/^\d{4}-\d\d-\d\dT\d\d:\d\d(?::\d\d(?:\.\d+)?)?(?:Z|[+-]\d\d:\d\d)$/i.test(value)) return NaN;
    return Date.parse(value);
  }

  function postingObservation(job) {
    const observed = job.live_check;
    if (!observed) return {kind: "unknown", label: "Not checked", detail: "Availability has not been checked."};
    const timestamp = Date.parse(observed.checked_at);
    const age = Date.now() - timestamp;
    if (!Number.isFinite(timestamp) || age < 0 || age > 86400000 || observed.stale) {
      return {kind: "stale", label: "Check is stale", detail: "The previous observation is stale or has an invalid date; current availability is unknown."};
    }
    if (observed.availability === "listed" && observed.application_deadline != null) {
      const deadline = postingDeadline(observed.application_deadline);
      if (!Number.isFinite(deadline)) return {kind: "unknown", label: "Availability unknown", detail: "The recorded application deadline is invalid or lacks a time zone; verify the posting directly."};
      if (deadline <= Date.now()) return {kind: "expired", label: "Deadline passed", detail: "The provider-reported deadline has passed since the check. Current availability is unknown; verify the posting directly."};
    }
    if (observed.availability === "page_reachable") return {kind: "unknown", label: "Page reachable · vacancy unverified", detail: observed.reason};
    if (observed.availability === "listed") return {kind: "listed", label: "Listed at check", detail: observed.reason};
    if (observed.availability === "expired") return {kind: "expired", label: "Deadline passed", detail: observed.reason};
    return {kind: "unknown", label: "Availability unknown", detail: observed.reason || "The check was inconclusive."};
  }

  function hasReviewableSourceText(job) {
    return job.application_stage !== "submitted" &&
      job.discovery_observations?.some((row) => Boolean(row.candidate_observed_at) &&
        (row.candidate_differs === true || (row.candidate_differs === undefined && !job.description?.trim())));
  }

  function activeCheckText(job) {
    return postingObservation(job).label + (job.live_check?.checked_at ? " · " + job.live_check.checked_at : "");
  }

  function decisionSummary(job, compact) {
    const summary = job.decision_summary || {};
    const container = node("div", compact ? "board-decision-compact" : "evidence-summary-grid");
    // Cards keep only the two triage signals visible; the remaining fields stay
    // readable by assistive technology and appear in full in the detail pane.
    const cardPrimary = ["Verified evidence", "Requirement to verify"];
    function field(label, value, note) {
      const row = node("div", compact ? "board-decision-field" + (cardPrimary.includes(label) ? "" : " board-decision-secondary") : "evidence-tile");
      const content = node("strong", "", value);
      row.append(node("small", "", label + " "), content);
      if (note) row.append(node("p", "", note));
      container.append(row);
      return content;
    }
    const fact = summary.verified_fact;
    field("Verified evidence", fact?.statement || "No verified fact linked",
      fact && [fact.section, fact.id].join(" · "));
    field("Match reason", fact ? "Shared posting terms: " + fact.matched_terms.join(", ")
      : job.fit_reasons?.[0] || "Unknown");
    const gap = summary.requirement_to_verify;
    field("Requirement to verify", gap ? gap.quote : "Unknown · no classified requirement",
      gap && (gap.category + (gap.term_link ? " · term overlap only" : " · no verified fact linked")));
    const today = new Date().toISOString().slice(0, 10);
    const dates = (job.discovery_observations || []).map(row => {
      const date = row.reported_posted_date;
      const reported = date && ["reported", "partial"].includes(row.posted_date_status);
      return row.source + " · " + (reported ? date + (row.posted_date_status === "partial" ? " (partial)" : "") +
        (date > today ? " (future date)" : "") : row.posted_date_status === "conflicting" ? "conflicting dates" : "unknown");
    });
    field("Source-reported posting date", dates.join(" / ") || "Unknown");
    const check = field("Active check", activeCheckText(job));
    const status = job.sponsorship_status === "historical_only" ? "Posting policy unknown" : sponsorshipLabel(job.sponsorship_status)[0];
    field("Posting sponsorship", status, summary.posting_quotes?.length
      ? summary.posting_quotes.map(quote => "“" + quote + "”").join(" ") : "No recognized wording available");
    const history = job.imported_sponsorship_history;
    const filings = [];
    function filing(row, basis) {
      const period = row.first_year === row.last_year ? "FY " + row.first_year : "FY " + row.first_year + "–" + row.last_year;
      return [row.employer, row.source, period, basis].join(" · ");
    }
    for (const row of history?.filings || []) filings.push(filing(row, history.match === "candidate_association"
      ? "candidate-linked entity (unverified)" : "exact employer-name match"));
    for (const row of summary.csv_filings || []) filings.push(filing(row, "user-supplied record, exact employer-name match"));
    field("Historical filing entity", filings.join(" / ") || "Unknown · No linked records",
      filings.length ? "Historical filings do not establish sponsorship for this role." : "");
    return {container, check};
  }

  function filteredJobs() {
    const needle = state.search.trim().toLocaleLowerCase();
    const filters = state.boardFilters;
    const jobs = state.jobs.filter((job) => {
      if (Boolean(job.archive) !== (state.filter === "archived")) return false;
      const searchable = [job.company, job.title, job.location, job.description].join(" ").toLocaleLowerCase();
      if (needle && !searchable.includes(needle)) return false;
      if (filters.location && !(job.location || "").toLocaleLowerCase().includes(filters.location.trim().toLocaleLowerCase())) return false;
      if (filters.source && !(job.source || "").toLocaleLowerCase().includes(filters.source.trim().toLocaleLowerCase())) return false;
      if (filters.remote === "remote" && job.remote !== true) return false;
      if (filters.sponsor !== "all" && job.sponsorship_status !== filters.sponsor) return false;
      if (filters.freshness !== "all" && postingObservation(job).kind !== filters.freshness) return false;
      if (state.filter === "priority") return Boolean(job.description?.trim()) && job.fit_score !== null && job.fit_score >= 70;
      if (state.filter === "sponsorship") return job.sponsorship_status === "posting_claims_sponsorship" || job.sponsorship_status === "historical_only";
      if (state.filter === "imported-history") return Boolean(job.imported_sponsorship_history?.sources?.length);
      if (state.filter === "missing-description") return !job.description?.trim();
      if (state.filter === "source-text-review") return hasReviewableSourceText(job);
      if (state.filter === "review") return job.packet_available && !job.review_decision;
      if (state.filter === "approved") return job.review_decision === "approved_for_manual_submission";
      if (state.filter === "submitted") return job.application_stage === "submitted";
      return true;
    });
    // The API loads jobs newest first; stable sorting preserves that order on ties.
    if (state.sort === "score") jobs.sort((a, b) => Number(Boolean(b.description?.trim())) - Number(Boolean(a.description?.trim())) || (b.fit_score ?? -1) - (a.fit_score ?? -1));
    return jobs;
  }

  function renderJobs() {
    if (freshnessTimer !== null) clearTimeout(freshnessTimer);
    freshnessTimer = null;
    const now = Date.now();
    const expirations = state.jobs.map((job) => {
      const observation = job.live_check;
      const timestamp = observation && Date.parse(observation.checked_at);
      if (!observation || observation.stale || !Number.isFinite(timestamp) || timestamp > now || now - timestamp > 86400000) return null;
      const deadline = observation.availability === "listed" ? postingDeadline(observation.application_deadline) : NaN;
      return Math.min(timestamp + 86400001, Number.isFinite(deadline) && deadline > now ? deadline : Infinity);
    }).filter((value) => value !== null);
    if (expirations.length) {
      freshnessTimer = setTimeout(() => {
        freshnessTimer = null;
        renderJobs();
        if (selectedAvailabilityUpdater) selectedAvailabilityUpdater();
      }, Math.max(1, expirations.reduce((earliest, value) => Math.min(earliest, value), Infinity) - now));
    }
    const jobs = filteredJobs();
    updateSelectionScope(jobs);
    const truncated = state.boardTruncated || (state.overview?.saved_jobs ?? state.overview?.total_jobs ?? 0) > state.jobs.length;
    byId("board-limit").hidden = !truncated;
    byId("board-limit").textContent = state.boardTruncated
      ? "The board reached its loading limit. Filters only search the " + state.jobs.length + " loaded opportunities; older jobs are not included."
      : truncated ? "The board may have changed while loading. Refresh to include newly added opportunities." : "";
    byId("visible-count").textContent = String(jobs.length) + (jobs.length === 1 ? " opportunity" : " opportunities");
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
      item.setAttribute("aria-label", "View " + job.title + " at " + job.company +
        (job.delivery ? ", " + deliveryLabel(job.delivery) : "") +
        (job.fit_score !== null && !job.description?.trim() ? "; keyword score incomplete, description missing" : ""));
      item.setAttribute("aria-pressed", String(state.selectedId === job.id));
      const top = node("div", "job-item-top");
      const left = node("div");
      left.append(node("h3", "", job.title), node("small", "", job.company + " · " + (job.location || "Location unspecified")));
      const mark = node("div", "company-letter", (job.company || "?").charAt(0).toUpperCase());
      top.append(left, mark);
      const lower = node("div", "job-item-bottom");
      const signal = sponsorshipLabel(job.sponsorship_status);
      lower.append(chip(signal[0], signal[1]));
      if (job.imported_sponsorship_history?.sources?.length) lower.append(chip("Imported employer history", "amber"));
      if (["conflicting", "ambiguous"].includes(job.remote_evidence?.status)) lower.append(chip("Remote details need review", "amber"));
      if (job.constraint_evidence?.some((row) => row.status === "flagged")) lower.append(chip("Constraint flagged", "amber"));
      else if (job.constraint_evidence?.some((row) => row.status === "unknown")) lower.append(chip("Constraint unknown", "amber"));
      if (job.source) lower.append(chip("Source: " + job.source));
      if (!job.description?.trim()) lower.append(chip("Description missing", "amber"));
      if (job.discovery_observations?.some((row) => row.description_comparison === "differs")) lower.append(chip("Source text differs", "amber"));
      if (hasReviewableSourceText(job)) lower.append(chip("Source text to review", "amber"));
      const observation = postingObservation(job);
      lower.append(chip(observation.label, observation.kind === "listed" ? "teal" : observation.kind === "expired" ? "red" : ""));
      if (job.packet_available) lower.append(chip(
        job.packet_stale ? "Draft outdated" : (job.review_decision ? "Reviewed" : "Draft ready"),
        job.review_decision && !job.packet_stale ? "teal" : "amber"
      ));
      if (job.delivery) lower.append(chip(deliveryLabel(job.delivery), job.delivery.state === "confirmed" ? "teal" : "amber"));
      else if (job.application_stage === "submitted") lower.append(chip("Submitted", "teal"));
      if (job.archive) lower.append(chip("Archived", ""));
      if (job.fit_score !== null) lower.append(node("span", "score", "Keyword " + (!job.description?.trim() ? "Incomplete " : "") + String(job.fit_score) + "/100"));
      const summary = decisionSummary(job, true).container;
      summary.id = "board-decision-" + job.id;
      item.setAttribute("aria-describedby", summary.id);
      item.append(top, lower, summary);
      list.append(item);
    }
  }

  function updateSelectionScope(jobs = filteredJobs()) {
    const warning = byId("selection-scope-warning");
    if (!warning) return;
    warning.hidden = !state.selectedId || jobs.some(job => job.id === state.selectedId);
  }

  async function selectJob(id) {
    const compact = window.matchMedia?.("(max-width: 1050px)").matches;
    const board = byId("jobs-screen");
    if (compact && state.selectedId === id && board.classList.contains("compact-list")) {
      board.classList.toggle("compact-list", false);
      board.classList.toggle("compact-detail", true);
      byId("detail-panel").focus?.();
      return;
    }
    if (state.selectedId !== id) {
      const editor = byId("detail-panel").querySelector?.("#job-description-editor");
      const description = editor?.querySelector?.("textarea");
      const source = editor?.querySelectorAll?.("textarea")?.[1];
      const capture = editor?.querySelector?.("#description-capture-input");
      if (editor?.open && description && (description.value !== description.defaultValue || source?.value || capture?.value) &&
          !window.confirm("Discard the unsaved job-description correction?")) return;
      const revisionEditor = byId("detail-panel").querySelector?.(".document-revision-studio");
      if ((revisionEditor?.dataset.dirty === "true" || revisionEditor?.querySelector?.(".experience-questions")?.dataset.dirty === "true") && !window.confirm("Discard unsent revision instructions or experience answers? Saved facts and revisions remain available.")) return;
      const picker = byId("detail-panel").querySelector?.(".tailoring-picker");
      if (picker?.dataset.dirty === "true" && !window.confirm("Discard your unfinished fact selection and ordering for this job?")) return;
      const inline = byId("detail-panel").querySelector?.("#inline-tailoring");
      if (inline?.dataset.pending === "true" && !window.confirm("An AI request is still running. Switching jobs discards its displayed result; the request may still complete. Continue?")) return;
      if (inline?.dataset.suggestions === "true" && !window.confirm("Switching jobs discards the displayed AI suggestions. Any wording already saved for review remains in your profile. Continue?")) return;
    }
    if (state.selectedId !== id) { state.detailTab = "overview"; state.studioOpen = false; }
    state.selectedId = id;
    board.classList.toggle("compact-list", false);
    board.classList.toggle("compact-detail", true);
    state.packet = null;
    renderJobs();
    renderDetail();
    if (state.screen === "jobs" && window.matchMedia?.("(max-width: 1050px)").matches) {
      const detail = byId("detail-panel");
      detail.setAttribute("tabindex", "-1");
      detail.scrollIntoView?.({block: "start"});
      detail.focus?.({preventScroll: true});
    }
    const initialWorkspaceRevision = state.workspace?.revision ?? null;
    const initialDescriptionRevision = state.jobs.find((job) => job.id === id)?.description_revision ?? 0;
    await loadPacket(id);
    if (state.selectedId === id) {
      const comparison = byId("detail-panel").querySelector?.("#fit-evidence");
      const preserve = comparison?.dataset.started === "true" &&
        (state.workspace?.revision ?? null) === initialWorkspaceRevision &&
        (state.jobs.find((job) => job.id === id)?.description_revision ?? 0) === initialDescriptionRevision;
      renderDetailPreservingEditor();
      if (preserve) byId("detail-panel").querySelector?.("#fit-evidence")?.replaceWith(comparison);
    }
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

  function liveApplication(job, destination) {
    const form = node("form", "form-stack"), fields = {};
    form.append(node("h4", "", "Submit with Career Agent"),
      node("p", "muted", job.title + " · " + job.company + " · " + destination));
    for (const [key, label, type] of [["name", "Full name", "text"], ["email", "Contact email", "email"],
      ["phone", "Contact phone", "tel"], ["resume", "Reviewed résumé PDF (up to 2 MiB)", "file"]]) {
      const wrapper = node("label", "", label), input = node("input");
      input.type = type; input.required = true;
      if (key === "resume") input.accept = "application/pdf";
      if (key === "name") input.value = state.workspace?.profile?.name || "";
      wrapper.append(input); form.append(wrapper); fields[key] = input;
    }
    const send = node("button", "button button-primary", "Approve and send to " + job.company);
    send.type = "submit";
    const status = node("p", "muted"); status.setAttribute("role", "status");
    form.append(send, status);
    form.addEventListener("submit", async event => {
      event.preventDefault();
      if (send.disabled) return;
      const file = fields.resume.files[0];
      if (!file || file.size > 2 * 1024 * 1024) { status.textContent = "Choose a PDF up to 2 MiB."; return; }
      if (!window.confirm("Send " + file.name + " and the displayed contact information to " + job.company + " for " + job.title + "?")) return;
      const data = {name: fields.name.value, email: fields.email.value, phone: fields.phone.value,
        destination, approved: true, filename: file.name};
      send.disabled = true;
      status.textContent = "Career Agent is opening the employer’s form. Complete any verification in the Chromium window.";
      try {
        data.content = await new Promise((resolve, reject) => {
          const reader = new FileReader(); reader.onload = () => resolve(reader.result.split(",")[1]);
          reader.onerror = () => reject(new Error("Unable to read the PDF.")); reader.readAsDataURL(file);
        });
        const result = await api("/api/jobs/" + job.id + "/lever-submit", "POST", data);
        status.textContent = result.status === "confirmed" ? "Employer confirmation received. Application recorded." : result.detail;
        notice(status.textContent);
      } catch (error) { notice(error.message, true); }
      await refresh(state.selectedId);
    });
    return form;
  }

  function section(heading) {
    const box = node("section", "detail-section");
    box.append(node("h4", "", heading));
    return box;
  }

  function deliveryLabel(delivery) {
    return {needs_user_action: "Needs user action", failed_before_sending: "Failed before sending",
      awaiting_confirmation: "Awaiting confirmation", confirmed: "Delivery confirmed",
      verified_not_received: "Employer verified non-receipt", duplicate: "Dispatch on another saved posting"}[delivery.state];
  }

  function deliveryPanel(jobId, delivery) {
    const panel = section("Application delivery");
    panel.append(node("strong", "", deliveryLabel(delivery)));
    if (delivery.state === "duplicate") {
      panel.append(button("View original dispatch in tracker", "button", async () => {
        showScreen("tracker"); await loadCRM();
        if (state.screen === "tracker") await selectCRMJob(delivery.owner_id);
      }));
      return panel;
    }
    if (delivery.updated_at) panel.append(node("p", "muted", "Recorded " + delivery.updated_at + " UTC"));
    if (delivery.dispatched && !delivery.has_pdf) {
      panel.append(node("p", "muted", "The historical dispatch retained a PDF fingerprint. The file and approved answers are unavailable."));
    }
    const base = "/api/jobs/" + jobId + "/delivery/";
    function download(label, artifact) {
      return button(label, "button", async () => {
        try {
          const response = await fetch(base + artifact, {headers: {"X-Session-Token": token}, cache: "no-store", credentials: "same-origin"});
          if (!response.ok) throw new Error("Unable to download this private delivery artifact.");
          const url = URL.createObjectURL(await response.blob());
          const link = node("a"); link.href = url; link.download = jobId + "-" + artifact;
          document.body.append(link); link.click(); link.remove();
          setTimeout(() => URL.revokeObjectURL(url), 1000);
        } catch (error) { notice(error.message, true); }
      });
    }
    const downloads = node("div", "button-row");
    downloads.append(download("Download private delivery record", "record.json"));
    if (delivery.has_pdf) downloads.append(download("Download dispatched résumé PDF", "resume.pdf"));
    panel.append(downloads);
    if (!["awaiting_confirmation", "verified_not_received"].includes(delivery.state)) return panel;

    const form = node("form", "form-stack");
    form.append(node("p", "", "Check the employer portal, confirmation email, or recruiting team. Record only an outcome you verified. An empty inbox alone leaves delivery unresolved."));
    const label = node("label", "field", "Verified employer outcome");
    const choice = node("select");
    for (const [value, text] of [["confirmed", "Received with confirmation reference"],
      ["without_reference", "Received, employer supplied no reference"], ["verified_not_received", "Employer verified it was not received"]]) {
      const option = node("option", "", text); option.value = value; choice.append(option);
    }
    label.append(choice);
    const description = node("span", "", "Employer confirmation reference");
    const evidenceLabel = node("label", "field"), input = node("input");
    input.required = true; input.minLength = 4; input.maxLength = 200;
    evidenceLabel.append(description, input);
    const consent = node("label", "check-label"), check = node("input"); check.type = "checkbox"; check.required = true;
    consent.append(check, document.createTextNode("I verified this outcome with the employer for this posting."));
    choice.addEventListener("change", () => {
      description.textContent = choice.value === "confirmed" ? "Employer confirmation reference" : "How the employer verified this outcome";
      input.maxLength = choice.value === "confirmed" ? 200 : 500; input.value = ""; check.checked = false;
    });
    const save = node("button", "button", "Record verified outcome locally"); save.type = "submit";
    form.append(label, evidenceLabel, consent, save); panel.append(form);
    form.addEventListener("submit", async event => {
      event.preventDefault();
      if (save.disabled || !check.checked) return;
      const payload = {attest_verified: true, state: choice.value === "verified_not_received" ? choice.value : "confirmed"};
      if (choice.value === "confirmed") payload.receipt = input.value;
      else { payload.note = input.value; payload.verified_without_reference = choice.value === "without_reference"; }
      save.disabled = true;
      try {
        await api(base + "reconcile", "POST", payload);
        notice("Verified delivery outcome recorded locally.");
        await refresh(state.selectedId);
      } catch (error) { notice(error.message, true); }
      finally { save.disabled = false; }
    });
    return panel;
  }

  function documentRevisionStudio(job) {
    const panel = node("details", "inline-tailoring document-revision-studio");
    panel.dataset.jobId = job.id;
    panel.append(node("summary", "", "AI resume and cover letter"), node("p", "muted",
      "Ask your chosen model to revise both documents for this opportunity. Revisions are saved separately from verified facts and application packets. Citations do not prove accuracy: review every claim before export."));
    const load = button("Load models and saved revisions", "button");
    const modelLabel = node("label", "field", "Revision model");
    const model = node("select"); model.setAttribute("aria-label", "Revision model"); modelLabel.append(model);
    const destination = node("p", "muted");
    const instructionLabel = node("label", "field", "Revision instructions");
    const instructions = node("textarea"); instructions.maxLength = 2000; instructions.rows = 3;
    instructions.placeholder = "Emphasize relevant leadership experience and keep the cover letter concise.";
    instructionLabel.append(instructions);
    const freshLabel = node("label", "check-label"), fresh = node("input"); fresh.type = "checkbox"; fresh.checked = true;
    freshLabel.append(fresh, document.createTextNode(" Build a new resume from all verified experience, without reusing the previous draft."));
    const interview = button("Find experience to add", "button"); interview.disabled = true;
    const questions = node("div", "experience-questions");
    const consentLabel = node("label", "check-label");
    const consent = node("input"); consent.type = "checkbox";
    consentLabel.append(consent, document.createTextNode(" I consent to sharing the posting, verified facts, instructions and drafts with this model for generation, validation and any requested repair. These workflows can make multiple model requests and cloud charges may apply."));
    const run = button("Generate resume and cover letter", "button button-primary"); run.disabled = true;
    const status = node("p", "muted"); status.setAttribute("role", "status");
    const results = node("div");
    let routes = [], parentId = 0, busy = false, revisionActions = [], reviewActions = [];
    const current = () => panel.isConnected && state.selectedId === job.id;
    const updateRoute = () => {
      consent.checked = false;
      const route = routes.find(r => r.route === model.value);
      destination.textContent = route ? route.provider + " · " + route.model + " · " + route.destination : "No models configured. Open AI tools to configure a provider.";
      run.disabled = busy || !route?.route_fingerprint || job.application_stage === "submitted";
      interview.disabled = run.disabled;
      revisionActions.forEach(action => { action.disabled = run.disabled; });
    };
    model.addEventListener("change", updateRoute);
    instructions.addEventListener("input", () => { panel.dataset.dirty = "true"; });
    function renderRevisions(history) {
      results.replaceChildren(); revisionActions = []; reviewActions = []; parentId = history[0]?.id || 0;
      if (!history.length) results.append(node("p", "muted", "No AI document revisions saved for this opportunity."));
      history.forEach((revision, index) => {
        const card = node("details", "ai-document-version"); card.open = index === 0;
        card.append(node("summary", "", "Revision " + revision.id + " · " + revision.model + " · " + (revision.stale ? "Source changed" : revision.reviewed_at ? "Reviewed" : "Needs review")),
          node("p", "muted", revision.created_at + " · " + revision.provider + (revision.reported_model ? " · Reported model: " + revision.reported_model : "")));
        const passages = node("div", "ai-document-passages");
        for (const kind of ["resume", "cover_letter"]) {
          passages.append(node("h4", "", kind === "resume" ? "AI resume revision" : "AI cover letter revision"));
          for (const row of revision.document[kind]) {
            const passage = node("section", "inline-suggestion");
            if (kind === "resume" && row.section) passage.append(node("strong", "", row.section));
            passage.append(node("p", "description", row.text));
            const source = node("details"); source.append(node("summary", "", "Compare cited facts"));
            for (const id of row.fact_ids) source.append(node("p", "", id + " · " + revision.sources[id]));
            passage.append(source);
            for (const warning of row.warnings || []) passage.append(node("p", "stale-warning", warning));
            passages.append(passage);
          }
        }
        const validation = revision.validation;
        const currentValidation = validation?.version === 2 && validation.support_audit?.complete === true;
        const canReview = currentValidation && validation.status === "completed" && validation.complete === true && validation.export_allowed === true && validation.claims?.complete === true;
        const validationBox = node("section", "inline-suggestion resume-validation");
        validationBox.append(node("h4", "", "Resume validation against job requirements"), node("p", "muted",
          "Checks this generated resume against the full saved posting and checks both documents against verified facts. Results are evidence judgments, not an ATS score, hiring prediction or proof that an undocumented skill is absent."));
        const validationStatus = node("p"); validationStatus.setAttribute("role", "status"); validationBox.append(validationStatus);
        if (!validation) {
          validationStatus.textContent = "Not validated. Validate this revision before confirming export.";
        } else {
          const complete = currentValidation && validation.status === "completed" && validation.complete === true;
          validationStatus.textContent = validation.status === "completed" && !currentValidation
            ? "Revalidation required: this saved result predates or lacks the additional match-support check. Run validation again before export."
            : complete
              ? (canReview ? "Validation completed. Review factual accuracy and remaining job requirements before export." : "Validation completed with claim issues. Repair unsupported wording before confirming export.")
              : "Validation incomplete or failed. Export remains unavailable; retry validation after reviewing the notice.";
          if (currentValidation) {
            const checkedCount = Number.isInteger(validation.support_audit.checked) && validation.support_audit.checked >= 0 ? String(validation.support_audit.checked) : "Count unavailable;";
            validationBox.append(node("p", "match-support-audit", "Match-support audit: " + checkedCount + " match and alternative-requirement assessments independently checked against the resume. This additional check is advisory, not a guarantee of qualification."));
          }
          if (validation.created_at) validationBox.append(node("p", "muted", "Validated " + validation.created_at + (validation.model ? " · " + validation.model : "")));
          for (const notice of validation.notices || []) validationBox.append(node("p", "stale-warning", notice));
          const labels = {supported: "Supported", partial: "Partially supported", not_demonstrated: "Not demonstrated", needs_confirmation: "Condition unresolved", context: "Posting context"};
          const requirements = validation.requirements || [];
          const counts = requirements.reduce((all, row) => { all[row.status] = (all[row.status] || 0) + 1; return all; }, {});
          validationBox.append(node("p", "", requirements.length + " posting items assessed. " + Object.entries(counts).map(([key, count]) => (labels[key] || key) + ": " + count).join(" · ")));
          const requirementList = node("details", "full-requirement-checklist");
          requirementList.append(node("summary", "", "Full posting checklist · " + requirements.length + " items"));
          for (const requirement of requirements) {
            const item = node("details", "requirement-validation");
            item.open = !["supported", "context"].includes(requirement.status);
            item.append(node("summary", "", (labels[requirement.status] || requirement.status) + " · " + (requirement.category || "unspecified") + " · " + requirement.quote),
              node("p", "", requirement.reason));
            for (const passageIndex of requirement.resume_indices || []) {
              const passage = revision.document.resume[passageIndex];
              if (passage) item.append(node("p", "description", "Resume passage " + (passageIndex + 1) + ": " + passage.text));
            }
            if ((requirement.profile_fact_ids || []).length) {
              const omitted = node("details"); omitted.append(node("summary", "", "Relevant profile evidence to compare"));
              for (const id of requirement.profile_fact_ids) {
                if (revision.sources[id]) omitted.append(node("p", "", id + " · " + revision.sources[id]));
              }
              omitted.append(node("p", "muted", "A profile fact does not count as resume evidence unless this draft accurately includes it.")); item.append(omitted);
            }
            requirementList.append(item);
          }
          validationBox.append(requirementList);
          const issues = (validation.claims?.passages || []).filter(row => row.status !== "supported");
          if (issues.length) {
            validationBox.append(node("h4", "", "Claim support issues"));
            for (const issue of issues) {
              const passage = revision.document[issue.kind]?.[issue.index];
              const item = node("section", "inline-suggestion");
              item.append(node("strong", "", (issue.kind === "resume" ? "Resume" : "Cover letter") + " passage " + (issue.index + 1) + " · " + issue.status), node("p", "", issue.reason));
              if (passage) item.append(node("p", "description", passage.text));
              validationBox.append(item);
            }
          }
        }
        if (index === 0 && !revision.stale) {
          const validate = button("Validate resume against job", "button", () => runRevisionAction("validate", revision.id));
          validate.disabled = busy || !routes.some(r => r.route === model.value && r.route_fingerprint) || job.application_stage === "submitted";
          revisionActions.push(validate); validationBox.append(validate);
          const includedFactIds = new Set(revision.document.resume.flatMap(row => row.fact_ids));
          const omittedEvidence = (validation?.requirements || []).some(row => (row.profile_fact_ids || []).some(id => !includedFactIds.has(id)));
          if ((validation?.claims?.passages || []).some(row => row.status !== "supported") || omittedEvidence) {
            const repair = button("Repair wording and add verified evidence", "button", () => runRevisionAction("repair", revision.id));
            repair.disabled = validate.disabled; revisionActions.push(repair); validationBox.append(repair,
              node("p", "muted", "Creates a new revision with supported wording and relevant verified evidence omitted from this draft, then validates it. It does not invent experience or change your profile."));
          }
        }
        card.append(validationBox, passages);
        if (index === 0 && !revision.stale) {
          if (revision.reviewed_at && canReview) {
            for (const [kind, label] of [["resume", "resume"], ["cover_letter", "cover letter"]]) {
              for (const [format, formatLabel] of [["pdf", "PDF"], ["docx", "DOCX"], ["txt", "text"]]) {
                const title = "Download reviewed AI " + label + " " + formatLabel;
                const download = button(title, "button", async () => {
                  if (download.disabled) return;
                  download.disabled = true;
                  try {
                    const response = await fetch("/api/jobs/" + job.id + "/document-revisions/" + revision.id + "/" + kind + "." + format, {
                      headers: {"X-Session-Token": token}, cache: "no-store", credentials: "same-origin"});
                    if (!response.ok) { const error = await response.json().catch(() => ({})); throw new Error(error.detail || "Export failed"); }
                    const url = URL.createObjectURL(await response.blob());
                    const link = document.createElement("a"); link.href = url; link.download = kind + "-" + job.id + "-" + revision.id + "." + format;
                    document.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000);
                  } catch (error) { status.textContent = error.message; }
                  finally { download.disabled = false; }
                });
                card.append(download);
              }
            }
          } else {
            const label = node("label", "check-label"), checked = node("input"); checked.type = "checkbox";
            label.append(checked, document.createTextNode(" I reviewed both AI documents against the cited facts and confirm every claim is accurate."));
            const approve = button("Confirm AI revision for export", "button", async () => {
              if (!checked.checked || approve.disabled || busy) return;
              setBusy(true);
              try {
                const result = await api("/api/jobs/" + job.id + "/document-revisions/" + revision.id + "/review", "POST", {acknowledged: true});
                if (current()) renderRevisions(result.revisions);
              } catch (error) { if (current()) status.textContent = error.message; }
              finally { setBusy(false); updateRoute(); }
            });
            checked.disabled = busy || !canReview;
            approve.disabled = true; checked.addEventListener("change", () => { approve.disabled = busy || !canReview || !checked.checked; });
            reviewActions.push({checked, approve, canReview});
            card.append(label, approve);
          }
        }
        if (revision.stale) card.append(node("p", "stale-warning", "The profile or posting changed. Generate and review a new revision before export."));
        results.append(card);
      });
    }
    function setBusy(value) {
      busy = value; panel.dataset.pending = String(value);
      load.disabled = value; model.disabled = value; instructions.disabled = value; fresh.disabled = value; consent.disabled = value;
      run.disabled = value; interview.disabled = value; revisionActions.forEach(action => { action.disabled = value; });
      reviewActions.forEach(({checked, approve, canReview}) => { checked.disabled = value || !canReview; approve.disabled = value || !canReview || !checked.checked; });
    }
    async function validateSavedRevision(route, revisionId) {
      status.textContent = "Revision saved. Validating its claims and all job requirements with " + route.model + "…";
      const result = await api("/api/jobs/" + job.id + "/document-revisions/" + revisionId + "/validate", "POST", {
        route: route.route, route_fingerprint: route.route_fingerprint, acknowledged: true});
      if (!current()) return;
      renderRevisions(result.revisions);
      const validation = result.revisions[0]?.validation;
      status.textContent = validation?.version === 2 && validation.status === "completed" && validation.complete && validation.support_audit?.complete === true
        ? "Validation saved. Review the full requirement checklist and claim findings before export. Verified facts remain unchanged."
        : "Draft saved, but validation is incomplete. Review the validation notice and retry; export remains unavailable.";
    }
    async function runRevisionAction(action, revisionId) {
      const route = routes.find(r => r.route === model.value);
      if (busy || !current() || revisionId !== parentId) return;
      if (!consent.checked || !route?.route_fingerprint) { status.textContent = "Choose a model and confirm sharing before validation or repair."; return; }
      setBusy(true);
      try {
        if (action === "validate") await validateSavedRevision(route, revisionId);
        else {
          status.textContent = "Repairing wording, adding relevant verified evidence and validating a new revision…";
          const result = await api("/api/jobs/" + job.id + "/document-revisions/" + revisionId + "/repair", "POST", {
            route: route.route, route_fingerprint: route.route_fingerprint, acknowledged: true});
          if (!current()) return;
          renderRevisions(result.revisions);
          status.textContent = result.revisions[0]?.validation?.version === 2 && result.revisions[0].validation.status === "completed" && result.revisions[0].validation.complete && result.revisions[0].validation.support_audit?.complete === true
            ? "Repaired revision saved. Review its validation findings and wording before export. Verified facts remain unchanged."
            : "Repaired draft saved, but validation is incomplete. Review the notice and retry; export remains unavailable.";
        }
      } catch (error) { if (current()) status.textContent = error.message + " Saved drafts remain available; validation and candidate review are required before export."; }
      finally { setBusy(false); updateRoute(); }
    }
    load.addEventListener("click", async () => {
      if (busy) return;
      setBusy(true);
      try {
        const [available, saved] = await Promise.all([api("/api/ai/routes"), api("/api/jobs/" + job.id + "/document-revisions")]);
        if (!current()) return;
        routes = available.routes; model.replaceChildren();
        for (const route of routes) { const option = node("option", "", route.route + " · " + route.model); option.value = route.route; model.append(option); }
        renderRevisions(saved.revisions); status.textContent = "Choose a model, add revision instructions, and confirm sharing.";
      } catch (error) { if (current()) status.textContent = error.message; }
      finally { setBusy(false); updateRoute(); }
    });
    run.addEventListener("click", async () => {
      const route = routes.find(r => r.route === model.value);
      if (busy) return;
      if (!consent.checked || !route?.route_fingerprint) { status.textContent = "Choose a model and confirm sharing before generation and validation."; return; }
      setBusy(true);
      status.textContent = "Generating a saved revision with " + route.model + "…";
      let generated = false;
      try {
        const result = await api("/api/jobs/" + job.id + "/document-revisions", "POST", {route: route.route,
          route_fingerprint: route.route_fingerprint, instructions: instructions.value, parent_id: parentId, fresh: fresh.checked, acknowledged: true});
        if (!current()) return;
        generated = true; panel.dataset.dirty = "false"; renderRevisions(result.revisions);
        await validateSavedRevision(route, result.revisions[0].id);
      } catch (error) { if (current()) status.textContent = (generated ? "Draft saved, but validation failed: " : "") + error.message; }
      finally { setBusy(false); updateRoute(); }
    });
    interview.addEventListener("click", async () => {
      const route = routes.find(r => r.route === model.value);
      if (busy || !consent.checked || !route?.route_fingerprint) return;
      if (questions.dataset.dirty === "true" && !window.confirm("Replace the interview and discard unsaved answers? Saved facts remain in your profile.")) return;
      busy = true; load.disabled = true; run.disabled = true; interview.disabled = true; model.disabled = true;
      status.textContent = "Finding posting requirements to discuss…";
      try {
        const result = await api("/api/jobs/" + job.id + "/ai-analysis", "POST", {route: route.route, route_fingerprint: route.route_fingerprint, acknowledged: true});
        if (!current()) return;
        questions.replaceChildren(); questions.dataset.dirty = "false";
        questions.append(node("h4", "", "Experience interview"), node("p", "muted", "These AI-selected excerpts may not cover the entire posting. Suggested fact links are unverified. Answer only from your real experience, or skip the question. Saving an answer does not verify or add it to your resume."));
        for (const requirement of result.requirements) {
          const card = node("details", "inline-suggestion"); card.open = requirement.fact_ids.length === 0;
          card.append(node("summary", "", requirement.quote), node("p", "", requirement.fact_ids.length ? "AI suggests existing evidence; review whether it actually supports this excerpt." : "No evidence linked by AI. This does not mean you lack the experience."), node("p", "", requirement.reason));
          for (const [id, statement] of Object.entries(requirement.facts)) card.append(node("p", "muted", id + " · " + statement));
          card.append(node("p", "", "Have you done relevant work that is missing from your profile? Describe what you personally did, where and when, and any result you can substantiate. Keep limitations and qualifications."));
          const answerLabel = node("label", "field", "Your experience"), answer = node("textarea"); answer.maxLength = 1000;
          answer.addEventListener("input", () => { questions.dataset.dirty = "true"; }); answerLabel.append(answer);
          const sectionLabel = node("label", "field", "Fact section"), section = node("select");
          for (const name of ["experience", "projects", "skills", "education", "certifications"]) { const option = node("option", "", name); option.value = name; section.append(option); } sectionLabel.append(section);
          const message = node("p", "muted"); message.setAttribute("role", "status");
          const save = button("Save answer as unverified fact", "button", async () => {
            if (save.disabled || !answer.value.trim()) return; save.disabled = true;
            try {
              const workspace = (await api("/api/workspace")).workspace;
              const saved = await api("/api/jobs/" + job.id + "/experience-answer", "POST", {expected_revision: workspace.revision,
                job_quote: requirement.quote, answer: answer.value, section: section.value, acknowledged: true});
              answer.disabled = true; section.disabled = true;
              questions.dataset.dirty = String(Array.from(questions.querySelectorAll("textarea")).some(field => !field.disabled && field.value.trim()));
              message.textContent = "Saved, not verified: " + saved.workspace.facts.find(f => f.id === saved.fact_id).statement;
              const confirmLabel = node("label", "check-label"), confirm = node("input"); confirm.type = "checkbox";
              confirmLabel.append(confirm, document.createTextNode(" I checked this saved statement and confirm it accurately describes my experience."));
              const verify = button("Verify this new fact", "button", async () => {
                if (!confirm.checked || verify.disabled) return; verify.disabled = true;
                try {
                  await api("/api/workspace/facts/" + saved.fact_id + "/verification", "POST", {expected_revision: saved.workspace.revision, verified: true, acknowledged: true});
                  message.textContent = "Fact verified. Generate a new resume to include it. Previous revisions may now be stale."; confirm.disabled = true;
                } catch (error) { message.textContent = error.message + " Review this saved fact in Career profile if the profile changed."; verify.disabled = false; }
              });
              verify.disabled = true; confirm.addEventListener("change", () => { verify.disabled = !confirm.checked; }); card.append(confirmLabel, verify);
            } catch (error) { message.textContent = error.message; save.disabled = false; }
          });
          card.append(answerLabel, sectionLabel, save, message); questions.append(card);
        }
        status.textContent = result.requirements.length ? "Answer and verify genuine additions, then build a new resume. Skipping an unsupported requirement is valid." : "No excerpts returned. Add real experience directly in Career profile.";
      } catch (error) { if (current()) status.textContent = error.message; }
      finally { busy = false; load.disabled = false; model.disabled = false; updateRoute(); }
    });
    panel.append(load, modelLabel, destination, instructionLabel, freshLabel, consentLabel, interview, questions, run, status, results);
    return panel;
  }

  function inlineTailoring(job) {
    const panel = node("details", "inline-tailoring");
    panel.id = "inline-tailoring"; panel.dataset.jobId = job.id;
    panel.dataset.descriptionRevision = String(job.description_revision ?? 0);
    panel.append(node("summary", "", "Compare AI wording with verified facts"), node("p", "muted",
      "With your consent, the saved posting and verified career facts are sent to the chosen provider. Cloud requests may incur charges; local servers may forward requests. Suggestions are unverified and never replace a fact or draft automatically."));
    const load = button("Load model routes", "button");
    const label = node("label", "block-label", "Model route");
    const select = node("select"); select.setAttribute("aria-label", "Inline model route"); label.append(select);
    const destination = node("p", "muted", "Load routes to review the destination.");
    const consentLabel = node("label", "check-label");
    const consent = node("input"); consent.type = "checkbox";
    consentLabel.append(consent, document.createTextNode(" I consent to sending this posting and my verified facts to the provider shown above."));
    const run = button("Suggest wording", "button button-primary"); run.disabled = true;
    const status = node("p", "muted"); status.setAttribute("role", "status");
    const results = node("div", "inline-tailoring-results");
    let routes = [], busy = false;
    const current = () => panel.isConnected && state.selectedId === job.id && state.screen === "jobs";
    const updateRoute = () => {
      consent.checked = false;
      const route = routes.find(item => item.route === select.value);
      destination.textContent = route ? route.provider + " · " + route.model + " · " + route.destination : "No route configured.";
      run.disabled = !route?.route_fingerprint;
    };
    select.addEventListener("change", () => { results.replaceChildren(); panel.dataset.suggestions = "false"; status.textContent = "Review the destination and confirm sharing."; updateRoute(); });
    load.addEventListener("click", async () => {
      if (busy) return;
      busy = true; load.disabled = true; run.disabled = true; consent.checked = false; results.replaceChildren(); panel.dataset.suggestions = "false";
      status.textContent = "Loading configured routes…";
      try {
        const result = await api("/api/ai/routes");
        if (!current()) return;
        routes = result.routes; select.replaceChildren();
        for (const route of routes) {
          const option = node("option", "", route.route + " (" + route.provider + ")");
          option.value = route.route; select.append(option);
        }
        updateRoute(); status.textContent = routes.length ? "Review the destination and confirm sharing." : "No routes configured. Open AI tools for setup.";
      } catch (error) { if (current()) status.textContent = error.message; }
      finally { busy = false; load.disabled = false; }
    });
    run.addEventListener("click", async () => {
      const route = routes.find(item => item.route === select.value);
      if (busy || !consent.checked || !route?.route_fingerprint) return;
      busy = true; load.disabled = true; run.disabled = true; select.disabled = true; consent.disabled = true; panel.dataset.pending = "true";
      results.replaceChildren(); panel.dataset.suggestions = "false"; status.textContent = "Waiting for the selected provider…";
      try {
        const result = await api("/api/jobs/" + job.id + "/ai-tailoring-suggestions", "POST",
          {route: route.route, route_fingerprint: route.route_fingerprint, acknowledged: true});
        if (!current()) return;
        status.textContent = result.notice + modelInfo(result) + " Checked: " + result.observed_at + ".";
        if (!result.suggestions.length) results.append(node("p", "muted", "No wording suggestions returned."));
        panel.dataset.suggestions = String(result.suggestions.length > 0);
        for (const suggestion of result.suggestions) {
          const card = node("section", "inline-suggestion");
          const comparison = node("div", "inline-comparison");
          const source = node("div"), wording = node("div");
          source.append(node("strong", "", "Verified fact · " + suggestion.fact_id), node("p", "", suggestion.original_fact));
          wording.append(node("strong", "", "Unverified suggestion"), node("p", "", suggestion.wording));
          comparison.append(source, wording); card.append(comparison, node("p", "muted", "Posting excerpt: " + suggestion.job_quote));
          for (const warning of suggestion.review_warnings || []) card.append(node("p", "", "Review warning: " + warning));
          const stage = button("Save unverified wording for review", "button", async () => {
            if (stage.disabled || !current()) return;
            stage.disabled = true;
            try {
              const workspace = (await api("/api/workspace")).workspace;
              if (!current()) return;
              if (!workspace.revision) throw new Error("Create a career profile before saving a suggestion.");
              await api("/api/jobs/" + job.id + "/stage-ai-wording", "POST", {expected_revision: workspace.revision,
                fact_id: suggestion.fact_id, source_statement: suggestion.original_fact,
                job_quote: suggestion.job_quote, wording: suggestion.wording, acknowledged: true});
              if (!current()) return;
              stage.textContent = "Saved for review";
              status.textContent = "Saved as an unverified fact. Review and verify it in Career profile before drafting.";
            } catch (error) { if (current()) { status.textContent = error.message; stage.disabled = false; } }
          });
          card.append(stage); results.append(card);
        }
      } catch (error) { if (current()) status.textContent = error.message; }
      finally { busy = false; panel.dataset.pending = "false"; load.disabled = false; select.disabled = false; consent.disabled = false; consent.checked = false; run.disabled = !routes.find(item => item.route === select.value)?.route_fingerprint; }
    });
    panel.append(load, label, destination, consentLabel, run, status, results);
    return panel;
  }

  function resumeGapPanel(job) {
    const panel = section("Resume gap analysis");
    panel.id = "resume-gap";
    panel.dataset.jobId = job.id;
    panel.dataset.revision = String(job.description_revision ?? 0);
    const fileLabel = node("label", "block-label", "Import resume");
    const file = node("input"); file.type = "file"; file.accept = ".docx,.pdf,.txt"; fileLabel.append(file);
    const textLabel = node("label", "block-label", "Resume text for comparison");
    const input = node("textarea"); input.rows = 5; input.maxLength = 20000; textLabel.append(input);
    const routeLabel = node("label", "block-label", "Model");
    const select = node("select"); routeLabel.append(select);
    const load = button("Load comparison models", "button");
    const consentLabel = node("label", "check-label");
    const consent = node("input"); consent.type = "checkbox";
    consentLabel.append(consent, document.createTextNode(" Share the displayed resume and posting with the selected model. Longer postings use multiple requests and cloud charges may apply."));
    const run = button("Compare my resume", "button button-primary");
    const status = node("p", "muted"); status.setAttribute("role", "status");
    const results = node("div");
    let routes = [], busy = false;
    const route = () => routes.find(r => r.route === select.value);
    function sync() {
      for (const control of [file, input, load, select, consent]) control.disabled = busy;
      run.disabled = busy || !job.description?.trim() || !route()?.route_fingerprint || !consent.checked || !input.value?.trim();
    }
    function reset() {
      consent.checked = false; results.replaceChildren(); status.textContent = ""; sync();
    }
    input.addEventListener("input", reset); select.addEventListener("change", reset);
    consent.addEventListener("change", sync);
    file.addEventListener("change", async () => {
      const chosen = file.files[0];
      if (!chosen) return;
      reset(); input.value = ""; busy = true; sync();
      try {
        if (chosen.size > 2 * 1024 * 1024) throw new Error("Choose a file up to 2 MB.");
        const encoded = await new Promise((resolve, reject) => {
          const reader = new FileReader();
          reader.onload = () => resolve(reader.result.split(",")[1]);
          reader.onerror = () => reject(new Error("Unable to read the file."));
          reader.readAsDataURL(chosen);
        });
        const extracted = await api("/api/workspace/resume-extract", "POST", {filename: chosen.name, content: encoded});
        input.value = extracted.text; status.textContent = "Review the extracted text before sharing.";
      } catch (error) { status.textContent = error.message; }
      finally { busy = false; file.value = ""; sync(); }
    });
    load.addEventListener("click", async () => {
      reset(); busy = true; sync(); routes = []; select.replaceChildren();
      try {
        routes = (await api("/api/ai/routes")).routes;
        for (const r of routes) {
          const option = node("option", "", r.route + " · " + r.model + " · " + r.destination);
          option.value = r.route; select.append(option);
        }
        if (!routes.length) status.textContent = "Configure a model in AI tools.";
      } catch (error) { status.textContent = error.message; }
      finally { busy = false; sync(); }
    });
    run.addEventListener("click", async () => {
      if (run.disabled) return;
      busy = true; sync(); results.replaceChildren(); status.textContent = "Comparing your resume with the posting…";
      try {
        const response = await api("/api/jobs/" + job.id + "/resume-gap", "POST", {
          resume_text: input.value, route: select.value, route_fingerprint: route().route_fingerprint,
          acknowledged: true, description_revision: job.description_revision ?? 0});
        const labels = {partial: "Partial evidence", missing_evidence: "Evidence missing", clarify: "Needs clarification", covered: "Covered", context: "Posting context"};
        for (const [key, label] of Object.entries(labels)) {
          const rows = response.items.filter(row => row.status === key);
          if (!rows.length) continue;
          const group = node("details", "gap-group"); group.open = ["partial", "missing_evidence", "clarify"].includes(key);
          group.append(node("summary", "", label + " (" + rows.length + ")"));
          for (const row of rows) {
            const item = node("section", "fit-evidence-item");
            const comparison = node("div", "gap-passages");
            const posting = node("div"), resume = node("div");
            posting.append(node("strong", "", "Posting · " + row.category), node("blockquote", "", row.quote));
            resume.append(node("strong", "", "Your resume"), node("blockquote", "", row.resume_quote || "No passage cited."));
            comparison.append(posting, resume);
            item.append(comparison, node("strong", "", "Assessment"), node("p", "", row.difference));
            if (row.next_step) item.append(node("strong", "", "Next step"), node("p", "", row.next_step));
            if (row.first_assessment) {
              const prior = node("details"); prior.open = true;
              prior.append(node("summary", "", "AI assessments differ · compare the evidence"),
                node("p", "", labels[row.first_assessment.status] + " · " + row.first_assessment.difference));
              if (row.first_assessment.resume_quote) prior.append(node("blockquote", "", row.first_assessment.resume_quote));
              item.append(prior);
            }
            group.append(item);
          }
          results.append(group);
        }
        status.textContent = response.items.length + " posting items compared with " + route().model + ".";
      } catch (error) { status.textContent = error.message; }
      finally { busy = false; consent.checked = false; sync(); }
    });
    panel.append(node("p", "muted", job.description?.trim() ? "Paste or import your resume to see supporting passages, gaps and next steps for this opportunity." : "Add the job description to compare this opportunity."),
      fileLabel, textLabel, load, routeLabel, consentLabel, run, status, results);
    sync(); return panel;
  }


  function renderDetailPreservingEditor() {
    const answers = byId("detail-panel").querySelector?.(".role-application-answers");
    const revisionEditor = byId("detail-panel").querySelector?.(".document-revision-studio");
    const keepRevision = revisionEditor?.dataset.jobId === state.selectedId;
    const editor = byId("detail-panel").querySelector?.("#job-description-editor");
    const keep = editor?.open && editor.dataset.jobId === state.selectedId;
    const picker = byId("detail-panel").querySelector?.(".tailoring-picker");
    const keepPicker = (picker?.open || picker?.dataset.dirty === "true") && picker.dataset.jobId === state.selectedId;
    const duplicates = byId("detail-panel").querySelector?.("#possible-duplicates");
    const keepDuplicates = duplicates?.dataset.started === "true" && duplicates.dataset.jobId === state.selectedId &&
      duplicates.dataset.archived === String(Boolean(state.jobs.find(job => job.id === state.selectedId)?.archive));
    const inline = byId("detail-panel").querySelector?.("#inline-tailoring");
    const currentJob = state.jobs.find(job => job.id === state.selectedId);
    const gap = byId("detail-panel").querySelector?.("#resume-gap");
    const keepGap = gap && currentJob && gap.dataset.jobId === currentJob.id && gap.dataset.revision === String(currentJob.description_revision ?? 0);
    const keepInline = inline?.open && inline.dataset.jobId === state.selectedId && currentJob &&
      inline.dataset.descriptionRevision === String(currentJob.description_revision ?? 0);
    renderDetail();
    if (answers?.dataset.jobId === state.selectedId && answers.open) {
      byId("detail-panel").querySelector?.(".role-application-answers")?.replaceWith(answers);
    }
    if (keepGap) byId("detail-panel").querySelector?.("#resume-gap")?.replaceWith(gap);
    if (keepRevision) byId("detail-panel").querySelector?.(".document-revision-studio")?.replaceWith(revisionEditor);
    if (keepInline) byId("detail-panel").querySelector?.("#inline-tailoring")?.replaceWith(inline);
    if (keepDuplicates) byId("detail-panel").querySelector?.("#possible-duplicates")?.replaceWith(duplicates);
    if (keepPicker) {
      const replacement = byId("detail-panel").querySelector?.(".tailoring-picker");
      if (replacement) replacement.replaceWith(picker);
    }
    if (keep) {
      const replacement = byId("detail-panel").querySelector?.("#job-description-editor");
      if (replacement) replacement.replaceWith(editor);
      else byId("detail-panel").append(editor);
    }
  }

  function renderDetail() {
    selectedAvailabilityUpdater = null;
    const detail = byId("detail-panel");
    detail.replaceChildren();
    const job = state.jobs.find((value) => value.id === state.selectedId);
    if (!job) {
      state.studioOpen = false;
      byId("jobs-screen").classList.toggle("studio-open", false);
      const empty = node("div", "empty-detail");
      if (state.selectedId) {
        empty.append(node("h3", "", "Opportunity outside the loaded board"), node("p", "", "Your description editor is preserved below. Its saved revision will be checked before changes are accepted."));
      } else {
        empty.append(node("span", "empty-icon", "◎"), node("h3", "", "Select an opportunity"), node("p", "", "Inspect its details and prepare your review."));
      }
      detail.append(empty);
      return;
    }
    const header = node("div", "detail-top");
    const heading = node("div");
    heading.append(node("span", "eyebrow", "OPPORTUNITY DETAILS"), node("h3", "", job.title), node("p", "", job.company));
    const headerActions = node("div", "detail-header-actions");
    const studioToggle = button("Open application studio", "button", () => {
      state.studioOpen = !state.studioOpen;
      syncStudio();
    });
    studioToggle.setAttribute("aria-controls", "job-pane-application");
    headerActions.append(node("div", "detail-badge", job.fit_score === null ? "Unscored" : "Keyword " + (!job.description?.trim() ? "Incomplete " : "") + job.fit_score + "/100"), studioToggle);
    header.append(heading, headerActions);
    const returnToList = button("← Back to opportunities", "compact-back", () => {
      byId("jobs-screen").classList.toggle("compact-detail", false);
      byId("jobs-screen").classList.toggle("compact-list", true);
      state.studioOpen = false;
      syncStudio();
      const current = byId("job-list").querySelector?.(".selected") || byId("job-list").querySelector?.("button");
      current?.focus();
    });
    detail.append(returnToList, header);
    const scopeWarning = node("p", "warning", "This selected opportunity is outside your current filters. Its details are kept open to preserve your work. Select a matching opportunity or clear the filters.");
    scopeWarning.id = "selection-scope-warning";
    scopeWarning.setAttribute("role", "status");
    detail.append(scopeWarning);
    updateSelectionScope();
    const info = node("div", "info-grid");
    const facts = [
      ["LOCATION", job.location || "Not specified"],
      ["WORK ARRANGEMENT", job.remote_evidence?.status === "conflicting" ? "Conflicting remote signals" :
        job.remote_evidence?.status === "ambiguous" ? "Remote marked; arrangement unclear" :
        job.remote ? "Saved as remote" : "Not confirmed remote"],
      ["TRACKING", job.application_stage],
      ...(job.delivery ? [["DELIVERY", deliveryLabel(job.delivery)]] : []),
      ["SOURCE", job.source || "Manual"],
    ];
    for (const pair of facts) {
      const cell = node("div");
      cell.append(node("small", "", pair[0]), node("strong", "", pair[1]));
      info.append(cell);
    }
    detail.append(info);
    const tabList = node("div", "detail-jump");
    tabList.setAttribute("role", "tablist"); tabList.setAttribute("aria-label", "Job workspace");
    const panes = {}, tabs = {};
    const sections = [["overview", "Overview"], ["fit", "Fit"], ["sponsorship", "Sponsorship"], ["application", "Application"]];
    function syncStudio() {
      info.hidden = state.detailTab !== "overview";
      byId("jobs-screen").classList.toggle("studio-open", state.studioOpen);
      studioToggle.textContent = state.studioOpen ? "Exit application studio" : "Open application studio";
      studioToggle.setAttribute("aria-pressed", String(state.studioOpen));
      studioToggle.hidden = state.detailTab !== "application";
    }
    function activateTab(key, focus = false) {
      state.detailTab = key;
      if (key !== "application") state.studioOpen = false;
      syncStudio();
      for (const [name] of sections) {
        const active = name === key;
        panes[name].hidden = !active;
        tabs[name].setAttribute("aria-selected", String(active));
        tabs[name].setAttribute("tabindex", active ? "0" : "-1");
      }
      if (focus) tabs[key].focus();
    }
    for (const [key, title] of sections) {
      const tab = button(title, "detail-jump-button", () => activateTab(key));
      const pane = node("div", "detail-tab-panel");
      tab.id = "job-tab-" + key; pane.id = "job-pane-" + key;
      tab.setAttribute("role", "tab"); tab.setAttribute("aria-controls", pane.id);
      pane.setAttribute("role", "tabpanel"); pane.setAttribute("aria-labelledby", tab.id);
      tab.addEventListener("keydown", (event) => {
        const index = sections.findIndex(([name]) => name === key);
        const target = event.key === "ArrowRight" ? (index + 1) % sections.length : event.key === "ArrowLeft" ? (index + sections.length - 1) % sections.length : event.key === "Home" ? 0 : event.key === "End" ? sections.length - 1 : -1;
        if (target >= 0) { event.preventDefault(); activateTab(sections[target][0], true); }
      });
      tabs[key] = tab; panes[key] = pane; tabList.append(tab);
    }
    detail.append(tabList, ...sections.map(([key]) => panes[key]));
    activateTab(state.detailTab);
    const evidenceSummary = node("section", "evidence-summary");
    evidenceSummary.setAttribute("aria-label", "Opportunity evidence at a glance");
    evidenceSummary.append(node("h4", "", "Decision summary"));
    const decision = decisionSummary(job, false);
    evidenceSummary.append(decision.container,
      button("Review sponsorship evidence", "plain-button", () => activateTab("sponsorship", true)));
    panes.overview.append(evidenceSummary);
    if (job.remote_evidence?.detail) panes.overview.append(node("p", "muted", job.remote_evidence.detail));
    if (job.discovery_observations?.length) {
      const discovery = section("Discovery history");
      discovery.append(node("p", "muted", "These are times your searches returned this URL, not proof that the posting is open, current, or unchanged."));
      for (const row of job.discovery_observations) {
        discovery.append(node("p", "", row.source + " · first returned " + row.first_seen + " · last returned " + row.last_seen + " · " + row.observation_count + " search(es)"));
        if (row.posted_date_status === "reported" || row.posted_date_status === "partial") {
          const future = row.reported_posted_date > new Date().toISOString().slice(0, 10);
          discovery.append(node("p", "muted", "Source-reported posting date: " + row.reported_posted_date + " · observed " + row.posted_date_observed_at +
            (row.posted_date_status === "partial" ? ". Other returned rows did not provide a consistent date." : ".") +
            (future ? " The reported date is in the future (UTC); verify it with the source." : "") +
            " This is not independently verified and does not establish that the role is open."));
        } else if (row.posted_date_status === "conflicting") {
          discovery.append(node("p", "muted", "The source returned conflicting posting dates in the latest observation; the date is unknown."));
        } else if (row.posted_date_status === "missing") {
          discovery.append(node("p", "muted", "The source did not provide a usable posting date in the latest observation."));
        }
        if (row.description_comparison === "differs") {
          discovery.append(node("p", "", "Source text observed " + row.description_observed_at + " differs from the saved description. It may be partial or outdated; inspect the original posting before relying on the draft."));
        } else if (row.description_comparison === "matches") {
          discovery.append(node("p", "muted", "Source text observed " + row.description_observed_at + " exactly matched the saved description; this does not verify the current posting."));
        } else {
          discovery.append(node("p", "muted", "Description comparison unavailable for this source or latest observation."));
        }
      }
      panes.overview.append(discovery);
    }
    const actions = node("div", "button-row");
    actions.append(safeLink("View original posting", job.url));
    actions.append(button(job.application_stage === "submitted" ? "View application" : "Prepare application", "button button-primary", () => activateTab("application", true)));
    if (job.delivery) panes.application.append(deliveryPanel(job.id, job.delivery));
    if (job.application_stage !== "submitted" && !job.lever_dispatch_requested && job.delivery?.state !== "duplicate" && !job.archive) {
      const target = /^https:\/\/jobs(?:\.eu)?\.lever\.co\/[a-z0-9_-]+\/[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}(?:\/apply)?\/?(?:[?#].*)?$/i.test(job.url)
        ? new URL(job.url) : null;
      const assistant = section("Apply on the employer’s site");
      if (target) target.pathname = target.pathname.replace(/(?:\/apply)?\/?$/, "/apply");
      assistant.append(safeLink(target ? "Open Lever application form" : "Open posting to apply", target?.href || job.url),
        node("p", "muted", "For a Lever application, open the Career Agent companion in Chrome or Edge to fill contact details. Attach your reviewed resume on the employer’s page, then choose Review this application in the companion."),
        node("p", "muted", "For an employer account, use the browser’s save-password prompt during sign-up. Choose Open password vault in the companion to manage saved logins."));
      if (target && state.settings.application_submission_enabled) {
        assistant.prepend(liveApplication(job, target.origin + target.pathname));
      }
      panes.application.append(assistant);
    }
    const openJobAI = () => {
      if (byId("ai-job").value !== job.id) {
        byId("ai-results").replaceChildren();
        byId("ai-consent").checked = false;
        byId("ai-status").textContent = "Loading selected opportunity…";
      }
      byId("ai-job").dataset.requestedJob = job.id;
      byId("nav-ai").click();
    };
    actions.append(button("Analyze with AI", "button", openJobAI));
    panes.overview.append(actions);

    const duplicates = section("Possible duplicate postings");
    duplicates.id = "possible-duplicates"; duplicates.dataset.jobId = job.id; duplicates.dataset.archived = String(Boolean(job.archive));
    duplicates.append(node("p", "muted", "Compare saved postings with the same company, title and location. Results reflect the time of the search; search again after changes. Different requisitions can share these details. Compare the original URLs before confirming a duplicate. Removed entries remain in Archived, with their own history."));
    const duplicateResults = node("div");
    duplicateResults.setAttribute("role", "status");
    const duplicateButton = button("Find possible duplicates", "button", async () => {
      if (duplicateButton.disabled) return;
      duplicateButton.disabled = true; duplicates.dataset.started = "true";
      duplicateResults.textContent = "Comparing saved opportunities…";
      try {
        const result = await api("/api/jobs/" + job.id + "/possible-duplicates");
        if (state.selectedId !== job.id || result.job_id !== job.id) return;
        duplicateResults.replaceChildren(node("p", "muted", result.reason));
        if (!result.jobs.length) duplicateResults.append(node("p", "", "No possible duplicates found in the checked records. Similar postings may still exist."));
        for (const other of result.jobs) {
          const item = node("div", "fit-evidence-item");
          item.append(node("strong", "", other.title + " · " + other.company),
            node("p", "", other.location + " · Source: " + other.source + " · " + (other.submission_recorded ? "Submission recorded" : "No submission recorded")),
            safeLink("View other original posting", other.url));
          if (state.jobs.some((entry) => entry.id === other.job_id)) item.append(button("Open other saved opportunity", "button", () => selectJob(other.job_id)));
          if (!job.archive) item.append(button("Remove this duplicate; keep selected role", "button", async (event) => {
            if (!window.confirm("Confirm these are the SAME vacancy, not separate requisitions. Archive " + other.title + " (" + other.url + ") and keep " + job.url + "? Drafts and history stay separate and can be restored.")) return;
            const control = event.currentTarget; control.disabled = true;
            try {
              await api("/api/jobs/" + other.job_id + "/archive", "POST", {acknowledged: true, reason: "duplicate", duplicate_of: job.id});
              item.replaceChildren(node("p", "", "Duplicate removed from active opportunities. Restore it from Archived if needed."));
              await refresh();
              notice("Duplicate archived; selected opportunity kept.");
            } catch (error) { notice(error.message, true); control.disabled = false; }
          }));
          duplicateResults.append(item);
        }
        if (result.results_truncated) duplicateResults.append(node("p", "", "Showing the first 10 possible duplicates; additional matches exist."));
        if (result.scan_limited) duplicateResults.append(node("p", "", "Only the 10,000 most recent saved opportunities were checked; older records may match."));
      } catch (error) { duplicateResults.textContent = "Unable to compare saved opportunities: " + error.message; }
      finally { duplicateButton.disabled = false; }
    });
    duplicates.append(duplicateButton, duplicateResults);
    panes.overview.append(duplicates);

    const availability = section("Posting availability");
    const availabilityStatus = node("div", "availability-status");
    availabilityStatus.setAttribute("role", "status");
    if (!availabilityInteractions.has(job.id)) availabilityInteractions.set(job.id, {pending: false, failure: null});
    const interaction = availabilityInteractions.get(job.id);
    let check = null;
    const showAvailability = () => {
      if (check) check.disabled = interaction.pending;
      if (interaction.pending) { availabilityStatus.textContent = "Checking the public posting…"; return; }
      const currentJob = state.jobs.find((value) => value.id === job.id) || job;
      const observation = postingObservation(currentJob);
      decision.check.textContent = activeCheckText(currentJob);
      availabilityStatus.replaceChildren(chip(observation.label, observation.kind === "listed" ? "teal" : observation.kind === "expired" ? "red" : ""),
        node("p", "", observation.detail));
      if (interaction.failure) availabilityStatus.append(node("p", "", "Check failed: " + interaction.failure + ". Previous observation remains unchanged."));
      if (currentJob.live_check && currentJob.live_check.checked_at) availabilityStatus.append(node("p", "", "Checked: " + currentJob.live_check.checked_at));
      if (currentJob.live_check && currentJob.live_check.source_url) availabilityStatus.append(safeLink("Observation source", currentJob.live_check.source_url));
    };
    selectedAvailabilityUpdater = showAvailability;
    showAvailability();
    availability.append(availabilityStatus, safeLink("Open original posting", job.url));
    {
      check = button("Check live availability", "button", async () => {
        if (interaction.pending) return;
        interaction.pending = true;
        interaction.failure = null;
        if (selectedAvailabilityUpdater) selectedAvailabilityUpdater();
        try {
          const observation = await api("/api/jobs/" + job.id + "/live-check", "POST", {acknowledged: true});
          const currentJob = state.jobs.find((value) => value.id === job.id);
          if (currentJob) { currentJob.live_check = observation; currentJob.archive = observation.archive || null; }
          if (observation.archive) {
            notice("Opportunity archived: " + (observation.archive.reason === "expired" ? "the verified posting deadline has passed." : "it was already removed from the active board."));
            await refresh();
          }
        } catch (error) { interaction.failure = error.message; }
        finally {
          interaction.pending = false;
          if (state.selectedId === job.id && selectedAvailabilityUpdater) selectedAvailabilityUpdater();
          renderJobs();
        }
      });
      check.disabled = interaction.pending;
      availability.append(node("p", "", "Checks the original website or its public hiring system. The saved posting URL is sent; no profile or résumé is sent."), check);
    }
    availability.append(node("p", "", "A listed posting is not a guarantee of current hiring or sponsorship. Checks automatically archive identity-matched postings with expired deadlines. Failed or blocked checks keep the opportunity active."));
    if (job.archive) {
      availability.append(node("p", "", "Archived: " + ({duplicate: "confirmed duplicate", confirmed_closed: "closure confirmed by you", expired: "verified deadline passed", tracker: "archived in application tracker"}[job.archive.reason] || job.archive.reason) + " · " + job.archive.archived_at));
      if (job.archive.duplicate_of) availability.append(node("p", "muted", "Kept opportunity ID: " + job.archive.duplicate_of));
      availability.append(button("Restore opportunity", "button", async (event) => {
        const control = event.currentTarget; control.disabled = true;
        try {
          await api("/api/jobs/" + job.id + "/restore", "POST", {acknowledged: true});
          await refresh(); notice("Opportunity restored. Select All active roles to see it. A new expired live check can archive it again.");
        } catch (error) { notice(error.message, true); control.disabled = false; }
      }));
    } else {
      availability.append(button("Archive confirmed closed role", "button", async (event) => {
        if (!window.confirm("Have you confirmed on the original website that this role is closed? Archive " + job.title + " at " + job.company + "? Your documents and application history will be retained.")) return;
        const control = event.currentTarget; control.disabled = true;
        try {
          await api("/api/jobs/" + job.id + "/archive", "POST", {acknowledged: true, reason: "confirmed_closed"});
          await refresh(); notice("Closed opportunity archived. Restore it from the Archived view if needed.");
        } catch (error) { notice(error.message, true); control.disabled = false; }
      }));
    }
    panes.overview.prepend(availability);

    const sponsor = section("Sponsorship assessment");
    sponsor.id = "job-sponsorship";
    const sponsorBox = node("div", "sponsorship-box");
    const label = sponsorshipLabel(job.sponsorship_status);
    sponsorBox.append(chip(label[0], label[1]), node("p", "", job.sponsorship_reason));
    sponsor.append(sponsorBox);
    if (job.imported_sponsorship_history?.sources?.length) {
      const history = job.imported_sponsorship_history;
      sponsor.append(node("p", "muted", "Imported historical records: " + history.sources.join(", ") + " · employer name: " + history.employer + ". " +
        (history.match === "candidate_association" ? "This job uses your unverified employer association. " : "This is an exact name match, not verified legal identity. ") +
        "Historical records do not establish current job sponsorship, hiring or individual eligibility. Open H-1B research to inspect sources and dates."));
    }
    sponsor.append(button("Research H-1B records and jobs", "button", () => {
      byId("h1b-query").value = job.company;
      byId("nav-h1b").click();
    }));
    panes.sponsorship.append(sponsor);

    panes.fit.append(resumeGapPanel(job));

    if (job.constraint_evidence?.length) {
      const constraints = section("Your hard constraints");
      constraints.append(node("p", "muted", "These are separate from the preference score. Review the source and confirm details before deciding."));
      for (const row of job.constraint_evidence) {
        const label = row.status === "flagged" ? "Flagged" : row.status === "source_marked" ? "Source marked" : "Unknown";
        constraints.append(node("strong", "", row.constraint + " — " + label), node("p", "", row.detail));
      }
      panes.fit.append(constraints);
    }

    if (job.title_level_evidence) {
      const preference = section("Title-level preference");
      const evidence = job.title_level_evidence;
      preference.append(node("p", "", "Saved title: " + evidence.title));
      preference.append(node("p", "", evidence.status === "matched"
        ? "Preferred title phrase found: " + evidence.matched_phrases.join(", ")
        : "No preferred title phrase found; level remains unknown."));
      preference.append(node("p", "muted", evidence.notice));
      panes.fit.append(preference);
    }

    if (job.fit_reasons && job.fit_reasons.length) {
      const fit = section("Why this role matched");
      if (!job.description?.trim()) fit.append(node("p", "muted", "The keyword score is incomplete because the job description is missing. It uses available title and location information; review the posting before comparing this role."));
      const ul = node("ul");
      for (const reason of job.fit_reasons) ul.append(node("li", "", reason));
      fit.append(ul);
      panes.fit.append(fit);
    }

    const evidence = section("Review explicit requirements");
    evidence.id = "fit-evidence";
    evidence.append(node("p", "", "Compare recognized posting sections with your verified facts. Keyword links are suggestions, not qualification decisions."));
    const evidenceResult = node("div");
    evidenceResult.setAttribute("role", "status");
    const evidenceButton = button("Compare requirements", "button", async () => {
      if (evidenceButton.disabled) return;
      evidence.dataset.started = "true";
      evidenceButton.disabled = true;
      evidenceResult.textContent = "Comparing saved posting text with verified facts…";
      try {
        const result = await api("/api/jobs/" + job.id + "/fit-evidence");
        if (result.job_id !== job.id || result.description_revision !== (job.description_revision ?? 0)) {
          throw new Error("The posting changed; refresh the board and compare again.");
        }
        evidenceResult.replaceChildren(node("p", "muted", result.notice));
        evidenceResult.append(node("p", "muted", "Snapshot of saved posting revision " + result.description_revision + " and profile revision " + result.workspace_revision + ". Compare again after changes."));
        if (!result.requirements.length) evidenceResult.append(node("p", "", "No clearly labeled requirement bullets found; review the full posting manually."));
        for (const row of result.requirements) {
          const item = node("div", "fit-evidence-item");
          const label = row.category === "required" ? "Labeled required" : row.category === "preferred" ? "Labeled preferred" : row.category === "candidate_context" ? "Candidate-related section; level unspecified" : "Qualification level unspecified";
          item.append(node("strong", "", label));
          if (row.source_heading && row.source_heading !== row.quote) item.append(node("p", "muted", "Section heading: " + row.source_heading));
          if (row.classification_note) item.append(node("p", "muted", row.classification_note));
          item.append(node("blockquote", "", row.quote));
          if (!row.suggested_facts.length) item.append(node("p", "", "No verified fact matched by keyword; evidence remains unknown."));
          for (const fact of row.suggested_facts) item.append(node("p", "", "Potentially relevant verified fact " + fact.id + ": " + fact.statement + " (" + (fact.match_basis === "shared_source_phrase" ? "shared source phrases: " : "saved keywords: ") + fact.matched_keywords.join(", ") + ")"));
          evidenceResult.append(item);
        }
        if (result.unrecognized_headings?.length) evidenceResult.append(node("p", "muted", "Unrecognized headings: " + result.unrecognized_headings.join("; ")));
        if (result.truncated) evidenceResult.append(node("p", "", "Some requirement sections were unrecognized or exceeded review limits; inspect the full posting."));
      } catch (error) {evidenceResult.textContent = "Unable to compare requirements: " + error.message;}
      finally {evidenceButton.disabled = false;}
    });
    evidence.append(evidenceButton, evidenceResult);
    panes.fit.append(evidence);

    const preparation = section("Interview preparation");
    preparation.id = "interview-preparation";
    preparation.append(node("p", "muted", "Build local practice prompts from the saved posting and your verified facts. No AI request, message, or application is sent."));
    const prepResult = node("div"); prepResult.setAttribute("role", "status");
    const prepButton = button("Build preparation guide", "button", async () => {
      if (prepButton.disabled) return;
      prepButton.disabled = true; prepResult.textContent = "Preparing local prompts…";
      try {
        const result = await api("/api/jobs/" + job.id + "/interview-preparation");
        if (!preparation.isConnected) return;
        if (result.job_id !== job.id || result.description_revision !== (job.description_revision ?? 0)) {
          throw new Error("The posting changed; refresh the board and prepare again.");
        }
        prepResult.replaceChildren(node("p", "muted", result.notice), node("p", "muted",
          "Saved posting revision " + result.description_revision + " · profile revision " + result.workspace_revision + "."));
        const general = node("ul");
        for (const question of result.general_questions) general.append(node("li", "", question));
        prepResult.append(node("h4", "", "Questions to prepare"), general);
        if (!result.prompts.length) prepResult.append(node("p", "", "No recognized qualification bullets found. Review the full posting and choose topics yourself."));
        for (const prompt of result.prompts) {
          const item = node("section", "fit-evidence-item");
          item.append(node("h4", "", prompt.question), node("p", "muted", "Posting section: " + prompt.source_heading), node("blockquote", "", prompt.quote));
          if (!prompt.suggested_facts.length) item.append(node("p", "", "No verified fact was linked by keyword; consider what to clarify. This is not a qualification gap."));
          for (const fact of prompt.suggested_facts) item.append(node("p", "", "Possible example to review · " + fact.id + ": " + fact.statement + " (" + (fact.match_basis === "shared_source_phrase" ? "shared source phrases: " : "saved keywords: ") + fact.matched_keywords.join(", ") + ")"));
          if (prompt.additional_fact_matches) item.append(node("p", "muted", prompt.additional_fact_matches + " additional keyword-linked facts are not shown; use Compare requirements to review them."));
          prepResult.append(item);
        }
        if (result.truncated) prepResult.append(node("p", "muted", "Some posting excerpts are not shown; inspect the full posting."));
      } catch (error) { if (preparation.isConnected) prepResult.textContent = "Unable to build preparation guide: " + error.message; }
      finally { prepButton.disabled = false; }
    });
    preparation.append(prepButton, prepResult); panes.application.append(preparation);

    const desc = section("Job description");
    desc.id = "job-overview";
    desc.append(node("p", "description", job.description || "No description saved."));
    if (!job.description?.trim()) desc.append(node("p", "muted", "Matching and sponsorship analysis cannot assess missing posting text. Open the original posting and use the description editor below to add text you have reviewed."));
    if (job.description_correction) {
      desc.append(node("p", "muted", "Manually corrected locally on " + job.description_correction.changed_at +
        ". Source note: " + (job.description_correction.source_note || "Not provided") + ". The source was not independently verified."));
    }
    if (job.application_stage !== "submitted") {
      const editor = node("details");
      editor.id = "job-description-editor";
      editor.dataset.jobId = job.id;
      editor.append(node("summary", "", "Correct saved description"));
      editor.append(node("p", "muted", "Paste the latest text you reviewed. This local correction is not independently verified. Changing it makes existing drafts and approvals outdated; create and review a new draft."));
      const form = node("form");
      const label = node("label", "block-label", "Corrected job description");
      const input = node("textarea"); input.rows = 8; input.maxLength = 50000; input.required = true;
      input.defaultValue = job.description || ""; input.value = job.description || ""; label.append(input); form.append(label);
      const sourceLabel = node("label", "block-label", "Source note (optional)");
      const source = node("textarea"); source.rows = 2; source.maxLength = 500;
      source.placeholder = "For example, the employer job page and date you checked";
      sourceLabel.append(source); form.append(sourceLabel);
      const confirmation = node("label", "check-label");
      const acknowledged = node("input"); acknowledged.type = "checkbox"; acknowledged.required = true;
      confirmation.append(acknowledged, document.createTextNode("I reviewed this corrected description."));
      input.addEventListener("input", () => { acknowledged.checked = false; });
      source.addEventListener("input", () => { acknowledged.checked = false; });
      form.append(confirmation);
      const capturePanel = node("details");
      capturePanel.append(node("summary", "", "Use a browser capture for this job"), node("p", "muted", "Paste a capture from the same job URL. The text fills the editor only; review and save it separately. Ambiguous or different URLs require manual review."));
      const captureLabel = node("label", "block-label", "Browser capture JSON");
      const captureInput = node("textarea"); captureInput.id = "description-capture-input"; captureInput.rows = 3; captureInput.maxLength = 60000;
      captureLabel.append(captureInput); capturePanel.append(captureLabel);
      const captureStatus = node("p", "muted"); captureStatus.setAttribute("role", "status");
      const captureButton = button("Fill description from capture", "button", async () => {
        if (captureButton.disabled) return;
        const originalCapture = captureInput.value, originalDescription = input.value;
        if (originalDescription.trim() && !window.confirm("Replace the description editor text with this capture?")) return;
        captureButton.disabled = true; submit.disabled = true;
        try {
          const capture = JSON.parse(originalCapture);
          if (!capture || Array.isArray(capture) || typeof capture !== "object") throw new Error("Invalid browser capture.");
          if (capture.text_source !== undefined && capture.text_source !== "structured_description") throw new Error("Invalid browser capture source.");
          const result = await api("/api/jobs/" + job.id + "/capture-preview", "POST", {format: capture.format, url: capture.url, selection: capture.selection});
          if (!editor.isConnected || state.selectedId !== job.id) return;
          if (captureInput.value !== originalCapture || input.value !== originalDescription) throw new Error("Editor changed while checking; review and try again.");
          if (result.job_id !== job.id || typeof result.description !== "string") throw new Error("Capture did not match this job.");
          input.value = result.description; acknowledged.checked = false;
          if (capture.text_source === "structured_description" && !source.value.trim()) source.value = "Structured JobPosting on captured page; source not independently verified.";
          captureStatus.textContent = capture.text_source === "structured_description" ? "Structured page text loaded into the editor only. It may be incomplete or describe another job; compare with the original and confirm before saving." : "Text loaded into the editor only. Review it and confirm before saving.";
          if (result.match_method === "linkedin_job_number") captureStatus.textContent += " LinkedIn URL job numbers match; compare the title and employer before saving.";
        } catch (error) { captureStatus.textContent = error instanceof SyntaxError ? "Paste a valid browser capture." : error.message; }
        finally { captureButton.disabled = false; submit.disabled = false; }
      });
      capturePanel.append(captureButton, captureStatus); form.append(capturePanel);
      {
        const candidates = node("details");
        candidates.append(node("summary", "", "Review returned source descriptions"), node("p", "muted", "Load text previously returned by an opt-in detail search. Compare the source title and employer with this job and its original posting; the text is unverified and may be stale or belong to a different listing."));
        const candidateStatus = node("p", "muted"); candidateStatus.setAttribute("role", "status");
        const candidateList = node("div");
        const loadCandidates = button("Load saved source text", "button", async () => {
          if (loadCandidates.disabled) return;
          loadCandidates.disabled = true; candidateStatus.textContent = "Loading saved source text…";
          try {
            const result = await api("/api/jobs/" + job.id + "/description-candidates");
            if (!editor.isConnected || state.selectedId !== job.id) return;
            if (result.job_id !== job.id || !Array.isArray(result.candidates)) throw new Error("Unable to load source text for this job.");
            candidateList.replaceChildren();
            candidateStatus.textContent = result.candidates.length ? "Review each source before using it. This does not save a correction." : "No saved source text is available for review.";
            for (const candidate of result.candidates) {
              const entry = node("section");
              entry.append(node("p", "", `Source: ${candidate.source} · received ${candidate.observed_at} · ${candidate.title} at ${candidate.company}`),
                           node("p", "description", candidate.description));
              entry.append(button("Use this source text", "button", () => {
                if (submit.disabled) return;
                if (input.value.trim() && !window.confirm("Replace the description editor text with this source text?")) return;
                input.value = candidate.description;
                if (!source.value.trim()) source.value = `JobSpy ${candidate.source}, received ${candidate.observed_at}; source text not independently verified.`;
                acknowledged.checked = false;
                candidateStatus.textContent = "Text loaded into the editor only. Review it and confirm before saving.";
              }));
              candidateList.append(entry);
            }
          } catch (error) { candidateStatus.textContent = error.message; }
          finally { loadCandidates.disabled = false; }
        });
        candidates.append(loadCandidates, candidateStatus, candidateList); form.append(candidates);
      }
      const submit = button("Save corrected description", "button button-primary"); submit.type = "submit";
      const status = node("p", "muted"); status.setAttribute("role", "status");
      form.append(submit, status);
      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        if (submit.disabled || !acknowledged.checked) return;
        const description = input.value, sourceNote = source.value;
        submit.disabled = true; input.disabled = true; source.disabled = true; acknowledged.disabled = true; captureButton.disabled = true; captureInput.disabled = true;
        try {
          const result = await api("/api/jobs/" + job.id + "/description", "PUT", {
            description, source_note: sourceNote, expected_revision: job.description_revision ?? 0, acknowledged: true
          });
          notice(result.changed ? "Description corrected. Regenerate and review any existing draft." : "Description was unchanged.");
          editor.open = false;
          if (state.selectedId === job.id) await refresh(job.id);
        } catch (error) { status.textContent = error.message; }
        finally { submit.disabled = false; input.disabled = false; source.disabled = false; acknowledged.disabled = false; captureButton.disabled = false; captureInput.disabled = false; }
      });
      editor.append(form); desc.append(editor);
    } else {
      desc.append(node("p", "muted", "The posting and reviewed packet are locked after submission is recorded."));
    }
    panes.overview.append(desc);

    const context = node("details", "studio-job-context");
    context.open = true;
    context.append(node("summary", "", "Saved job description"),
      node("p", "muted", "Source text is unverified and may be incomplete or outdated. Review the original posting."),
      node("p", "description", job.description || "No description saved. Review the original posting before preparing a draft."));
    panes.application.append(context);
    const drafts = section("Application preparation");
    drafts.id = "job-application";
    const p = node("p", "", "Prepare documents from verified career facts. Review AI-rewritten claims before export; applications are never sent automatically.");
    drafts.append(p);
    const workflowActions = node("div", "button-row");
    workflowActions.append(button("Analyze this role with AI", "button", openJobAI), button("Track application progress", "button", async () => {
      showScreen("tracker");
      await loadCRM();
      if (state.screen === "tracker") await selectCRMJob(job.id);
    }));
    drafts.append(workflowActions);
    drafts.append(applicationAnswers(job.id));
    drafts.append(documentRevisionStudio(job), inlineTailoring(job));
    const missingProfile = state.settings.profile_configured === false;
    const missingFacts = state.settings.facts_configured === false;
    if (job.application_stage !== "submitted" && (missingProfile || missingFacts)) {
      const needed = [];
      if (missingProfile) needed.push("a saved career profile");
      if (missingFacts) needed.push("at least one verified career fact");
      drafts.append(node("p", "muted", "To create a draft, add " + needed.join(" and ") + ". Review each fact and verify it yourself."));
      if (state.settings.legacy_files_unavailable) drafts.append(node("p", "muted", "A configured profile or fact file could not be read or validated. Check the local configuration or set up the career profile here."));
      drafts.append(button("Open career profile for draft setup", "button", () => byId("nav-profile").click()));
    }
    if (job.application_stage === "submitted") {
      drafts.append(node("p", "", "This submitted application's reviewed packet is locked. Draft regeneration is disabled."));
    } else {
      const draftButton = button(job.packet_available ? "Regenerate draft" : "Create application draft", "button button-primary", createDraft);
      draftButton.disabled = missingProfile || missingFacts;
      drafts.append(draftButton);
      if (!missingProfile && !missingFacts) {
      const choose = node("details", "tailoring-picker");
      choose.dataset.jobId = job.id;
      choose.dataset.dirty = "false";
      choose.append(node("summary", "", "Choose facts for a tailored draft"));
      choose.append(node("p", "muted", "Select verified facts and arrange sections and facts. Statements are copied verbatim; changes create a new draft that needs a fresh review."));
      choose.append(node("p", "muted", "To change a statement, edit it in Career profile and verify the changed fact before including it."));
      choose.append(node("p", "muted", "Keyword mentions compare each fact's saved keywords with the job title and description. They are suggestions, not proof of fit or a reason to omit other facts."));
      const choices = node("div");
      const status = node("p", "muted"); status.setAttribute("role", "status");
      const load = button("Load verified facts", "button", async () => {
        if (load.disabled) return;
        if (choose.dataset.dirty === "true" && !window.confirm("Reload verified facts and discard your unfinished selection and ordering?")) return;
        load.disabled = true;
        try {
          const result = await api("/api/jobs/" + job.id + "/tailoring/facts");
          if (!Number.isInteger(result.max_facts) || result.max_facts < 1) throw new Error("Invalid selection limit; reload and try again.");
          if (result.job_id !== job.id || result.description_revision !== (job.description_revision ?? 0)) throw new Error("The posting changed; refresh the board before choosing facts.");
          const limit = result.max_facts;
          const sections = ["experience", "projects", "skills", "education", "certifications"];
          const selected = [];
          const selectionCount = node("p", "tailoring-count");
          selectionCount.setAttribute("aria-live", "polite");
          const markChanged = () => { choose.dataset.dirty = "true"; updateCount(); };
          function updateCount() {
            const count = selected.filter(item => item.box.checked).length;
            selectionCount.textContent = count + " of " + limit + " facts selected" + (count > limit ? " — remove " + (count - limit) + " before creating a draft." : ".");
          }
          let layoutBusy = false;
          for (const fact of result.facts) {
            if (!Array.isArray(fact.matched_keywords) || !fact.matched_keywords.every(word => typeof word === "string")) throw new Error("Invalid fact evidence; reload and try again.");
            const label = node("label", "check-label");
            const box = document.createElement("input"); box.type = "checkbox";
            box.addEventListener("change", markChanged);
            label.append(box, document.createTextNode(fact.section + " · " + fact.statement));
            selected.push({box, label, id: fact.id, section: fact.section, matched: fact.matched_keywords});
          }
          const submit = button("Create tailored draft", "button button-primary", async () => {
            const fact_ids = selected.filter((item) => item.box.checked).map((item) => item.id);
            if (!fact_ids.length || fact_ids.length > limit) {status.textContent = "Select between 1 and " + limit + " facts."; return;}
            if (submit.disabled) return;
            submit.disabled = true; load.disabled = true; layoutBusy = true; renderLayout();
            selected.forEach((item) => {item.box.disabled = true;});
            try {
              await api("/api/jobs/" + job.id + "/tailored-draft", "POST", {fact_ids, section_order: sections.slice(), workspace_revision: result.workspace_revision, facts_sha256: result.facts_sha256, job_description_revision: result.description_revision});
              choose.dataset.dirty = "false";
              notice("Tailored draft created locally. Review it before using.");
              if (state.selectedId === job.id) await refresh(job.id);
            } catch (error) {status.textContent = error.message;}
            finally {submit.disabled = false; load.disabled = false; layoutBusy = false; selected.forEach((item) => {item.box.disabled = false;}); renderLayout();}
          });
          function renderLayout() {
            choices.replaceChildren();
            updateCount(); choices.append(selectionCount);
            sections.forEach((section, index) => {
              const group = node("div", "tailoring-section");
              group.append(node("strong", "", section));
              const up = button("Move " + section + " section up", "button", () => {
                [sections[index - 1], sections[index]] = [sections[index], sections[index - 1]];
                markChanged(); renderLayout();
              });
              const down = button("Move " + section + " section down", "button", () => {
                [sections[index], sections[index + 1]] = [sections[index + 1], sections[index]];
                markChanged(); renderLayout();
              });
              up.disabled = layoutBusy || index === 0; down.disabled = layoutBusy || index === sections.length - 1;
              group.append(up, down);
              const items = selected.filter((item) => item.section === section);
              items.forEach((item, position) => {
                const row = node("div", "tailoring-fact");
                row.append(item.label);
                row.append(node("p", "muted", item.matched.length ? "Keyword mentions: " + item.matched.join(", ") : "No fact keywords found in the saved title or description; relevance is unknown."));
                const move = (delta) => {
                  const from = selected.indexOf(item), to = selected.indexOf(items[position + delta]);
                  [selected[from], selected[to]] = [selected[to], selected[from]];
                  markChanged(); renderLayout();
                };
                const earlier = button("Move fact " + item.id + " up", "button", () => move(-1));
                const later = button("Move fact " + item.id + " down", "button", () => move(1));
                earlier.disabled = layoutBusy || position === 0; later.disabled = layoutBusy || position === items.length - 1;
                row.append(earlier, later); group.append(row);
              });
              choices.append(group);
            });
            choices.append(submit);
          }
          choose.dataset.dirty = "false";
          renderLayout();
          status.textContent = result.facts.length ? "Choose up to " + limit + " verified facts to include." + (result.description_missing ? " The description is missing, so keyword mentions use only the title." : "") : "No verified facts available.";
          submit.disabled = !result.facts.length;
        } catch (error) {status.textContent = error.message;}
        finally {load.disabled = false;}
      });
      choose.append(load, choices, status); drafts.append(choose);
      }
    }
    panes.application.append(drafts);
    if (state.packet) renderPacket(detail, job, state.packet);
    else {
      const empty = node("section", "studio-placeholder");
      empty.append(node("h4", "", "Your application preview"), node("p", "", "Choose verified facts and create a draft. Your résumé and review controls will appear here."));
      detail.append(empty);
    }
  }

  function renderPacket(container, job, record) {
    const packet = record.packet;
    const review = record.review;
    const stale = Boolean(record.stale);
    const card = node("section", "review-card");
    card.append(node("h4", "", "Application packet"), node("p", "", "Review each statement before using it. Packet fingerprint: " + packet.packet_sha256.slice(0, 12) + "…"));
    if (stale) card.append(node("p", "stale-warning", "The career profile or job description changed since this packet was drafted. Regenerate and review before using it."));
    const resume = node("details");
    const resumeTitle = node("summary", "", "Resume draft");
    const resumeText = node("pre", "", packet.resume_markdown);
    resume.append(resumeTitle);
    const blocks = packet.resume_blocks;
    if (Array.isArray(blocks) && blocks.length <= 48 && blocks.length > 0 && blocks.every((block) =>
        block && ["name", "contact", "headline", "section", "fact"].includes(block.kind) && typeof block.text === "string")) {
      resume.append(node("p", "muted", "Review these literal text blocks and their order. Each fact remains one item in Word and PDF, even if its text contains line breaks."));
      const preview = node("div", "resume-block-preview");
      for (const block of blocks) {
        const tag = block.kind === "name" ? "h4" : block.kind === "section" ? "h5" : "p";
        preview.append(node(tag, "resume-block " + (block.kind === "fact" ? "resume-block-fact" : ""), block.text));
      }
      resume.append(preview, node("p", "muted", "Plain-text draft:"));
    }
    resume.append(resumeText);
    const letter = node("details");
    letter.append(node("summary", "", "Cover-note draft"), node("pre", "", packet.cover_note));
    card.append(resume, letter);
    const versions = node("details", "resume-comparison");
    versions.append(node("summary", "", "Compare previous draft versions"), node("p", "muted",
      "Read-only comparison with stored workflow drafts for this job. Earlier drafts may contain facts since changed or revoked. Prior approval does not apply to the current version. Legacy drafts or missing checkpoints may not be available."));
    const versionStatus = node("p", "muted"); versionStatus.setAttribute("role", "status");
    const versionLabel = node("label", "field", "Previous workflow draft");
    const versionSelect = node("select"); versionLabel.append(versionSelect); versionLabel.hidden = true;
    const comparisonOutput = node("div", "resume-comparison-grid");
    let versionRequest = 0;
    versionSelect.addEventListener("change", () => { versionRequest++; comparisonOutput.replaceChildren(); versionStatus.textContent = "Select Compare to load this draft."; });
    const compareVersion = button("Compare with current draft", "button", async () => {
      if (compareVersion.disabled || !versionSelect.value) return;
      const selected = versionSelect.value, request = ++versionRequest;
      compareVersion.disabled = true; comparisonOutput.replaceChildren(); versionStatus.textContent = "Loading stored draft…";
      try {
        const previous = await api("/api/jobs/" + job.id + "/packet/versions/" + encodeURIComponent(selected));
        if (!card.isConnected || request !== versionRequest || versionSelect.value !== selected) return;
        const old = node("section"); old.append(node("h5", "", "Selected stored draft"), node("h6", "", "Résumé"), node("pre", "", previous.resume_markdown), node("h6", "", "Cover note"), node("pre", "", previous.cover_note));
        const current = node("section"); current.append(node("h5", "", "Current displayed draft"), node("h6", "", "Résumé"), node("pre", "", packet.resume_markdown), node("h6", "", "Cover note"), node("pre", "", packet.cover_note));
        comparisonOutput.append(old, current);
        versionStatus.textContent = "Read-only comparison. Review the current draft separately before approval.";
      } catch (error) { if (request === versionRequest) versionStatus.textContent = error.message; }
      finally { compareVersion.disabled = false; }
    });
    compareVersion.hidden = true;
    const loadVersions = button("Load draft versions", "button", async () => {
      if (loadVersions.disabled) return;
      const request = ++versionRequest;
      loadVersions.disabled = true; compareVersion.hidden = true; versionLabel.hidden = true;
      comparisonOutput.replaceChildren(); versionStatus.textContent = "Loading version history…";
      try {
        const result = await api("/api/jobs/" + job.id + "/packet/versions");
        if (!card.isConnected || request !== versionRequest) return;
        versionSelect.replaceChildren();
        for (const version of result.versions) {
          const option = node("option", "", version.created_at + " · " + version.status.replaceAll("_", " ") + " · " + version.packet_sha256.slice(0, 12));
          option.value = version.run_id; versionSelect.append(option);
        }
        if (!result.versions.length) { versionStatus.textContent = "No stored workflow drafts were found for this job."; return; }
        const older = result.versions.find((version) => version.packet_sha256 !== packet.packet_sha256);
        if (older) versionSelect.value = older.run_id;
        versionLabel.hidden = false; compareVersion.hidden = false;
        versionStatus.textContent = "Up to 20 published workflow drafts are listed; checkpoints may be unavailable.";
      } catch (error) { if (request === versionRequest) versionStatus.textContent = error.message; }
      finally { loadVersions.disabled = false; }
    });
    versions.append(loadVersions, versionLabel, compareVersion, versionStatus, comparisonOutput); card.append(versions);

    if (Array.isArray(packet.fact_evidence) && Array.isArray(packet.omitted_fact_ids) &&
        packet.fact_evidence.every((fact) => fact && typeof fact.id === "string" && typeof fact.section === "string" &&
          typeof fact.statement === "string" && Array.isArray(fact.matched_keywords) && fact.matched_keywords.every((item) => typeof item === "string")) &&
        packet.omitted_fact_ids.every((item) => typeof item === "string")) {
      if (Array.isArray(packet.baseline_fact_evidence) &&
          packet.baseline_fact_evidence.every((fact) => fact && typeof fact.id === "string" &&
            typeof fact.section === "string" && typeof fact.statement === "string")) {
        const comparison = node("details", "resume-comparison");
        comparison.append(node("summary", "", "Compare verified facts and tailored draft"),
          node("p", "muted", "This baseline is your verified fact bank at drafting time, grouped in the default section order. It is not your uploaded résumé. The draft copies selected facts verbatim; review omissions and order before approving."));
        const grid = node("div", "resume-comparison-grid");
        const baseline = node("section");
        baseline.append(node("h5", "", "Verified facts at drafting"));
        const included = new Set(packet.fact_evidence.map((fact) => fact.id));
        const originalList = node("ol");
        for (const fact of packet.baseline_fact_evidence) {
          const item = node("li");
          item.append(node("strong", "", fact.id + " · " + fact.section + (included.has(fact.id) ? " · Included" : " · Omitted")), node("p", "", fact.statement));
          originalList.append(item);
        }
        baseline.append(originalList);
        const tailored = node("section");
        tailored.append(node("h5", "", "Facts in draft order"));
        const draftList = node("ol");
        for (const fact of packet.fact_evidence) {
          const item = node("li");
          item.append(node("strong", "", fact.id + " · " + fact.section), node("p", "", fact.statement));
          draftList.append(item);
        }
        tailored.append(draftList);
        grid.append(baseline, tailored); comparison.append(grid); card.append(comparison);
      }
      const evidence = node("details", "packet-evidence");
      evidence.append(node("summary", "", "Review source facts and omissions"),
        node("p", "", "These statements were confirmed by you when the draft was created. Keyword matches help order facts; they do not establish qualifications or hiring likelihood."));
      for (const fact of packet.fact_evidence) {
        const row = node("div", "packet-evidence-row");
        row.append(node("strong", "", fact.id + " · " + fact.section), node("p", "", fact.statement));
        row.append(node("small", "", fact.matched_keywords.length
          ? "Posting keyword matches: " + fact.matched_keywords.join(", ")
          : "No listed keywords matched the posting."));
        evidence.append(row);
      }
      evidence.append(node("p", "", packet.omitted_fact_ids.length
        ? "Verified facts not included in this draft: " + packet.omitted_fact_ids.join(", ") + ". Review these in Career profile."
        : "All verified facts available at drafting were included."));
      card.append(evidence);
    } else {
      card.append(node("p", "muted", "Fact-level comparison is available for newly generated packets. This saved packet remains valid under its original fingerprint."));
    }

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
      const downloadWord = button("Download reviewed resume Word", "button", async () => {
        downloadWord.disabled = true;
        try {
          const response = await fetch("/api/jobs/" + job.id + "/resume.docx?packet_sha256=" + packet.packet_sha256, {
            headers: {"X-Session-Token": token}, cache: "no-store", credentials: "same-origin"
          });
          if (!response.ok) {
            const error = await response.json().catch(() => ({}));
            throw new Error(error.detail || "Word export failed");
          }
          const url = URL.createObjectURL(await response.blob());
          const link = document.createElement("a");
          link.href = url;
          link.download = "resume-" + job.id + "-" + packet.packet_sha256.slice(0, 12) + ".docx";
          document.body.append(link);
          link.click();
          link.remove();
          setTimeout(() => URL.revokeObjectURL(url), 1000);
          notice("Reviewed Word resume downloaded. Changes made outside this app are not tracked; review them before use.");
        } catch (error) { notice(error.message, true); }
        finally { downloadWord.disabled = false; }
      });
      tools.append(downloadWord);
      const downloadCover = button("Download reviewed cover note", "button", async () => {
        downloadCover.disabled = true;
        try {
          const response = await fetch("/api/jobs/" + job.id + "/cover-note.txt?packet_sha256=" + packet.packet_sha256, {
            headers: {"X-Session-Token": token}, cache: "no-store", credentials: "same-origin"
          });
          if (!response.ok) {
            const error = await response.json().catch(() => ({}));
            throw new Error(error.detail || "Cover note export failed");
          }
          const url = URL.createObjectURL(await response.blob());
          const link = document.createElement("a"); link.href = url;
          link.download = "cover-note-" + job.id + "-" + packet.packet_sha256.slice(0, 12) + ".txt";
          document.body.append(link); link.click(); link.remove();
          setTimeout(() => URL.revokeObjectURL(url), 1000);
          notice("Reviewed cover note downloaded. Edits made outside this app are not tracked; review before use.");
        } catch (error) { notice(error.message, true); }
        finally { downloadCover.disabled = false; }
      });
      tools.append(downloadCover);
      tools.append(node("p", "muted", "Edits made after download are not tracked or covered by this approval."));
    } else {
      tools.append(node("span", "muted", "Approve the current packet to download a résumé or cover note."));
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
    if (!stale && review && review.decision === "approved_for_manual_submission" && job.application_stage !== "submitted" &&
        !job.lever_dispatch_requested && job.delivery?.state !== "duplicate") {
      const submitted = node("div", "detail-section");
      submitted.append(node("h4", "", "Already applied on employer site?"));
      submitted.append(node("p", "", "Only record submission after you have submitted it yourself and received confirmation."));
      const receiptLabel = node("label", "field", "Employer confirmation reference");
      const receipt = document.createElement("input");
      receipt.type = "text"; receipt.minLength = 4; receipt.maxLength = 200;
      receipt.id = "submission-reference";
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
    if (job.application_stage === "submitted") card.append(node("p", "", job.delivery?.dispatched
      ? "Submission recorded. See Application delivery for the receipt."
      : "Submission recorded from your attestation. See Application delivery for the receipt."));
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
      resume_contact: byId("career-resume-contact").value.trim(),
      target_titles: linesFromText(byId("career-targets").value),
      skills: linesFromText(byId("career-skills").value),
      preferred_locations: linesFromText(byId("career-locations").value),
      preferred_title_levels: linesFromText(byId("career-title-levels").value),
      allow_remote: byId("career-remote").checked,
      require_remote: byId("career-require-remote").checked,
      excluded_companies: linesFromText(byId("career-excluded-companies").value),
      minimum_annual_base_usd: byId("career-minimum-base").value.trim() ? Number(byId("career-minimum-base").value) : null
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
    byId("career-resume-contact").value = p.resume_contact || "";
    byId("career-targets").value = p.target_titles.join("\n");
    byId("career-skills").value = p.skills.join("\n");
    byId("career-locations").value = p.preferred_locations.join("\n");
    byId("career-title-levels").value = (p.preferred_title_levels || []).join("\n");
    byId("career-remote").checked = p.allow_remote;
    byId("career-require-remote").checked = p.require_remote;
    byId("career-excluded-companies").value = p.excluded_companies.join("\n");
    byId("career-minimum-base").value = p.minimum_annual_base_usd == null ? "" : String(p.minimum_annual_base_usd);
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

  async function loadWorkspace(preserveEditor = false) {
    const sequence = ++workspaceLoadSequence;
    try {
      const [data, history] = await Promise.all([
        api("/api/workspace"), api("/api/workspace/history")
      ]);
      if (sequence !== workspaceLoadSequence || state.screen !== "profile") return;
      if (state.workspace && data.workspace.revision < state.workspace.revision) return;
      const draft = preserveEditor && state.workspace ? snapshotEditorInputs() : null;
      state.workspace = data.workspace;
      renderWorkspace(history.revisions);
      if (draft) restoreEditorInputs(draft);
    } catch (error) {
      if (sequence === workspaceLoadSequence) notice("Unable to load private career workspace: " + error.message, true);
    }
  }

  root.addEventListener("career-workspace-changed", () => { if (state.screen === "profile") loadWorkspace(true); });

  function renderWorkspace(revisions) {
    const workspace = state.workspace;
    if (!workspace) return;
    const profile = workspace.profile;
    byId("profile-revision").textContent = "Revision " + workspace.revision;
    byId("career-name").value = profile.name;
    byId("career-headline").value = profile.headline;
    byId("career-resume-contact").value = profile.resume_contact || "";
    byId("career-targets").value = profile.target_titles.join("\n");
    byId("career-skills").value = profile.skills.join("\n");
    byId("career-locations").value = profile.preferred_locations.join("\n");
    byId("career-title-levels").value = (profile.preferred_title_levels || []).join("\n");
    byId("career-remote").checked = profile.allow_remote;
    byId("career-require-remote").checked = profile.require_remote || false;
    byId("career-excluded-companies").value = (profile.excluded_companies || []).join("\n");
    byId("career-minimum-base").value = profile.minimum_annual_base_usd == null ? "" : String(profile.minimum_annual_base_usd);
    byId("legacy-import").hidden = workspace.revision !== 0 || !state.settings.legacy_import_available;
    byId("career-portable-import").hidden = workspace.revision !== 0;
    byId("career-export").disabled = workspace.revision === 0;
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
      if (fact.derived_from) {
        card.append(node("small", "muted", "AI wording source fact " + fact.derived_from.fact_id + ": " + fact.derived_from.statement));
        card.append(node("small", "muted", "Source posting excerpt: " + fact.derived_from.job_quote));
      }
      card.append(node("small", "muted", "Private evidence: " + fact.source_note));
      const actions = node("div", "button-row");
      const factActions = [button("Edit", "button", () => editFact(fact)),
        button(fact.verified_by_candidate ? "Revoke verification" : "Verify this fact", fact.verified_by_candidate ? "button" : "button button-primary", () => toggleFactVerification(fact)),
        button("Delete", "button button-danger", () => deleteFact(fact.id))];
      for (const action of factActions) { action.disabled = workspaceMutationBusy; actions.append(action); }
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

  function setWorkspaceMutationBusy(busy) {
    workspaceMutationBusy = busy;
    byId("profile-screen").inert = busy;
    byId("career-facts-list").inert = busy;
    for (const action of byId("career-facts-list").querySelectorAll?.(".career-fact button") || []) action.disabled = busy;
    byId("profile-screen").setAttribute("aria-busy", String(busy));
    for (const id of ["career-profile-form", "career-fact-form"]) {
      const submit = byId(id).querySelector?.('button[type="submit"]');
      if (submit) submit.disabled = busy;
    }
    byId("import-career-data").disabled = busy;
  }

  async function saveCareerWorkspace(profile, facts, actionLabel) {
    if (workspaceMutationBusy) return;
    if (!state.workspace) return notice("Open the career profile first.", true);
    setWorkspaceMutationBusy(true);
    try {
      const response = await api("/api/workspace", "PUT", {
        expected_revision: state.workspace.revision, profile, facts
      });
      state.workspace = response.workspace;
      // A saved fact closes its editor. Saving preferences must not erase
      // a separate fact draft that the candidate was still typing.
      if (actionLabel !== "Preferences" && actionLabel !== "Résumé selections") resetFactEditor();
      notice(actionLabel + " saved locally. Any added or changed career fact must be verified separately.");
      await refresh(undefined, true);
      return true;
    } catch (error) {
      notice(error.status === 409 ? "Another edit was saved. Reload the profile before editing." : error.message, true);
      return false;
    } finally {setWorkspaceMutationBusy(false);}
  }

  async function toggleFactVerification(fact) {
    if (!state.workspace || workspaceMutationBusy) return;
    if (!fact.verified_by_candidate && !window.confirm(
      "Confirm this career statement is accurate and supported by your private evidence?"
    )) return;
    setWorkspaceMutationBusy(true);
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
    finally {setWorkspaceMutationBusy(false);}
  }

  async function deleteFact(factId) {
    if (workspaceMutationBusy || !state.workspace || !window.confirm("Delete this fact from the current career profile? Its historical revisions remain stored locally.")) return;
    if (state.editingFactId === factId) resetFactEditor();
    await saveCareerWorkspace(state.workspace.profile, state.workspace.facts.filter((fact) => fact.id !== factId), "Fact");
  }

  async function importConfiguredProfile() {
    if (workspaceMutationBusy) return;
    if (!window.confirm("Copy your configured profile and verified facts into this private local workspace? Your original files will remain unchanged.")) return;
    setWorkspaceMutationBusy(true);
    try {
      const response = await api("/api/workspace/import", "POST", {acknowledged: true});
      state.workspace = response.workspace;
      resetFactEditor();
      notice("Existing career files imported into your local workspace.");
      await refresh(undefined, false);
    } catch (error) {notice(error.message, true);}
    finally {setWorkspaceMutationBusy(false);}
  }

  byId("career-export").addEventListener("click", async () => {
    const control = byId("career-export"), status = byId("career-portable-status");
    if (control.disabled || workspaceMutationBusy) return;
    control.disabled = true;
    try {
      const response = await fetch('/api/workspace/export', {headers: {'X-Session-Token': token}, cache: 'no-store', credentials: 'same-origin'});
      if (!response.ok) {
        const error = await response.json().catch(() => ({}));
        throw new Error(typeof error.detail === 'string' ? error.detail : 'Profile export failed');
      }
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement('a'); link.href = url; link.download = 'career-profile.json';
      document.body.append(link); link.click(); link.remove(); URL.revokeObjectURL(url);
      status.textContent = 'Profile downloaded. It contains private data and is not encrypted or a full backup.';
    } catch (error) { status.textContent = error.message; }
    finally { control.disabled = !state.workspace || state.workspace.revision === 0; }
  });

  byId("career-import").addEventListener("click", async () => {
    if (workspaceMutationBusy || !state.workspace || state.workspace.revision !== 0) return;
    const file = byId("career-import-file").files[0], status = byId("career-portable-status");
    if (!file || !file.size || file.size > 4 * 1024 * 1024) { status.textContent = 'Choose a nonempty profile JSON file up to 4 MB.'; return; }
    if (!window.confirm('Import this file into the empty saved profile? Unsaved editor text will be replaced, and all imported facts will require verification again.')) return;
    setWorkspaceMutationBusy(true);
    try {
      let document;
      try { document = JSON.parse(await file.text()); } catch (_) { throw new Error('Choose a valid career profile JSON export.'); }
      const result = await api('/api/workspace/import-portable', 'POST', {document, acknowledged: true});
      state.workspace = result.workspace;
      renderWorkspace([]);
      resetFactEditor();
      await refresh(undefined, false);
      status.textContent = 'Profile imported locally. Review and verify each fact before using it in a draft.';
      byId("career-import-file").value = '';
    } catch (error) { status.textContent = error.status === 409 ? 'A saved profile already exists. Reload before continuing; nothing was overwritten.' : error.message; }
    finally { setWorkspaceMutationBusy(false); }
  });


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
      if (url.endsWith("/crm/stage")) await refresh(state.selectedId);
      else await loadCRM();
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
    const total = Number.isInteger(summary.open_followup_count) ? summary.open_followup_count : summary.open_followups.length;
    if (total > summary.open_followups.slice(0, 6).length) due.append(node("p", "muted", "Showing the first " + Math.min(6, summary.open_followups.length) + " of " + total + " open follow-ups."));
    const allTasks = node("div");
    let loadingTasks = false;
    async function showTasks(offset) {
      if (loadingTasks) return;
      loadingTasks = true; allTasks.replaceChildren(node("p", "muted", "Loading local follow-ups…"));
      try {
        const page = await api("/api/crm/followups?offset=" + offset);
        if (state.crmSummary !== summary || state.screen !== "tracker") return;
        allTasks.replaceChildren(node("p", "muted", "Open follow-ups " + (page.tasks.length ? offset + 1 : 0) + "–" + (page.tasks.length ? offset + page.tasks.length : 0) + " of " + page.total + ". Dates use this computer's local calendar. Lists can change; refresh to recheck."));
        for (const task of page.tasks) {
          const entry = node("div", "crm-record");
          entry.append(node("strong", "", task.title), node("small", task.overdue ? "overdue" : "", task.due_date + " · " + task.company + " · " + task.job_title));
          const controls = node("div", "button-row");
          controls.append(button("Open tracked job", "button", () => {
            if (state.crmJobId && !window.confirm("Open this job? Unsaved tracker form edits will be discarded.")) return;
            selectCRMJob(task.job_id);
          }));
          controls.append(button("Mark completed", "button", async () => {
            if (!window.confirm("Confirm this follow-up was completed? No email will be sent." + (state.crmJobId ? " Unsaved tracker form edits will be discarded." : ""))) return;
            await crmPost("/api/crm/followups/" + task.followup_id + "/complete", {acknowledged: true}, "Follow-up marked completed.");
          }));
          controls.append(button("Cancel follow-up", "button", async () => {
            if (!window.confirm("Cancel this follow-up? No message will be sent." + (state.crmJobId ? " Unsaved tracker form edits will be discarded." : ""))) return;
            await crmPost("/api/crm/followups/" + task.followup_id + "/cancel", {acknowledged: true}, "Follow-up cancelled.");
          }));
          const date = node("input"); date.type = "date"; date.value = task.due_date;
          date.setAttribute("aria-label", "Reschedule " + task.title);
          controls.append(date, button("Reschedule", "button", () => {
            if (state.crmJobId && !window.confirm("Reschedule this task? Unsaved tracker form edits will be discarded.")) return;
            return crmPost("/api/crm/followups/" + task.followup_id + "/reschedule", {due_date: date.value}, "Follow-up rescheduled.");
          }));
          entry.append(controls); allTasks.append(entry);
        }
        if (offset > 0) allTasks.append(button("Previous follow-ups", "button", () => showTasks(Math.max(0, offset - 20))));
        if (Number.isInteger(page.next_offset)) allTasks.append(button("Next follow-ups", "button", () => showTasks(page.next_offset)));
      } catch (error) { if (state.crmSummary === summary) allTasks.textContent = error.message; }
      finally { loadingTasks = false; }
    }
    if (total) due.append(button("Browse all open follow-ups", "button", () => showTasks(0)), allTasks);
    root.append(due);
    const suggestions = node("div", "crm-due-list");
    suggestions.append(node("h3", "", "Applications to review"), node("p", "muted",
      "Confirmed applications with no pending follow-up and at least 14 days since the last recorded active stage. Check the employer’s latest response before deciding whether to follow up."));
    const review = summary.review_suggestions || [];
    if (!review.length) suggestions.append(node("p", "muted", "No applications currently meet these criteria."));
    for (const item of review) {
      const row = button("", "crm-due-item", () => selectCRMJob(item.job_id));
      row.append(node("strong", "", item.title + " · " + item.company),
        node("small", "", item.stage + " · recorded " + item.recorded_on + " · " + item.days + " calendar days ago (UTC)"));
      suggestions.append(row);
    }
    if ((summary.review_suggestion_count || 0) > review.length)
      suggestions.append(node("p", "muted", "Showing " + review.length + " of " + summary.review_suggestion_count + " applications to review."));
    root.append(suggestions);
    const outcomePanel = byId("crm-outcomes");
    outcomePanel.replaceChildren();
    outcomePanel.append(node("h3", "", "Recorded application outcomes"));
    const outcomes = summary.outcomes;
    if (!outcomes) return;
    const milestones = node("div", "crm-stage-counts");
    for (const [key, label] of [["submitted", "Confirmed submissions"], ["interviewed", "Interview recorded"],
      ["offered", "Offer recorded"], ["accepted", "Accepted offer"]]) {
      const metric = node("div", "crm-count");
      metric.append(node("strong", "", outcomes[key]), node("small", "", label));
      milestones.append(metric);
    }
    outcomePanel.append(milestones);
    if (outcomes.submitted) {
      outcomePanel.append(node("p", "muted", "Interview rate: " + outcomes.interview_rate + "% · Offer rate: " +
        outcomes.offer_rate + "% · Denominator: " + outcomes.submitted + " confirmed submissions."));
    } else {
      outcomePanel.append(node("p", "muted", "Rates appear after a submission is confirmed. Drafts and approvals are excluded."));
    }
    outcomePanel.append(node("p", "muted", "All-time candidate-recorded milestones, counted once per opportunity. Later outcomes retain earlier milestones; skipped stages are not inferred. These records are not independently verified or predictions."));
  }

  function renderCRMJobs() {
    const list = byId("crm-jobs");
    list.replaceChildren();
    const boardView = byId("crm-layout").value === "board";
    list.classList.toggle("crm-kanban", boardView);
    byId("crm-layout-help").textContent = boardView ? "Scroll across stages and select a role to review its details. Change its stage in the details panel." : "";
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
    if (!visible.length) { list.append(node("p", "loading", "No opportunities match these filters.")); return; }
    const stages = [
      ["Discovered", ["discovered"]], ["Saved", ["saved", "shortlisted"]],
      ["Applied", ["applied"]], ["Interviewing", ["screening", "assessment", "interview"]],
      ["Offers", ["offer", "accepted"]], ["Closed", ["archived", "rejected", "withdrawn"]],
      ["Other", []]
    ];
    const columns = new Map();
    if (boardView) {
      for (const [title, values] of stages) {
        const members = visible.filter(job => values.length ? values.includes(job.stage) : !stages.some(([, known]) => known.includes(job.stage)));
        if (title === "Other" && !members.length) continue;
        const column = node("section", "crm-kanban-column");
        column.setAttribute("aria-label", title + " opportunities");
        column.append(node("h3", "crm-kanban-heading", title + " · " + members.length));
        const cards = node("div", "crm-kanban-cards");
        column.append(cards); list.append(column);
        for (const job of members) columns.set(job.job_id, cards);
      }
    }
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
      if (job.delivery) chips.append(chip(deliveryLabel(job.delivery), job.delivery.state === "confirmed" ? "teal" : "amber"));
      else if (job.application_stage === "submitted") chips.append(chip("Manually submitted", ""));
      item.append(chips);
      (boardView ? columns.get(job.id) : list).append(item);
    }
  }

  async function selectCRMJob(jobId, followupOffset = 0) {
    state.crmJobId = jobId;
    state.crmDetails = null;
    const requestNumber = ++state.crmRequestSeq;
    renderCRMJobs();
    renderCRMDetail();
    if (byId("crm-layout").value === "board") {
      const panel = byId("crm-detail");
      panel.setAttribute("tabindex", "-1");
      panel.scrollIntoView?.({behavior: "smooth", block: "start"});
      panel.focus?.({preventScroll: true});
    }
    try {
      const details = await api("/api/jobs/" + jobId + "/crm" + (followupOffset ? "?followup_offset=" + followupOffset : ""));
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
    if (data.delivery) panel.append(deliveryPanel(item.id, data.delivery));
    if (!data.delivery || (data.application_submitted && !data.delivery.dispatched)) panel.append(node("p", "muted", data.application_submitted
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
      statusSection.append(node("p", "muted", "This is a final pipeline outcome."));
      if (data.correction_option) {
        const correction = data.correction_option;
        const form = node("form", "crm-form");
        form.append(node("p", "", `If ${correction.from_stage} was recorded by mistake, restore ${correction.restore_stage}. The original event and submission receipt remain in history.`));
        const reason = crmInput("Reason for correction (private)", "reason", "text", true, 1000);
        const confirm = node("label", "check-label");
        const checked = document.createElement("input"); checked.type = "checkbox"; checked.required = true;
        confirm.append(checked, document.createTextNode("I confirm this outcome was recorded in error."));
        const submit = button("Correct mistaken outcome", "button"); submit.type = "submit";
        form.append(reason.field, confirm, submit);
        form.addEventListener("submit", async (event) => {
          event.preventDefault();
          if (submit.disabled || !checked.checked || !reason.control.value.trim()) return;
          submit.disabled = true;
          try {
            await api("/api/jobs/" + item.id + "/crm/correct-terminal", "POST", {
              expected_event_id: correction.event_id, reason: reason.control.value, acknowledged: true
            });
            notice("Outcome corrected locally; submission receipt is unchanged.");
            await loadCRM();
          } catch (error) { notice(error.message, true); }
          finally { submit.disabled = false; }
        });
        statusSection.append(form);
      }
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
    if (!data.followups.length) tasks.append(node("p", "muted", data.followup_offset ? "No tasks on this page; the list may have changed." : "No follow-up tasks yet."));
    for (const followup of data.followups) {
      const row = node("div", "crm-record");
      row.append(node("strong", "", followup.title));
      row.append(node("small", "", followup.due_date + (followup.contact_name ? " · " + followup.contact_name : "")));
      if (followup.cancelled) {
        row.append(chip("Cancelled", "muted"));
      } else if (followup.completed_at) {
        row.append(chip("Completed", "teal"));
      } else {
        const buttons = node("div", "button-row");
        buttons.append(button("Mark completed", "button", async () => {
          if (!window.confirm("Confirm this follow-up was completed? No email will be sent.")) return;
          await crmPost("/api/crm/followups/" + followup.followup_id + "/complete",
            {acknowledged: true}, "Follow-up marked completed.");
        }));
        buttons.append(button("Cancel follow-up", "button", async () => {
          if (!window.confirm("Cancel this follow-up? No message will be sent.")) return;
          await crmPost("/api/crm/followups/" + followup.followup_id + "/cancel",
            {acknowledged: true}, "Follow-up cancelled.");
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
    const taskOffset = data.followup_offset || 0;
    const taskTotal = Number.isInteger(data.followup_total) ? data.followup_total : data.followups.length;
    if (taskTotal > data.followups.length || taskOffset) {
      tasks.append(node("p", "muted", "Showing " + (data.followups.length ? taskOffset + 1 : 0) + "–" + (data.followups.length ? taskOffset + data.followups.length : 0) + " of " + taskTotal + " follow-ups. Lists can change; refresh to recheck."));
      const page = (offset) => {
        if (window.confirm("Change follow-up page? Unsaved tracker form edits on this job will be discarded.")) selectCRMJob(item.id, offset);
      };
      if (taskOffset) tasks.append(button("Previous job follow-ups", "button", () => page(Math.max(0, taskOffset - 100))));
      if (Number.isInteger(data.followup_next_offset)) tasks.append(button("Next job follow-ups", "button", () => page(data.followup_next_offset)));
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
      if (event.corrects_event_id) row.append(node("small", "muted", "Correction of event " + event.corrects_event_id));
      if (event.corrected_by_event_id) row.append(node("small", "muted", "Corrected by event " + event.corrected_by_event_id));
      if (event.note) row.append(node("p", "", event.note));
      history.append(row);
    }
    panel.append(history);
  }

  async function loadCRM() {
    if (state.screen === "tracker") { state.crmDetails = null; renderCRMDetail(); }
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

  async function loadBoardJobs(sequence) {
    let cursor = null, previous = Infinity;
    let characters = 0;
    const jobs = [], maxJobs = 10000, maxCharacters = 32 * 1024 * 1024;
    do {
      const page = await api("/api/jobs?limit=100&include_archived=true" + (cursor === null ? "" : "&before=" + cursor));
      if (sequence !== state.refreshSequence) return null;
      if (!Array.isArray(page.jobs) || page.jobs.length > 100) throw new Error("Invalid opportunity page");
      const pageCharacters = JSON.stringify(page.jobs).length;
      if (characters + pageCharacters > maxCharacters) {
        if (!jobs.length) throw new Error("Opportunity data exceeds the board loading limit");
        return {jobs, truncated: true};
      }
      characters += pageCharacters;
      jobs.push(...page.jobs);
      cursor = page.next_before ?? null;
      if (cursor !== null && !page.jobs.length) throw new Error("Invalid empty opportunity page");
      if (cursor !== null && (!Number.isSafeInteger(cursor) || cursor < 1 || cursor >= previous)) {
        throw new Error("Invalid opportunity cursor; refresh to retry");
      }
      if (cursor !== null) previous = cursor;
      if (jobs.length >= maxJobs && cursor !== null) return {jobs, truncated: true};
    } while (cursor !== null);
    return {jobs, truncated: false};
  }

  async function refresh(keepSelected, preserveEditor = true) {
    const sequence = ++state.refreshSequence;
    const screenAtStart = state.screen;
    const trackerRequestAtStart = state.crmRequestSeq;
    const profileNavigationAtStart = profileNavigationSequence;
    try {
      const results = await Promise.all([
        loadBoardJobs(sequence), api("/api/overview"), api("/api/settings"), api("/api/reviews")
      ]);
      if (sequence !== state.refreshSequence || results[0] === null) return;
      state.jobs = results[0].jobs;
      state.boardTruncated = results[0].truncated;
      state.overview = results[1];
      state.settings = results[2];
      // Settings may arrive after profile navigation. Update this control
      // without rerendering the editor or replacing any in-progress inputs.
      if (state.workspace) byId("legacy-import").hidden = state.workspace.revision !== 0 || !state.settings.legacy_import_available;
      state.reviews = results[3].reviews;
      const previous = keepSelected || state.selectedId;
      const oldEditor = byId("detail-panel").querySelector?.("#job-description-editor");
      const keepEditor = oldEditor?.open && oldEditor.dataset.jobId === previous;
      state.selectedId = state.jobs.some((job) => job.id === previous) || keepEditor ? previous : null;
      if (keepSelected && state.selectedId) {
        const index = filteredJobs().findIndex((job) => job.id === state.selectedId);
        if (index >= 0) state.jobsPage = Math.floor(index / 10);
      }
      renderMetrics();
      renderJobs();
      renderActivity();
      const selectedId = state.selectedId;
      const previousPacketHash = state.packet?.packet?.packet_sha256;
      if (selectedId) await loadPacket(selectedId); else state.packet = null;
      if (sequence !== state.refreshSequence) return;
      if (state.selectedId === selectedId) {
        const receipt = byId("detail-panel").querySelector?.("#submission-reference")?.value;
        renderDetailPreservingEditor();
        if (receipt !== undefined && previousPacketHash && previousPacketHash === state.packet?.packet?.packet_sha256) {
          const input = byId("detail-panel").querySelector?.("#submission-reference");
          if (input) input.value = receipt;
        }
      }
      if (state.screen === "profile" && screenAtStart === "profile" && profileNavigationSequence === profileNavigationAtStart) {
        await loadWorkspace(preserveEditor);
      }
      // Navigation to the tracker starts its own load. A refresh that began on
      // another screen must not reload it again and overwrite an active form.
      if (state.screen === "tracker" && screenAtStart === "tracker" && state.crmRequestSeq === trackerRequestAtStart) await loadCRM();
    } catch (error) {
      if (sequence === state.refreshSequence) notice("Unable to load local workspace: " + error.message, true);
    }
  }

  async function loadHome() {
    const list = byId("home-followups");
    list.replaceChildren(node("p", "muted", "Loading local follow-ups…"));
    try {
      const summary = await api("/api/crm");
      if (state.screen !== "home") return;
      list.replaceChildren();
      if (!summary.open_followups?.length) list.append(node("p", "muted", "No pending follow-ups. Add one from the application tracker when you need it."));
      for (const task of (summary.open_followups || []).slice(0, 3)) {
        const item = button("", "home-followup", async () => {
          showScreen("tracker");
          await loadCRM();
          if (state.screen === "tracker") await selectCRMJob(task.job_id);
        });
        item.append(node("strong", "", task.title), node("small", "", task.due_date + " · " + task.company));
        list.append(item);
      }
      if (summary.open_followup_count > 3) list.append(node("p", "muted", "Open the application tracker to see all follow-ups."));
    } catch (error) { if (state.screen === "home") list.replaceChildren(node("p", "muted", "Unable to load follow-ups: " + error.message)); }
  }
  byId("nav-home").addEventListener("click", () => { showScreen("home"); loadHome(); });
  byId("nav-settings").addEventListener("click", () => showScreen("settings"));
  byId("settings-models").addEventListener("click", () => byId("nav-ai").click());
  byId("settings-searches").addEventListener("click", () => byId("nav-automation").click());
  byId("settings-records").addEventListener("click", () => {
    byId("nav-h1b").click();
    byId("h1b-data-admin-heading").scrollIntoView?.({behavior: "smooth", block: "start"});
  });
  byId("settings-profile-export").addEventListener("click", () => {
    byId("nav-profile").click();
    const target = byId("career-export");
    const details = target.closest("details");
    if (details) details.open = true;
    target.scrollIntoView?.({behavior: "smooth", block: "center"});
    target.focus();
  });
  let wizardStep = 0, wizardActive = false, wizardResume = null;
  function showWizardStep(step, focusInput = false) {
    wizardStep = step;
    const headings = ["1 of 3 · Preferences", "2 of 3 · Import a résumé", "3 of 3 · Review and verify facts"];
    const instructions = ["Enter your name and goals, then choose Save preferences. A saved name is required before verifying facts.",
      "Extract text locally or paste it. Choose statements to stage for review; this step is optional.",
      "Review each statement and its source. Save new facts as unverified, then verify only the claims you can confirm. Your name must be saved in Preferences first."];
    byId("profile-wizard-heading").textContent = headings[step];
    byId("profile-wizard-instructions").textContent = instructions[step];
    byId("career-profile-form").hidden = step !== 0;
    byId("profile-wizard-resume").hidden = step !== 1;
    byId("career-facts-panel").hidden = step !== 2;
    byId("profile-wizard-previous").disabled = step === 0;
    byId("profile-wizard-next").textContent = step === 2 ? "Review setup completion" : "Next step";
    const target = focusInput ? byId(step === 1 ? "resume-file" : step === 0 ? "career-name" : "fact-statement") : byId("profile-wizard-heading");
    target.scrollIntoView?.({behavior: "smooth", block: "center"}); target.focus();
  }
  function startProfileWizard(step, focusInput = false) {
    if (!wizardActive) {
      wizardResume = byId("resume-file").closest("details");
      if (!wizardResume) return;
      byId("profile-wizard-resume").append(wizardResume);
      wizardResume.open = true; wizardActive = true;
    }
    byId("profile-wizard").hidden = false;
    for (const id of ["profile-portability", "profile-assistant", "profile-revisions-panel"]) byId(id).hidden = true;
    showWizardStep(step, focusInput);
  }
  function exitProfileWizard() {
    if (!wizardActive) return;
    byId("career-facts-panel").insertBefore(wizardResume, byId("career-fact-form"));
    byId("profile-wizard").hidden = true; byId("profile-wizard-resume").hidden = true;
    byId("career-profile-form").hidden = false; byId("career-facts-panel").hidden = false;
    for (const id of ["profile-portability", "profile-assistant", "profile-revisions-panel"]) byId(id).hidden = false;
    wizardActive = false; wizardResume = null;
    byId("profile-start-guided").focus();
  }
  byId("profile-start-guided").addEventListener("click", () => startProfileWizard(0));
  byId("profile-start-resume").addEventListener("click", () => startProfileWizard(1, true));
  byId("profile-start-preferences").addEventListener("click", () => startProfileWizard(0, true));
  byId("profile-wizard-previous").addEventListener("click", () => { if (wizardActive && wizardStep > 0) showWizardStep(wizardStep - 1); });
  byId("profile-wizard-next").addEventListener("click", () => {
    if (!wizardActive) return;
    if (wizardStep < 2) { showWizardStep(wizardStep + 1); return; }
    const profile = state.workspace?.profile;
    if (!profile?.name || !profile.target_titles?.length || !profile.skills?.length) {
      showWizardStep(0);
      notice("Setup is incomplete. Save your name, target roles and skills in Preferences. You can exit guided setup and return later.", true);
      return;
    }
    if (Object.entries(readCareerPreferences()).some(([key, value]) => JSON.stringify(value) !== JSON.stringify(profile[key]))) {
      showWizardStep(0);
      notice("Review and save your preference changes before completing setup.", true);
      return;
    }
    if (!state.workspace.facts.some(fact => fact.verified_by_candidate)) {
      notice("Setup is incomplete. Save and individually verify at least one career fact. Only confirm statements you know are accurate.", true);
      return;
    }
    if (byId("fact-statement").value.trim()) {
      notice("There is a statement in the fact editor. Save it or clear the form before completing setup.", true);
      return;
    }
    exitProfileWizard();
    notice("Profile setup complete. Review your verified facts before preparing applications.");
  });
  byId("profile-wizard-exit").addEventListener("click", exitProfileWizard);
  byId("home-next").addEventListener("click", () => byId("nav-" + (byId("home-next").dataset.destination || "profile")).click());
  byId("open-discovery").addEventListener("click", () => { byId("discovery-panel").open = true; byId("discover-query").focus(); });
  byId("home-jobs").addEventListener("click", () => byId("nav-jobs").click());
  byId("home-tracker").addEventListener("click", () => byId("nav-tracker").click());
  byId("home-h1b").addEventListener("click", () => byId("nav-h1b").click());
  byId("nav-jobs").addEventListener("click", () => showScreen("jobs"));
  byId("nav-h1b").addEventListener("click", () => showScreen("h1b"));
  byId("nav-automation").addEventListener("click", () => {showScreen("automation"); byId("automation-panel").open = true; byId("automation-refresh").click();});
  byId("nav-ai").addEventListener("click", () => {showScreen("ai"); byId("ai-panel").open = true; byId("ai-load").click();});
  byId("open-profile").addEventListener("click", () => byId("nav-profile").click());
  byId("setup-find-jobs").addEventListener("click", () => {
    byId("discovery-panel").open = true;
    byId("discover-query").focus();
  });
  byId("setup-add-job").addEventListener("click", () => byId("show-add-job").click());
  byId("ai-open-profile").addEventListener("click", () => byId("nav-profile").click());
  const manualSources = [["discover-site-indeed", "indeed"], ["discover-site-linkedin", "linkedin"], ["discover-site-zip", "zip_recruiter"]];
  for (const id of ["discover-query", "discover-location", "discover-results", "discover-details", "discover-stage-changes", ...manualSources.map(([id]) => id)]) {
    byId(id).addEventListener("input", () => { byId("discover-consent").checked = false; });
    byId(id).addEventListener("change", () => { byId("discover-consent").checked = false; });
  }
  byId("discover-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const submit = byId("discover-submit");
    const status = byId("discover-status");
    if (submit.disabled) return;
    if (!byId("discover-consent").checked) {
      status.textContent = "Confirm sending this search to external job sources.";
      return;
    }
    const sites = manualSources.filter(([id]) => byId(id).checked).map(([, source]) => source);
    if (!sites.length) { status.textContent = "Choose at least one job source."; return; }
    if (byId("discover-stage-changes").checked && !byId("discover-details").checked) {
      status.textContent = "Request individual job details to retain changed source descriptions."; return;
    }
    const controls = ["discover-query", "discover-location", "discover-results", "discover-details", "discover-stage-changes", "discover-consent", ...manualSources.map(([id]) => id)];
    controls.forEach(id => { byId(id).disabled = true; });
    submit.disabled = true;
    byId("discover-form").setAttribute("aria-busy", "true");
    status.textContent = "Searching job sources… This may take a few minutes.";
    try {
      const result = await api("/api/discover", "POST", {
        query: byId("discover-query").value, location: byId("discover-location").value,
        results: Number(byId("discover-results").value), sites, ...(byId("discover-details").checked ? {fetch_details: true} : {}), ...(byId("discover-stage-changes").checked ? {stage_changed_descriptions: true} : {}), acknowledged: true
      });
      status.textContent = result.found + " returned · " + result.new + " added · " + result.duplicates +
        " duplicates. Sources may return partial results." + (result.found ? "" : " No usable rows is inconclusive and may indicate a source failure.");
      if (result.source_status) {
        const labels = {returned: "returned rows", reported_error: "reported an error", reported_warning: "reported a warning", inconclusive: "no usable rows; inconclusive"};
        status.textContent += " " + Object.entries(result.source_status).map(([source, item]) =>
          source + ": " + (labels[item.state] || "unknown") + " (" + item.returned + ")").join(" · ");
      }
      byId("discover-consent").checked = false;
      await refresh(undefined, true);
    } catch (error) {
      status.textContent = error.message;
    } finally {
      controls.forEach(id => { byId(id).disabled = false; });
      submit.disabled = false;
      byId("discover-form").setAttribute("aria-busy", "false");
    }
  });
  byId("nav-activity").addEventListener("click", () => showScreen("activity"));
  byId("nav-profile").addEventListener("click", async () => {
    if (state.screen === "profile") return;
    showScreen("profile");
    if (workspaceMutationBusy) return;
    const sequence = ++profileNavigationSequence;
    const profileScreen = byId("profile-screen");
    profileScreen.inert = true;
    profileScreen.setAttribute("aria-busy", "true");
    try { await loadWorkspace(true); }
    finally {
      if (sequence === profileNavigationSequence && !workspaceMutationBusy) {
        profileScreen.inert = false; profileScreen.setAttribute("aria-busy", "false");
      }
    }
  });
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
  byId("crm-layout").addEventListener("change", renderCRMJobs);
  byId("crm-priority-filter").addEventListener("change", renderCRMJobs);
  byId("crm-clear-filters").addEventListener("click", () => {
    for (const id of ["crm-search", "crm-stage-filter", "crm-priority-filter"]) byId(id).value = "";
    renderCRMJobs();
  });
  byId("import-career-data").addEventListener("click", importConfiguredProfile);
  let profileChatRoutes = [];
  let profileChatBusy = false;
  let profileChatHistory = [];
  byId("profile-chat-clear").addEventListener("click", () => {
    if (profileChatBusy) return;
    profileChatHistory = [];
    byId("profile-chat-answer").replaceChildren();
    byId("profile-chat-consent").checked = false;
    byId("profile-chat-submit").disabled = !profileChatRoutes.length;
    byId("profile-chat-status").textContent = "Conversation cleared. Your question has been kept for editing.";
  });
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
      byId("profile-chat-submit").disabled = !profileChatRoutes.length || profileChatHistory.length >= 12;
    }
  });
  byId("profile-chat-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (profileChatBusy || !byId("profile-chat-consent").checked || profileChatHistory.length >= 12) return;
    profileChatBusy = true;
    const ids = ["profile-chat-load", "profile-chat-submit", "profile-chat-route", "profile-chat-question", "profile-chat-facts", "profile-chat-consent", "profile-chat-clear"];
    ids.forEach((id) => {byId(id).disabled = true;});
    byId("profile-chat-status").textContent = "Waiting for the selected provider…";
    const question = byId("profile-chat-question").value.trim();
    try {
      const selected = profileChatRoutes.find((item) => item.route === byId("profile-chat-route").value);
      if (!selected?.route_fingerprint) throw new Error("Reload model routes and confirm the destination before sharing.");
      const response = await api("/api/workspace/chat", "POST", {
        route: selected.route,
        route_fingerprint: selected.route_fingerprint,
        message: question,
        history: profileChatHistory,
        include_verified_facts: byId("profile-chat-facts").checked,
        acknowledged: true
      });
      profileChatHistory.push({role: "user", content: question}, {role: "assistant", content: response.answer});
      byId("profile-chat-answer").append(node("p", "", "You: " + question), node("p", "", "Assistant: " + response.answer));
      byId("profile-chat-question").value = "";
      byId("profile-chat-status").textContent = `Unverified suggestion · ${response.provider}. No profile changes were made.` + modelInfo(response) +
        (profileChatHistory.length >= 12 ? " Conversation limit reached; clear it to start again." : "");
    } catch (error) {
      byId("profile-chat-status").textContent = error.message;
    } finally {
      profileChatBusy = false;
      ids.forEach((id) => {byId(id).disabled = false;});
      byId("profile-chat-submit").disabled = profileChatHistory.length >= 12 || !profileChatRoutes.length;
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
      byId("resume-ai-consent").checked = false;
      byId("resume-ai-results").replaceChildren();
      resumeSuggestions = [];
      byId("resume-lines").replaceChildren();
      byId("resume-use-line").disabled = true;
      byId("resume-bulk-lines").replaceChildren(); byId("resume-bulk-save").disabled = true; byId("resume-combine").disabled = true;
      status.textContent = "Text extracted. PDF page counters and clear line wraps are normalized. Compare with your original, then choose statements to review.";
    } catch (error) { status.textContent = error.message; }
    finally { control.disabled = false; }
  });
  byId("resume-text").addEventListener("input", () => {
    resumeSuggestions = [];
    byId("resume-lines").replaceChildren();
    byId("resume-use-line").disabled = true;
    byId("resume-bulk-lines").replaceChildren(); byId("resume-bulk-save").disabled = true; byId("resume-combine").disabled = true;
    byId("resume-bulk-status").textContent = "Find suggestions again after editing the résumé text.";
    byId("resume-status").textContent = "Extract suggestions again after editing the text.";
    byId("resume-ai-consent").checked = false;
    byId("resume-ai-results").replaceChildren();
    byId("resume-ai-status").textContent = "Résumé text changed; review it and consent again before requesting suggestions.";
  });
  let resumeAiRoutes = [], resumeAiBusy = false;
  function showResumeAiDestination() {
    byId("resume-ai-consent").checked = false;
    byId("resume-ai-results").replaceChildren();
    const route = resumeAiRoutes.find((item) => item.route === byId("resume-ai-route").value);
    byId("resume-ai-destination").textContent = route ? `${route.provider} · ${route.model} · ${route.destination}` : "No model route selected.";
    byId("resume-ai-submit").disabled = !route || resumeAiBusy;
  }
  byId("resume-ai-route").addEventListener("change", showResumeAiDestination);
  byId("resume-ai-load").addEventListener("click", async () => {
    if (resumeAiBusy) return;
    resumeAiBusy = true; byId("resume-ai-load").disabled = true; byId("resume-ai-submit").disabled = true;
    try {
      const result = await api("/api/ai/routes");
      resumeAiRoutes = result.routes;
      const select = byId("resume-ai-route"), previous = select.value;
      select.replaceChildren();
      for (const route of resumeAiRoutes) {
        const option = node("option", "", `${route.route} (${route.provider})`);
        option.value = route.route; select.append(option);
      }
      if (resumeAiRoutes.some((route) => route.route === previous)) select.value = previous;
      byId("resume-ai-status").textContent = resumeAiRoutes.length ? "Review the destination and résumé text, then consent." : "No model routes configured.";
    } catch (error) { resumeAiRoutes = []; byId("resume-ai-status").textContent = error.message; }
    finally { resumeAiBusy = false; byId("resume-ai-load").disabled = false; showResumeAiDestination(); }
  });
  byId("resume-ai-submit").addEventListener("click", async () => {
    if (resumeAiBusy || !byId("resume-ai-consent").checked || !byId("resume-ai-route").value) {
      byId("resume-ai-status").textContent = "Select a route, review the text and confirm sharing first."; return;
    }
    const source = byId("resume-text").value, route = byId("resume-ai-route").value;
    const selectedRoute = resumeAiRoutes.find((item) => item.route === route);
    if (!selectedRoute?.route_fingerprint) { byId("resume-ai-status").textContent = "Reload model routes and confirm the destination before sharing."; return; }
    if (!source.trim() || source.length > 20000) { byId("resume-ai-status").textContent = "Enter résumé text of at most 20,000 characters."; return; }
    resumeAiBusy = true; byId("resume-ai-submit").disabled = true; byId("resume-ai-load").disabled = true;
    byId("resume-ai-results").replaceChildren(); byId("resume-ai-status").textContent = "Waiting for the selected provider…";
    try {
      const response = await api("/api/workspace/ai-resume-suggestions", "POST", {route, route_fingerprint: selectedRoute.route_fingerprint, resume_text: source, acknowledged: true});
      if (byId("resume-text").value !== source || byId("resume-ai-route").value !== route) {
        byId("resume-ai-status").textContent = "Text or route changed; suggestions were discarded. Review and request again."; return;
      }
      byId("resume-ai-status").textContent = response.notice + modelInfo(response);
      if (!response.suggestions.length) byId("resume-ai-results").append(node("p", "muted", "No suggestions returned; this does not mean there are no career facts."));
      for (const suggestion of response.suggestions) {
        const card = node("section", "career-fact");
        card.append(node("strong", "", "Suggested category: " + suggestion.section), node("blockquote", "", suggestion.quote));
        card.append(button("Review this excerpt as a career fact", "button", () => {
          if (byId("resume-text").value !== source || byId("resume-ai-route").value !== route) {
            byId("resume-ai-status").textContent = "Text or route changed; request suggestions again."; return;
          }
          if ((state.editingFactId || byId("fact-statement").value || byId("fact-source").value || byId("fact-keywords").value) &&
              !window.confirm("Replace the unsaved fact editor contents with this résumé excerpt?")) return;
          resetFactEditor(); byId("fact-category").value = suggestion.section;
          byId("fact-statement").value = suggestion.quote;
          if (wizardActive) showWizardStep(2, true); else byId("fact-statement").focus();
          byId("resume-ai-status").textContent = "Review the excerpt and category, add a private evidence note, then save it unverified. Verification is a separate action.";
        }));
        byId("resume-ai-results").append(card);
      }
    } catch (error) { byId("resume-ai-status").textContent = error.message; }
    finally { resumeAiBusy = false; byId("resume-ai-submit").disabled = !resumeAiRoutes.length; byId("resume-ai-load").disabled = false; byId("resume-ai-consent").checked = false; }
  });
  // Sections are suggestions based only on explicit headings, not inferred credentials.
  const resumeHeadings = {
    "experience": "experience", "work experience": "experience", "professional experience": "experience", "employment history": "experience",
    "projects": "projects", "project experience": "projects", "thought leadership": "projects",
    "selected leadership impact": "experience",
    "skills": "skills", "technical skills": "skills", "core skills": "skills", "technology and platforms": "skills",
    "education": "education", "academic background": "education",
    "certifications": "certifications", "certificates": "certifications", "licenses and certifications": "certifications"
  };
  const otherResumeHeadings = new Set(["summary", "professional summary", "profile", "contact", "contact information", "awards", "interests", "references"]);
  let resumeSuggestions = [];
  byId("resume-extract").addEventListener("click", () => {
    const lines = byId("resume-text").value.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
    const select = byId("resume-lines");
    select.replaceChildren();
    const bulk = byId("resume-bulk-lines"); bulk.replaceChildren();
    resumeSuggestions = [];
    let section = null;
    for (const line of lines) {
      const heading = line.replace(/:$/, "").trim().toLowerCase();
      if (Object.hasOwn(resumeHeadings, heading)) { section = resumeHeadings[heading]; continue; }
      if (otherResumeHeadings.has(heading)) { section = null; continue; }
      resumeSuggestions.push({statement: line, section});
    }
    for (const [index, suggestion] of resumeSuggestions.slice(0, 200).entries()) {
      const prefix = suggestion.section ? `Suggested ${suggestion.section}` : "Unclassified";
      const line = suggestion.statement;
      const option = node("option", "", prefix + " · " + (line.length > 120 ? line.slice(0, 117) + "…" : line));
      option.value = String(index);
      select.append(option);
      const bulkOption = node("option", "", prefix + " · " + (line.length > 120 ? line.slice(0, 117) + "…" : line));
      bulkOption.value = String(index); bulk.append(bulkOption);
    }
    byId("resume-use-line").disabled = !resumeSuggestions.length;
    byId("resume-bulk-save").disabled = !resumeSuggestions.length;
    byId("resume-combine").disabled = !resumeSuggestions.length;
    byId("resume-bulk-status").textContent = "Select the statements you want to stage; nothing is saved yet.";
    byId("resume-status").textContent = (resumeSuggestions.length > 200 ? "Showing the first 200 suggestions. " : `${resumeSuggestions.length} suggestions found. `) +
      "Categories follow recognized section headings and may be wrong when headings are missed; review every statement and category. Nothing is saved or verified.";
  });
  byId("resume-use-line").addEventListener("click", () => {
    const value = byId("resume-lines").value;
    const index = Number(value);
    const suggestion = resumeSuggestions[index];
    if (value === "" || !suggestion || !Number.isInteger(index) || index < 0 || index >= Math.min(resumeSuggestions.length, 200)) {
      byId("resume-status").textContent = "Choose a suggested statement first.";
      return;
    }
    if (suggestion.statement.length > 1000) {
      byId("resume-status").textContent = "This line exceeds 1,000 characters. Edit the résumé text and extract suggestions again.";
      return;
    }
    if ((state.editingFactId || byId("fact-statement").value || byId("fact-source").value || byId("fact-keywords").value) &&
        !window.confirm("Replace the unsaved fact editor contents with this résumé line?")) return;
    resetFactEditor();
    if (suggestion.section) byId("fact-category").value = suggestion.section;
    byId("fact-statement").value = suggestion.statement;
    if (wizardActive) showWizardStep(2, true); else byId("fact-statement").focus();
    byId("resume-status").textContent = (suggestion.section ? `Suggested category: ${suggestion.section}. ` : "No category was inferred; choose one. ") +
      "Review or edit the statement, add a private evidence note, and save it as an unverified fact. Statements need at least 8 characters.";
  });
  byId("resume-combine").addEventListener("click", () => {
    const status = byId("resume-bulk-status");
    const indices = Array.from(byId("resume-bulk-lines").selectedOptions, option => Number(option.value));
    if (indices.length < 2 || indices.length > 30 || indices.some(i => !Number.isInteger(i) || i < 0 || i >= 200 || !resumeSuggestions[i])) {
      status.textContent = "Select between 2 and 30 suggested lines to combine."; return;
    }
    const selected = indices.map(i => resumeSuggestions[i]);
    const statement = selected.map(item => item.statement).join(" ");
    if (statement.length < 8 || statement.length > 1000) {
      status.textContent = "The combined statement must contain 8–1,000 characters. Choose fewer lines or edit the résumé text."; return;
    }
    const sections = new Set(selected.map(item => item.section));
    const category = sections.size === 1 && selected[0].section ? selected[0].section : byId("resume-bulk-category").value;
    if (!category) { status.textContent = "Choose a category for the combined unclassified or mixed lines."; return; }
    if ((state.editingFactId || byId("fact-statement").value || byId("fact-source").value || byId("fact-keywords").value) &&
        !window.confirm("Replace the unsaved fact editor contents with the selected résumé lines?")) return;
    resetFactEditor();
    byId("fact-category").value = category;
    byId("fact-statement").value = statement;
    if (wizardActive) showWizardStep(2, true); else byId("fact-statement").focus();
    status.textContent = "Lines combined in résumé order for review. Check wording and category, add a private source note, then save and verify separately. Nothing has been saved yet.";
  });
  byId("resume-bulk-save").addEventListener("click", async () => {
    if (!state.workspace || workspaceMutationBusy) return;
    const status = byId("resume-bulk-status");
    const indices = Array.from(byId("resume-bulk-lines").selectedOptions, option => Number(option.value));
    if (!indices.length || indices.length > 30) { status.textContent = "Select between 1 and 30 statements."; return; }
    const selected = indices.map(index => resumeSuggestions[index]);
    if (selected.some((item, index) => !Number.isInteger(indices[index]) || indices[index] < 0 || indices[index] >= 200 || !item || item.statement.length < 8 || item.statement.length > 1000)) {
      status.textContent = "Each selected line must contain 8–1,000 characters; edit the résumé text and find suggestions again if needed."; return;
    }
    const fallback = byId("resume-bulk-category").value;
    if (selected.some(item => !item.section) && !fallback) { status.textContent = "Choose a category for unclassified lines."; return; }
    const source = byId("resume-bulk-source").value.trim();
    if (!source || source.length > 500) { status.textContent = "Enter a private source note of up to 500 characters."; return; }
    const existing = new Set(state.workspace.facts.map(fact => fact.statement));
    const unique = new Set(selected.map(item => item.statement));
    if (unique.size !== selected.length || selected.some(item => existing.has(item.statement))) {
      status.textContent = "A selected statement is duplicated or already exists. Select distinct new statements."; return;
    }
    if (state.workspace.facts.length + selected.length > 100) { status.textContent = "The career profile allows at most 100 facts."; return; }
    const facts = selected.map(item => ({id: "fact-" + crypto.randomUUID().replaceAll("-", "").slice(0, 12),
      section: item.section || fallback, statement: item.statement, keywords: [], source_note: source, verified_by_candidate: false}));
    const saved = await saveCareerWorkspace(state.workspace.profile, [...state.workspace.facts, ...facts], "Résumé selections");
    if (saved) {
      for (const option of Array.from(byId("resume-bulk-lines").selectedOptions)) option.selected = false;
      status.textContent = `${facts.length} statements saved as unverified. Review and verify each separately.`;
    }
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
      ...(existing?.derived_from ? {derived_from: existing.derived_from} : {}),
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
  byId("cleanup-expired").addEventListener("click", async (event) => {
    if (!window.confirm("Archive all saved opportunities whose exact posting was checked in the last 24 hours and already had an expired deadline at check time? This uses saved checks and makes no website requests. Unknown, blocked and stale results are kept.")) return;
    const control = event.currentTarget; control.disabled = true;
    try {
      const result = await api("/api/opportunities/cleanup", "POST", {acknowledged: true});
      await refresh(); notice(result.archived + " expired opportunities archived. Drafts and application history retained.");
    } catch (error) { notice(error.message, true); }
    finally { control.disabled = false; }
  });
  byId("search").addEventListener("input", (event) => {state.search = event.target.value; state.jobsPage = 0; renderJobs();});
  byId("filter").addEventListener("change", (event) => {state.filter = event.target.value; state.jobsPage = 0; renderJobs();});
  byId("board-sort").addEventListener("change", (event) => {state.sort = event.target.value === "recent" ? "recent" : "score"; state.jobsPage = 0; renderJobs();});
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
  byId("show-add-job").addEventListener("click", () => {
    byId("add-job-form").hidden = false;
    byId("add-job-form").querySelector?.('[name="title"]')?.focus();
  });
  byId("job-capture-apply").addEventListener("click", () => {
    const status = byId("job-capture-status"), form = byId("add-job-form");
    try {
      const raw = byId("job-capture-input").value;
      if (raw.length > 60000) throw new Error("Capture is too large.");
      const capture = JSON.parse(raw);
      if (!capture || Array.isArray(capture) || typeof capture !== "object" ||
          capture.format !== "career-agent-job-capture-v1" ||
          typeof capture.title !== "string" || !capture.title.trim() || capture.title.length > 200 ||
          typeof capture.url !== "string" || capture.url.length > 2048 ||
          typeof capture.selection !== "string" || capture.selection.length > 50000) throw new Error("Invalid browser capture.");
      const url = new URL(capture.url);
      if (!["http:", "https:"].includes(url.protocol) || url.username || url.password) throw new Error("Use an HTTP or HTTPS job URL without credentials.");
      if (capture.text_source !== undefined && capture.text_source !== "structured_description") throw new Error("Invalid browser capture source.");
      const metadata = capture.metadata;
      if (metadata !== undefined && (!metadata || Array.isArray(metadata) || typeof metadata !== "object" ||
          Object.keys(metadata).some(key => !["job_title", "company"].includes(key)) ||
          [metadata.job_title, metadata.company].some(value => value !== undefined && (typeof value !== "string" || value.length > 200)))) {
        throw new Error("Invalid browser capture metadata.");
      }
      const title = form.querySelector('[name="title"]'), jobUrl = form.querySelector('[name="job_url"]'), description = form.querySelector('[name="description"]');
      const company = form.querySelector('[name="company"]'), location = form.querySelector('[name="location"]'), remote = form.querySelector('[name="is_remote"]');
      if ((title.value || jobUrl.value || description.value || company.value || location.value || remote.checked) && !window.confirm("Replace the title, URL and description? Existing company, location and remote status stay as entered; a blank company may be filled from page metadata.")) return;
      title.value = metadata?.job_title?.trim() || capture.title; jobUrl.value = capture.url; description.value = capture.selection;
      if (!company.value && metadata?.company?.trim()) company.value = metadata.company.trim();
      status.textContent = (capture.text_source === "structured_description" ? "Structured page text filled. It may be incomplete or describe another job. " : "Fields filled. ") + "Page-supplied details are unverified; review title, company, location, remote status and description before saving.";
    } catch (error) { status.textContent = error instanceof SyntaxError ? "Paste a valid browser capture." : error.message; }
  });
  byId("close-add-job").addEventListener("click", () => {
    byId("add-job-form").hidden = true;
    byId("show-add-job").focus();
  });
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
      byId("show-add-job").focus();
      notice(result.created ? "Opportunity added." : "This opportunity was already tracked.");
      await refresh(result.job_id);
    } catch (error) {notice(error.message, true);}
  });

  refresh().then(() => { if (state.screen === "home") loadHome(); });
})();
