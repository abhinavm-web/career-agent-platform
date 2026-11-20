"use strict";

// Each panel owns its request path and controls, including while the board refreshes.
window.applicationAnswers = function ({api, node, button}) {
  function field(title, control) {
    const label = node("label", "block-label", title);
    label.append(control);
    return label;
  }

  function textarea(value = "") {
    const input = node("textarea");
    input.rows = 2;
    input.maxLength = 4000;
    input.value = value;
    return input;
  }

  function topics(bank, other) {
    const select = node("select");
    const choices = [{topic: "", label: "Choose a topic"}, ...bank];
    if (other) choices.push({topic: "other", label: "Other employer question"});
    for (const item of choices) {
      const option = node("option", "", item.label);
      option.value = item.topic;
      select.append(option);
    }
    return select;
  }

  return function panel(jobId) {
    const path = jobId ? "/api/jobs/" + jobId + "/application-answers" : "/api/workspace/application-answers";
    const details = node("details", jobId ? "role-application-answers" : "panel profile-editor");
    if (jobId) details.dataset.jobId = jobId;
    const body = node("fieldset");
    body.append(node("legend", "", jobId ? "Employer questions" : "Profile answers"));
    const content = node("div");
    const status = node("p", "muted");
    status.setAttribute("role", "status");
    let loaded = false;
    let busy = false;

    async function run(action) {
      if (busy) return;
      busy = true;
      body.disabled = true;
      status.textContent = "";
      try { await action(); }
      catch (error) { status.textContent = error.message; }
      finally { busy = false; body.disabled = false; }
    }

    function render(data) {
      content.replaceChildren();
      if (jobId) renderRole(data);
      else renderProfile(data);
      loaded = true;
    }

    function save(payload, message) {
      return run(async () => {
        render(await api(path, "PUT", {...payload, confirmed: true}));
        status.textContent = message;
      });
    }

    function renderProfile(bank) {
      const select = topics(bank, false);
      const answer = textarea();
      const entry = () => bank.find(item => item.topic === select.value);
      const confirm = button("Confirm and save profile answer", "button", () =>
        save({topic: select.value, answer: answer.value, expected_revision: entry().revision}, "Profile answer saved."));
      const clear = button("Clear saved profile answer", "button", () =>
        save({topic: select.value, answer: "", expected_revision: entry().revision}, "Profile answer cleared."));
      function sync() {
        confirm.disabled = !entry() || !answer.value.trim();
        clear.disabled = !entry()?.answer;
      }
      select.addEventListener("change", () => { answer.value = entry()?.answer || ""; sync(); });
      answer.addEventListener("input", sync);
      sync();
      content.append(field("Question topic", select), field("Your answer", answer), confirm, clear);
    }

    function renderRole({bank, answers, locked}) {
      const editor = node("div");
      for (const item of answers) {
        const card = node("section");
        card.append(node("h4", "description", item.question));
        if (item.answer) {
          const value = textarea(item.answer);
          value.readOnly = true;
          card.append(field(item.reused ? "Confirmed from profile" : "Confirmed for this role", value));
          card.append(button("Copy confirmed answer", "button", () => run(async () => {
            const latest = await api(path);
            const current = latest.answers.find(row => row.question === item.question);
            if (!current || current.revision !== item.revision || current.answer !== item.answer) {
              render(latest);
              throw new Error("This answer changed. Review the latest answer before copying.");
            }
            if (!details.isConnected || !details.open || details.closest("[hidden]")) return;
            try { await navigator.clipboard.writeText(current.answer); }
            catch {
              body.disabled = false;
              value.focus(); value.select();
              throw new Error("Use your device’s Copy command on the selected answer.");
            }
            status.textContent = "Answer copied. Paste it into the matching employer field.";
          })));
        } else if (locked && item.stale) {
          const value = textarea(item.historical_answer);
          value.readOnly = true;
          card.append(field("Previously confirmed answer · profile has since changed", value));
        } else {
          card.append(node("p", "muted", item.stale ? "Profile answer changed. Review this question again." : "Unanswered"));
        }
        if (!locked) card.append(button("Review this question", "button", () => edit(item)));
        content.append(card);
      }
      if (locked) {
        content.append(node("p", "muted", "Answers are locked for this submitted role."));
        return;
      }
      function edit(item) {
        const question = textarea(item?.question);
        question.readOnly = Boolean(item);
        const select = topics(bank, true);
        select.value = item?.topic || "";
        const preview = textarea();
        preview.readOnly = true;
        const override = textarea(item && !item.reused ? item.answer : "");
        const selected = () => bank.find(row => row.topic === select.value);
        function submit(mode) {
          const profile = selected();
          return save({question: question.value, topic: select.value, expected_revision: item?.revision || 0,
            mode, answer: mode === "reuse" ? profile.answer : mode === "role" ? override.value : "",
            profile_revision: mode === "reuse" ? profile.revision : null},
          mode === "unanswered" ? "Question saved as unanswered." : "Answer confirmed for this question.");
        }
        const reuse = button("Confirm reuse for this exact question", "button", () => submit("reuse"));
        const own = button("Confirm role-only answer", "button", () => submit("role"));
        const pending = button("Save as unanswered", "button", () => submit("unanswered"));
        const previewField = field("Saved profile answer for review", preview);
        function sync() {
          preview.value = selected()?.answer || "";
          previewField.hidden = reuse.hidden = !preview.value;
          const ready = Boolean(select.value && question.value.trim());
          reuse.disabled = !ready || !preview.value;
          own.disabled = !ready || !override.value.trim();
          pending.disabled = !ready;
        }
        for (const input of [question, override]) input.addEventListener("input", sync);
        select.addEventListener("change", sync);
        sync();
        editor.replaceChildren(node("h4", "", item ? "Review saved question" : "Add an employer question"),
          field("Employer’s exact question", question), field("Question topic", select), previewField,
          reuse, field("Your answer for this role only", override), own, pending);
        if (item) {
          editor.append(button("Cancel review", "button", () => edit(null)));
          select.focus();
        }
      }
      content.append(editor);
      edit(null);
    }

    const load = () => run(async () => render(await api(path)));
    details.addEventListener("toggle", () => { if (details.open && !loaded) return load(); });
    body.append(content, button("Reload answers", "button", load));
    details.append(node("summary", "", jobId ? "Application form answers" : "Application answer bank"),
      node("p", "muted", jobId ? "Paste the question exactly as the employer asks it. Review each answer before copying it into the form."
        : "Save answers in your own words. Each employer question requires your confirmation before reuse."),
      body, status);
    return details;
  };
};
