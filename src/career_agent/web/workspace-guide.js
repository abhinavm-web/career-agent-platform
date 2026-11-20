"use strict";

(() => {
  const get = id => document.getElementById(id);
  const workspace = get("workspace"), dialog = get("workspace-help"), toggle = get("control-help");
  const guideText = {
    home: "Start with your profile, compare saved opportunities, then check the tracker for an application that needs attention.",
    jobs: "Select a saved role to compare your experience with the posting, inspect sponsorship evidence and prepare an application.",
    tracker: "Select a role to review delivery evidence, recruiter contacts and follow-ups. Check for a receipt before deciding whether another attempt is appropriate.",
    profile: "Save your goals, verify individual career facts and maintain the answers you want to reuse across applications.",
    h1b: "Compare the posting’s own language with dated records for a matching filing entity. Check the relationship between the hiring company and the legal employer.",
    ai: "Choose a provider and review the information it will receive. Generated suggestions remain available for your review.",
    automation: "Choose the query, sources and interval. Enabled searches run while the dashboard is running.",
    settings: "Manage model connections, public research data and workspace backups here.",
    activity: "Inspect earlier review decisions and the application materials each decision concerned."
  };
  const topics = [
    [/^refresh data$/i, "Reload saved workspace data, delivery records and the current opportunity list."],
    [/^menu$|^close menu$/i, "Show or close the workspace navigation on small screens."],
    [/^fit$/i, "Review matched and missing evidence beside the relevant job requirements."],
    [/^overview$/i, "Review the role, hiring company and the evidence available for your decision."],
    [/^application$|application studio/i, "Review the application materials, job-specific answers and delivery record for this role."],
    [/^next$|^previous$/i, "Move between pages of the current list while keeping its filters."],
    [/reconcil|receipt|pending confirmation|awaiting confirmation|dispatch|delivery status/i, "Check the employer’s response. A requested submission can remain pending until a confirmation or an explicit review resolves it."],
    [/authorization|sponsor.*now|sponsor.*future|transfer|answer bank|reusable answer|employer question/i, "Check the exact wording, country and timing. Confirm the response for this role or save a role-specific answer when the default does not fit."],
    [/filing|lca|petition|legal entity|sponsors?hip|h-1b/i, guideText.h1b],
    [/freshness|last checked|availability|posting observation|live check/i, "The posting date and the last availability check answer different questions. Review their dates and open the employer posting if evidence is stale."],
    [/decision|keyword score|fit evidence|evidence to review|requirement|qualification/i, "Compare the cited posting language with your saved experience. Missing evidence means this workspace has yet to support the requirement."],
    [/submit application|approve and send|submit with career agent/i, "Review the destination, contact details, and final PDF. Approval starts a real employer submission. Complete any employer verification in its browser window."],
    [/confirm|verify|verified|attest/i, "Check the statement and its source before confirming. Other workflow steps may rely on this recorded decision."],
    [/archive|duplicate/i, "Inspect the original opportunity and any existing application before archiving. Archived roles remain available from the archive filter."],
    [/provider|model route|AI analysis/i, guideText.ai],
    [/tailor|résumé|resume|cover letter|document revision/i, "Review the selected job and the candidate evidence used in this document. Check its wording and version before exporting or applying."],
    [/saved search|recurring|interval/i, guideText.automation],
    [/follow-up|reminder/i, "Set a date and context for your next step with the employer. The tracker keeps the reminder in your workspace."],
    [/filter|sort|search saved|location filter/i, "Adjust the list of displayed opportunities. Clear the filters to bring saved roles back into view."],
    [/backup|export|download/i, "Review the scope and destination of the file. Exports can contain your private career or application information."],
    [/discovery|jobspy|job sources/i, "Choose sources and search terms to find roles. Results can be incomplete or refer to postings that have changed."]
  ];
  const controls = "button, a[href], input, select, textarea, label, summary, [role=tab], [data-help]";
  let inspect = false, opener = null;
  const brief = text => (text || "").replace(/\s+/g, " ").trim();
  function nameOf(el) {
    const label = el.labels?.[0] || el;
    const text = Array.from(label.childNodes).filter(n => n.nodeType === Node.TEXT_NODE).map(n => n.textContent).join(" ");
    return brief(el.getAttribute("aria-label") || text || el.getAttribute("title") || el.textContent).slice(0, 140);
  }
  function describe(el) {
    const described = (el.getAttribute("aria-describedby") || "").split(/\s+/).map(id => get(id)?.textContent).filter(Boolean).join(" ");
    if (el.dataset.help || described) return el.dataset.help || described;
    if (el.id.startsWith("nav-")) return guideText[el.id.slice(4)];
    const label = nameOf(el);
    const topic = topics.find(([pattern]) => pattern.test(label));
    if (topic) return topic[1];
    const scope = el.closest("form, .detail-section, .panel, details, .screen");
    const explanation = scope?.querySelector(":scope > .section-description, :scope > p.muted, :scope > p.section-description, :scope > .section-heading .section-description");
    if (explanation && brief(explanation.textContent)) return brief(explanation.textContent);
    const screen = el.closest(".screen")?.id.replace("-screen", "") || "home";
    return guideText[screen] || guideText.home;
  }
  function showHelp(el, trigger) {
    opener = trigger;
    get("guide-title").textContent = nameOf(el) || "Workspace help";
    get("guide-copy").textContent = describe(el);
    dialog.showModal();
  }
  function setInspect(value) {
    inspect = value;
    document.body.classList.toggle("inspect-controls", value);
    toggle.setAttribute("aria-pressed", String(value));
    get("guide-announcement").textContent = value ? "Control help on. Choose a control to read its help, or press Escape to exit." : "Control help off.";
  }
  toggle.addEventListener("click", () => setInspect(!inspect));
  get("guide-close").addEventListener("click", () => dialog.close());
  dialog.addEventListener("close", () => {
    if (opener?.isConnected) opener.focus({preventScroll: true});
    opener = null;
  });
  const targetFor = target => target.closest?.(controls);
  function explainInstead(event) {
    const el = targetFor(event.target);
    if (!inspect || !el || el === toggle || dialog.contains(el) || !get("app").contains(el)) return;
    event.preventDefault(); event.stopImmediatePropagation();
    if (event.type === "click" || event.type === "keydown") {
      setInspect(false);
      const field = el.control || el;
      showHelp(field, field.matches(":disabled") ? toggle : field);
    }
  }
  document.addEventListener("pointerdown", explainInstead, true);
  document.addEventListener("click", explainInstead, true);
  document.addEventListener("keydown", event => {
    if (dialog.open) return;
    if (event.key === "Escape" && inspect) { setInspect(false); toggle.focus(); }
    if (event.key === "F1" && get("app").contains(event.target)) {
      event.preventDefault();
      const el = targetFor(event.target) || document.querySelector(".screen:not([hidden]) h2");
      if (el) showHelp(el, event.target);
    } else if (["Enter", " ", "ArrowDown", "ArrowUp"].includes(event.key)) explainInstead(event);
  }, true);

  // Section help stays visible; inspection also covers controls created after the initial render.
  const headings = "h2, .panel > h3, .panel > div > h3, .detail-section > h4, form > h3, form > h4, .profile-editor > h3";
  function decorate(root) {
    if (!(root instanceof Element)) return;
    const items = root.matches(headings) ? [root] : root.querySelectorAll(headings);
    for (const heading of items) {
      if (heading.closest("button, a, summary, [role=tab]") || heading.querySelector(".section-info")) continue;
      const info = document.createElement("button");
      info.type = "button"; info.className = "section-info";
      const headingName = brief(heading.textContent);
      heading.setAttribute("aria-label", headingName);
      info.setAttribute("aria-label", "Information about " + headingName);
      info.setAttribute("aria-haspopup", "dialog");
      info.addEventListener("click", () => showHelp(heading, info));
      heading.append(info);
    }
  }
  decorate(workspace);
  new MutationObserver(records => {
    for (const record of records) {
      if (record.target.matches?.(headings) && !record.target.querySelector(".section-info")) decorate(record.target);
      else for (const child of record.addedNodes) decorate(child);
    }
  }).observe(workspace, {childList: true, subtree: true});

  const menu = get("menu-toggle"), rail = menu.closest(".rail");
  function setMenu(open) {
    rail.classList.toggle("menu-open", open);
    menu.setAttribute("aria-expanded", String(open));
    menu.textContent = open ? "Close menu" : "Menu";
  }
  menu.addEventListener("click", () => setMenu(menu.getAttribute("aria-expanded") !== "true"));
  get("workspace-nav").addEventListener("click", event => { if (event.target.closest(".nav-item")) setMenu(false); });
  rail.addEventListener("keydown", event => { if (event.key === "Escape") { setMenu(false); menu.focus(); } });
})();
