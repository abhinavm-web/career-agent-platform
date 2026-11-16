"use strict";
(function () {
  const get = id => document.getElementById("profile-review-" + id);
  const root = document.getElementById("app"), panel = document.getElementById("profile-review");
  let routes = [], busy = false, revision = 0;
  const el = (tag, text, cls) => { const n = document.createElement(tag); n.textContent = text || ""; if (cls) n.className = cls; return n; };
  async function api(path, method = "GET", data) {
    const response = await fetch(path, {method, headers: {"X-Session-Token": root.dataset.session, "Content-Type": "application/json"},
      cache: "no-store", credentials: "same-origin", ...(data === undefined ? {} : {body: JSON.stringify(data)})});
    const result = await response.json();
    if (!response.ok) throw new Error(response.status === 403 ? "Session expired. Reload the application." : result.detail || "Request failed");
    return result;
  }
  function field(card, text, control) { const label = el("label", text, "block-label"); label.append(control); card.append(label); return control; }
  function button(card, text, action) { const b = el("button", text, "button"); b.type = "button"; b.addEventListener("click", action); card.append(b); return b; }
  function destination() {
    get("consent").checked = false;
    const r = routes.find(r => r.route === get("route").value);
    get("destination").textContent = r ? `${r.provider} · ${r.model} · ${r.destination}` : "Load a model route before requesting questions.";
    get("generate").disabled = busy || !r;
  }
  async function run(action) {
    if (busy) return;
    busy = true;
    // Retain disabled states for controls that require explicit acknowledgement.
    const controls = [...panel.querySelectorAll("button, input, textarea, select")].map(c => [c, c.disabled]);
    controls.forEach(([c]) => { c.disabled = true; });
    try { await action(); } catch (error) { get("status").textContent = error.message; }
    finally { busy = false; controls.forEach(([c, disabled]) => { if (c.isConnected) c.disabled = disabled; }); get("generate").disabled = !routes.length; }
  }
  function changed() { root.dispatchEvent(new Event("career-workspace-changed")); }
  function render(data, preserveAnswers = true) {
    const drafts = new Map(preserveAnswers ? [...get("results").querySelectorAll(".profile-review-question")].map(card => [card.dataset.question, [...card.querySelectorAll("textarea, select")].map(c => c.value)]) : []);
    revision = data.workspace_revision;
    const output = get("results"); output.replaceChildren();
    if (!data.reviews.length) output.append(el("p", "No saved questions yet."));
    data.reviews.forEach((review, reviewIndex) => {
      const group = el("details", "", "profile-review-batch"); group.open = reviewIndex === 0;
      group.append(el("summary", `Review ${review.id} · ${review.questions.filter(q => q.verified).length}/${review.questions.length} verified · ${review.model} · ${new Date(review.created_at).toLocaleString()}`));
      if (!review.questions.length) group.append(el("p", "No questions identified. This is not a certification of accuracy."));
      review.questions.forEach((q, index) => {
        const card = el("section", "", "career-fact profile-review-question"); group.append(card); card.dataset.question = `${review.id}:${index}`;
        card.append(el("h4", q.question), el("p", "AI-generated question; check its assumptions against the evidence.", "muted"));
        if (q.quote) card.append(el("strong", "Resume excerpt"), el("blockquote", q.quote));
        q.fact_ids.forEach(id => card.append(el("p", `${id} · ${review.sources[id].verified_by_candidate ? "Verified" : "Unverified"}: ${review.sources[id].statement}`)));
        const path = `/api/workspace/reviews/${review.id}/questions/${index}`;
        if (q.status === "unresolved") { card.append(el("p", "Left unresolved. No profile change was made.")); return; }
        if (q.status === "answered") {
          card.append(el("p", "Saved answer: " + q.answer));
          if (q.saved_changed) { card.append(el("p", "The saved fact has since changed or been removed. Review it in Career facts.")); return; }
          if (q.verified) { card.append(el("strong", "Verified by you. Generate a new application draft to use the updated profile.")); return; }
          card.append(el("p", "Saved as unverified. Check the exact wording before verifying."));
          const confirm = field(card, "I confirm this saved statement is accurate", el("input")); confirm.type = "checkbox";
          const verify = button(card, "Verify saved answer", () => run(async () => {
            if (!confirm.checked) return;
            await api(`/api/workspace/facts/${q.saved_fact_id}/verification`, "POST", {expected_revision: revision, verified: true, acknowledged: true});
            render(await api("/api/workspace/reviews")); changed(); get("status").textContent = "Answer verified. Existing application drafts need regeneration after profile changes.";
          }));
          verify.disabled = true; confirm.addEventListener("change", () => { verify.disabled = !confirm.checked; });
          return;
        }
        if (q.stale) { card.append(el("p", "Cited facts changed. Request a new review before answering this question.")); return; }
        const answer = field(card, "Your accurate career statement", el("textarea")); answer.rows = 3; answer.maxLength = 2000;
        answer.placeholder = "State exactly what you did, its scope, and any confirmed result. Leave uncertain details out.";
        const category = field(card, "Answer category", el("select"));
        ["experience", "projects", "skills", "education", "certifications"].forEach(value => { const o = el("option", value); o.value = value; category.append(o); });
        if (q.fact_ids.length) category.value = review.sources[q.fact_ids[0]].section;
        const target = field(card, "How should this answer update your profile?", el("select"));
        [["choose", "Choose an action"], ["", "Add a new fact"], ...q.fact_ids.map(id => [id, "Replace " + id])].forEach(([value, text]) => { const o = el("option", text); o.value = value; target.append(o); });
        const acknowledge = field(card, "I reviewed this wording and the selected update", el("input")); acknowledge.type = "checkbox";
        const save = button(card, "Save answer for verification", () => run(async () => {
          if (!acknowledge.checked || target.value === "choose" || !answer.value.trim()) return;
          render(await api(path + "/answer", "POST", {expected_revision: revision, answer: answer.value, section: category.value, replace_fact_id: target.value, acknowledged: true}));
          changed(); get("status").textContent = "Answer saved unverified. Read the saved statement, then verify it separately.";
        }));
        save.disabled = true;
        const ready = () => { save.disabled = !acknowledge.checked || !answer.value.trim() || target.value === "choose"; };
        const draft = drafts.get(card.dataset.question);
        if (draft?.length === 3) [answer.value, category.value, target.value] = draft;
        acknowledge.addEventListener("change", ready);
        [answer, category, target].forEach(control => control.addEventListener(control === answer ? "input" : "change", () => { acknowledge.checked = false; ready(); }));
        button(card, "Leave unresolved", () => run(async () => {
          if (answer.value.trim() && !window.confirm("Leave this question unresolved and discard this unsaved answer?")) return;
          render(await api(path + "/defer", "POST", {acknowledged: true})); get("status").textContent = "Question left unresolved. No career facts changed.";
        }));
      });
      output.append(group);
    });
  }
  get("route").addEventListener("change", destination);
  get("resume").addEventListener("input", () => { get("consent").checked = false; });
  get("load").addEventListener("click", () => run(async () => {
    if ([...get("results").querySelectorAll("textarea")].some(t => t.value.trim()) && !window.confirm("Reload saved questions and discard unsaved answers?")) return;
    const [models, reviews] = await Promise.all([api("/api/ai/routes"), api("/api/workspace/reviews")]);
    routes = models.routes; const previous = get("route").value || reviews.reviews[0]?.route; get("route").replaceChildren();
    routes.forEach(r => { const o = el("option", `${r.route} (${r.provider})`); o.value = r.route; get("route").append(o); });
    if (routes.some(r => r.route === previous)) get("route").value = previous;
    destination(); render(reviews, false); get("status").textContent = "Saved questions loaded. Answers change your profile only when you save them.";
  }));
  get("extract").addEventListener("click", () => run(async () => {
    const file = get("file").files[0];
    if (!file || !file.size || file.size > 2 * 1024 * 1024) throw new Error("Choose a PDF, DOCX or text file of at most 2 MB.");
    if (get("resume").value.trim()) throw new Error("Clear the comparison text before extracting a different file.");
    const content = await new Promise((resolve, reject) => { const reader = new FileReader(); reader.onload = () => resolve(String(reader.result).split(",", 2)[1]); reader.onerror = () => reject(new Error("Unable to read file")); reader.readAsDataURL(file); });
    const result = await api("/api/workspace/resume-extract", "POST", {filename: file.name, content});
    get("resume").value = result.text; get("consent").checked = false; get("status").textContent = "Resume text extracted. Review it before sharing with the model.";
  }));
  get("generate").addEventListener("click", () => run(async () => {
    if (!get("consent").checked) throw new Error("Confirm sharing your fact statements and resume text before requesting questions.");
    if ([...get("results").querySelectorAll("textarea")].some(t => t.value.trim()) && !window.confirm("Start a new review and discard unsaved answers?")) return;
    const route = routes.find(r => r.route === get("route").value);
    if (!route?.route_fingerprint) throw new Error("Reload model routes before sharing.");
    get("status").textContent = "Reviewing your profile and preparing questions...";
    try {
      render(await api("/api/workspace/reviews", "POST", {route: route.route, route_fingerprint: route.route_fingerprint, resume_text: get("resume").value, acknowledged: true}), false);
      get("status").textContent = "Questions saved. Review the evidence, then answer one question at a time. No facts were changed.";
    } finally { get("consent").checked = false; }
  }));
})();
