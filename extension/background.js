// Relays caption lines from the Meet tab to the local transcript server.
// Done here rather than in the content script so the request isn't subject to
// meet.google.com's CORS / mixed-content rules.
chrome.runtime.onMessage.addListener((lines, _sender, sendResponse) => {
  fetch('http://localhost:8765/captions', { method: 'POST', body: lines })
    .then((res) => res.ok, () => false)
    .then((ok) => {
      chrome.action.setBadgeText({ text: ok ? 'ON' : 'ERR' });
      chrome.action.setBadgeBackgroundColor({ color: ok ? '#1e8e3e' : '#d93025' });
      sendResponse(ok);
    });
  return true; // keep the channel open for the async sendResponse
});
