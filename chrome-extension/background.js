// background.js — Service Worker for YT Transcript extension

// ── Read .env from the extension folder ───────────────────────────────────

async function loadEnvFile() {
  try {
    const url  = chrome.runtime.getURL(".env");
    const resp = await fetch(url);
    if (!resp.ok) return {};

    const text   = await resp.text();
    const values = {};

    for (const line of text.split("\n")) {
      const trimmed = line.trim();
      if (!trimmed || trimmed.startsWith("#")) continue;
      const eq = trimmed.indexOf("=");
      if (eq === -1) continue;
      const key = trimmed.slice(0, eq).trim();
      const val = trimmed.slice(eq + 1).trim().replace(/^["']|["']$/g, "");
      if (key && val) values[key] = val;
    }

    return values;
  } catch (e) {
    console.warn("[YT Transcript] Could not read .env:", e.message);
    return {};
  }
}

async function autoLoadApiKey() {
  const env = await loadEnvFile();

  if (!env.SUPADATA_API_KEY) {
    console.log("[YT Transcript] SUPADATA_API_KEY not found in .env");
    return;
  }

  // Only set savePath if user hasn't manually overridden it
  const existing = await chrome.storage.local.get(["savePath"]);
  const update   = { apiKey: env.SUPADATA_API_KEY };
  if (!existing.savePath && env.SAVE_PATH) {
    update.savePath = env.SAVE_PATH;
  }

  await chrome.storage.local.set(update);
  console.log("[YT Transcript] API key loaded from .env ✓");
}

// Run on every service worker startup (extension load / browser start)
autoLoadApiKey();

// ── Message router ─────────────────────────────────────────────────────────

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg.type === "RELOAD_API_KEY") {
    autoLoadApiKey().then(() => sendResponse({ ok: true }));
    return true;
  }

  if (msg.type === "FETCH_PLAYLIST") {
    fetchPlaylistVideos(msg.url).then(sendResponse);
    return true; // keep channel open for async
  }

  if (msg.type === "SAVE_FILE") {
    saveTranscriptFile(msg).then(sendResponse);
    return true;
  }

  if (msg.type === "REVEAL_FILE") {
    revealInFinder(msg).then(sendResponse);
    return true;
  }
});

// ── Fetch playlist video IDs ───────────────────────────────────────────────
// Opens the playlist page in a hidden tab, injects a content script to
// scrape video IDs from the page DOM, then closes the tab.

async function fetchPlaylistVideos(playlistUrl) {
  try {
    // Create a tab to load the playlist
    const tab = await chrome.tabs.create({ url: playlistUrl, active: false });

    // Wait for the tab to finish loading
    await waitForTabLoad(tab.id);

    // Inject scraper
    const results = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      func: scrapePlaylistFromPage,
    });

    chrome.tabs.remove(tab.id);

    const items = results?.[0]?.result || [];
    return { items };
  } catch (e) {
    console.error("fetchPlaylistVideos error:", e);
    return { items: [] };
  }
}

function waitForTabLoad(tabId) {
  return new Promise(resolve => {
    function listener(id, info) {
      if (id === tabId && info.status === "complete") {
        chrome.tabs.onUpdated.removeListener(listener);
        // Extra delay for YouTube's JS to render
        setTimeout(resolve, 2000);
      }
    }
    chrome.tabs.onUpdated.addListener(listener);
  });
}

// Runs in the context of the playlist page
function scrapePlaylistFromPage() {
  const items = [];
  const seen  = new Set();

  // YouTube renders playlist items as <a> tags with /watch?v=...&list=...
  document.querySelectorAll("a#video-title, ytd-playlist-video-renderer a").forEach(a => {
    const href = a.href || "";
    const m    = href.match(/[?&]v=([A-Za-z0-9_-]{11})/);
    if (m && !seen.has(m[1])) {
      seen.add(m[1]);
      items.push({
        videoId: m[1],
        url:     `https://www.youtube.com/watch?v=${m[1]}`,
        title:   a.title || a.textContent?.trim() || m[1],
      });
    }
  });

  return items;
}

// ── Save transcript file ───────────────────────────────────────────────────
// Uses the chrome.downloads API to save the file to the user's chosen folder.

async function saveTranscriptFile({ filename, savePath, content }) {
  try {
    const blob    = new Blob([content], { type: "text/plain" });
    const dataUrl = await blobToDataUrl(blob);

    // Build the download path
    // savePath is something like /Users/you/Desktop/Code Projects/Youtube-Transcript/downloads
    // We strip the leading slash for chrome.downloads (it uses relative paths from Downloads
    // folder unless conflictAction is used with a full path via the filename field).
    // Chrome downloads API doesn't support absolute paths directly — we use the filename
    // field with a relative path from the default Downloads folder, OR we use a workaround
    // via a data URL download with the suggested filename.

    const cleanName = filename.replace(/[/\\:*?"<>|]/g, "_");

    await chrome.downloads.download({
      url:      dataUrl,
      filename: cleanName,
      saveAs:   false,
      conflictAction: "overwrite",
    });

    return { ok: true };
  } catch (e) {
    console.error("saveTranscriptFile error:", e);
    return { ok: false, error: e.message };
  }
}

function blobToDataUrl(blob) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload  = () => resolve(reader.result);
    reader.onerror = reject;
    reader.readAsDataURL(blob);
  });
}

// ── Reveal in Finder ───────────────────────────────────────────────────────
// Chrome extensions can't open Finder directly. Best we can do is open the
// downloads page so the user can right-click → Show in Finder.

async function revealInFinder({ videoId, title, savePath }) {
  // Try to find the download entry and show it
  const cleanName = (title || videoId).replace(/[^a-zA-Z0-9_\- ]/g, "_").slice(0, 80).trim() + ".txt";

  const items = await chrome.downloads.search({ filenameRegex: cleanName.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") });
  if (items.length > 0) {
    chrome.downloads.show(items[0].id);
  } else {
    // Fallback: open chrome downloads page
    chrome.tabs.create({ url: "chrome://downloads" });
  }
  return { ok: true };
}
