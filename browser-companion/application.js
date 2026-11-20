"use strict";

// Runs in Chrome's isolated world, in the top document only. Approval lives
// in this document and popup. Navigation, timeout, or edits invalidate it.
function leverApplication(operation, nonce, contact) {
  const url = new URL(location.href);
  const path = /^\/([a-z0-9_-]+)\/([0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})\/apply\/?$/i.exec(url.pathname);
  if (!path || !["https://jobs.lever.co", "https://jobs.eu.lever.co"].includes(url.origin) ||
      url.username || url.password) return {error: "Open the employer’s Lever application form first."};
  const form = document.querySelector("form#application-form");
  const submit = form?.querySelector("button#btn-submit");
  const title = document.querySelector("h2")?.textContent.trim();
  if (!title || !form || !submit || form.method !== "post" || new URL(form.action).origin !== url.origin ||
      new URL(form.action).pathname !== url.pathname || submit.form !== form || submit.disabled ||
      submit.hasAttribute("formaction") || !/^submit application$/i.test(submit.textContent.trim()) ||
      !submit.getClientRects().length || form.querySelector('input[type="password"]')) {
    return {error: "This form is unavailable or has changed. Use the employer’s application page."};
  }
  if (globalThis.careerAgentSubmitAttempted) return {error: "Submission already requested on this page. Check the employer’s response."};
  if (operation === "fill") {
    const name = form.querySelector('input[name="name"][type="text"]');
    const email = form.querySelector('input[name="email"]');
    if (!name || !email || !["email","text"].includes(email.type)) return {error: "Complete the contact fields on this form."};
    if (!contact?.name || !contact.email || contact.name.length > 200 || contact.email.length > 254) return {error: "Enter your name and email."};
    let count = 0;
    for (const [field, value] of [[name, contact.name], [email, contact.email]]) {
      if (field.value || field.disabled || field.readOnly) continue;
      field.value = value;
      field.dispatchEvent(new Event("input", {bubbles: true}));
      field.dispatchEvent(new Event("change", {bubbles: true}));
      count++;
    }
    delete globalThis.careerAgentReview;
    return {filled: count};
  }
  if (!form.reportValidity()) return {error: "Complete the required fields on the employer’s page, then review again."};
  const fields = [], signature = [], files = [];
  for (const element of form.elements) {
    if (!element.name || element.matches(":disabled") || !["INPUT","SELECT","TEXTAREA"].includes(element.tagName)) continue;
    const type = element.type;
    if (["button","submit","reset","image"].includes(type)) continue;
    if (type === "password") return {error: "Finish authentication on the employer’s page."};
    if (["radio","checkbox"].includes(type) && !element.checked) continue;
    let value = element.value, display = value;
    if (type === "file") {
      const selected = Array.from(element.files);
      files.push(...selected);
      value = selected.map(f => [f.name, f.size, f.lastModified]);
      display = selected.map(f => f.name).join(", ");
    } else if (element.tagName === "SELECT") {
      const options = Array.from(element.selectedOptions);
      value = options.map(o => o.value);
      display = options.map(o => o.textContent.trim()).join(", ");
    }
    signature.push([element.name, type, value]);
    // Internal provider state stays inside this document.
    if (type === "hidden" || /^(?:g-recaptcha-response|h-captcha-response)$/i.test(element.name)) continue;
    const label = element.labels?.[0]?.textContent.trim() || element.getAttribute("aria-label") || element.name;
    if (display) fields.push([label, display]);
  }
  if (!fields.length) return {error: "Complete the application before reviewing it."};
  const snapshot = JSON.stringify([url.href, title, form.action, fields, signature]);
  if (operation === "preview") {
    globalThis.careerAgentReview = {nonce, snapshot, files, form, submit, expires: Date.now() + 300000};
    setTimeout(() => {
      if (globalThis.careerAgentReview?.nonce === nonce) delete globalThis.careerAgentReview;
    }, 300000);
    return {title, site: path[1], destination: url.origin + url.pathname, fields};
  }
  const review = globalThis.careerAgentReview;
  delete globalThis.careerAgentReview;
  if (operation !== "submit" || !review || review.nonce !== nonce || review.snapshot !== snapshot ||
      review.expires <= Date.now() || review.form !== form || review.submit !== submit ||
      files.length !== review.files.length || files.some((f, i) => f !== review.files[i])) {
    return {error: "The application changed or its review expired. Review it again."};
  }
  globalThis.careerAgentSubmitAttempted = true;
  submit.click();
  return {requested: true};
}

const reviewButton = document.getElementById("review-application");
const approveButton = document.getElementById("send-application");
const preview = document.getElementById("application-preview");
const applicationStatus = document.getElementById("application-status");
let approval = null;

document.getElementById("fill-contact").addEventListener("submit", async event => {
  event.preventDefault();
  approval = null; approveButton.hidden = true; preview.replaceChildren();
  const form = event.currentTarget;
  try {
    const [tab] = await chrome.tabs.query({active: true, currentWindow: true});
    const contact = {name: form.elements.fullName.value.trim(), email: form.elements.email.value.trim()};
    const [reply] = await chrome.scripting.executeScript({target: {tabId: tab.id}, func: leverApplication, args: ["fill", "", contact]});
    if (reply?.result?.error || !Number.isInteger(reply?.result?.filled)) throw new Error(reply?.result?.error || "Unable to fill contact details.");
    applicationStatus.textContent = reply.result.filled + " empty contact fields filled. Review the employer’s form.";
    form.reset();
  } catch (error) { applicationStatus.textContent = error.message; }
});

reviewButton.addEventListener("click", async () => {
  approval = null; approveButton.hidden = true; reviewButton.disabled = true;
  preview.replaceChildren(); applicationStatus.textContent = "";
  try {
    const [tab] = await chrome.tabs.query({active: true, currentWindow: true});
    const nonce = crypto.randomUUID();
    const [reply] = await chrome.scripting.executeScript({target: {tabId: tab.id}, func: leverApplication, args: ["preview", nonce]});
    const result = reply?.result;
    if (!result?.fields || !reply.documentId) throw new Error(result?.error || "Unable to read this application.");
    const heading = document.createElement("strong"); heading.textContent = result.title + " · " + result.site;
    const link = document.createElement("p"); link.textContent = result.destination; preview.append(heading, link);
    const list = document.createElement("dl");
    for (const [key, value] of result.fields) {
      const term = document.createElement("dt"), answer = document.createElement("dd");
      term.textContent = key; answer.textContent = value; list.append(term, answer);
    }
    preview.append(list); approval = {tabId: tab.id, documentId: reply.documentId, nonce}; approveButton.hidden = false;
  } catch (error) { applicationStatus.textContent = error.message; }
  finally { reviewButton.disabled = false; }
});

approveButton.addEventListener("click", async () => {
  const current = approval;
  if (!current) return;
  approval = null; approveButton.hidden = true; reviewButton.disabled = true;
  try {
    const [tab] = await chrome.tabs.query({active: true, currentWindow: true});
    if (tab.id !== current.tabId) throw new Error("Active tab changed. Review the application again.");
    const [reply] = await chrome.scripting.executeScript({target: {tabId: current.tabId, documentIds: [current.documentId]},
      func: leverApplication, args: ["submit", current.nonce]});
    if (!reply?.result?.requested) throw new Error(reply?.result?.error || "Check the employer’s page for the outcome.");
    applicationStatus.textContent = "Submission requested. Complete any employer verification, then record the confirmation in Career Agent.";
  } catch (error) { applicationStatus.textContent = error.message + " Check the employer’s page before retrying."; }
  finally { reviewButton.disabled = false; }
});

document.getElementById("open-vault").addEventListener("click", async () => {
  const edge = /Edg\//.test(navigator.userAgent);
  try { await chrome.tabs.create({url: edge ? "edge://settings/passwords" : "chrome://password-manager/passwords"}); }
  catch { applicationStatus.textContent = "Open Passwords and autofill in your browser settings."; }
});
