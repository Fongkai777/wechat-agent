(() => {
  const shell = document.querySelector("#rawShell");
  const sidebar = document.querySelector("#rawSidebar");
  const handle = document.querySelector("#sidebarResizeHandle");
  if (!shell || !sidebar || !handle) return;

  const storageKey = "wechat-agent.chat-sidebar-width";
  const defaultWidth = 320;
  const minWidth = 280;
  const maxWidth = 640;
  const minConversationWidth = 380;
  let preferredWidth = defaultWidth;
  let drag = null;

  try {
    const saved = Number(window.localStorage.getItem(storageKey));
    if (Number.isFinite(saved) && saved > 0) {
      preferredWidth = Math.max(minWidth, Math.min(maxWidth, saved));
    }
  } catch (_) {
    // Resizing still works when browser storage is unavailable.
  }

  function bounds() {
    const width = shell.getBoundingClientRect().width || window.innerWidth;
    return { max: Math.max(minWidth, Math.min(maxWidth, width - minConversationWidth)), mobile: width <= 760 };
  }

  function applyWidth(width) {
    const { max } = bounds();
    const actual = Math.round(Math.max(minWidth, Math.min(max, width)));
    shell.style.setProperty("--chat-sidebar-width", `${actual}px`);
    handle.setAttribute("aria-valuemax", String(max));
    handle.setAttribute("aria-valuenow", String(actual));
    return actual;
  }

  function persistWidth() {
    try {
      window.localStorage.setItem(storageKey, String(preferredWidth));
    } catch (_) {
      // The current width remains usable without persistence.
    }
  }

  function finishDrag(event) {
    if (!drag || (event && event.pointerId !== drag.pointerId)) return;
    const pointerId = drag.pointerId;
    drag = null;
    shell.classList.remove("is-resizing");
    if (handle.hasPointerCapture(pointerId)) handle.releasePointerCapture(pointerId);
    persistWidth();
  }

  handle.addEventListener("pointerdown", (event) => {
    if (event.button !== 0 || event.isPrimary === false || bounds().mobile || drag) return;
    event.preventDefault();
    drag = { pointerId: event.pointerId, startX: event.clientX, startWidth: sidebar.getBoundingClientRect().width };
    handle.setPointerCapture(event.pointerId);
    handle.focus({ preventScroll: true });
    shell.classList.add("is-resizing");
  });

  handle.addEventListener("pointermove", (event) => {
    if (!drag || event.pointerId !== drag.pointerId) return;
    preferredWidth = applyWidth(drag.startWidth + event.clientX - drag.startX);
  });

  for (const name of ["pointerup", "pointercancel", "lostpointercapture"]) {
    handle.addEventListener(name, finishDrag);
  }
  window.addEventListener("blur", () => finishDrag());

  handle.addEventListener("dblclick", () => {
    if (bounds().mobile) return;
    preferredWidth = defaultWidth;
    applyWidth(preferredWidth);
    persistWidth();
  });

  handle.addEventListener("keydown", (event) => {
    if (bounds().mobile) return;
    const step = event.shiftKey ? 50 : 10;
    const current = sidebar.getBoundingClientRect().width;
    let next;
    if (event.key === "ArrowLeft") next = current - step;
    else if (event.key === "ArrowRight") next = current + step;
    else if (event.key === "Home") next = minWidth;
    else if (event.key === "End") next = bounds().max;
    else if (event.key === "Enter") next = defaultWidth;
    else return;
    event.preventDefault();
    preferredWidth = applyWidth(next);
    persistWidth();
  });

  window.addEventListener("resize", () => {
    finishDrag();
    // Constrain the layout without overwriting the user's desktop preference.
    applyWidth(preferredWidth);
  });
  applyWidth(preferredWidth);
})();
