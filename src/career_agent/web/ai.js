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
    if (!r.ok) throw new Error(typeof value.detail === "string" ? value.detail : "AI request failed");
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
      const [models, jobs] = await Promise.all([api("/api/ai/routes"), api("/api/jobs")]);
      routes = models.routes;
      for (const id of ["ai-route", "ai-job"]) el(id).replaceChildren();
      for (const r of routes) { const n = text("option", `${r.route} (${r.provider})`); n.value = r.route; el("ai-route").append(n); }
      for (const j of jobs.jobs) { const n = text("option", `${j.company} — ${j.title}`); n.value = j.id; el("ai-job").append(n); }
      changed();
      el("ai-run").disabled = !routes.length || !jobs.jobs.length;
      el("ai-status").textContent = routes.length ? "Choose a model and job, then confirm data sharing." : "No model routes configured. See docs/AI_MODELS.md for local setup.";
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
    try {
      const r = await api(`/api/jobs/${encodeURIComponent(el("ai-job").value)}/ai-analysis`, {route: el("ai-route").value, acknowledged: true});
      el("ai-status").textContent = `${r.notice} Model: ${r.model}. Checked: ${r.observed_at}.`;
      if (!r.requirements.length) el("ai-results").append(text("p", "No requirements returned. This does not establish a match or absence of requirements."));
      for (const row of r.requirements) {
        const section = document.createElement("section");
        section.append(text("blockquote", row.quote), text("p", "AI interpretation (unverified): " + row.reason));
        for (const [id, statement] of Object.entries(row.facts)) section.append(text("p", `${id}: ${statement}`));
        if (!row.fact_ids.length) section.append(text("p", "Evidence unknown; no candidate deficit inferred."));
        el("ai-results").append(section);
      }
    } catch (e) { el("ai-status").textContent = e.message; }
    finally { busy = false; ids.forEach((id) => { el(id).disabled = false; }); el("ai-consent").checked = false; }
  });
})();
