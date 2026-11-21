"use strict";

/* Résumé tab: your original résumé beside the version recommended for one role.
 * Recommended content is verified facts only. Anything new enters through a
 * step-by-step wizard that ends with an explicit confirmation. Dynamic text is
 * inserted with textContent only. No model call happens without consent. */
window.resumeCompare = function ({api, node, button, notice, onDraftCreated}) {
  const SECTION_LABELS = {experience: "Experience", projects: "Projects", skills: "Skills", education: "Education", certifications: "Certifications"};
  const QUESTION = "Have you done work related to this requirement? Describe what you personally did, where and when, and any result you can substantiate. Keep limitations and qualifications.";

  return function create(job) {
    const root = node("div", "rc");
    const state = {data: null, loading: true, error: "", editingOriginal: false, aiMatches: new Map(), matchStatus: "",
      wizard: null, busy: false};
    const live = () => root.isConnected;

    async function refresh() {
      state.loading = true; state.error = ""; render();
      try { state.data = await api("/api/jobs/" + job.id + "/resume-compare"); }
      catch (error) { state.error = error.message; }
      state.loading = false;
      if (live()) render();
    }

    /* ---------- shared AI consent step (route + explicit consent) ---------- */
    function consentStep(prompt, onReady) {
      const box = node("div", "rc-consent");
      const status = node("p", "muted"); status.setAttribute("role", "status");
      const load = button("Choose an AI model", "button", async () => {
        load.disabled = true; status.textContent = "Loading configured models…";
        try {
          const {routes} = await api("/api/ai/routes");
          if (!routes.length) { status.textContent = "No AI model is configured. Open Settings & data → Model routes to add one."; load.disabled = false; return; }
          load.remove(); status.textContent = "";
          const label = node("label", "field", "Model"), select = node("select");
          for (const route of routes) { const option = node("option", "", route.route + " · " + route.model); option.value = route.route; select.append(option); }
          label.append(select);
          const destination = node("p", "muted");
          const consent = node("input"); consent.type = "checkbox";
          const consentLabel = node("label", "check-label"); consentLabel.append(consent, document.createTextNode(" " + prompt));
          const go = button("Continue", "button button-primary"); go.disabled = true;
          const current = () => routes.find(r => r.route === select.value);
          const sync = () => { const r = current(); destination.textContent = r ? r.provider + " · " + r.model + " · " + r.destination : ""; go.disabled = !consent.checked || !r?.route_fingerprint; };
          select.addEventListener("change", () => { consent.checked = false; sync(); }); consent.addEventListener("change", sync);
          go.addEventListener("click", () => { if (!go.disabled) onReady(current(), status, go); });
          sync(); box.append(label, destination, consentLabel, go, status);
        } catch (error) { status.textContent = error.message; load.disabled = false; }
      });
      box.append(load, status);
      return box;
    }

    /* ---------- left column: original résumé ---------- */
    function originalColumn() {
      const col = node("article", "rc-col rc-original");
      col.setAttribute("aria-labelledby", "rc-original-title");
      const title = node("h5", "", "Your original résumé"); title.id = "rc-original-title";
      col.append(title);
      const original = state.data.original;
      if (original.revision === 0 || state.editingOriginal) { col.append(originalForm(original)); return col; }
      col.append(node("p", "muted", (original.filename || "Pasted text") + " · saved " + original.saved_at + " · unchanged"));
      const text = node("pre", "rc-text", original.text);
      col.append(text, button("Replace original résumé", "button", () => { state.editingOriginal = true; render(); }));
      return col;
    }

    function originalForm(original) {
      const form = node("div", "rc-form");
      form.append(node("p", "", original.revision === 0
        ? "Add your current résumé once. It stays unchanged on this side so you can see what the recommendation adds."
        : "Replace the saved résumé text."));
      const file = node("input"); file.type = "file"; file.accept = ".pdf,.docx,.txt";
      const fileLabel = node("label", "field", "Upload a PDF, Word or text file (read locally)"); fileLabel.append(file);
      const area = node("textarea"); area.rows = 12; area.maxLength = 20000; area.value = original.text || "";
      const areaLabel = node("label", "field", "Or paste your résumé text"); areaLabel.append(area);
      const status = node("p", "muted"); status.setAttribute("role", "status");
      let filename = original.filename || "";
      file.addEventListener("change", async () => {
        const chosen = file.files[0]; if (!chosen) return;
        status.textContent = "Reading " + chosen.name + " locally…";
        try {
          const content = await new Promise((resolve, reject) => { const reader = new FileReader();
            reader.onload = () => resolve(String(reader.result).split(",")[1]); reader.onerror = () => reject(new Error("Unable to read this file.")); reader.readAsDataURL(chosen); });
          area.value = (await api("/api/workspace/resume-extract", "POST", {filename: chosen.name, content})).text;
          filename = chosen.name; status.textContent = "Text extracted. Check it, then save.";
        } catch (error) { status.textContent = error.message; }
      });
      const consent = node("input"); consent.type = "checkbox";
      const consentLabel = node("label", "check-label"); consentLabel.append(consent, document.createTextNode(" Save this text in my local workspace. It is stored unencrypted on this computer and never sent anywhere."));
      const save = button("Save original résumé", "button button-primary", async () => {
        if (save.disabled) return;
        if (!area.value.trim()) { status.textContent = "Add résumé text first."; return; }
        if (!consent.checked) { status.textContent = "Confirm saving the text locally."; return; }
        save.disabled = true;
        try {
          await api("/api/workspace/resume-original", "PUT", {text: area.value, filename, expected_revision: original.revision, acknowledged: true});
          state.editingOriginal = false; await refresh();
        } catch (error) { status.textContent = error.message; save.disabled = false; }
      });
      form.append(fileLabel, areaLabel, consentLabel, save, status);
      if (original.revision > 0) form.append(button("Cancel", "button", () => { state.editingOriginal = false; render(); }));
      return form;
    }

    /* ---------- right column: recommended résumé ---------- */
    function recommendedColumn() {
      const col = node("article", "rc-col rc-recommended");
      col.setAttribute("aria-labelledby", "rc-recommended-title");
      const title = node("h5", "", "Recommended for " + job.title); title.id = "rc-recommended-title";
      col.append(title);
      const {data} = state;
      const extra = [...state.aiMatches.keys()];
      const listed = new Set(data.sections.flatMap(s => s.bullets.map(b => b.fact_id)));
      if (!data.sections.length && !extra.length) {
        col.append(node("p", "", data.verified_fact_count
          ? "None of your verified facts share terms with this posting yet. Use “Add experience” below, or find matches with AI."
          : "You have no verified career facts yet. Verify facts in your career profile before a recommendation can be built."));
        if (!data.verified_fact_count) col.append(button("Open career profile", "button button-primary", () => document.getElementById("nav-profile")?.click()));
      }
      let count = 0;
      for (const section of data.sections) {
        col.append(node("h6", "", SECTION_LABELS[section.section] || section.section));
        const list = node("ul", "rc-bullets");
        for (const bullet of section.bullets) { list.append(bulletItem(bullet)); count += 1; }
        col.append(list);
        if (section.hidden) col.append(node("p", "muted", section.hidden + " more matching facts not shown in this section."));
      }
      for (const id of extra) {
        if (listed.has(id)) continue;
        const match = state.aiMatches.get(id);
        col.append(node("h6", "", "Suggested by AI"), (() => { const list = node("ul", "rc-bullets"); list.append(bulletItem({statement: match.statement, matched_terms: [], in_original: false, ai: match})); return list; })());
        count += 1;
      }
      if (data.unused_fact_count) col.append(node("p", "muted", data.unused_fact_count + " verified fact" + (data.unused_fact_count === 1 ? " is" : "s are") + " left out because they do not relate to this posting."));
      const actions = node("div", "rc-actions");
      actions.append(button("Find more matches with AI", "button", () => { state.matchStep = true; render(); }));
      const make = button("Create application draft from " + (count === 1 ? "this bullet" : "these " + count + " bullets"), "button button-primary", () => makeDraft(make, count));
      make.disabled = count === 0 || job.application_stage === "submitted";
      actions.append(make);
      col.append(actions);
      if (state.matchStep) col.append(matchWizard());
      const status = node("p", "muted", state.matchStatus); status.setAttribute("role", "status"); col.append(status);
      return col;
    }

    function bulletItem(bullet) {
      const item = node("li", "rc-bullet " + (bullet.in_original ? "rc-in" : "rc-added"));
      const tag = node("span", "rc-tag" + (bullet.ai ? " rc-tag-ai" : ""), bullet.ai ? "AI match · review" : bullet.in_original ? "In your résumé" : "Added for this role");
      item.append(tag, node("span", "rc-statement", bullet.statement));
      const meta = [];
      if (bullet.matched_terms?.length) meta.push("Shares: " + bullet.matched_terms.join(", "));
      if (bullet.ai) meta.push("Matched to: " + bullet.ai.quote.trim().split("\n")[0]);
      if (bullet.answers) meta.push("Answers: " + bullet.answers.trim().split("\n")[0].replace(/^[-*•]\s*/, ""));
      if (bullet.fact_id) meta.push(bullet.fact_id);
      if (meta.length) item.append(node("small", "rc-meta", meta.join(" · ")));
      return item;
    }

    function matchWizard() {
      const box = node("div", "rc-step");
      box.append(node("h6", "", "Find more matches with AI"),
        node("p", "muted", "The model only chooses which of your verified facts fit the posting. It cannot rewrite them or add anything new, and every match is shown for your review."));
      box.append(consentStep("I consent to sending this posting and my verified fact statements to this model.", async (route, status, go) => {
        go.disabled = true; status.textContent = "Asking " + route.model + "…";
        try {
          const result = await api("/api/jobs/" + job.id + "/ai-tailoring-suggestions", "POST", {route: route.route, route_fingerprint: route.route_fingerprint, acknowledged: true});
          for (const row of result.suggestions) state.aiMatches.set(row.fact_id, {quote: row.job_quote, statement: row.original_fact, warnings: row.review_warnings});
          state.matchStep = false;
          state.matchStatus = result.suggestions.length ? "AI matched " + result.suggestions.length + " verified fact(s). Review them before drafting." : "AI found no additional matches.";
          if (live()) render();
        } catch (error) { status.textContent = error.message; go.disabled = false; }
      }), button("Cancel", "button", () => { state.matchStep = false; render(); }));
      return box;
    }

    async function makeDraft(make, count) {
      make.disabled = true; state.matchStatus = "Creating a draft from " + count + " verified facts…"; render();
      try {
        const info = await api("/api/jobs/" + job.id + "/tailoring/facts");
        const wanted = new Set([...state.data.sections.flatMap(s => s.bullets.map(b => b.fact_id)), ...state.aiMatches.keys()]);
        const ids = info.facts.filter(f => wanted.has(f.id)).map(f => f.id).slice(0, info.max_facts);
        if (!ids.length) throw new Error("No verified facts to include.");
        await api("/api/jobs/" + job.id + "/tailored-draft", "POST", {fact_ids: ids, section_order: Object.keys(SECTION_LABELS),
          workspace_revision: info.workspace_revision, facts_sha256: info.facts_sha256, job_description_revision: info.description_revision});
        state.matchStatus = "Draft created. Review and export it in Application.";
        if (live()) { render(); onDraftCreated?.(); }
      } catch (error) { state.matchStatus = error.message; if (live()) render(); }
    }

    /* ---------- add-experience wizard: one step at a time ---------- */
    function wizard() {
      const box = node("section", "rc-wizard");
      box.setAttribute("aria-label", "Add experience");
      const gaps = state.data.gaps;
      box.append(node("h5", "", "Add experience from this posting"));
      if (!gaps.length) { box.append(node("p", "muted", state.data.requirements_truncated
        ? "No unaddressed requirement sections were recognized. Read the posting for requirements this tool could not classify."
        : "Every recognized requirement already links to a verified fact.")); return box; }
      const w = state.wizard;
      const steps = ["Choose a requirement", "Describe what you did", "Confirm"];
      const progress = node("ol", "rc-steps");
      steps.forEach((label, i) => { const li = node("li", i + 1 === (w?.step || 1) ? "current" : "", label); if (i + 1 === (w?.step || 1)) li.setAttribute("aria-current", "step"); progress.append(li); });
      box.append(progress);
      if (!w) { box.append(stepPick(gaps)); return box; }
      box.append(w.step === 2 ? stepAnswer(w) : stepConfirm(w));
      return box;
    }

    function stepPick(gaps) {
      const area = node("div", "rc-step");
      area.append(node("p", "", gaps.length + " requirement" + (gaps.length === 1 ? " has" : "s have") + " no verified fact yet. Choose one you have real experience with. Skipping is fine."));
      const list = node("ul", "rc-gap-list");
      for (const gap of gaps) {
        const item = node("li"), pick = button(gap.quote.replace(/^[-*•]\s*/, ""), "rc-gap", () => {
          state.wizard = {step: 2, gap, answer: "", section: "experience", question: QUESTION, aiStatus: ""}; render(); });
        item.append(pick, node("small", "muted", gap.category.replace("_", " "))); list.append(item);
      }
      area.append(list); return area;
    }

    function stepAnswer(w) {
      const area = node("div", "rc-step");
      area.append(node("blockquote", "rc-quote", w.gap.quote), node("p", "rc-question", w.question));
      const answerLabel = node("label", "field", "What you did"), answer = node("textarea"); answer.rows = 5; answer.maxLength = 1000; answer.value = w.answer;
      answer.addEventListener("input", () => { w.answer = answer.value; next.disabled = !answer.value.trim(); });
      answerLabel.append(answer);
      const sectionLabel = node("label", "field", "Résumé section"), section = node("select");
      for (const name of Object.keys(SECTION_LABELS)) { const o = node("option", "", SECTION_LABELS[name]); o.value = name; section.append(o); }
      section.value = w.section; section.addEventListener("change", () => { w.section = section.value; }); sectionLabel.append(section);
      const next = button("Next: review", "button button-primary", () => { w.answer = answer.value; if (!w.answer.trim()) return; w.step = 3; render(); });
      next.disabled = !w.answer.trim();
      const ask = node("div", "rc-ask");
      ask.append(button("Ask AI for a sharper question", "button", () => {
        ask.replaceChildren(consentStep("I consent to sending this requirement, my draft answer and my verified fact statements to this model.", async (route, status, go) => {
          go.disabled = true; status.textContent = "Asking " + route.model + "…";
          try {
            const result = await api("/api/jobs/" + job.id + "/ai-experience-question", "POST", {route: route.route, route_fingerprint: route.route_fingerprint,
              requirement: w.gap.quote, draft_answer: answer.value, acknowledged: true});
            w.answer = answer.value; w.question = result.question; if (live()) render();
          } catch (error) { status.textContent = error.message; go.disabled = false; }
        }));
      }));
      area.append(ask, answerLabel, sectionLabel, next, button("Back", "button", () => { state.wizard = null; render(); }));
      return area;
    }

    function stepConfirm(w) {
      const area = node("div", "rc-step");
      area.append(node("p", "", "This exact text will be added to your verified facts and your recommended résumé. Nothing is rewritten."),
        node("blockquote", "rc-quote rc-final", w.answer.trim()), node("small", "muted", "Section: " + SECTION_LABELS[w.section] + " · answers the requirement: " + w.gap.quote.split("\n")[0]));
      const confirm = node("input"); confirm.type = "checkbox";
      const label = node("label", "check-label"); label.append(confirm, document.createTextNode(" I checked this statement. It accurately describes my own experience."));
      const status = node("p", "muted"); status.setAttribute("role", "status");
      const add = button("Add to my résumé", "button button-primary", async () => {
        if (add.disabled) return; add.disabled = true; status.textContent = "Saving…";
        try {
          const workspace = (await api("/api/workspace")).workspace;
          const saved = await api("/api/jobs/" + job.id + "/experience-answer", "POST", {expected_revision: workspace.revision,
            job_quote: w.gap.quote, answer: w.answer.trim(), section: w.section, acknowledged: true});
          await api("/api/workspace/facts/" + saved.fact_id + "/verification", "POST", {expected_revision: saved.workspace.revision, verified: true, acknowledged: true});
          state.wizard = null; state.matchStatus = "Added. It now appears in the recommended résumé.";
          await refresh();
        } catch (error) { status.textContent = error.message; add.disabled = !confirm.checked; }
      });
      add.disabled = true; confirm.addEventListener("change", () => { add.disabled = !confirm.checked; });
      area.append(label, add, button("Back", "button", () => { w.step = 2; render(); }), status);
      return area;
    }

    /* ---------- page ---------- */
    function render() {
      root.replaceChildren();
      if (state.loading && !state.data) { root.append(node("p", "muted", "Loading your résumé comparison…")); return; }
      if (state.error) { root.append(node("p", "warning", state.error), button("Try again", "button", refresh)); return; }
      const head = node("header", "rc-head");
      head.append(node("p", "muted", "Left: your résumé exactly as saved. Right: verified facts that fit this posting. Nothing is invented; a new statement only appears after you confirm it."),
        (() => { const legend = node("p", "rc-legend"); legend.append(node("span", "rc-tag", "In your résumé"), node("span", "rc-tag rc-tag-added", "Added for this role"), node("span", "rc-tag rc-tag-ai", "AI match · review")); return legend; })());
      const grid = node("div", "rc-grid"); grid.append(originalColumn(), recommendedColumn());
      root.append(head, grid, wizard());
    }

    render();
    return {element: root, refresh};
  };
};
