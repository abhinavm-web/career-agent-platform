"use strict";
document.getElementById("capture").addEventListener("click", async () => {
  const button = document.getElementById("capture"), status = document.getElementById("status");
  button.disabled = true;
  try {
    const [tab] = await chrome.tabs.query({active: true, currentWindow: true});
    if (!tab?.id) throw new Error("No active page is available.");
    const [result] = await chrome.scripting.executeScript({target: {tabId: tab.id}, func: () => {
      const capture = {title: document.title, url: location.href, selection: window.getSelection()?.toString() || ""};
      // Treat page metadata as an unverified hint. Do not choose between
      // multiple postings or infer identity from unrelated structured data.
      const scripts = document.querySelectorAll('script[type="application/ld+json"]');
      const postings = []; let bounded = scripts.length <= 20, visited = 0;
      for (let i = 0; i < Math.min(scripts.length, 20); i++) {
        const script = scripts[i];
        if ((script.textContent || "").length > 100000) { bounded = false; break; }
        let root;
        try { root = JSON.parse(script.textContent); } catch (_) { continue; }
        const queue = [root];
        while (queue.length) {
          if (++visited > 500) { bounded = false; break; }
          const item = queue.shift();
          if (Array.isArray(item)) {
            if (queue.length + item.length > 500) { bounded = false; break; }
            queue.push(...item); continue;
          }
          if (!item || typeof item !== "object") continue;
          const kinds = Array.isArray(item["@type"]) ? item["@type"] : [item["@type"]];
          if (kinds.some(kind => kind === "JobPosting" || kind === "https://schema.org/JobPosting")) postings.push(item);
          if (item["@graph"]) queue.push(item["@graph"]);
        }
        if (!bounded) break;
      }
      if (bounded && postings.length === 1) {
        const posting = postings[0], org = posting.hiringOrganization;
        const clean = value => typeof value === "string" && value.trim() && value.length <= 200 ? value.trim() : "";
        const jobTitle = clean(posting.title), company = clean(org && !Array.isArray(org) && typeof org === "object" ? org.name : "");
        if (jobTitle || company) capture.metadata = {job_title: jobTitle, company};
      }
      return capture;
    }});
    const data = result?.result;
    if (!data || !data.title.trim() || data.title.length > 200) throw new Error("The page title is empty or longer than 200 characters. Add the job manually instead.");
    const url = new URL(data.url);
    if (!["http:", "https:"].includes(url.protocol) || url.username || url.password || data.url.length > 2048) throw new Error("This page does not have a supported job URL.");
    if (!data.selection.trim()) throw new Error("Select the job description on the page first.");
    if (data.selection.length > 50000) throw new Error("The selection exceeds 50,000 characters. Select a shorter excerpt.");
    const payload = JSON.stringify({format: "career-agent-job-capture-v1", title: data.title, url: data.url, selection: data.selection,
      ...(data.metadata ? {metadata: data.metadata} : {})});
    if (payload.length > 60000) throw new Error("The capture is too large. Select a shorter excerpt.");
    await navigator.clipboard.writeText(payload);
    status.textContent = "Copied. In Career Agent, open Add opportunity → Paste a browser capture.";
  } catch (error) { status.textContent = error?.message || "Could not capture this page."; }
  finally { button.disabled = false; }
});
