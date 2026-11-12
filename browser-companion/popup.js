"use strict";
document.getElementById("capture").addEventListener("click", async () => {
  const button = document.getElementById("capture"), status = document.getElementById("status");
  button.disabled = true;
  try {
    const [tab] = await chrome.tabs.query({active: true, currentWindow: true});
    if (!tab?.id) throw new Error("No active page is available.");
    const [result] = await chrome.scripting.executeScript({target: {tabId: tab.id}, func: () => ({
      title: document.title, url: location.href, selection: window.getSelection()?.toString() || ""
    })});
    const data = result?.result;
    if (!data || !data.title.trim() || data.title.length > 200) throw new Error("The page title is empty or longer than 200 characters. Add the job manually instead.");
    const url = new URL(data.url);
    if (!["http:", "https:"].includes(url.protocol) || url.username || url.password || data.url.length > 2048) throw new Error("This page does not have a supported job URL.");
    if (!data.selection.trim()) throw new Error("Select the job description on the page first.");
    if (data.selection.length > 50000) throw new Error("The selection exceeds 50,000 characters. Select a shorter excerpt.");
    const payload = JSON.stringify({format: "career-agent-job-capture-v1", title: data.title, url: data.url, selection: data.selection});
    if (payload.length > 60000) throw new Error("The capture is too large. Select a shorter excerpt.");
    await navigator.clipboard.writeText(payload);
    status.textContent = "Copied. In Career Agent, open Add opportunity → Paste a browser capture.";
  } catch (error) { status.textContent = error?.message || "Could not capture this page."; }
  finally { button.disabled = false; }
});
