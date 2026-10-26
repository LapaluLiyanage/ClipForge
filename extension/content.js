/* ClipForge content script — a floating button on YouTube and Facebook video pages.
   Deliberately not spliced into either site's own action row: that markup changes
   often, and a self-contained pill survives their redesigns. */

(function () {
  const BTN_ID = "clipforge-fab";

  const host = location.hostname;
  const isFacebook = /(^|\.)facebook\.com$/.test(host);

  // Paths that point at a single Facebook video: /watch/?v=, /reel/<id>,
  // /<page>/videos/<id>, /videos/<id>, /share/v/<id>, /share/r/<id>, /video.php?v=
  const FB_VIDEO_PATH =
    /^\/(watch\/?($|\?)|watch\/live|reel\/|videos\/|video\.php|share\/[vr]\/|[^/]+\/videos\/)/;

  const onWatchPage = () => {
    if (isFacebook) {
      return FB_VIDEO_PATH.test(location.pathname + location.search);
    }
    return (
      /\/watch|\/shorts\//.test(location.pathname) ||
      location.hostname === "music.youtube.com"
    );
  };

  function mount() {
    if (!onWatchPage()) { unmount(); return; }
    if (document.getElementById(BTN_ID)) return;

    if (isDismissed()) return;

    // A wrapper holding the main button plus two small controls:
    //   pin   - keep the full "ClipForge" pill showing; unpinned it shrinks to
    //           a small icon and only expands on hover, so it covers little.
    //   close - hide it for this tab (the toolbar icon and right-click menu
    //           still work).
    const fab = document.createElement("div");
    fab.id = BTN_ID;

    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "cf-main";
    btn.title = "Open ClipForge";
    btn.innerHTML =
      `<svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true">
         <path d="M12 3v10m0 0 4-4m-4 4-4-4" fill="none" stroke="currentColor"
               stroke-width="2.1" stroke-linecap="round" stroke-linejoin="round"/>
         <path d="M4 17v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2" fill="none"
               stroke="currentColor" stroke-width="2.1" stroke-linecap="round"/>
       </svg><span>ClipForge</span>`;

    btn.addEventListener("click", () => {
      chrome.runtime.sendMessage({ type: "OPEN_POPUP", url: location.href }, () => {
        // If the popup can't be opened programmatically (older Chrome), tell
        // the user where to click instead of failing silently.
        if (chrome.runtime.lastError) flash(btn, "Click the ClipForge icon ↗");
      });
    });

    const pin = document.createElement("button");
    pin.type = "button";
    pin.className = "cf-pin";
    pin.innerHTML =
      `<svg viewBox="0 0 24 24" width="12" height="12" aria-hidden="true">
         <path d="M9 4h6l-1 6 3 3H7l3-3-1-6zM12 13v7" fill="none" stroke="currentColor"
               stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
       </svg>`;
    pin.addEventListener("click", (e) => {
      e.stopPropagation();
      setPinned(fab, !fab.classList.contains("cf-pinned"), true);
    });

    const close = document.createElement("button");
    close.type = "button";
    close.className = "cf-close";
    close.title = "Hide for this tab";
    close.setAttribute("aria-label", "Hide ClipForge button for this tab");
    close.innerHTML =
      `<svg viewBox="0 0 24 24" width="12" height="12" aria-hidden="true">
         <path d="M6 6l12 12M18 6 6 18" fill="none" stroke="currentColor"
               stroke-width="2.4" stroke-linecap="round"/>
       </svg>`;
    close.addEventListener("click", (e) => {
      e.stopPropagation();
      dismiss();
    });

    fab.append(btn, pin, close);
    setPinned(fab, false, false);
    document.body.appendChild(fab);

    // Restore the saved pin state (asynchronous, so the first paint is compact).
    try {
      chrome.storage.local.get("fabPinned", ({ fabPinned }) => {
        if (!chrome.runtime.lastError) setPinned(fab, !!fabPinned, false);
      });
    } catch { /* storage unavailable - stay compact */ }
  }

  function setPinned(fab, pinned, save) {
    fab.classList.toggle("cf-pinned", pinned);
    const pin = fab.querySelector(".cf-pin");
    pin.title = pinned ? "Unpin (shrink to icon)" : "Pin (keep ClipForge showing)";
    pin.setAttribute("aria-pressed", String(pinned));
    if (save) {
      try { chrome.storage.local.set({ fabPinned: pinned }); } catch { /* ignore */ }
    }
  }

  // "Closed" lasts for this tab only, so it survives in-site navigation but a
  // new tab starts fresh. sessionStorage can throw in locked-down contexts.
  const DISMISS_KEY = "clipforge-fab-hidden";
  function isDismissed() {
    try { return sessionStorage.getItem(DISMISS_KEY) === "1"; } catch { return false; }
  }
  function dismiss() {
    try { sessionStorage.setItem(DISMISS_KEY, "1"); } catch { /* ignore */ }
    unmount();
  }

  function flash(btn, text) {
    const span = btn.querySelector("span");
    const original = span.textContent;
    span.textContent = text;
    btn.classList.add("clipforge-flash");
    setTimeout(() => {
      span.textContent = original;
      btn.classList.remove("clipforge-flash");
    }, 2600);
  }

  function unmount() {
    document.getElementById(BTN_ID)?.remove();
  }

  mount();

  // Both sites are single-page apps. YouTube fires its own navigation event;
  // Facebook fires nothing we can hear from an isolated content script, so we
  // also watch for the URL changing. Debounced so we're not doing work on
  // every mutation.
  let t = null;
  const schedule = () => { clearTimeout(t); t = setTimeout(mount, 350); };
  window.addEventListener("yt-navigate-finish", schedule);
  new MutationObserver(schedule).observe(document.body, { childList: true, subtree: false });

  let lastHref = location.href;
  setInterval(() => {
    if (location.href !== lastHref) {
      lastHref = location.href;
      schedule();
    }
  }, 700);
})();
