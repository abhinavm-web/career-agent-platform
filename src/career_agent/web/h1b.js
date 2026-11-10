"use strict";
(function () {
  const get = (id) => document.getElementById(id);
  const token = get("app").dataset.session;
  let sequence = 0;
  let nextOffset = null;
  let searchedQuery = "";
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
    const body = await response.json();
    if (!response.ok) throw new Error(typeof body.detail === "string" ? body.detail : "H-1B request failed");
    return body;
  }
  function coverage(value) {
    get("h1b-coverage").textContent = value.records + " imported H-1B cases · fiscal years " +
      (value.first_year ?? "none") + "–" + (value.last_year ?? "none") + " · latest capture " +
      (value.latest_capture ?? "none") + ". Coverage is incomplete. User imports are not independently verified; dates do not establish current job policy.";
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
    return card;
  }
  async function search(offset = 0) {
    const query = get("h1b-query").value.trim();
    const current = ++sequence;
    get("h1b-results").replaceChildren();
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
      for (const job of result.jobs) {
        const card = node("section");
        card.className = "panel profile-editor";
        card.append(node("h3", job.title + " · " + job.company));
        card.append(node("p", job.policy.replaceAll("_", " ") + " · " + job.reason));
        card.append(node("p", "Recent exact-name certified LCA cases: " + job.history.recent_certified_cases + ". History does not determine job policy. Posting availability has not been rechecked."));
        for (const quote of job.quotes) card.append(node("blockquote", quote));
        card.append(link("Check original job posting", job.url));
        results.append(card);
      }
    } catch (error) {
      if (current === sequence) status(error.message);
    } finally {
      if (current === sequence) get("h1b-search-submit").disabled = false;
    }
  }
  get("nav-h1b").addEventListener("click", async () => {
    try { await load(); if (get("h1b-query").value.trim()) await search(); }
    catch (error) { status(error.message); }
  });
  get("h1b-search").addEventListener("submit", (event) => { event.preventDefault(); search(); });
  get("h1b-query").addEventListener("input", () => {
    sequence++;
    get("h1b-search-submit").disabled = false;
    get("h1b-results").replaceChildren();
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
    get("h1b-results").replaceChildren();
    get("h1b-next").hidden = true;
    try {
      await api("/api/h1b", "PUT", {need: get("h1b-need").value, min_year: Number(get("h1b-min-year").value), include_unknown: get("h1b-unknown").checked});
      status("H-1B preferences saved. They guide research, not eligibility decisions.");
      if (get("h1b-query").value.trim()) await search();
    } catch (error) { status(error.message); }
    finally { control.disabled = false; get("h1b-search-submit").disabled = false; }
  });
  get("h1b-import").addEventListener("submit", async (event) => {
    event.preventDefault();
    const control = get("h1b-import-submit");
    if (control.disabled) return;
    const file = get("h1b-file").files[0];
    if (!get("h1b-consent").checked || !file) { status("Choose a public CSV and confirm its provenance."); return; }
    if (file.size > 8 * 1024 * 1024) { status("Use the CLI to import files larger than 8 MB."); return; }
    control.disabled = true;
    status("Importing local H-1B records…");
    try {
      const result = await api("/api/h1b/import", "POST", {csv: await file.text(), source_url: get("h1b-source").value,
        captured_on: get("h1b-captured").value, acknowledged: true});
      get("h1b-consent").checked = false;
      const data = await api("/api/h1b");
      coverage(data.coverage);
      sequence++;
      get("h1b-results").replaceChildren();
      get("h1b-next").hidden = true;
      get("h1b-search-submit").disabled = false;
      status(result.added + " added · " + result.updated + " updated · " + result.duplicates + " duplicates · " + result.older_skipped + " older records skipped · " + result.other_visas_skipped + " other visa records skipped. Search again to update results.");
    } catch (error) { status(error.message); }
    finally { control.disabled = false; }
  });
})();
