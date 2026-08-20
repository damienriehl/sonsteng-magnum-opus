// Accessible native-form controller for the scope-gated assessment review page.
(() => {
  "use strict";
  const main = document.querySelector("[data-assessment-id]");
  const form = document.getElementById("assessment-override-form");
  const status = document.getElementById("assessment-override-status");
  if (!main || !form || !status) return;

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = form.querySelector("button[type=submit]");
    const data = new FormData(form);
    button.disabled = true;
    button.setAttribute("aria-busy", "true");
    status.textContent = "Recording the signed override…";
    try {
      const response = await fetch("/edit/v1/assessment-override", {
        method: "POST",
        credentials: "same-origin",
        headers: { "content-type": "application/json", "X-Edit-Request": "1" },
        body: JSON.stringify({
          id: `assessment-override-${crypto.randomUUID()}`,
          assessment_id: main.dataset.assessmentId,
          heading_id: data.get("heading_id"),
          score: Number(data.get("score")),
          note: data.get("note"),
        }),
      });
      if (!response.ok) throw new Error(String(response.status));
      status.textContent = "Override recorded with your authenticated identity. Reloading the audit record.";
      status.focus();
      location.reload();
    } catch {
      status.textContent = "The override was not recorded. Review the fields and try again.";
      status.focus();
      button.disabled = false;
    } finally {
      button.removeAttribute("aria-busy");
    }
  });
})();
