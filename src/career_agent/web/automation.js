"use strict";
(function () {
  const el = (id) => document.getElementById(id);
  const node = (tag, value) => { const n = document.createElement(tag); n.textContent = value; return n; };
  let loading = false;
  const openEditors = new Set();
  async function api(path, data, method = "POST") {
    const response = await fetch(path, {method: data ? method : "GET", cache: "no-store",
      headers: {"X-Session-Token": el("app").dataset.session, "Content-Type": "application/json"},
      ...(data ? {body: JSON.stringify(data)} : {})});
    const result = await response.json();
    if (!response.ok) throw new Error(typeof result.detail === "string" ? result.detail : "Workflow request failed");
    return result;
  }
  function action(label, path, data) {
    const button = node("button", label); button.type = "button"; button.className = "button";
    button.addEventListener("click", async () => {
      button.disabled = true;
      try { await api(path, data); await load(); }
      catch (e) { el("automation-status").textContent = e.message; }
      finally { button.disabled = false; }
    });
    return button;
  }
  function editor(s) {
    const details = document.createElement("details");
    details.append(node("summary", "Edit paused search"));
    details.addEventListener("toggle", () => { if (details.open) openEditors.add(details); else openEditors.delete(details); });
    const form = document.createElement("form"); form.className = "crm-form";
    function field(label, value, type, min, max) {
      const wrapper = node("label", label), input = document.createElement("input");
      input.type = type; input.value = value; input.required = true;
      if (type === "number") { input.min = min; input.max = max; }
      else input.maxLength = 160;
      wrapper.append(input); form.append(wrapper); return input;
    }
    const query = field("Job titles / query", s.settings.query, "text");
    const location = field("Location", s.settings.location, "text");
    const minutes = field("Every (minutes)", s.interval_minutes, "number", 60, 10080);
    const results = field("Results per source", s.settings.results, "number", 1, 50);
    const siteFields = {};
    for (const [id, label] of [["indeed", "Indeed"], ["linkedin", "LinkedIn"], ["zip_recruiter", "ZipRecruiter"]]) {
      const wrapper = node("label", label), input = document.createElement("input");
      input.type = "checkbox"; input.checked = s.settings.sites.includes(id); siteFields[id] = input;
      wrapper.prepend(input); form.append(wrapper);
    }
    const consentLabel = node("label", " I confirm these settings for future recurring searches.");
    const consent = document.createElement("input"); consent.type = "checkbox"; consent.required = true;
    consentLabel.prepend(consent); form.append(consentLabel);
    const save = node("button", "Save paused search"); save.type = "submit";
    const cancel = node("button", "Cancel editing"); cancel.type = "button";
    const message = node("p", "The search stays paused. Resume separately to allow future requests. Past run history does not retain the old query or all previous settings."); message.setAttribute("role", "status");
    form.append(save, cancel, message);
    cancel.addEventListener("click", () => { openEditors.delete(details); details.open = false; load(); });
    form.addEventListener("submit", async event => {
      event.preventDefault(); if (save.disabled || !consent.checked) return;
      const controls = [query, location, minutes, results, ...Object.values(siteFields), consent, save, cancel];
      controls.forEach(control => { control.disabled = true; });
      try {
        await api(`/api/automation/searches/${s.id}`, {query: query.value, location: location.value,
          interval_minutes: Number(minutes.value), results: Number(results.value),
          sites: Object.keys(siteFields).filter(id => siteFields[id].checked), acknowledged: true,
          expected_settings: s.settings, expected_interval_minutes: s.interval_minutes}, "PUT");
        openEditors.delete(details); details.open = false; await load();
      } catch (e) { message.textContent = e.message; }
      finally { controls.forEach(control => { control.disabled = false; }); }
    });
    details.append(form); return details;
  }
  async function load() {
    if (loading || openEditors.size || el("automation-searches").querySelector("details[open]")) return;
    loading = true;
    try {
      const data = await api("/api/automation");
      if (openEditors.size || el("automation-searches").querySelector("details[open]")) return;
      for (const id of ["automation-searches", "automation-runs", "workflow-runs"]) el(id).replaceChildren();
      for (const s of data.schedules) {
        const card = node("section", `${s.settings.query} · ${s.settings.location} · ${s.settings.sites.join(", ")} · every ${s.interval_minutes / 60}h · ${s.enabled ? "enabled" : "paused"} · next due ${new Date(s.next_run * 1000).toLocaleString()}`);
        card.append(action(s.enabled ? "Pause" : "Resume", `/api/automation/searches/${s.id}`, {enabled: !s.enabled, acknowledged: true}));
        if (!s.enabled) card.append(editor(s), action("Remove saved search", `/api/automation/searches/${s.id}`, {remove: true, acknowledged: true}));
        el("automation-searches").append(card);
      }
      for (const r of data.runs) el("automation-runs").append(node("p", `${new Date(r.started * 1000).toLocaleString()} · ${r.status} · ${r.found ?? "—"} returned / ${r.added ?? "—"} added · ${r.message || "In progress"}`));
      for (const r of data.workflows) {
        const card = node("section", `${r.job_id} · ${r.status} · ${r.created_at} · ${r.error || ""}`);
        if (["failed", "queued"].includes(r.status)) card.append(action("Resume local workflow", `/api/workflows/${r.run_id}/retry`, {acknowledged: true}));
        el("workflow-runs").append(card);
      }
      el("automation-status").textContent = data.schedules.length ? "Schedules saved locally. Use Refresh on the opportunity board to see newly imported jobs. Pausing discards an in-flight import; its network request may take up to two minutes to finish." : "No automatic searches enabled yet.";
    } catch (e) { el("automation-status").textContent = e.message; }
    finally { loading = false; }
  }
  el("automation-refresh").addEventListener("click", load);
  el("automation-panel").addEventListener("toggle", () => { if (el("automation-panel").open) load(); });
  el("schedule-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (el("schedule-save").disabled || !el("schedule-consent").checked) return;
    el("schedule-save").disabled = true;
    try {
      await api("/api/automation/searches", {query: el("schedule-query").value, location: el("schedule-location").value,
        interval_minutes: Number(el("schedule-hours").value) * 60, results: Number(el("schedule-results").value),
        sites: ["indeed", "linkedin", "zip_recruiter"].filter((s) => el("schedule-" + s).checked), acknowledged: true});
      el("schedule-consent").checked = false; await load();
    } catch (e) { el("automation-status").textContent = e.message; }
    finally { el("schedule-save").disabled = false; }
  });
  setInterval(() => { if (el("automation-panel").open && !document.hidden) load(); }, 15000);
})();
