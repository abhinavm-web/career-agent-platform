"use strict";

/* The beginner app: three places (My résumé, Jobs, one job) and nothing else.
 * Text is inserted with textContent only. Nothing is ever sent to an employer. */
(function () {
  const root = document.getElementById("app");
  const main = document.getElementById("main");
  const live = document.getElementById("live");
  const token = root.dataset.session;
  let info = null;

  function el(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined) node.textContent = text;
    return node;
  }
  function btn(label, cls, onClick) {
    const node = el("button", "btn " + (cls || ""), label);
    node.type = "button";
    if (onClick) node.addEventListener("click", onClick);
    return node;
  }
  function field(label, control) {
    const wrap = el("label", "field", label);
    wrap.append(control);
    return wrap;
  }
  function say(message) { live.textContent = ""; live.textContent = message; }

  async function request(path, method = "GET", body) {
    const response = await fetch(path, {method, cache: "no-store", credentials: "same-origin",
      headers: {"X-Session-Token": token, ...(body === undefined ? {} : {"Content-Type": "application/json"})},
      ...(body === undefined ? {} : {body: JSON.stringify(body)})});
    if (!response.ok) {
      let detail = "Something went wrong. Please try again.";
      try { const data = await response.json(); detail = typeof data.detail === "string" ? data.detail : detail; } catch (e) { /* keep default */ }
      const error = new Error(response.status === 403 ? "This page expired. Please reload it." : detail);
      error.status = response.status;
      throw error;
    }
    return response;
  }
  const api = async (path, method, body) => (await request(path, method, body)).json();

  async function download(path, filename, method, body) {
    const blob = await (await request(path, method, body)).blob();
    const link = el("a");
    link.href = URL.createObjectURL(blob);
    link.download = filename;
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(link.href), 4000);
  }

  function printOnly(paper) {
    paper.classList.add("print-target");
    const done = () => { paper.classList.remove("print-target"); window.removeEventListener("afterprint", done); };
    window.addEventListener("afterprint", done);
    window.print();
  }

  /* ---------------------------------------------------------------- My résumé */
  function viewResume() {
    const w = {step: 1, text: "", filename: "", name: "", contact: "", lines: [], answers: [], index: 0};
    const stepNames = ["Add your résumé", "Your name", "Check each line", "Done"];
    main.replaceChildren();
    const holder = el("div");
    main.append(el("h1", "", "Set up your résumé"), el("p", "lead", "You only do this once. We will only ever use lines you say are true."), holder);

    function frame(content) {
      holder.replaceChildren();
      const steps = el("ol", "steps");
      stepNames.forEach((label, i) => {
        const item = el("li", i + 1 === w.step ? "current" : i + 1 < w.step ? "done" : "", (i + 1) + ". " + label);
        if (i + 1 === w.step) item.setAttribute("aria-current", "step");
        steps.append(item);
      });
      const card = el("div", "card");
      card.append(content);
      holder.append(steps, card);
    }

    function stepOne(error) {
      const box = el("div");
      box.append(el("h2", "", "Add your résumé"));
      const file = el("input"); file.type = "file"; file.accept = ".pdf,.docx,.txt";
      const area = el("textarea"); area.value = w.text; area.maxLength = 20000;
      const status = el("p", "error"); status.setAttribute("role", "alert"); status.textContent = error || "";
      file.addEventListener("change", async () => {
        const chosen = file.files[0]; if (!chosen) return;
        status.textContent = "Reading your file…";
        try {
          const content = await new Promise((resolve, reject) => { const reader = new FileReader();
            reader.onload = () => resolve(String(reader.result).split(",")[1]); reader.onerror = () => reject(new Error("We could not read that file.")); reader.readAsDataURL(chosen); });
          area.value = (await api("/api/workspace/resume-extract", "POST", {filename: chosen.name, content})).text;
          w.filename = chosen.name; status.textContent = "";
        } catch (e) { status.textContent = e.message; }
      });
      const next = btn("Next", "primary", async () => {
        if (!area.value.trim()) { status.textContent = "Add your résumé first: upload a file or paste the text."; return; }
        next.disabled = true; status.textContent = "";
        try {
          const parsed = await api("/api/simple/parse", "POST", {text: area.value});
          if (!parsed.lines.length) throw new Error("We could not find any lines in that résumé. Try pasting the text instead.");
          Object.assign(w, {text: area.value, name: w.name || parsed.name, contact: parsed.contact, lines: parsed.lines, answers: [], index: 0, step: 2});
          stepTwo();
        } catch (e) { status.textContent = e.message; next.disabled = false; }
      });
      box.append(field("Upload a PDF, Word or text file", file), el("p", "muted", "or"), field("Paste your résumé here", area), status, el("div", "row"));
      box.lastChild.append(next);
      frame(box);
    }

    function stepTwo(error) {
      const box = el("div");
      box.append(el("h2", "", "What is your name?"), el("p", "muted", "This goes at the top of your résumé and cover letter."));
      const name = el("input"); name.type = "text"; name.value = w.name; name.maxLength = 120; name.autocomplete = "name";
      const status = el("p", "error"); status.setAttribute("role", "alert"); status.textContent = error || "";
      const row = el("div", "row");
      row.append(btn("Back", "", () => { w.step = 1; stepOne(); }), btn("Next", "primary", () => {
        if (!name.value.trim()) { status.textContent = "Please type your name."; return; }
        w.name = name.value.trim(); w.step = 3; stepThree();
      }));
      box.append(field("Your name", name), status, row);
      frame(box); name.focus();
    }

    function stepThree() {
      const box = el("div");
      const total = w.lines.length, line = w.lines[w.index];
      box.append(el("h2", "", "Is this line true?"), el("p", "muted", "Line " + (w.index + 1) + " of " + total + ". Only say yes if it is true about you."));
      const bar = el("div", "progress"); const fill = el("span"); fill.style.width = Math.round(100 * w.index / total) + "%"; bar.append(fill);
      const card = el("div", "line-card"); card.append(el("small", "", line.section), document.createTextNode(line.text));
      const answer = (yes) => { w.answers[w.index] = yes; if (w.index + 1 < total) { w.index += 1; stepThree(); } else { w.step = 4; stepFour(); } };
      const row = el("div", "row");
      row.append(btn("Yes, this is true", "primary", () => answer(true)), btn("No, skip it", "", () => answer(false)));
      if (w.index > 0) row.append(btn("Back", "", () => { w.index -= 1; stepThree(); }));
      box.append(bar, card, row);
      frame(box); box.querySelector(".btn.primary").focus();
    }

    function stepFour(error) {
      const confirmed = w.lines.filter((_, i) => w.answers[i]);
      const box = el("div");
      box.append(el("h2", "", "You said " + confirmed.length + " of " + w.lines.length + " lines are true"),
        el("p", "", "Lines you skipped will never be used. By saving, you confirm every line you said yes to is accurate."));
      const status = el("p", "error"); status.setAttribute("role", "alert"); status.textContent = error || "";
      const row = el("div", "row");
      const save = btn("Save and find a job", "primary", async () => {
        if (!confirmed.length) { status.textContent = "Say yes to at least one line so we have something to use."; return; }
        save.disabled = true; status.textContent = "";
        try {
          await api("/api/simple/resume", "POST", {name: w.name, contact: w.contact, filename: w.filename, text: w.text,
            confirmed: confirmed.map(({text, section}) => ({text, section})), acknowledged: true,
            workspace_revision: info.workspace_revision, resume_revision: info.resume_revision});
          info = await api("/api/simple/state");
          say("Your résumé is saved.");
          location.hash = "#/jobs";
        } catch (e) { status.textContent = e.message; save.disabled = false; }
      });
      row.append(btn("Back", "", () => { w.step = 3; w.index = w.lines.length - 1; stepThree(); }), save);
      box.append(status, row);
      frame(box);
    }
    stepOne();
  }

  /* --------------------------------------------------------------------- Jobs */
  async function viewJobs() {
    main.replaceChildren();
    main.append(el("h1", "", "Pick a job to apply for"));
    main.append(el("p", "lead", info.name ? "Hi " + info.name.split(" ")[0] + ". Choose a job and we will get your résumé and cover letter ready." : "Choose a job."));
    const bar = el("div", "row");
    const search = el("input"); search.type = "search"; search.placeholder = "Search jobs"; search.setAttribute("aria-label", "Search jobs");
    const addToggle = btn("Add a job", "primary");
    bar.append(search, addToggle);
    const formHolder = el("div"), list = el("ul", "jobs");
    main.append(bar, formHolder, list);

    async function load() {
      const data = await api("/api/simple/jobs?q=" + encodeURIComponent(search.value));
      list.replaceChildren();
      if (!data.jobs.length) {
        const empty = el("li", "card");
        empty.append(el("strong", "", search.value ? "No jobs match that search." : "You have no jobs yet."),
          el("p", "muted", "Found a job you like? Choose Add a job and paste it in."));
        list.append(empty); return;
      }
      for (const job of data.jobs) {
        const item = el("li"), open = el("button", "job"); open.type = "button";
        open.append(el("strong", "", job.title), el("span", "muted", [job.company, job.location].filter(Boolean).join(" · ")));
        open.addEventListener("click", () => { location.hash = "#/job/" + job.id; });
        item.append(open); list.append(item);
      }
    }
    let timer = null;
    search.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(() => load().catch(() => {}), 250); });

    addToggle.addEventListener("click", () => {
      if (formHolder.firstChild) { formHolder.replaceChildren(); return; }
      const card = el("div", "card");
      card.append(el("h2", "", "Add a job"));
      const inputs = {};
      for (const [key, label, type] of [["title", "Job title", "text"], ["company", "Company", "text"], ["location", "Where is it? (optional)", "text"], ["job_url", "Link to the job posting", "url"]]) {
        inputs[key] = el("input"); inputs[key].type = type; inputs[key].maxLength = 500; card.append(field(label, inputs[key]));
      }
      inputs.description = el("textarea"); inputs.description.maxLength = 50000;
      card.append(field("Paste the job description (this helps us match your résumé)", inputs.description));
      const status = el("p", "error"); status.setAttribute("role", "alert");
      const save = btn("Save job", "primary", async () => {
        status.textContent = ""; save.disabled = true;
        try {
          const result = await api("/api/jobs", "POST", {title: inputs.title.value, company: inputs.company.value, location: inputs.location.value,
            job_url: inputs.job_url.value, description: inputs.description.value});
          location.hash = "#/job/" + result.job_id;
        } catch (e) { status.textContent = e.message; save.disabled = false; }
      });
      const row = el("div", "row"); row.append(save, btn("Cancel", "", () => formHolder.replaceChildren()));
      card.append(status, row); formHolder.append(card); inputs.title.focus();
    });
    await load();
    main.append(el("p", "note", "Want to search job websites? Open More tools, then Opportunity board, then Find jobs."));
  }

  /* ------------------------------------------------------------------ One job */
  function resumePaper(resume) {
    const paper = el("article", "paper");
    paper.append(el("p", "name", resume.name || "Your name"));
    for (const line of [resume.headline, resume.contact]) if (line) paper.append(el("p", "sub", line));
    for (const section of resume.sections) {
      paper.append(el("h4", "", section.title));
      const list = el("ul");
      for (const bullet of section.bullets) {
        const item = el("li", bullet.new ? "new" : "");
        if (bullet.new) item.append(el("span", "tag", "NEW"));
        item.append(document.createTextNode(bullet.text)); list.append(item);
      }
      paper.append(list);
    }
    return paper;
  }
  function originalPaper(rows) {
    const paper = el("article", "paper");
    for (const row of rows) {
      if (row.kind === "blank") paper.append(el("p", "orig-line", " "));
      else paper.append(el("p", row.kind === "heading" ? "orig-heading" : "orig-line", row.text));
    }
    return paper;
  }
  function letterPaper(letter) {
    const paper = el("article", "paper");
    let list = null;
    for (const item of letter.paragraphs) {
      if (item.type === "bullet") { if (!list) { list = el("ul"); paper.append(list); } list.append(el("li", "", item.text)); }
      else { list = null; paper.append(el("p", "", item.text)); }
    }
    return paper;
  }

  async function viewJob(id) {
    main.replaceChildren(el("p", "muted", "Getting your documents ready…"));
    let data;
    try { data = await api("/api/jobs/" + id + "/simple"); }
    catch (e) {
      main.replaceChildren(el("h1", "", e.status === 412 ? "Add your résumé first" : "We could not open this job"), el("p", "lead", e.message));
      main.append(btn("Set up my résumé", "primary", () => { location.hash = "#/resume"; })); return;
    }
    const {job} = data;
    let letter = data.cover_letter, letterHolder = null;
    main.replaceChildren();
    const back = el("a", "link", "← All jobs"); back.href = "#/jobs";
    main.append(back, el("h1", "", job.title), el("p", "lead", [job.company, job.location].filter(Boolean).join(" · ")));

    main.append(el("h2", "", "1. Your résumé"),
      el("p", "lead", data.added_count ? "Left is your original. Right is the version we recommend for this job. " + data.added_count + " line" + (data.added_count === 1 ? " was" : "s were") + " added because they match the job (shown in green)."
        : "Left is your original. Right is the version we recommend for this job. Nothing needed to be added."));
    const papers = el("div", "papers");
    const left = el("div", "paper-wrap"), right = el("div", "paper-wrap");
    const orig = originalPaper(data.original), rec = resumePaper(data.resume);
    left.append(el("h3", "", "Your original"), orig); right.append(el("h3", "", "Recommended for this job"), rec);
    papers.append(left, right); main.append(papers);

    const more = el("div", "ai-box");
    more.append(el("strong", "", "Did you do something that fits this job?"));
    if (data.job_asks.length) more.append(el("p", "note", "This job asks for: " + data.job_asks.join(" · ")));
    const what = el("textarea"); what.maxLength = 1000; what.rows = 3; what.setAttribute("aria-label", "Something true you did that fits this job");
    what.placeholder = "Example: I ran the front desk at my school's office for two years.";
    const sure = el("input"); sure.type = "checkbox";
    const sureLabel = el("label", "check"); sureLabel.append(sure, document.createTextNode("This is true about me."));
    const addStatus = el("p", "error"); addStatus.setAttribute("role", "alert");
    const addLine = btn("Add it to my résumé", "", async () => {
      addStatus.textContent = "";
      if (what.value.trim().length < 8) { addStatus.textContent = "Write a sentence about what you did."; return; }
      if (!sure.checked) { addStatus.textContent = "Tick the box to say it is true."; return; }
      addLine.disabled = true;
      try {
        await api("/api/simple/add-line", "POST", {text: what.value, job_id: id, workspace_revision: info.workspace_revision, acknowledged: true});
        say("Added to your résumé."); info = await api("/api/simple/state"); await viewJob(id);
      } catch (e) { addStatus.textContent = e.message; addLine.disabled = false; }
    });
    more.append(what, sureLabel, addLine, addStatus);
    main.append(more);

    main.append(el("h2", "", "2. Your cover letter"));
    letterHolder = el("div"); main.append(letterHolder);
    function showLetter() {
      letterHolder.replaceChildren();
      const paper = letterPaper(letter); paper.id = "letter-paper"; letterHolder.append(paper);
      letterHolder.append(el("p", "note", letter.written_by === "ai" ? "Written by AI from your true lines. Read every sentence before you send it." : "Made from your true lines. Change the wording in Word if you like."));
    }
    showLetter();
    if (info.ai_available) {
      const box = el("div", "ai-box"); main.append(box);
      const start = btn("Write a better letter with AI", "", async () => {
        start.disabled = true;
        try {
          const {routes} = await api("/api/ai/routes");
          if (!routes.length) { box.append(el("p", "error", "No AI service is set up yet.")); start.disabled = false; return; }
          const route = routes[0];
          start.remove();
          const consent = el("input"); consent.type = "checkbox";
          const label = el("label", "check"); label.append(consent, document.createTextNode("Send my true résumé lines and this job to " + route.provider + " (" + route.model + ") so it can write the letter."));
          const go = btn("Write my letter", "primary"); go.disabled = true;
          consent.addEventListener("change", () => { go.disabled = !consent.checked; });
          const status = el("p", "error"); status.setAttribute("role", "alert");
          go.addEventListener("click", async () => {
            go.disabled = true; status.textContent = "Writing…";
            try {
              const result = await api("/api/jobs/" + id + "/simple/ai-cover-letter", "POST", {route: route.route, route_fingerprint: route.route_fingerprint, acknowledged: true});
              letter = result.cover_letter; showLetter(); box.remove(); say("Your AI cover letter is ready.");
            } catch (e) { status.textContent = e.message; go.disabled = !consent.checked; }
          });
          box.append(label, go, status);
        } catch (e) { box.append(el("p", "error", e.message)); start.disabled = false; }
      });
      box.append(start, el("p", "note", "Optional. Your letter above already works without AI."));
    }

    main.append(el("h2", "", "3. Apply"), el("p", "lead", "We never send anything for you. Download your documents, then apply on the employer's site."));
    const status = el("p", "error"); status.setAttribute("role", "alert");
    const actions = el("div", "actions");
    const guard = (work) => async () => { status.textContent = ""; try { await work(); } catch (e) { status.textContent = e.message; } };
    actions.append(
      btn("Download résumé (Word)", "primary", guard(() => download("/api/jobs/" + id + "/simple/resume.docx", "resume.docx"))),
      btn("Save résumé as PDF", "", () => printOnly(rec)),
      btn("Download cover letter (Word)", "primary", guard(() => download("/api/jobs/" + id + "/simple/cover-letter.docx", "cover-letter.docx", "POST", {paragraphs: letter.paragraphs}))),
      btn("Save cover letter as PDF", "", () => printOnly(document.getElementById("letter-paper"))));
    main.append(actions, status);
    if (job.url) {
      const open = el("a", "btn primary", "Open the job posting to apply");
      open.href = job.url; open.target = "_blank"; open.rel = "noopener noreferrer";
      const row = el("div", "row"); row.append(open); main.append(row);
    }
    main.append(el("p", "note", "Tip: to save as PDF, choose “Save as PDF” as the printer in the window that opens."));
  }

  /* ------------------------------------------------------------------- Router */
  function markNav(name) {
    for (const [id, key] of [["nav-jobs", "jobs"], ["nav-resume", "resume"]]) {
      const link = document.getElementById(id);
      if (key === name) link.setAttribute("aria-current", "page"); else link.removeAttribute("aria-current");
    }
  }
  async function route() {
    try {
      info = await api("/api/simple/state");
      const hash = location.hash.replace(/^#\/?/, "");
      const [page, arg] = hash.split("/");
      if (page !== "resume" && !info.resume_saved) { location.hash = "#/resume"; return; }
      if (page === "resume") { markNav("resume"); viewResume(); }
      else if (page === "job" && arg) { markNav("jobs"); await viewJob(arg); }
      else { markNav("jobs"); await viewJobs(); }
      main.focus();
      document.title = "Career Agent";
    } catch (error) {
      main.replaceChildren(el("h1", "", "Something went wrong"), el("p", "lead", error.message));
    }
  }
  window.addEventListener("hashchange", route);
  route();
})();
