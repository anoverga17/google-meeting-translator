// Relays caption batches from the Meet tab to the local transcript server.
// Done here rather than in the content script so the request isn't subject to
// meet.google.com's CORS / mixed-content rules.
const SERVER = 'http://127.0.0.1:8765';

function setBadge(ok) {
  chrome.action.setBadgeText({ text: ok ? 'ON' : 'ERR' });
  chrome.action.setBadgeBackgroundColor({ color: ok ? '#1e8e3e' : '#d93025' });
}

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg?.type !== 'captions') return;

  fetch(`${SERVER}/captions`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ meeting: msg.meeting, lines: msg.lines }),
  })
    .then((res) => {
      setBadge(res.ok);
      sendResponse({ ok: res.ok, error: res.ok ? null : `HTTP ${res.status}` });
    })
    .catch((err) => {
      setBadge(false);
      sendResponse({ ok: false, error: String(err) });
    });

  return true; // keep the channel open for the async sendResponse
});
