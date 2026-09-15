/* ClipForge content script — a floating button on YouTube watch pages.
   Deliberately not spliced into YouTube's own action row: that markup changes
   often, and a self-contained pill survives their redesigns. */

(function () {
  const BTN_ID = "clipforge-fab";

  const onWatchPage = () =>
    /\/watch|\/shorts\//.test(location.pathname) ||
    location.hostname === "music.youtube.com";

  function mount() {
    if (!onWatchPage()) { unmount(); return; }
    if (document.getElementById(BTN_ID)) return;

    const btn = document.createElement("button");
    btn.id = BTN_ID;
    btn.type = "button";
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

    document.body.appendChild(btn);
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

  // YouTube is a single-page app: re-check on its own navigation event and on
  // DOM churn, debounced so we're not doing work on every mutation.
  let t = null;
  const schedule = () => { clearTimeout(t); t = setTimeout(mount, 350); };
  window.addEventListener("yt-navigate-finish", schedule);
  new MutationObserver(schedule).observe(document.body, { childList: true, subtree: false });
})();
