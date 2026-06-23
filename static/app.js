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

// Filtro al volo per la sezione "Scambi con l'Autorita" (Compagine).
(function () {
  var list = document.getElementById("scambi-list");
  if (!list) return;
  var search = document.getElementById("scambi-search");
  var chips = document.getElementById("scambi-chips");
  var empty = document.getElementById("scambi-empty");
  var moreBtn = document.getElementById("scambi-more");
  var activeCat = "";
  var showAll = false;
  var LIMIT = 6;

  function apply() {
    var q = (search && search.value ? search.value : "").trim().toLowerCase();
    var filtering = !!activeCat || !!q;
    var rows = list.querySelectorAll(".document-row");
    var matched = 0, shown = 0;
    rows.forEach(function (b) {
      var cat = b.getAttribute("data-cat") || "";
      var text = b.getAttribute("data-text") || "";
      var match = (!activeCat || cat === activeCat) && (!q || text.indexOf(q) !== -1);
      // a riposo mostra solo i primi LIMIT; quando filtri o "mostra tutti", tutti
      var visible = match && (filtering || showAll || matched < LIMIT);
      b.style.display = visible ? "" : "none";
      if (match) matched++;
      if (visible) shown++;
    });
    if (empty) empty.hidden = shown !== 0;
    if (moreBtn) {
      if (!filtering && matched > LIMIT) {
        moreBtn.hidden = false;
        moreBtn.textContent = showAll ? "Mostra meno" : "Mostra tutti (" + matched + ")";
      } else {
        moreBtn.hidden = true;
      }
    }
  }

  if (moreBtn) moreBtn.addEventListener("click", function () { showAll = !showAll; apply(); });
  if (search) search.addEventListener("input", apply);
  if (chips) {
    chips.addEventListener("click", function (e) {
      var btn = e.target.closest(".chip");
      if (!btn) return;
      activeCat = btn.getAttribute("data-cat") || "";
      chips.querySelectorAll(".chip").forEach(function (c) { c.classList.remove("active"); });
      btn.classList.add("active");
      apply();
    });
  }
  apply();  // applica subito il limite "mostra alcuni"
})();

// Form documenti dedicato di Compagine: apre il modale con la categoria gia' impostata.
(function () {
  var modal = document.querySelector("[data-doc-modal]");
  if (!modal) return;
  var sel = modal.querySelector("[data-doc-category]");
  var titleH = modal.querySelector("[data-doc-title-h]");
  function openDoc(cat, label) {
    if (sel && cat) {
      for (var i = 0; i < sel.options.length; i++) {
        if (sel.options[i].value === cat) { sel.selectedIndex = i; break; }
      }
    }
    if (titleH && label) titleH.textContent = label;
    modal.hidden = false;
  }
  function closeDoc() { modal.hidden = true; }
  document.querySelectorAll("[data-open-doc]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      openDoc(btn.getAttribute("data-category") || "", btn.getAttribute("data-doc-title") || "");
    });
  });
  modal.querySelectorAll("[data-close-doc]").forEach(function (b) {
    b.addEventListener("click", closeDoc);
  });
  modal.addEventListener("click", function (e) { if (e.target === modal) closeDoc(); });
})();

// Registri: campo "altro" rivelato quando si seleziona l'opzione "altro" in una tendina guidata.
document.querySelectorAll("select.guided-select").forEach((sel) => {
  const toggle = () => {
    const inp = sel.parentNode.querySelector(".altro-input");
    if (!inp) return;
    inp.style.display = (sel.value || "").trim().toLowerCase() === "altro" ? "block" : "none";
  };
  sel.addEventListener("change", toggle);
  toggle();
});
