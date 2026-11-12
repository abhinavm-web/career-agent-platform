"use strict";
async function captureJob(mode) {
  const buttons = [document.getElementById("capture"), document.getElementById("capture-structured")], status = document.getElementById("status");
  buttons.forEach(button => { button.disabled = true; });
  try {
    const [tab] = await chrome.tabs.query({active: true, currentWindow: true});
    if (!tab?.id) throw new Error("No active page is available.");
    const [result] = await chrome.scripting.executeScript({target: {tabId: tab.id}, args: [mode], func: (captureMode) => {
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
        if (captureMode === "structured" && typeof posting.description === "string" && posting.description.length <= 100000) {
          // Template content is inert and is never attached to the active page.
          const template = document.createElement("template");
          template.innerHTML = posting.description;
          const blocks = new Set(["P", "DIV", "LI", "BR", "UL", "OL", "H1", "H2", "H3", "H4", "H5", "H6", "TR"]);
          const excluded = new Set(["SCRIPT", "STYLE", "TEMPLATE", "NOSCRIPT", "SVG", "IFRAME"]);
          const pieces = [], queue = [{node: template.content, end: false}];
          let count = 0;
          while (queue.length && ++count <= 10000) {
            const {node, end} = queue.pop();
            if (end) { pieces.push("\n"); continue; }
            if (node.nodeType === 3) { pieces.push(node.nodeValue || ""); continue; }
            if (node.nodeType === 1 && excluded.has(node.tagName)) continue;
            if (node.nodeType === 1 && blocks.has(node.tagName)) queue.push({node, end: true});
            const children = node.childNodes || [];
            for (let i = children.length - 1; i >= 0; i--) queue.push({node: children[i], end: false});
          }
          if (count <= 10000) capture.selection = pieces.join("").replace(/\r\n?/g, "\n").replace(/[ \t]+\n/g, "\n").replace(/\n{3,}/g, "\n\n").trim();
          else capture.selection = "";
          capture.text_source = "structured_description";
        }
      }
      if (captureMode === "structured" && !capture.text_source) capture.selection = "";
      return capture;
    }});
    const data = result?.result;
    if (!data || !data.title.trim() || data.title.length > 200) throw new Error("The page title is empty or longer than 200 characters. Add the job manually instead.");
    const url = new URL(data.url);
    if (!["http:", "https:"].includes(url.protocol) || url.username || url.password || data.url.length > 2048) throw new Error("This page does not have a supported job URL.");
    if (!data.selection.trim()) throw new Error(mode === "structured" ? "No single usable structured job description was found. Select text on the page instead." : "Select the job description on the page first.");
    if (data.selection.includes("\0")) throw new Error("The page text contains unsupported characters. Select and review the text manually.");
    if (data.selection.length > 50000) throw new Error("The selection exceeds 50,000 characters. Select a shorter excerpt.");
    const payload = JSON.stringify({format: "career-agent-job-capture-v1", title: data.title, url: data.url, selection: data.selection,
      ...(data.metadata ? {metadata: data.metadata} : {}), ...(data.text_source ? {text_source: data.text_source} : {})});
    if (payload.length > 60000) throw new Error("The capture is too large. Select a shorter excerpt.");
    await navigator.clipboard.writeText(payload);
    status.textContent = mode === "structured" ? "Copied structured page text. Review it carefully in Career Agent before saving." : "Copied. In Career Agent, open Add opportunity → Paste a browser capture.";
  } catch (error) { status.textContent = error?.message || "Could not capture this page."; }
  finally { buttons.forEach(button => { button.disabled = false; }); }
}
document.getElementById("capture").addEventListener("click", () => captureJob("selection"));
document.getElementById("capture-structured").addEventListener("click", () => captureJob("structured"));
