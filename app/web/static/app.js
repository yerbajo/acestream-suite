// The little behaviours htmx does not cover. Loaded on every page.
"use strict";

(() => {
  // How long a copy button keeps its tick before it offers to copy again.
  const COPY_TICK_MS = 1500;

  // Every button that copies carries the value in `data-copy` and confirms in place: the icon turns
  // into a tick, the tooltip says so, and the page's live region says it out loud for screen readers.

  // The clipboard API is missing on plain http and on older browsers, so the text goes through a
  // throwaway selection instead. It has to be off screen and unreadable rather than `display: none`,
  // which would make it impossible to select.
  function copyBySelection(text) {
    const scratch = document.createElement("textarea");
    scratch.value = text;
    scratch.setAttribute("readonly", "");
    scratch.style.position = "fixed";
    scratch.style.opacity = "0";
    document.body.appendChild(scratch);
    scratch.select();
    const copied = document.execCommand("copy");
    scratch.remove();
    return copied ? Promise.resolve() : Promise.reject(new Error("copy failed"));
  }

  function copyText(text) {
    if (navigator.clipboard && window.isSecureContext) {
      return navigator.clipboard.writeText(text).catch(() => copyBySelection(text));
    }
    return copyBySelection(text);
  }

  function announce(message) {
    const region = document.getElementById("announcer");
    if (region) region.textContent = message;
  }

  function showCopied(button) {
    button.classList.add("copied");
    button.title = "Copiado";
    announce("Hash copiado");
    setTimeout(() => {
      button.classList.remove("copied");
      button.title = "Copiar";
    }, COPY_TICK_MS);
  }

  // Delegated from the document, so buttons that htmx swapped in afterwards work without rebinding.
  document.addEventListener("click", (event) => {
    const button = event.target.closest("[data-copy]");
    if (!button) return;
    copyText(button.dataset.copy).then(() => showCopied(button), () => announce("No se pudo copiar"));
  });
})();
