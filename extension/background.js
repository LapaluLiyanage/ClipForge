/* ClipForge service worker: context menu, badge, completion notifications */

const HOST = "http://127.0.0.1:8787";
const WATCHED = new Set();

chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.removeAll(() => {
    chrome.contextMenus.create({
      id: "clipforge-page",
      title: "Open ClipForge for this page",
      contexts: ["page", "video"],
      // Only offered on pages that are actually a single video, so the menu
      // never hands the helper a Facebook feed or a YouTube home page.
      documentUrlPatterns: [
        "https://*.youtube.com/*",
        "https://youtu.be/*",
        "https://*.facebook.com/watch/*",
        "https://*.facebook.com/reel/*",
        "https://*.facebook.com/videos/*",
        "https://*.facebook.com/*/videos/*",
        "https://*.facebook.com/share/v/*",
        "https://*.facebook.com/share/r/*",
        "https://*.facebook.com/video.php*",
        "https://fb.watch/*",
      ],
    });
    chrome.contextMenus.create({
      id: "clipforge-link",
      title: "Open ClipForge for this link",
      contexts: ["link"],
    });
  });
});

chrome.contextMenus.onClicked.addListener(async (info) => {
  const url = info.linkUrl || info.pageUrl;
  if (url) await chrome.storage.local.set({ pendingUrl: url });
  openPopup();
});

function openPopup() {
  if (chrome.action.openPopup) {
    chrome.action.openPopup().catch(() => {});
  }
}

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg && msg.type === "OPEN_POPUP") {
    if (msg.url) chrome.storage.local.set({ pendingUrl: msg.url });
    openPopup();
    sendResponse({ ok: true });
    return true;
  }
  if (msg && msg.type === "JOB_STARTED") {
    WATCHED.add(msg.id);
    chrome.alarms.create("clipforge-poll", { periodInMinutes: 0.1 });
    sendResponse({ ok: true });
    return true;
  }
  return false;
});

chrome.alarms?.onAlarm.addListener(async (alarm) => {
  if (alarm.name !== "clipforge-poll") return;
  try {
    const res = await fetch(`${HOST}/jobs`);
    if (!res.ok) throw new Error("bad status");
    const { jobs } = await res.json();
    const busy = jobs.filter((j) => j.status === "running" || j.status === "queued");

    chrome.action.setBadgeText({ text: busy.length ? String(busy.length) : "" });
    chrome.action.setBadgeBackgroundColor({ color: "#6d5cf7" });

    for (const job of jobs) {
      if (!WATCHED.has(job.id)) continue;
      if (job.status === "done" || job.status === "error") {
        WATCHED.delete(job.id);
        chrome.notifications.create({
          type: "basic",
          iconUrl: "icons/icon128.png",
          title: job.status === "done" ? "ClipForge — saved" : "ClipForge — failed",
          message: job.status === "done"
            ? (job.filename || job.title || "Download complete")
            : (job.error || "Download failed"),
        });
      }
    }
    if (!busy.length && !WATCHED.size) chrome.alarms.clear("clipforge-poll");
  } catch {
    chrome.action.setBadgeText({ text: "" });
    chrome.alarms.clear("clipforge-poll");
  }
});
