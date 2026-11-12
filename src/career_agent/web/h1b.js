"use strict";
(function () {
  const get = (id) => document.getElementById(id);
  const token = get("app").dataset.session;
  let sequence = 0;
  let nextOffset = null;
  let searchedQuery = "";
  let opportunitySequence = 0;
  let opportunityNext = null;
  const observationTimers = new Map();
  function clearResults(id) {
    const container = get(id);
    for (const [box, timer] of observationTimers) {
      if (container.contains(box)) { clearTimeout(timer); observationTimers.delete(box); }
    }
    container.replaceChildren();
  }
  const status = (message) => { get("h1b-status").textContent = message; };
  const node = (tag, content) => {
    const el = document.createElement(tag);
    if (content !== undefined) el.textContent = content;
    return el;
  };
  const link = (label, url) => {
    const el = node("a", label);
    el.href = url;
    el.target = "_blank";
    el.rel = "noopener noreferrer";
    return el;
  };
  async function api(url, method = "GET", data) {
    const response = await fetch(url, {method, headers: {"X-Session-Token": token, "Content-Type": "application/json"},
      cache: "no-store", credentials: "same-origin", ...(data === undefined ? {} : {body: JSON.stringify(data)})});
    return responseBody(response);
  }
  async function responseBody(response) {
    const body = await response.json();
    if (!response.ok) {
      if (response.status === 403 && body.detail === "Session token required") {
        throw new Error("Your local session expired after the app restarted. Copy any unsaved changes, then refresh this page; no USCIS token is required.");
      }
      throw new Error(typeof body.detail === "string" ? body.detail : "H-1B request failed");
    }
    return body;
  }
  function coverage(value) {
    get("h1b-coverage").textContent = value.records + " imported H-1B cases · fiscal years " +
      (value.first_year ?? "none") + "–" + (value.last_year ?? "none") + " · latest capture " +
      (value.latest_capture ?? "none") + ". Coverage is incomplete. User imports are not independently verified; dates do not establish current job policy.";
    if (value.lca_imports?.length) {
      const history = node("details");
      history.append(node("summary", "Recent DOL import history (up to 10)"));
      history.append(node("p", "Capture dates and URLs are supplied by you. Decision dates are the range observed in imported H-1B rows, not a verified reporting period. Fingerprints cover selected fields, not original file bytes. Older imports may not appear."));
      for (const item of value.lca_imports) {
        const details = node("p", "Captured " + item.captured_on + " · imported " + item.imported_at +
          " · observed decisions " + (item.first_decision ?? "none") + "–" + (item.last_decision ?? "none") +
          " · added " + item.counts.added + ", updated " + item.counts.updated + ", duplicates " + item.counts.duplicates +
          ", older skipped " + item.counts.older_skipped + ", other visas skipped " + item.counts.other_visas_skipped +
          " · selected-field SHA-256 " + item.selected_fields_sha256 + " · source: " + item.source_url);
        history.append(details);
      }
      get("h1b-coverage").append(history);
    }
    for (const snapshot of value.uscis_snapshots || []) {
      get("h1b-coverage").append(node("p", "USCIS FY " + snapshot.fiscal_year + " · user-supplied reporting date " + snapshot.data_as_of +
        " · retrieved " + snapshot.retrieved_at + " · " + snapshot.row_count + " employer groups · " + snapshot.transport.replaceAll("_", " ")));
    }
  }
  async function load() {
    const result = await api("/api/h1b");
    get("h1b-need").value = result.settings.need;
    get("h1b-min-year").value = result.settings.min_year;
    get("h1b-unknown").checked = result.settings.include_unknown;
    coverage(result.coverage);
  }
  function companyCard(company) {
    const card = node("section");
    card.className = "panel profile-editor";
    card.append(node("h3", company.employer));
    card.append(node("p", "Exact name matching ignores case and whitespace only. Affiliates and aliases are not merged. Recent certified cases: " + company.recent_certified_cases + ". These are LCA cases, not open jobs or unique workers."));
    const counts = node("ul");
    for (const row of company.counts) counts.append(node("li", "FY " + row.fiscal_year + " · " + row.case_status + ": " + row.cases));
    card.append(counts);
    if (!company.counts.length) card.append(node("p", "No cases in the configured fiscal-year window. Sponsorship remains unknown."));
    const details = node("details");
    details.append(node("summary", "Latest stored records (up to 20; may predate the selected window)"));
    for (const row of company.records) {
      const entry = node("p", row.case_number + " · FY " + row.fiscal_year + " · " + row.case_status + " · " + row.city + ", " + row.state + " · " + row.job_title + " · captured " + row.captured_on + " · ");
      entry.append(link("Source", row.source_url));
      details.append(entry);
    }
    card.append(details);
    if (company.uscis) {
      const decisions = node("details");
      decisions.append(node("summary", "USCIS historical decisions (separate from LCAs)"));
      decisions.append(node("p", company.uscis.warning));
      if (!company.uscis.records.length) decisions.append(node("p", "No exact-name USCIS records in the selected window. This is unknown, not evidence of no sponsorship."));
      for (const row of company.uscis.records) {
        const entry = node("p", "FY " + row.fiscal_year + " · " + row.city + ", " + row.state + " · NAICS " + row.naics +
          " · initial approvals/denials: " + (row.initial_approval ?? "unknown") + "/" + (row.initial_denial ?? "unknown") +
          " · continuing approvals/denials: " + (row.continuing_approval ?? "unknown") + "/" + (row.continuing_denial ?? "unknown") +
          " · data as of " + row.data_as_of + " · " + row.transport.replaceAll("_", " ") + " · ");
        entry.append(link("USCIS source", row.source_url));
        decisions.append(entry);
      }
      card.append(decisions);
    }
    return card;
  }
  function liveObservation(box, result) {
    if (observationTimers.has(box)) clearTimeout(observationTimers.get(box));
    observationTimers.delete(box);
    box.replaceChildren();
    if (!result) { box.append(node("p", "No live check recorded.")); return; }
    const timestamp = Date.parse(result.checked_at), age = Date.now() - timestamp;
    const stale = result.stale || !Number.isFinite(timestamp) || age < 0 || age > 86400000;
    const availability = stale ? "unknown" : result.availability;
    box.append(node("p", (stale ? "STALE OBSERVATION · " : "") + availability.replaceAll("_", " ") + " · checked " + result.checked_at + " · " + result.reason));
    if (!stale) observationTimers.set(box, setTimeout(() => {
      observationTimers.delete(box);
      if (box.isConnected) liveObservation(box, result);
    }, Math.max(1, 86400001 - age)));
    if (result.policy) {
      box.append(node("p", "Policy at check time: " + result.policy.policy.replaceAll("_", " ") + " · " + result.policy.reason));
      for (const quote of result.policy.quotes) box.append(node("blockquote", quote));
    }
    box.append(link("Checked source", result.source_url));
  }
  function jobCard(job) {
    const card = node("section");
    card.className = "panel profile-editor";
    card.append(node("h3", job.title + " · " + job.company));
    if (job.location) card.append(node("p", job.location));
    card.append(node("p", job.policy.replaceAll("_", " ") + " · " + job.reason));
    const association = job.employer_association;
    card.append(node("p", "Recent certified LCA cases for " + job.history.employer + " in local imports: " + job.history.recent_certified_cases + ". Missing records do not mean no sponsorship. The assessment above uses the saved posting."));
    if (association?.employer) card.append(node("p", "Candidate-provided employer association, not independently verified · " + association.employer + " · " + association.source_note + " · " + association.changed_at + ". Confirm the actual legal entity before relying on these records."));
    const historyDetails = node("details");
    historyDetails.append(node("summary", "View imported employer records"), companyCard(job.history));
    card.append(historyDetails);
    const associationDetails = node("details");
    associationDetails.append(node("summary", "Associate an imported legal employer"));
    associationDetails.append(node("p", "Use an exact legal employer name from imported DOL or USCIS records after checking the entity. Your association is a research note, not proof of identity or sponsorship."));
    const associationForm = node("form");
    associationForm.className = "crm-form";
    const employerLabel = node("label", "Imported legal employer name");
    employerLabel.className = "field";
    const employerInput = node("input"); employerInput.required = true; employerInput.maxLength = 300; employerInput.value = association?.employer || ""; employerLabel.append(employerInput);
    const noteLabel = node("label", "How you checked the association");
    noteLabel.className = "field";
    const noteInput = node("input"); noteInput.required = true; noteInput.maxLength = 500; noteInput.placeholder = "For example, employer careers page and date"; noteLabel.append(noteInput);
    const confirmLabel = node("label");
    confirmLabel.className = "check-label";
    const confirm = node("input"); confirm.type = "checkbox"; confirm.required = true;
    confirmLabel.append(confirm, document.createTextNode(" I understand this association is not independently verified."));
    const save = node("button", "Save association"); save.type = "submit"; save.className = "button";
    const message = node("p"); message.setAttribute("role", "status");
    associationForm.append(employerLabel, noteLabel, confirmLabel, save, message);
    associationForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (save.disabled || !confirm.checked) return;
      save.disabled = true;
      try {
        const updated = await api("/api/jobs/" + job.id + "/h1b/employer", "PUT", {
          employer: employerInput.value, source_note: noteInput.value,
          expected_revision: association?.revision || 0, acknowledged: true
        });
        if (!card.isConnected) return;
        if (observationTimers.has(observation)) clearTimeout(observationTimers.get(observation));
        observationTimers.delete(observation);
        card.replaceWith(jobCard(updated));
      } catch (error) { message.textContent = error.message; }
      finally { save.disabled = false; }
    });
    if (association?.employer) {
      const remove = node("button", "Remove association"); remove.type = "button"; remove.className = "button";
      remove.addEventListener("click", async () => {
        if (remove.disabled || !window.confirm("Remove this candidate-provided association? The job will again use its posted company name.")) return;
        remove.disabled = true;
        try {
          const updated = await api("/api/jobs/" + job.id + "/h1b/employer", "PUT", {
            employer: "", source_note: "Association removed by candidate", expected_revision: association.revision, acknowledged: true
          });
          if (!card.isConnected) return;
          if (observationTimers.has(observation)) clearTimeout(observationTimers.get(observation));
          observationTimers.delete(observation);
          card.replaceWith(jobCard(updated));
        } catch (error) { message.textContent = error.message; }
        finally { remove.disabled = false; }
      });
      associationForm.append(remove);
    }
    associationDetails.append(associationForm); card.append(associationDetails);
    for (const quote of job.quotes) card.append(node("blockquote", quote));
    card.append(link("Check original job posting", job.url));
    const observation = node("div");
    observation.setAttribute("role", "status");
    liveObservation(observation, job.live_check);
    const check = node("button", "Check live ATS posting");
    check.type = "button";
    check.className = "button";
    check.disabled = !job.live_check_supported;
    check.addEventListener("click", async () => {
      check.disabled = true;
      if (observationTimers.has(observation)) clearTimeout(observationTimers.get(observation));
      observationTimers.delete(observation);
      observation.textContent = "Checking public ATS data…";
      try {
        const result = await api("/api/jobs/" + job.id + "/live-check", "POST", {acknowledged: true});
        if (observation.isConnected) liveObservation(observation, result);
      }
      catch (error) { if (observation.isConnected) observation.textContent = "Check failed: " + error.message + ". Previous saved observation is unchanged."; }
      finally { check.disabled = false; }
    });
    card.append(node("p", job.live_check_supported ? "This contacts the public ATS for this posting. Observations become stale after 24 hours." : "Live checks support Greenhouse and Lever postings. Check this original posting manually; availability remains unknown."), check, observation);
    return card;
  }
  async function opportunities(offset = 0) {
    const current = ++opportunitySequence;
    const results = get("h1b-opportunities-results");
    const message = get("h1b-opportunities-status");
    const query = get("h1b-opportunities-query").value.trim();
    const category = get("h1b-opportunities-category").value;
    clearResults("h1b-opportunities-results");
    get("h1b-opportunities-next").hidden = true;
    message.textContent = "Searching saved opportunities…";
    try {
      const result = await api("/api/h1b/opportunities?q=" + encodeURIComponent(query) + "&category=" + encodeURIComponent(category) + "&offset=" + offset);
      if (current !== opportunitySequence) return;
      opportunityNext = result.next_offset;
      get("h1b-opportunities-next").hidden = opportunityNext === null;
      message.textContent = result.total + " matching opportunities · " + result.counts.potential + " possible · " + result.counts.review + " need clarification · " + result.counts.excluded + " indicate a restriction. Counts cover all signals for your search.";
      if (!result.jobs.length) results.append(node("p", "No saved opportunities match. Try another search or find more jobs with JobSpy."));
      for (const job of result.jobs) results.append(jobCard(job));
    } catch (error) { if (current === opportunitySequence) message.textContent = error.message; }
  }
  async function search(offset = 0) {
    const query = get("h1b-query").value.trim();
    const current = ++sequence;
    clearResults("h1b-results");
    get("h1b-next").hidden = true;
    if (query.length < 2) { status("Enter at least two characters."); return; }
    get("h1b-search-submit").disabled = true;
    status("Searching local records…");
    try {
      const result = await api("/api/h1b/search?q=" + encodeURIComponent(query) + "&offset=" + offset);
      if (current !== sequence) return;
      coverage(result.coverage);
      searchedQuery = query;
      nextOffset = result.next_offset;
      get("h1b-next").hidden = nextOffset === null;
      status(result.company_total + " matching employer names · " + result.jobs.length + " stored jobs. " + result.warning);
      const results = get("h1b-results");
      results.append(node("h3", "Employer filing history"));
      if (!result.companies.length) results.append(node("p", "No employer records found in this local dataset. Try the legal entity name; this does not mean the company never sponsors."));
      for (const company of result.companies) results.append(companyCard(company));
      results.append(node("h3", "Jobs in your workspace"));
      if (!result.jobs.length) results.append(node("p", "No matching stored jobs with these preferences. Use Opportunity board search to discover jobs; no live job search was run here."));
      for (const job of result.jobs) results.append(jobCard(job));
    } catch (error) {
      if (current === sequence) status(error.message);
    } finally {
      if (current === sequence) get("h1b-search-submit").disabled = false;
    }
  }
  get("nav-h1b").addEventListener("click", async () => {
    try { await load(); await opportunities(); if (get("h1b-query").value.trim()) await search(); }
    catch (error) { status(error.message); }
  });
  get("h1b-opportunities-form").addEventListener("submit", (event) => { event.preventDefault(); opportunities(); });
  for (const id of ["h1b-opportunities-query", "h1b-opportunities-category"]) get(id).addEventListener("input", () => {
    opportunitySequence++;
    opportunityNext = null;
    clearResults("h1b-opportunities-results");
    get("h1b-opportunities-next").hidden = true;
    get("h1b-opportunities-status").textContent = "Find opportunities to apply the updated filters.";
  });
  get("h1b-opportunities-next").addEventListener("click", () => { if (opportunityNext !== null) opportunities(opportunityNext); });
  get("h1b-find-jobs").addEventListener("click", () => {
    get("nav-jobs").click();
    get("discovery-panel").open = true;
    get("discover-query").focus();
  });
  get("h1b-search").addEventListener("submit", (event) => { event.preventDefault(); search(); });
  get("h1b-query").addEventListener("input", () => {
    sequence++;
    get("h1b-search-submit").disabled = false;
    clearResults("h1b-results");
    get("h1b-next").hidden = true;
    status("Search to update the results for this company.");
  });
  get("h1b-next").addEventListener("click", () => {
    if (nextOffset !== null && get("h1b-query").value.trim() === searchedQuery) search(nextOffset);
  });
  get("h1b-settings").addEventListener("submit", async (event) => {
    event.preventDefault();
    const control = get("h1b-save");
    if (control.disabled) return;
    control.disabled = true;
    sequence++;
    clearResults("h1b-results");
    get("h1b-next").hidden = true;
    try {
      await api("/api/h1b", "PUT", {need: get("h1b-need").value, min_year: Number(get("h1b-min-year").value), include_unknown: get("h1b-unknown").checked});
      status("H-1B preferences saved. They guide research, not eligibility decisions.");
      await opportunities();
      if (get("h1b-query").value.trim()) await search();
    } catch (error) { status(error.message); }
    finally { control.disabled = false; get("h1b-search-submit").disabled = false; }
  });
  get("h1b-import").addEventListener("submit", async (event) => {
    event.preventDefault();
    const control = get("h1b-import-submit");
    if (control.disabled) return;
    const file = get("h1b-file").files[0];
    if (!get("h1b-consent").checked || !file) { status("Choose a public LCA file and confirm its provenance."); return; }
    const workbook = file.name.toLowerCase().endsWith(".xlsx");
    if (!workbook && !file.name.toLowerCase().endsWith(".csv")) { status("Choose a CSV or XLSX file."); return; }
    if (!file.size || file.size > (workbook ? 512 : 8) * 1024 * 1024) {
      status(workbook ? "Choose a nonempty XLSX workbook no larger than 512 MB." : "Choose a nonempty CSV up to 8 MB, or use the CLI for a larger CSV."); return;
    }
    control.disabled = true;
    status("Importing local H-1B records…");
    let progressActive = workbook, progressTimer = null, progressPending = false;
    const pollProgress = async () => {
      if (!progressActive || progressPending) return;
      progressPending = true;
      try {
        const progress = await api("/api/h1b/import-progress");
        if (!progressActive) return;
        if (progress.phase === "uploading") status("Uploading locally: " + (progress.amount / 1048576).toFixed(1) + " MB received…");
        if (progress.phase === "opening") status("Opening workbook locally; large files can take several minutes…");
        if (progress.phase === "scanning") status("Checking workbook structure: " + progress.amount.toLocaleString() + " rows scanned in the current sheet…");
        if (progress.phase === "reading") status("Reading " + progress.amount.toLocaleString() + " rows. Records are saved only after the entire import succeeds…");
      } catch (_) { /* The import request reports its own errors. */ }
      finally { progressPending = false; }
    };
    try {
      let result;
      if (workbook) {
        progressTimer = setInterval(pollProgress, 2000);
        const params = new URLSearchParams({source_url: get("h1b-source").value, captured_on: get("h1b-captured").value});
        const response = await fetch("/api/h1b/import-workbook?" + params.toString(), {method: "POST", body: file,
          headers: {"X-Session-Token": token, "Content-Type": "application/octet-stream", "X-LCA-Acknowledged": "true"},
          cache: "no-store", credentials: "same-origin"});
        result = await responseBody(response);
      } else {
        result = await api("/api/h1b/import", "POST", {csv: await file.text(), source_url: get("h1b-source").value,
          captured_on: get("h1b-captured").value, acknowledged: true});
      }
      get("h1b-consent").checked = false;
      const data = await api("/api/h1b");
      coverage(data.coverage);
      sequence++;
      clearResults("h1b-results");
      get("h1b-next").hidden = true;
      get("h1b-search-submit").disabled = false;
      status(result.added + " added · " + result.updated + " updated · " + result.duplicates + " duplicates · " + result.older_skipped + " older records skipped · " + result.other_visas_skipped + " other visa records skipped. Search again to update results.");
    } catch (error) { status(error.message); }
    finally { progressActive = false; if (progressTimer !== null) clearInterval(progressTimer); control.disabled = false; }
  });
  async function updateUSCIS(action) {
    if (get("uscis-refresh").disabled) return;
    if (!get("uscis-consent").checked) { status("Confirm the USCIS source and reporting date."); return; }
    const data = {source_url: get("uscis-source").value, fiscal_year: Number(get("uscis-year").value),
      data_as_of: get("uscis-as-of").value, acknowledged: true};
    if (action === "import") {
      const file = get("uscis-file").files[0];
      if (!file || file.size > 8 * 1024 * 1024) { status("Choose a USCIS CSV up to 8 MB, or use the CLI for a larger file."); return; }
      data.csv = await file.text();
    }
    get("uscis-refresh").disabled = get("uscis-import").disabled = true;
    status("Updating USCIS history… Existing data stays available if the update fails.");
    try {
      const result = await api("/api/h1b/uscis/" + action, "POST", data);
      coverage((await api("/api/h1b")).coverage);
      sequence++;
      clearResults("h1b-results");
      get("h1b-next").hidden = true;
      get("h1b-search-submit").disabled = false;
      get("uscis-consent").checked = false;
      status("USCIS FY " + result.fiscal_year + ": " + result.rows + " employer groups · " + (result.unchanged ? "unchanged" : "snapshot replaced") + ". Search again to see results.");
    } catch (error) { status(error.message); }
    finally { get("uscis-refresh").disabled = get("uscis-import").disabled = false; }
  }
  get("uscis-form").addEventListener("submit", (event) => { event.preventDefault(); updateUSCIS("refresh"); });
  get("uscis-import").addEventListener("click", () => updateUSCIS("import"));
})();
