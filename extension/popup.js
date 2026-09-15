/* ClipForge popup — talks to the local helper on 127.0.0.1:8787 */

const HOST = "http://127.0.0.1:8787";
const $ = (id) => document.getElementById(id);

let OPTIONS = null;
let CURRENT = null;      // last probe result
let MODE = "video";
let pollTimer = null;
let probeSeq = 0;

/* ------------------------------------------------------------------ */
/* helpers                                                             */
/* ------------------------------------------------------------------ */
async function api(path, opts = {}) {
  const res = await fetch(HOST + path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  const text = await res.text();
  let body = null;
  try { body = text ? JSON.parse(text) : null; } catch { body = { detail: text }; }
  if (!res.ok) throw new Error((body && body.detail) || `HTTP ${res.status}`);
  return body;
}

function show(el, on) { el.classList.toggle("hidden", !on); }

function fillSelect(sel, items, selected) {
  sel.innerHTML = "";
  for (const it of items) {
    const o = document.createElement("option");
    o.value = it.id;
    o.textContent = it.label;
    sel.appendChild(o);
  }
  if (selected != null && items.some((i) => i.id === selected)) sel.value = selected;
}

function humanBytes(n) {
  if (!n) return "";
  const u = ["B", "KB", "MB", "GB"];
  let v = n, i = 0;
  while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
  return `${v.toFixed(i === 0 ? 0 : 1)} ${u[i]}`;
}

function humanTime(s) {
  if (s == null) return "";
  const m = Math.floor(s / 60), sec = Math.round(s % 60);
  return m ? `${m}m ${sec}s` : `${sec}s`;
}

const isVideoUrl = (u) => /^https?:\/\/\S+\.\S+/i.test((u || "").trim());

/* ------------------------------------------------------------------ */
/* connection + preflight                                              */
/* ------------------------------------------------------------------ */
async function connect() {
  show($("offline"), false);
  show($("preflight"), false);
  show($("main"), false);
  try {
    const health = await api("/health");
    $("dot").className = "dot on";
    $("dot").title = `ClipForge helper ${health.version} — ready`;

    if (!health.ok) {
      const list = $("preflight-list");
      list.innerHTML = "";
      for (const c of health.checks) {
        const li = document.createElement("li");
        li.innerHTML = `<span class="mark ${c.ok ? "ok" : "bad"}">${c.ok ? "✓" : "✕"}</span>
          <span><strong>${c.label}</strong><br><span class="detail">${c.detail}</span></span>`;
        list.appendChild(li);
      }
      show($("preflight"), true);
      return;
    }

    OPTIONS = await api("/options");
    buildControls();
    show($("main"), true);
    await autoFillFromTab();
    refreshJobs();
    startPolling();
  } catch {
    $("dot").className = "dot off";
    $("dot").title = "Helper offline";
    show($("offline"), true);
  }
}

/* ------------------------------------------------------------------ */
/* controls                                                            */
/* ------------------------------------------------------------------ */
async function buildControls() {
  const saved = await chrome.storage.local.get([
    "mode", "quality", "container", "bitrate",
    "audioFormat", "audioBitrate", "wavDepth", "wavRate", "wavChannels",
  ]);

  fillSelect($("quality"), OPTIONS.video_qualities, saved.quality || "1080");
  fillSelect($("container"), OPTIONS.video_containers, saved.container || "mp4");
  fillSelect($("bitrate"), OPTIONS.mp3_bitrates, saved.bitrate || "320");
  fillSelect($("wav-depth"), OPTIONS.wav_depths, saved.wavDepth || "16");
  fillSelect($("wav-rate"), OPTIONS.wav_rates, saved.wavRate || "44100");
  fillSelect($("wav-channels"), OPTIONS.wav_channels, saved.wavChannels || "source");
  fillSelect($("audio-format"), OPTIONS.other_audio_formats, saved.audioFormat || "m4a");
  fillSelect($("audio-bitrate"), OPTIONS.mp3_bitrates, saved.audioBitrate || "192");

  setMode(saved.mode || "video");

  for (const tab of document.querySelectorAll(".tab")) {
    tab.addEventListener("click", () => setMode(tab.dataset.mode));
  }
  for (const sel of document.querySelectorAll("#controls select")) {
    sel.addEventListener("change", persist);
  }
}

function setMode(mode) {
  MODE = mode;
  for (const t of document.querySelectorAll(".tab")) {
    t.classList.toggle("active", t.dataset.mode === mode);
  }
  for (const p of document.querySelectorAll(".pane")) {
    p.classList.toggle("hidden", p.dataset.pane !== mode);
  }
  $("btn-go").textContent =
    mode === "video" ? "Download video"
    : mode === "mp3" ? "Extract MP3"
    : mode === "wav" ? "Extract WAV"
    : "Extract audio";
  persist();
}

function persist() {
  chrome.storage.local.set({
    mode: MODE,
    quality: $("quality").value,
    container: $("container").value,
    bitrate: $("bitrate").value,
    wavDepth: $("wav-depth").value,
    wavRate: $("wav-rate").value,
    wavChannels: $("wav-channels").value,
    audioFormat: $("audio-format").value,
    audioBitrate: $("audio-bitrate").value,
  });
}

/* ------------------------------------------------------------------ */
/* probing                                                             */
/* ------------------------------------------------------------------ */
async function autoFillFromTab() {
  // A context-menu click stashes the URL for us; it wins over the active tab.
  try {
    const { pendingUrl } = await chrome.storage.local.get("pendingUrl");
    if (pendingUrl) {
      await chrome.storage.local.remove("pendingUrl");
      $("url").value = pendingUrl;
      await doProbe(pendingUrl);
      return;
    }
  } catch { /* storage unavailable - fall through */ }

  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    const url = tab && tab.url;
    if (url && /youtube\.com\/(watch|shorts)|youtu\.be\//.test(url)) {
      $("url").value = url;
      await doProbe(url);
      return;
    }
  } catch { /* no tab access — fall through */ }
  $("url").focus();
}

async function doProbe(url) {
  const seq = ++probeSeq;
  show($("probe-error"), false);
  show($("preview"), false);
  show($("controls"), false);
  show($("probing"), true);
  try {
    const info = await api("/probe", { method: "POST", body: JSON.stringify({ url }) });
    if (seq !== probeSeq) return;               // a newer probe superseded this one
    CURRENT = info;

    $("thumb").src = info.thumbnail || "";
    $("title").textContent = info.title || "Untitled";
    const bits = [];
    if (info.uploader) bits.push(info.uploader);
    if (info.duration_string) bits.push(info.duration_string);
    if (info.available_qualities.length) bits.push(`up to ${info.available_qualities[0].label}`);
    $("sub").textContent = bits.join(" · ");

    // Restrict the quality list to what this video actually has.
    const heights = info.available_qualities.map((q) => q.height);
    const allowed = OPTIONS.video_qualities.filter(
      (q) => q.height === null || heights.includes(q.height)
    ).map((q) => {
      const match = info.available_qualities.find((a) => a.height === q.height);
      return match && match.approx_size
        ? { id: q.id, label: `${q.label} · ~${match.approx_size}` }
        : q;
    });
    const want = $("quality").value;
    fillSelect($("quality"), allowed, allowed.some((a) => a.id === want) ? want : allowed[0].id);

    if (info.best_audio_bitrate) {
      $("mp3-hint").textContent =
        `Source audio is about ${info.best_audio_bitrate} kbps. Encoding above that adds size, not quality.`;
    }

    show($("preview"), true);
    show($("controls"), true);
  } catch (err) {
    if (seq !== probeSeq) return;
    $("probe-error").textContent = err.message;
    show($("probe-error"), true);
  } finally {
    if (seq === probeSeq) show($("probing"), false);
  }
}

/* ------------------------------------------------------------------ */
/* downloading                                                         */
/* ------------------------------------------------------------------ */
async function startDownload() {
  const url = $("url").value.trim();
  if (!isVideoUrl(url)) {
    $("probe-error").textContent = "Enter a valid video URL first.";
    show($("probe-error"), true);
    return;
  }
  const payload = {
    url,
    mode: MODE,
    quality: $("quality").value,
    container: $("container").value,
    bitrate: MODE === "audio" ? $("audio-bitrate").value : $("bitrate").value,
    audio_format: $("audio-format").value,
    wav_depth: $("wav-depth").value,
    wav_rate: $("wav-rate").value,
    wav_channels: $("wav-channels").value,
    title: CURRENT && CURRENT.title,
    thumbnail: CURRENT && CURRENT.thumbnail,
  };
  $("btn-go").disabled = true;
  try {
    const job = await api("/download", { method: "POST", body: JSON.stringify(payload) });
    show($("probe-error"), false);
    // Hand the job to the service worker so progress keeps being tracked and a
    // notification fires even after this popup closes.
    chrome.runtime.sendMessage({ type: "JOB_STARTED", id: job.id }, () => void chrome.runtime.lastError);
    refreshJobs();
    startPolling();
  } catch (err) {
    $("probe-error").textContent = err.message;
    show($("probe-error"), true);
  } finally {
    $("btn-go").disabled = false;
  }
}

function stageLabel(job) {
  switch (job.stage) {
    case "resolving":   return "Resolving streams…";
    case "downloading": {
      const parts = [`${job.progress.toFixed(0)}%`];
      if (job.total) parts.push(`${humanBytes(job.downloaded)} / ${humanBytes(job.total)}`);
      if (job.speed) parts.push(`${humanBytes(job.speed)}/s`);
      if (job.eta) parts.push(`${humanTime(job.eta)} left`);
      return parts.join(" · ");
    }
    case "merging":    return "Merging video + audio…";
    case "converting": return "Encoding audio…";
    case "artwork":    return "Writing cover art…";
    case "tagging":    return "Writing metadata…";
    case "done":       return job.filename || "Saved";
    case "cancelled":  return "Cancelled";
    default:           return job.error || "Queued";
  }
}

function renderJobs(jobs) {
  const box = $("jobs");
  box.innerHTML = "";
  for (const job of jobs.slice(0, 6)) {
    const el = document.createElement("div");
    el.className = "job";

    const active = job.status === "running" || job.status === "queued";
    const fillClass = job.status === "error" ? "err" : job.status === "done" ? "ok" : "";
    const width = job.status === "done" ? 100 : job.progress;

    el.innerHTML = `
      <div class="job-top">
        <div class="job-name" title="${escapeHtml(job.title || job.url)}">${escapeHtml(job.title || job.url)}</div>
        <span class="job-tag ${job.mode}">${job.mode}</span>
      </div>
      <div class="track"><div class="fill ${fillClass}" style="width:${width}%"></div></div>
      <div class="job-bottom">
        <div class="job-stat ${job.status === "error" ? "err" : ""}">${escapeHtml(job.status === "error" ? job.error : stageLabel(job))}</div>
        <div class="job-actions"></div>
      </div>`;

    const actions = el.querySelector(".job-actions");
    if (active) {
      const b = document.createElement("button");
      b.className = "mini"; b.textContent = "Cancel";
      b.onclick = () => api(`/jobs/${job.id}/cancel`, { method: "POST" }).then(refreshJobs).catch(() => {});
      actions.appendChild(b);
    } else if (job.status === "done" && job.filepath) {
      const b = document.createElement("button");
      b.className = "mini"; b.textContent = "Show in folder";
      b.onclick = () => api(`/reveal/${job.id}`, { method: "POST" }).catch(() => {});
      actions.appendChild(b);
    }
    box.appendChild(el);
  }
}

function escapeHtml(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

async function refreshJobs() {
  try {
    const { jobs } = await api("/jobs");
    renderJobs(jobs);
    const busy = jobs.some((j) => j.status === "running" || j.status === "queued");
    if (!busy) stopPolling();
    return busy;
  } catch { stopPolling(); }
}

function startPolling() {
  if (pollTimer) return;
  pollTimer = setInterval(refreshJobs, 700);
}
function stopPolling() {
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
}

/* ------------------------------------------------------------------ */
/* settings                                                            */
/* ------------------------------------------------------------------ */
async function openSettings() {
  try {
    const s = await api("/settings");
    $("set-output").value = s.output_dir;
    $("set-template").value = s.filename_template;
    $("set-cookies").value = s.cookies_from_browser || "";
    $("set-thumb").checked = s.embed_thumbnail;
    $("set-meta").checked = s.embed_metadata;
    $("set-chapters").checked = s.embed_chapters;
    $("set-subs").checked = s.write_subtitles;
    show($("settings-msg"), false);
    show($("main"), false);
    show($("settings"), true);
  } catch (err) {
    alert("Can't reach the helper: " + err.message);
  }
}

async function saveSettings() {
  try {
    await api("/settings", {
      method: "POST",
      body: JSON.stringify({
        output_dir: $("set-output").value.trim(),
        filename_template: $("set-template").value.trim(),
        cookies_from_browser: $("set-cookies").value,
        embed_thumbnail: $("set-thumb").checked,
        embed_metadata: $("set-meta").checked,
        embed_chapters: $("set-chapters").checked,
        write_subtitles: $("set-subs").checked,
      }),
    });
    const msg = $("settings-msg");
    msg.textContent = "Saved.";
    msg.className = "alert ok";
    show(msg, true);
  } catch (err) {
    const msg = $("settings-msg");
    msg.textContent = err.message;
    msg.className = "alert";
    show(msg, true);
  }
}

/* ------------------------------------------------------------------ */
/* wiring                                                              */
/* ------------------------------------------------------------------ */
$("btn-retry").onclick = connect;
$("btn-recheck").onclick = connect;
$("btn-load").onclick = () => {
  const u = $("url").value.trim();
  if (isVideoUrl(u)) doProbe(u);
};
$("url").addEventListener("keydown", (e) => {
  if (e.key === "Enter") $("btn-load").click();
});
$("url").addEventListener("paste", () => {
  setTimeout(() => {
    const u = $("url").value.trim();
    if (isVideoUrl(u)) doProbe(u);
  }, 30);
});
$("btn-go").onclick = startDownload;
if (new URLSearchParams(location.search).get("pinned") === "1") {
  $("btn-pin").remove();
} else {
  $("btn-pin").onclick = () => {
    chrome.windows.create({
      url: chrome.runtime.getURL("popup.html?pinned=1"),
      type: "popup",
      width: 420,
      height: 640,
    });
    window.close();
  };
}
$("btn-settings").onclick = openSettings;
$("btn-close-settings").onclick = () => {
  show($("settings"), false);
  show($("main"), true);
};
$("btn-save").onclick = saveSettings;
$("btn-update").onclick = async () => {
  const b = $("btn-update");
  b.disabled = true; b.textContent = "Updating…";
  try {
    const r = await api("/update-ytdlp", { method: "POST" });
    const msg = $("settings-msg");
    msg.textContent = r.ok ? "yt-dlp updated. Restart the helper." : "Update failed.";
    msg.className = r.ok ? "alert ok" : "alert";
    show(msg, true);
  } catch (err) {
    const msg = $("settings-msg");
    msg.textContent = err.message; msg.className = "alert"; show(msg, true);
  } finally {
    b.disabled = false; b.textContent = "Update yt-dlp";
  }
};

window.addEventListener("unload", stopPolling);
connect();
