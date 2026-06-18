document.querySelectorAll("[data-autosubmit]").forEach((control) => {
  control.addEventListener("change", () => {
    control.form.submit();
  });
});

document.querySelectorAll("[data-context-combobox]").forEach((input) => {
  const syncContextValue = () => {
    const form = input.form;
    const hidden = form?.querySelector("[data-context-value]");
    const list = document.getElementById(input.getAttribute("list") || "");
    if (!hidden || !list) return;
    const option = Array.from(list.options).find((item) => item.value === input.value);
    hidden.value = option?.dataset.value || "";
  };
  input.addEventListener("input", syncContextValue);
  input.form?.addEventListener("submit", syncContextValue);
});

document.querySelectorAll("form").forEach((form) => {
  form.addEventListener("submit", () => {
    const button = form.querySelector("button[type='submit']");
    if (button) {
      button.setAttribute("aria-busy", "true");
    }
  });
});

document.querySelectorAll("[data-generate-meeting-link]").forEach((button) => {
  button.addEventListener("click", () => {
    const target = document.querySelector(button.dataset.target || "");
    if (!target) return;
    const stamp = Math.random().toString(36).slice(2, 5) + "-" + Math.random().toString(36).slice(2, 6);
    const provider = button.dataset.generateMeetingLink;
    target.value = provider === "zoom"
      ? `https://zoom.us/j/${Date.now().toString().slice(-10)}`
      : `https://meet.google.com/ecsp-${stamp}`;
    target.dispatchEvent(new Event("input", { bubbles: true }));
  });
});

document.querySelectorAll("[data-copy-from]").forEach((button) => {
  button.addEventListener("click", async () => {
    const source = document.querySelector(button.dataset.copyFrom || "");
    if (!source) return;
    const text = source.value || source.textContent || "";
    try {
      await navigator.clipboard.writeText(text);
      button.textContent = "Copiato";
      window.setTimeout(() => {
        button.textContent = "Copia testo";
      }, 1200);
    } catch {
      source.focus();
      source.select?.();
    }
  });
});

document.querySelectorAll("[data-field-state]").forEach((control) => {
  const wrapper = control.closest(".comm-field");
  const badge = wrapper?.querySelector(".field-head em");
  if (!wrapper || !badge) return;
  const originalState = control.dataset.fieldState;
  const syncFieldState = () => {
    const value = (control.value || "").trim();
    if (originalState === "prefilled" && value) {
      wrapper.classList.remove("is-empty", "is-manual");
      wrapper.classList.add("is-prefilled");
      badge.textContent = "precompilato";
      return;
    }
    if (value) {
      wrapper.classList.remove("is-empty", "is-prefilled");
      wrapper.classList.add("is-manual");
      badge.textContent = "manuale";
    } else {
      wrapper.classList.remove("is-manual", "is-prefilled");
      wrapper.classList.add("is-empty");
      badge.textContent = "vuoto";
    }
  };
  control.addEventListener("input", syncFieldState);
});
