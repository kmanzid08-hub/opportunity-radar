"use strict";
document.querySelectorAll("form").forEach((form) => {
  form.addEventListener("submit", (event) => {
    if (form.dataset.confirm && !window.confirm(form.dataset.confirm)) {
      event.preventDefault();
      return;
    }
    const button = form.querySelector('button[type="submit"]');
    if (button) {
      button.disabled = true;
      button.textContent = form.hasAttribute("data-analysis") ? "Analyzing…" : form.method.toLowerCase() === "get" ? "Loading…" : "Saving…";
    }
    const progress = form.querySelector("[data-progress]");
    if (progress) progress.textContent = "Analysis is running. Please keep this page open.";
  });
});
window.addEventListener("pageshow", (event) => { if (event.persisted) window.location.reload(); });
