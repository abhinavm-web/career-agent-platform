"use strict";
(function () {
  const el = (id) => document.getElementById(id);
  const token = el("app").dataset.session;
  let routes = [];
  let busy = false;
  const text = (tag, value) => { const n = document.createElement(tag); n.textContent = value; return n; };
  async function api(path, data) {
    const r = await fetch(path, {method: data ? "POST" : "GET", cache: "no-store",
      headers: {"X-Session-Token": token, "Content-Type": "application/json"},
      ...(data ? {body: JSON.stringify(data)} : {})});
    const value = await r.json();
    if (!r.ok) throw new Error(r.status === 403 && value.detail === "Session token required"
      ? "Your local session expired after the server restarted. Copy any unsaved changes, refresh this page, then try again."
      : typeof value.detail === "string" ? value.detail : "AI request failed");
    return value;
  }
  function changed() {
    el("ai-consent").checked = false;
    el("ai-results").replaceChildren();
    const r = routes.find((r) => r.route === el("ai-route").value);
    el("ai-destination").textContent = r ? `${r.provider} · ${r.model} · ${r.destination} · output cap ${r.max_tokens} tokens` : "No route configured";
  }
  el("ai-route").addEventListener("change", changed);
  el("ai-job").addEventListener("change", changed);
  el("ai-load").addEventListener("click", async () => {
    if (busy) return;
    busy = true; el("ai-load").disabled = true; el("ai-run").disabled = true;
    try {
      const previousRoute = el("ai-route").value;
      const previousJob = el("ai-job").value;
      const [models, jobs, settings] = await Promise.all([api("/api/ai/routes"), api("/api/jobs"), api("/api/settings")]);
      routes = models.routes;
      for (const id of ["ai-route", "ai-job"]) el(id).replaceChildren();
      for (const r of routes) { const n = text("option", `${r.route} (${r.provider})`); n.value = r.route; el("ai-route").append(n); }
      for (const j of jobs.jobs) { const n = text("option", `${j.company} — ${j.title}`); n.value = j.id; el("ai-job").append(n); }
      if (previousRoute && routes.some((route) => route.route === previousRoute)) el("ai-route").value = previousRoute;
      const requestedJob = el("ai-job").dataset.requestedJob;
      if (requestedJob && jobs.jobs.some((job) => job.id === requestedJob)) el("ai-job").value = requestedJob;
      else if (previousJob && jobs.jobs.some((job) => job.id === previousJob)) el("ai-job").value = previousJob;
      delete el("ai-job").dataset.requestedJob;
      const sameSelection = !requestedJob && previousRoute === el("ai-route").value && previousJob === el("ai-job").value;
      if (!sameSelection) changed();
      const ready = settings.facts_configured;
      el("ai-consent").checked = false;
      if (!ready) el("ai-results").replaceChildren();
      el("ai-open-profile").hidden = ready;
      el("ai-run").disabled = !routes.length || !jobs.jobs.length || !ready;
      if (!sameSelection || !el("ai-results").children.length || !ready) {
        el("ai-status").textContent = !ready ? "Verify at least one career fact before analysis." :
          (routes.length ? "Choose a model and job, then confirm data sharing." : "No model routes configured. See docs/AI_MODELS.md for local setup.");
      }
    } catch (e) { el("ai-status").textContent = e.message; }
    finally { busy = false; el("ai-load").disabled = false; }
  });
  el("ai-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (busy || !el("ai-consent").checked) return;
    busy = true;
    const ids = ["ai-load", "ai-run", "ai-route", "ai-job", "ai-consent"];
    ids.forEach((id) => { el(id).disabled = true; });
    el("ai-results").replaceChildren();
    el("ai-status").textContent = "Analyzing with the selected provider…";
    const jobId = el("ai-job").value;
    try {
      const r = await api(`/api/jobs/${encodeURIComponent(jobId)}/ai-analysis`, {route: el("ai-route").value, acknowledged: true});
      if (el("ai-job").dataset.requestedJob && el("ai-job").dataset.requestedJob !== jobId) return;
      el("ai-status").textContent = `${r.notice} Model: ${r.model}. Checked: ${r.observed_at}.`;
      if (!r.requirements.length) el("ai-results").append(text("p", "No requirements returned. This does not establish a match or absence of requirements."));
      for (const row of r.requirements) {
        const section = document.createElement("section");
        section.append(text("blockquote", row.quote), text("p", "AI interpretation (unverified): " + row.reason));
        for (const [id, statement] of Object.entries(row.facts)) section.append(text("p", `${id}: ${statement}`));
        if (!row.fact_ids.length) section.append(text("p", "Evidence unknown; no candidate deficit inferred."));
        el("ai-results").append(section);
      }
    } catch (e) {
      if (!el("ai-job").dataset.requestedJob || el("ai-job").dataset.requestedJob === jobId) el("ai-status").textContent = e.message;
    }
    finally {
      busy = false; ids.forEach((id) => { el(id).disabled = false; }); el("ai-consent").checked = false;
      if (el("ai-job").dataset.requestedJob) el("ai-load").click();
    }
  });
})();
