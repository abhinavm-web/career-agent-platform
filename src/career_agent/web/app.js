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
    crmSummary: null, crmDetails: null, crmJobId: null, crmRequestSeq: 0, jobsPage: 0, refreshSequence: 0, boardTruncated: false,
    boardFilters: {location: "", source: "", remote: "all", sponsor: "all", freshness: "all"}
  };
  let freshnessTimer = null;
  let selectedAvailabilityUpdater = null;
  let workspaceMutationBusy = false;
  let workspaceLoadSequence = 0;
  let profileNavigationSequence = 0;

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
      const message = response.status === 403 && data.detail === "Session token required"
        ? "Your local session expired after the server restarted. Copy any unsaved changes, refresh this page, then try again."
        : data.detail || "Request failed";
      const error = new Error(message);
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
    const ready = s.profile_configured && s.facts_configured;
    el.textContent = ready ? "Candidate profile configured" : "Profile / facts not configured";
    el.classList.toggle("warn", !ready);
    byId("profile-prompt").hidden = ready;
    byId("setup-progress").textContent = (s.profile_configured ? "Goals configured" : "Add your goals for matching") +
      " · " + (s.facts_configured ? "Career facts verified" : "Verify a career fact for AI analysis and drafts") + ".";
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
    const truncated = state.boardTruncated || (state.overview?.total_jobs ?? 0) > state.jobs.length;
    byId("board-limit").hidden = !truncated;
    byId("board-limit").textContent = state.boardTruncated
      ? "The board reached its loading limit. Filters only search the " + state.jobs.length + " loaded opportunities; older jobs are not included."
      : truncated ? "The board may have changed while loading. Refresh to include newly added opportunities." : "";
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
      if (job.constraint_evidence?.some((row) => row.status === "flagged")) lower.append(chip("Constraint flagged", "amber"));
      else if (job.constraint_evidence?.some((row) => row.status === "unknown")) lower.append(chip("Constraint unknown", "amber"));
      if (job.source) lower.append(chip("Source: " + job.source));
      if (job.discovery_observations?.some((row) => row.description_comparison === "differs")) lower.append(chip("Source text differs", "amber"));
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
    if (state.selectedId !== id) {
      const editor = byId("detail-panel").querySelector?.("#job-description-editor");
      const description = editor?.querySelector?.("textarea");
      const source = editor?.querySelectorAll?.("textarea")?.[1];
      if (editor?.open && description && (description.value !== description.defaultValue || source?.value) &&
          !window.confirm("Discard the unsaved job-description correction?")) return;
    }
    state.selectedId = id;
    state.packet = null;
    renderJobs();
    renderDetail();
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

  function section(heading) {
    const box = node("section", "detail-section");
    box.append(node("h4", "", heading));
    return box;
  }

  function renderDetailPreservingEditor() {
    const editor = byId("detail-panel").querySelector?.("#job-description-editor");
    const keep = editor?.open && editor.dataset.jobId === state.selectedId;
    const picker = byId("detail-panel").querySelector?.(".tailoring-picker");
    const keepPicker = picker?.open && picker.dataset.jobId === state.selectedId;
    renderDetail();
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
    if (job.discovery_observations?.length) {
      const discovery = section("Discovery history");
      discovery.append(node("p", "muted", "These are times your searches returned this URL, not proof that the posting is open, current, or unchanged."));
      for (const row of job.discovery_observations) {
        discovery.append(node("p", "", row.source + " · first returned " + row.first_seen + " · last returned " + row.last_seen + " · " + row.observation_count + " search(es)"));
        if (row.description_comparison === "differs") {
          discovery.append(node("p", "", "Source text observed " + row.description_observed_at + " differs from the saved description. It may be partial or outdated; inspect the original posting before relying on the draft."));
        } else if (row.description_comparison === "matches") {
          discovery.append(node("p", "muted", "Source text observed " + row.description_observed_at + " matched the saved description ignoring case and whitespace; this does not verify the current posting."));
        } else {
          discovery.append(node("p", "muted", "Description comparison unavailable for this source or latest observation."));
        }
      }
      detail.append(discovery);
    }
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

    const duplicates = section("Possible duplicate postings");
    duplicates.append(node("p", "muted", "Compare saved postings with the same company, title and location. Different requisitions can share these details; nothing will be merged or removed."));
    const duplicateResults = node("div");
    duplicateResults.setAttribute("role", "status");
    const duplicateButton = button("Find possible duplicates", "button", async () => {
      if (duplicateButton.disabled) return;
      duplicateButton.disabled = true;
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
          duplicateResults.append(item);
        }
        if (result.results_truncated) duplicateResults.append(node("p", "", "Showing the first 10 possible duplicates; additional matches exist."));
        if (result.scan_limited) duplicateResults.append(node("p", "", "Only the 10,000 most recent saved opportunities were checked; older records may match."));
      } catch (error) { duplicateResults.textContent = "Unable to compare saved opportunities: " + error.message; }
      finally { duplicateButton.disabled = false; }
    });
    duplicates.append(duplicateButton, duplicateResults);
    detail.append(duplicates);

    const availability = section("Posting availability");
    const availabilityStatus = node("div", "availability-status");
    availabilityStatus.setAttribute("role", "status");
    let checkingAvailability = false;
    let checkFailure = null;
    const showAvailability = () => {
      if (checkingAvailability) return;
      const observation = postingObservation(job);
      availabilityStatus.replaceChildren(chip(observation.label, observation.kind === "listed" ? "teal" : observation.kind === "expired" ? "red" : ""),
        node("p", "", observation.detail));
      if (checkFailure) availabilityStatus.append(node("p", "", "Check failed: " + checkFailure + ". Previous observation remains unchanged."));
      if (job.live_check && job.live_check.checked_at) availabilityStatus.append(node("p", "", "Checked: " + job.live_check.checked_at));
      if (job.live_check && job.live_check.source_url) availabilityStatus.append(safeLink("Observation source", job.live_check.source_url));
    };
    selectedAvailabilityUpdater = showAvailability;
    showAvailability();
    availability.append(availabilityStatus);
    if (job.live_check_supported) {
      const check = button("Check live availability", "button", async () => {
        if (check.disabled) return;
        check.disabled = true;
        checkingAvailability = true;
        checkFailure = null;
        availabilityStatus.textContent = "Checking the public posting…";
        try {
          job.live_check = await api("/api/jobs/" + job.id + "/live-check", "POST", {acknowledged: true});
          checkingAvailability = false;
          showAvailability();
          renderJobs();
        } catch (error) { checkingAvailability = false; checkFailure = error.message; showAvailability(); }
        finally { checkingAvailability = false; check.disabled = false; }
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

    if (job.constraint_evidence?.length) {
      const constraints = section("Your hard constraints");
      constraints.append(node("p", "muted", "These are separate from the preference score. Review the source and confirm details before deciding."));
      for (const row of job.constraint_evidence) {
        const label = row.status === "flagged" ? "Flagged" : row.status === "source_marked" ? "Source marked" : "Unknown";
        constraints.append(node("strong", "", row.constraint + " — " + label), node("p", "", row.detail));
      }
      detail.append(constraints);
    }

    if (job.title_level_evidence) {
      const preference = section("Title-level preference");
      const evidence = job.title_level_evidence;
      preference.append(node("p", "", "Saved title: " + evidence.title));
      preference.append(node("p", "", evidence.status === "matched"
        ? "Preferred title phrase found: " + evidence.matched_phrases.join(", ")
        : "No preferred title phrase found; level remains unknown."));
      preference.append(node("p", "muted", evidence.notice));
      detail.append(preference);
    }

    if (job.fit_reasons && job.fit_reasons.length) {
      const fit = section("Why this role matched");
      const ul = node("ul");
      for (const reason of job.fit_reasons) ul.append(node("li", "", reason));
      fit.append(ul);
      detail.append(fit);
    }

    const evidence = section("Review explicit requirements");
    evidence.id = "fit-evidence";
    evidence.append(node("p", "", "Compare clearly labeled requirement bullets with your verified facts. Keyword links are suggestions, not qualification decisions."));
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
          item.append(node("strong", "", row.category === "required" ? "Labeled required" : "Labeled preferred"), node("blockquote", "", row.quote));
          if (!row.suggested_facts.length) item.append(node("p", "", "No verified fact matched by keyword; evidence remains unknown."));
          for (const fact of row.suggested_facts) item.append(node("p", "", "Potentially relevant verified fact " + fact.id + ": " + fact.statement + " (shared keywords: " + fact.matched_keywords.join(", ") + ")"));
          evidenceResult.append(item);
        }
        if (result.truncated) evidenceResult.append(node("p", "", "Some content exceeded review limits; inspect the full posting."));
      } catch (error) {evidenceResult.textContent = "Unable to compare requirements: " + error.message;}
      finally {evidenceButton.disabled = false;}
    });
    evidence.append(evidenceButton, evidenceResult);
    detail.append(evidence);

    const desc = section("Job description");
    desc.append(node("p", "description", job.description || "No description saved."));
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
      form.append(confirmation);
      const submit = button("Save corrected description", "button button-primary"); submit.type = "submit";
      const status = node("p", "muted"); status.setAttribute("role", "status");
      form.append(submit, status);
      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        if (submit.disabled || !acknowledged.checked) return;
        const description = input.value, sourceNote = source.value;
        submit.disabled = true; input.disabled = true; source.disabled = true; acknowledged.disabled = true;
        try {
          const result = await api("/api/jobs/" + job.id + "/description", "PUT", {
            description, source_note: sourceNote, expected_revision: job.description_revision ?? 0, acknowledged: true
          });
          notice(result.changed ? "Description corrected. Regenerate and review any existing draft." : "Description was unchanged.");
          editor.open = false;
          if (state.selectedId === job.id) await refresh(job.id);
        } catch (error) { status.textContent = error.message; }
        finally { submit.disabled = false; input.disabled = false; source.disabled = false; acknowledged.disabled = false; }
      });
      editor.append(form); desc.append(editor);
    } else {
      desc.append(node("p", "muted", "The posting and reviewed packet are locked after submission is recorded."));
    }
    detail.append(desc);

    const drafts = section("Application preparation");
    const p = node("p", "", "Drafts copy only candidate-confirmed career facts and cannot send an application.");
    drafts.append(p);
    if (job.application_stage === "submitted") {
      drafts.append(node("p", "", "This submitted application's reviewed packet is locked. Draft regeneration is disabled."));
    } else {
      drafts.append(button(job.packet_available ? "Regenerate draft" : "Create application draft", "button button-primary", createDraft));
      const choose = node("details", "tailoring-picker");
      choose.dataset.jobId = job.id;
      choose.append(node("summary", "", "Choose facts for a tailored draft"));
      choose.append(node("p", "muted", "Select verified facts and arrange sections and facts. Statements are copied verbatim; changes create a new draft that needs a fresh review."));
      choose.append(node("p", "muted", "To change a statement, edit it in Career profile and verify the changed fact before including it."));
      const choices = node("div");
      const status = node("p", "muted"); status.setAttribute("role", "status");
      const load = button("Load verified facts", "button", async () => {
        if (load.disabled) return;
        load.disabled = true;
        try {
          const result = await api("/api/tailoring/facts");
          if (!Number.isInteger(result.max_facts) || result.max_facts < 1) throw new Error("Invalid selection limit; reload and try again.");
          const limit = result.max_facts;
          const sections = ["experience", "projects", "skills", "education", "certifications"];
          const selected = [];
          let layoutBusy = false;
          for (const fact of result.facts) {
            const label = node("label", "check-label");
            const box = document.createElement("input"); box.type = "checkbox";
            label.append(box, document.createTextNode(fact.section + " · " + fact.statement));
            selected.push({box, label, id: fact.id, section: fact.section});
          }
          const submit = button("Create tailored draft", "button button-primary", async () => {
            const fact_ids = selected.filter((item) => item.box.checked).map((item) => item.id);
            if (!fact_ids.length || fact_ids.length > limit) {status.textContent = "Select between 1 and " + limit + " facts."; return;}
            if (submit.disabled) return;
            submit.disabled = true; load.disabled = true; layoutBusy = true; renderLayout();
            selected.forEach((item) => {item.box.disabled = true;});
            try {
              await api("/api/jobs/" + job.id + "/tailored-draft", "POST", {fact_ids, section_order: sections.slice(), workspace_revision: result.workspace_revision, facts_sha256: result.facts_sha256});
              notice("Tailored draft created locally. Review it before using.");
              if (state.selectedId === job.id) await refresh(job.id);
            } catch (error) {status.textContent = error.message;}
            finally {submit.disabled = false; load.disabled = false; layoutBusy = false; selected.forEach((item) => {item.box.disabled = false;}); renderLayout();}
          });
          function renderLayout() {
            choices.replaceChildren();
            sections.forEach((section, index) => {
              const group = node("div", "tailoring-section");
              group.append(node("strong", "", section));
              const up = button("Move " + section + " section up", "button", () => {
                [sections[index - 1], sections[index]] = [sections[index], sections[index - 1]];
                renderLayout();
              });
              const down = button("Move " + section + " section down", "button", () => {
                [sections[index], sections[index + 1]] = [sections[index + 1], sections[index]];
                renderLayout();
              });
              up.disabled = layoutBusy || index === 0; down.disabled = layoutBusy || index === sections.length - 1;
              group.append(up, down);
              const items = selected.filter((item) => item.section === section);
              items.forEach((item, position) => {
                const row = node("div", "tailoring-fact");
                row.append(item.label);
                const move = (delta) => {
                  const from = selected.indexOf(item), to = selected.indexOf(items[position + delta]);
                  [selected[from], selected[to]] = [selected[to], selected[from]];
                  renderLayout();
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
          renderLayout();
          status.textContent = result.facts.length ? "Choose up to " + limit + " verified facts to include." : "No verified facts available.";
          submit.disabled = !result.facts.length;
        } catch (error) {status.textContent = error.message;}
        finally {load.disabled = false;}
      });
      choose.append(load, choices, status); drafts.append(choose);
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
    if (stale) card.append(node("p", "stale-warning", "The career profile or job description changed since this packet was drafted. Regenerate and review before using it."));
    const resume = node("details");
    const resumeTitle = node("summary", "", "Resume draft");
    const resumeText = node("pre", "", packet.resume_markdown);
    resume.append(resumeTitle, resumeText);
    const letter = node("details");
    letter.append(node("summary", "", "Cover-note draft"), node("pre", "", packet.cover_note));
    card.append(resume, letter);

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
      tools.append(node("p", "muted", "Word edits made after download are not tracked or covered by this approval."));
    } else {
      tools.append(node("span", "muted", "Approve the current packet to download a resume PDF or Word document."));
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
    byId("career-title-levels").value = (profile.preferred_title_levels || []).join("\n");
    byId("career-remote").checked = profile.allow_remote;
    byId("career-require-remote").checked = profile.require_remote || false;
    byId("career-excluded-companies").value = (profile.excluded_companies || []).join("\n");
    byId("career-minimum-base").value = profile.minimum_annual_base_usd == null ? "" : String(profile.minimum_annual_base_usd);
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
      if (fact.derived_from) {
        card.append(node("small", "muted", "AI wording source fact " + fact.derived_from.fact_id + ": " + fact.derived_from.statement));
        card.append(node("small", "muted", "Source posting excerpt: " + fact.derived_from.job_quote));
      }
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

  function setWorkspaceMutationBusy(busy) {
    workspaceMutationBusy = busy;
    byId("profile-screen").inert = busy;
    byId("career-facts-list").inert = busy;
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
      if (actionLabel !== "Preferences") resetFactEditor();
      notice(actionLabel + " saved locally. Any added or changed career fact must be verified separately.");
      await refresh(undefined, true);
    } catch (error) {
      notice(error.status === 409 ? "Another edit was saved. Reload the profile before editing." : error.message, true);
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
    if (!data.followups.length) tasks.append(node("p", "muted", "No follow-up tasks yet."));
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
      const page = await api("/api/jobs?limit=100" + (cursor === null ? "" : "&before=" + cursor));
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
    try {
      const results = await Promise.all([
        loadBoardJobs(sequence), api("/api/overview"), api("/api/settings"), api("/api/reviews")
      ]);
      if (sequence !== state.refreshSequence || results[0] === null) return;
      state.jobs = results[0].jobs;
      state.boardTruncated = results[0].truncated;
      state.overview = results[1];
      state.settings = results[2];
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
      if (state.screen === "profile") {
        await loadWorkspace(preserveEditor);
      }
      if (state.screen === "tracker") await loadCRM();
    } catch (error) {
      if (sequence === state.refreshSequence) notice("Unable to load local workspace: " + error.message, true);
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
      byId("profile-chat-status").textContent = `Unverified suggestion · ${response.provider} · ${response.model}. No profile changes were made.` +
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
      status.textContent = "Text extracted locally. Review it, then extract lines to choose a fact.";
    } catch (error) { status.textContent = error.message; }
    finally { control.disabled = false; }
  });
  byId("resume-text").addEventListener("input", () => {
    resumeSuggestions = [];
    byId("resume-lines").replaceChildren();
    byId("resume-use-line").disabled = true;
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
      byId("resume-ai-status").textContent = response.notice;
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
          byId("fact-statement").value = suggestion.quote; byId("fact-statement").focus();
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
    "projects": "projects", "project experience": "projects",
    "skills": "skills", "technical skills": "skills", "core skills": "skills",
    "education": "education", "academic background": "education",
    "certifications": "certifications", "certificates": "certifications", "licenses and certifications": "certifications"
  };
  const otherResumeHeadings = new Set(["summary", "professional summary", "profile", "contact", "contact information", "awards", "interests", "references"]);
  let resumeSuggestions = [];
  byId("resume-extract").addEventListener("click", () => {
    const lines = byId("resume-text").value.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
    const select = byId("resume-lines");
    select.replaceChildren();
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
    }
    byId("resume-use-line").disabled = !resumeSuggestions.length;
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
    byId("fact-statement").focus();
    byId("resume-status").textContent = (suggestion.section ? `Suggested category: ${suggestion.section}. ` : "No category was inferred; choose one. ") +
      "Review or edit the statement, add a private evidence note, and save it as an unverified fact. Statements need at least 8 characters.";
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
  byId("show-add-job").addEventListener("click", () => {
    byId("add-job-form").hidden = false;
    byId("add-job-form").querySelector?.('[name="title"]')?.focus();
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

  refresh();
})();
