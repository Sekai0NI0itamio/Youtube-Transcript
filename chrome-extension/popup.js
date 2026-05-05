// popup.js — YT Transcript Chrome Extension

// ── Constants ──────────────────────────────────────────────────────────────

const DEFAULT_CATEGORIES = ["Life Lessons", "Life Tips", "Learning", "Entertainment", "Ideas", "Other"];
const SUPADATA_ENDPOINT  = "https://api.supadata.ai/v1/youtube/transcript";

// Auto-classify keywords → category
const CLASSIFY_MAP = {
  "Life Lessons":   ["lesson", "mistake", "regret", "wisdom", "advice", "life", "success", "failure", "mindset", "growth", "discipline", "habit"],
  "Life Tips":      ["tip", "hack", "trick", "productivity", "routine", "morning", "health", "fitness", "diet", "sleep", "money", "finance", "budget"],
  "Learning":       ["tutorial", "how to", "learn", "course", "explain", "guide", "programming", "code", "science", "math", "history", "language", "study"],
  "Entertainment":  ["funny", "comedy", "prank", "reaction", "vlog", "gaming", "music", "movie", "review", "trailer", "challenge", "shorts"],
  "Ideas":          ["idea", "startup", "business", "innovation", "creative", "design", "build", "project", "invention", "future", "technology"],
};

// ── State ──────────────────────────────────────────────────────────────────

let videos      = [];   // array of video objects (persisted in chrome.storage)
let activeFilter = "all";
let pendingCatVideoId = null;   // video waiting for category assignment
let customCategories  = [];     // user-added categories beyond defaults

// ── DOM refs ───────────────────────────────────────────────────────────────

const $ = id => document.getElementById(id);

const elList          = $("video-list");
const elInputUrl      = $("input-url");
const elBtnAdd        = $("btn-add-url");
const elBtnTranscribe = $("btn-transcribe-current");
const elBtnCopyAll    = $("btn-copy-all");
const elBtnSettings   = $("btn-settings-toggle");
const elSettingsPanel = $("settings-panel");
const elSetupBanner   = $("setup-banner");
const elCatFilterBar  = $("category-filter-bar");
const elCatCopyRow    = $("cat-copy-row");
const elCatCopyLabel  = $("cat-copy-label");
const elBtnCopyCat    = $("btn-copy-category");
const elCatModal      = $("cat-modal");
const elCatGrid       = $("cat-grid");
const elCustomCatRow  = $("custom-cat-row");
const elInputCustom   = $("input-custom-cat");
const elBtnAddCustom  = $("btn-add-custom-cat");
const elBtnCatCancel  = $("btn-cat-cancel");
const elToast         = $("toast");
const elInputApiKey   = $("input-api-key");
const elInputSavePath = $("input-save-path");
const elBtnSaveSet    = $("btn-save-settings");
const elBtnCancelSet  = $("btn-cancel-settings");
const elBtnShowSet    = $("btn-show-settings");

// ── Init ───────────────────────────────────────────────────────────────────

async function init() {
  const data = await chrome.storage.local.get(["videos", "apiKey", "savePath", "customCategories"]);
  videos           = data.videos           || [];
  customCategories = data.customCategories || [];

  if (!data.apiKey) {
    elSetupBanner.classList.remove("hidden");
  }

  if (data.apiKey)   elInputApiKey.value   = data.apiKey;
  if (data.savePath) elInputSavePath.value = data.savePath;

  buildCategoryBar();
  renderList();
  detectCurrentTab();
}

// ── Settings ───────────────────────────────────────────────────────────────

elBtnSettings.addEventListener("click", () => {
  elSettingsPanel.classList.toggle("hidden");
});

elBtnShowSet.addEventListener("click", () => {
  elSetupBanner.classList.add("hidden");
  elSettingsPanel.classList.remove("hidden");
});

elBtnSaveSet.addEventListener("click", async () => {
  const key  = elInputApiKey.value.trim();
  const path = elInputSavePath.value.trim();
  if (!key) { toast("API key cannot be empty", true); return; }
  await chrome.storage.local.set({ apiKey: key, savePath: path });
  elSettingsPanel.classList.add("hidden");
  elSetupBanner.classList.add("hidden");
  toast("Settings saved ✓");
});

elBtnCancelSet.addEventListener("click", () => {
  elSettingsPanel.classList.add("hidden");
});

// ── Detect current tab ─────────────────────────────────────────────────────

async function detectCurrentTab() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab?.url) return;
  const url = tab.url;
  if (isYouTubeUrl(url)) {
    elInputUrl.value = url;
    elInputUrl.placeholder = "Current page detected ↑";
  }
}

// ── URL helpers ────────────────────────────────────────────────────────────

function isYouTubeUrl(url) {
  return /youtube\.com|youtu\.be/.test(url);
}

function extractVideoId(url) {
  const patterns = [
    /[?&]v=([A-Za-z0-9_-]{11})/,
    /youtu\.be\/([A-Za-z0-9_-]{11})/,
    /\/embed\/([A-Za-z0-9_-]{11})/,
    /\/shorts\/([A-Za-z0-9_-]{11})/,
  ];
  for (const p of patterns) {
    const m = url.match(p);
    if (m) return m[1];
  }
  return null;
}

function extractPlaylistId(url) {
  const m = url.match(/[?&]list=([A-Za-z0-9_-]+)/);
  return m ? m[1] : null;
}

function isPlaylistUrl(url) {
  return /[?&]list=/.test(url);
}

// ── Parse URL → list of video IDs ─────────────────────────────────────────

async function resolveUrls(rawUrl) {
  // Returns array of { url, videoId, title }
  const url = rawUrl.trim();

  if (!isYouTubeUrl(url)) {
    toast("Not a YouTube URL", true);
    return [];
  }

  // Playlist
  if (isPlaylistUrl(url)) {
    toast("Fetching playlist…");
    const items = await fetchPlaylistItems(url);
    return items;
  }

  // Single video
  const videoId = extractVideoId(url);
  if (!videoId) {
    toast("Could not extract video ID", true);
    return [];
  }

  return [{ url, videoId, title: null }];
}

// Fetch playlist video IDs via YouTube page scraping (content script)
async function fetchPlaylistItems(playlistUrl) {
  return new Promise(resolve => {
    chrome.runtime.sendMessage(
      { type: "FETCH_PLAYLIST", url: playlistUrl },
      response => {
        if (response?.items) resolve(response.items);
        else resolve([]);
      }
    );
  });
}

// ── Auto-classify ──────────────────────────────────────────────────────────

function autoClassify(title = "", description = "") {
  const text = (title + " " + description).toLowerCase();
  for (const [cat, keywords] of Object.entries(CLASSIFY_MAP)) {
    if (keywords.some(kw => text.includes(kw))) return cat;
  }
  return null; // unknown — user must pick
}

// ── Add URL button ─────────────────────────────────────────────────────────

elBtnAdd.addEventListener("click", () => addFromInput());
elInputUrl.addEventListener("keydown", e => { if (e.key === "Enter") addFromInput(); });

async function addFromInput() {
  const raw = elInputUrl.value.trim();
  if (!raw) return;
  elInputUrl.value = "";
  await queueUrls(raw, false);
}

// ── Transcribe current page button ────────────────────────────────────────

elBtnTranscribe.addEventListener("click", async () => {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab?.url || !isYouTubeUrl(tab.url)) {
    toast("Open a YouTube video first", true);
    return;
  }

  // Get page title from tab
  const pageTitle = tab.title?.replace(" - YouTube", "").trim() || null;
  await queueUrls(tab.url, true, pageTitle);
});

// ── Queue URLs for transcription ───────────────────────────────────────────

async function queueUrls(rawUrl, markNew = false, knownTitle = null) {
  const { apiKey } = await chrome.storage.local.get("apiKey");
  if (!apiKey) {
    elSetupBanner.classList.remove("hidden");
    toast("Set your API key first", true);
    return;
  }

  const items = await resolveUrls(rawUrl);
  if (!items.length) return;

  const newIds = [];

  for (const item of items) {
    // Skip duplicates
    if (videos.find(v => v.videoId === item.videoId)) {
      toast(`Already in list: ${item.videoId}`);
      continue;
    }

    const title    = item.title || knownTitle || item.videoId;
    const category = autoClassify(title);

    const video = {
      videoId:   item.videoId,
      url:       item.url,
      title,
      category,  // null = needs user input
      status:    "pending",
      chars:     0,
      text:      null,
      isNew:     markNew,
      addedAt:   Date.now(),
    };

    videos.unshift(video);
    newIds.push(item.videoId);
  }

  await persist();
  renderList();

  // For each new video: if category unknown, ask user; then transcribe
  for (const vid of newIds) {
    const video = videos.find(v => v.videoId === vid);
    if (!video) continue;

    if (!video.category) {
      // Ask user to pick category before transcribing
      const chosen = await promptCategory(video.title || video.videoId);
      if (!chosen) {
        // User cancelled — mark as pending with no category
        continue;
      }
      video.category = chosen;
      await persist();
      renderList();
    }

    transcribeVideo(video.videoId);
  }
}

// ── Transcribe a single video ──────────────────────────────────────────────

async function transcribeVideo(videoId) {
  const video = videos.find(v => v.videoId === videoId);
  if (!video) return;

  const { apiKey, savePath } = await chrome.storage.local.get(["apiKey", "savePath"]);
  if (!apiKey) return;

  video.status = "loading";
  renderList();

  try {
    const text = await callSupadata(video.url, apiKey);
    if (!text) throw new Error("Empty response from Supadata");

    video.text   = text;
    video.chars  = text.length;
    video.status = "done";

    // If title was just the videoId, try to extract a better one from transcript start
    if (video.title === video.videoId && text.length > 20) {
      // Keep as-is; title will be fetched by content script if available
    }

    // Save to disk via background service worker
    if (savePath) {
      chrome.runtime.sendMessage({
        type:     "SAVE_FILE",
        filename: `${sanitizeFilename(video.title || video.videoId)}.txt`,
        savePath,
        content:  text,
      });
    }

  } catch (e) {
    video.status = "error";
    video.error  = e.message;
    toast(`Error: ${e.message}`, true);
  }

  await persist();
  renderList();
}

// ── Supadata API call ──────────────────────────────────────────────────────

async function callSupadata(videoUrl, apiKey) {
  const params = new URLSearchParams({ url: videoUrl, text: "false" });
  const resp = await fetch(`${SUPADATA_ENDPOINT}?${params}`, {
    headers: { "x-api-key": apiKey },
  });

  if (resp.status === 429) {
    await sleep(15000);
    return callSupadata(videoUrl, apiKey);
  }

  if (!resp.ok) {
    const body = await resp.text();
    throw new Error(`Supadata ${resp.status}: ${body.slice(0, 120)}`);
  }

  const data = await resp.json();
  const content = data.content;

  if (Array.isArray(content)) {
    return content.map(c => c.text?.trim()).filter(Boolean).join(" ");
  }
  if (typeof content === "string" && content.trim()) {
    return content.trim();
  }

  // Fallback field names
  for (const key of ["transcript", "text"]) {
    const val = data[key];
    if (typeof val === "string" && val.trim()) return val.trim();
  }

  throw new Error("No transcript content in response");
}

// ── Category modal ─────────────────────────────────────────────────────────

function promptCategory(videoTitle) {
  return new Promise(resolve => {
    $("cat-modal-subtitle").textContent = videoTitle || "";
    elCatModal.classList.remove("hidden");
    elCustomCatRow.classList.add("hidden");
    elInputCustom.value = "";

    // Rebuild grid with all categories
    buildCatGrid(resolve);

    elBtnCatCancel.onclick = () => {
      elCatModal.classList.add("hidden");
      resolve(null);
    };
  });
}

function buildCatGrid(resolve) {
  const allCats = [...DEFAULT_CATEGORIES, ...customCategories.filter(c => !DEFAULT_CATEGORIES.includes(c))];
  elCatGrid.innerHTML = "";

  for (const cat of allCats) {
    const btn = document.createElement("button");
    btn.className = "cat-btn";
    btn.dataset.cat = cat;
    btn.textContent = cat === "Other" ? "Other ＋" : cat;
    btn.addEventListener("click", () => {
      if (cat === "Other") {
        elCustomCatRow.classList.remove("hidden");
        elInputCustom.focus();
        return;
      }
      elCatModal.classList.add("hidden");
      resolve(cat);
    });
    elCatGrid.appendChild(btn);
  }

  elBtnAddCustom.onclick = async () => {
    const name = elInputCustom.value.trim();
    if (!name) return;
    if (!customCategories.includes(name)) {
      customCategories.push(name);
      await chrome.storage.local.set({ customCategories });
      buildCategoryBar();
    }
    elCatModal.classList.add("hidden");
    resolve(name);
  };

  elInputCustom.onkeydown = e => {
    if (e.key === "Enter") elBtnAddCustom.click();
  };
}

// ── Category filter bar ────────────────────────────────────────────────────

function buildCategoryBar() {
  const allCats = [...DEFAULT_CATEGORIES, ...customCategories.filter(c => !DEFAULT_CATEGORIES.includes(c))];
  elCatFilterBar.innerHTML = "";

  const allChip = makeChip("All", "all");
  elCatFilterBar.appendChild(allChip);

  for (const cat of allCats) {
    elCatFilterBar.appendChild(makeChip(cat, cat));
  }
}

function makeChip(label, value) {
  const btn = document.createElement("button");
  btn.className = "cat-chip" + (activeFilter === value ? " active" : "");
  btn.dataset.cat = value;
  btn.textContent = label;
  btn.addEventListener("click", () => {
    activeFilter = value;
    document.querySelectorAll(".cat-chip").forEach(c => c.classList.remove("active"));
    btn.classList.add("active");
    updateCatCopyRow();
    renderList();
  });
  return btn;
}

function updateCatCopyRow() {
  if (activeFilter === "all") {
    elCatCopyRow.classList.add("hidden");
  } else {
    elCatCopyRow.classList.remove("hidden");
    elCatCopyLabel.innerHTML = `Copy all in <strong>${activeFilter}</strong>`;
  }
}

// ── Render video list ──────────────────────────────────────────────────────

function renderList() {
  const filtered = activeFilter === "all"
    ? videos
    : videos.filter(v => v.category === activeFilter);

  if (!filtered.length) {
    elList.innerHTML = `<div class="empty-state">${
      activeFilter === "all"
        ? "No transcripts yet.<br>Open a YouTube video and click Transcribe."
        : `No videos in <strong>${activeFilter}</strong> yet.`
    }</div>`;
    return;
  }

  elList.innerHTML = "";
  for (const video of filtered) {
    elList.appendChild(makeCard(video));
  }
}

function makeCard(video) {
  const card = document.createElement("div");
  card.className = "video-card" + (video.isNew ? " new" : "");
  card.dataset.id = video.videoId;

  const statusHtml = {
    pending: `<span class="card-status pending">Pending</span>`,
    loading: `<span class="card-status loading"><span class="spinner"></span>Transcribing…</span>`,
    done:    `<span class="card-status done">Done</span>`,
    error:   `<span class="card-status error">Error</span>`,
  }[video.status] || "";

  const catLabel = video.category
    ? `<span class="cat-badge" data-id="${video.videoId}" title="Change category">${video.category}</span>`
    : `<span class="cat-badge" data-id="${video.videoId}" style="border-color:#ff4444;color:#ff9999">⚠ No category</span>`;

  const charsLabel = video.chars
    ? `<span class="card-chars">${(video.chars / 1000).toFixed(1)}k chars</span>`
    : "";

  card.innerHTML = `
    <div class="card-top">
      <div class="card-title">${escHtml(video.title || video.videoId)}</div>
      ${statusHtml}
    </div>
    <div class="card-meta">
      ${catLabel}
      ${charsLabel}
    </div>
    <div class="card-actions">
      <button class="card-action-btn" data-action="copy"   data-id="${video.videoId}">Copy Text</button>
      <button class="card-action-btn" data-action="reveal" data-id="${video.videoId}">Show in Finder</button>
      <button class="card-action-btn" data-action="open"   data-id="${video.videoId}">Open Video</button>
      <button class="card-action-btn danger" data-action="delete" data-id="${video.videoId}">Remove</button>
    </div>
  `;

  // Toggle expand on card click (not on buttons)
  card.addEventListener("click", e => {
    if (e.target.closest("button")) return;
    card.classList.toggle("expanded");
  });

  // Category badge click → re-assign
  card.querySelector(".cat-badge").addEventListener("click", async e => {
    e.stopPropagation();
    const chosen = await promptCategory(video.title || video.videoId);
    if (chosen) {
      video.category = chosen;
      await persist();
      renderList();
    }
  });

  // Action buttons
  card.querySelectorAll("[data-action]").forEach(btn => {
    btn.addEventListener("click", e => {
      e.stopPropagation();
      handleCardAction(btn.dataset.action, btn.dataset.id);
    });
  });

  return card;
}

// ── Card actions ───────────────────────────────────────────────────────────

async function handleCardAction(action, videoId) {
  const video = videos.find(v => v.videoId === videoId);
  if (!video) return;

  switch (action) {
    case "copy":
      if (!video.text) { toast("No transcript yet", true); return; }
      await navigator.clipboard.writeText(video.text);
      toast("Copied to clipboard ✓");
      break;

    case "reveal":
      chrome.runtime.sendMessage({ type: "REVEAL_FILE", videoId, title: video.title, savePath: (await chrome.storage.local.get("savePath")).savePath });
      break;

    case "open":
      chrome.tabs.create({ url: video.url });
      break;

    case "delete":
      videos = videos.filter(v => v.videoId !== videoId);
      await persist();
      renderList();
      break;
  }
}

// ── Copy all ───────────────────────────────────────────────────────────────

elBtnCopyAll.addEventListener("click", () => copyFiltered("all"));
elBtnCopyCat.addEventListener("click", () => copyFiltered(activeFilter));

async function copyFiltered(filter) {
  const subset = filter === "all" ? videos : videos.filter(v => v.category === filter);
  const done   = subset.filter(v => v.status === "done" && v.text);

  if (!done.length) { toast("No transcripts to copy", true); return; }

  const combined = done.map(v =>
    `=== ${v.title || v.videoId} [${v.category || "Uncategorised"}] ===\n${v.text}`
  ).join("\n\n");

  await navigator.clipboard.writeText(combined);
  toast(`Copied ${done.length} transcript(s) ✓`);
}

// ── Persist ────────────────────────────────────────────────────────────────

async function persist() {
  // Don't store full text in storage if it's large — keep it but be aware
  await chrome.storage.local.set({ videos });
}

// ── Toast ──────────────────────────────────────────────────────────────────

let toastTimer;
function toast(msg, isError = false) {
  elToast.textContent = msg;
  elToast.className   = "toast" + (isError ? " error" : "");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { elToast.className = "toast hidden"; }, 2800);
}

// ── Utilities ──────────────────────────────────────────────────────────────

function escHtml(str) {
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function sanitizeFilename(str) {
  return str.replace(/[^a-zA-Z0-9_\- ]/g, "_").slice(0, 80).trim();
}

function sleep(ms) {
  return new Promise(r => setTimeout(r, ms));
}

// ── Boot ───────────────────────────────────────────────────────────────────

init();
