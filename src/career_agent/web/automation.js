"use strict";
(function () {
  const el = (id) => document.getElementById(id);
  const node = (tag, value) => { const n = document.createElement(tag); n.textContent = value; return n; };
  let loading = false;
  async function api(path, data) {
    const response = await fetch(path, {method: data ? "POST" : "GET", cache: "no-store",
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
  async function load() {
    if (loading) return;
    loading = true;
    try {
      const data = await api("/api/automation");
      for (const id of ["automation-searches", "automation-runs", "workflow-runs"]) el(id).replaceChildren();
      for (const s of data.schedules) {
        const card = node("section", `${s.settings.query} · ${s.settings.location} · ${s.settings.sites.join(", ")} · every ${s.interval_minutes / 60}h · ${s.enabled ? "enabled" : "paused"} · next due ${new Date(s.next_run * 1000).toLocaleString()}`);
        card.append(action(s.enabled ? "Pause" : "Resume", `/api/automation/searches/${s.id}`, {enabled: !s.enabled, acknowledged: true}));
        if (!s.enabled) card.append(action("Remove saved search", `/api/automation/searches/${s.id}`, {remove: true, acknowledged: true}));
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
