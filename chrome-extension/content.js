// content.js — runs on YouTube pages
// Sends page metadata (title, description) to the extension on request.

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg.type === "GET_PAGE_META") {
    sendResponse(getPageMeta());
  }
});

function getPageMeta() {
  const title = document.querySelector("h1.ytd-video-primary-info-renderer")?.textContent?.trim()
    || document.querySelector("h1.title")?.textContent?.trim()
    || document.title?.replace(" - YouTube", "").trim()
    || "";

  const description = document.querySelector("#description-text")?.textContent?.trim()
    || document.querySelector("ytd-expander #content")?.textContent?.trim()
    || "";

  const channelName = document.querySelector("#channel-name a")?.textContent?.trim()
    || document.querySelector("ytd-channel-name a")?.textContent?.trim()
    || "";

  return { title, description, channelName };
}
