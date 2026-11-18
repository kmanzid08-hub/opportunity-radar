"use strict";

/* Clear progress feedback; never intercept or duplicate a real form action. */
document.querySelectorAll("form").forEach((form) => {
  form.addEventListener("submit", (event) => {
    if (form.dataset.confirm && !window.confirm(form.dataset.confirm)) {
      event.preventDefault();
      return;
    }
    const button = form.querySelector('button[type="submit"]');
    if (button) {
      button.disabled = true;
      button.setAttribute("aria-busy", "true");
      button.textContent = form.hasAttribute("data-analysis")
        ? "Analyzing…"
        : form.method.toLowerCase() === "get"
          ? "Finding matches…"
          : "Saving…";
    }
    const progress = form.querySelector("[data-progress]");
    if (progress) {
      progress.setAttribute("role", "status");
      progress.textContent = "We're working on your analysis. Keep this page open.";
    }
  });
});

/* A compact accessible navigation menu, without a distracting extra page. */
const more = document.querySelector(".nav-more");
if (more) {
  document.addEventListener("pointerdown", (event) => {
    if (!more.contains(event.target)) more.open = false;
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && more.open) {
      more.open = false;
      more.querySelector("summary")?.focus();
    }
  });
}

/* Show subtle feedback as people move through the dashboard. */
const cards = document.querySelectorAll(".opportunity-card");
if ("IntersectionObserver" in window && cards.length > 0 && !window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
  const observer = new IntersectionObserver((entries, obs) => {
    entries.forEach((entry) => {
      if (entry.isIntersecting) {
        entry.target.classList.add("is-visible");
        obs.unobserve(entry.target);
      }
    });
  }, { threshold: 0.04, rootMargin: "0px 0px 70px 0px" });
  cards.forEach((card) => {
    card.classList.add("awaiting-reveal");
    observer.observe(card);
  });
}

window.addEventListener("pageshow", (event) => {
  if (event.persisted) window.location.reload();
});
