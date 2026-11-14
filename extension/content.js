// Reads Google Meet live captions and forwards finalized text to the local
// transcript server (via background.js).
//
// Meet rewrites a caption in place while someone is speaking (and Japanese ASR
// revises earlier characters a lot), so we don't send every DOM change. Each
// caption block is tracked, and only text that has settled is committed:
//   - once it hasn't changed for STABLE_MS, or
//   - once it has been pending MAX_WAIT_MS (minus the still-volatile tail),
//     so long monologues without pauses still come through.

const CONFIG = {
  pollMs: 500,
  stableMs: 1500,
  maxWaitMs: 8000,
  volatileTailChars: 12,
  maxOutbox: 2000,
  debug: false,
};

// Meet's class names are obfuscated and change from time to time. If captions
// stop arriving, inspect the captions panel and update these. When the block
// selector matches nothing, we fall back to reading the whole region as one
// stream (speaker becomes "Unknown").
const SELECTORS = {
  region: [
    '[role="region"][aria-label="Captions"]',
    '[role="region"][aria-label="字幕"]', // Meet UI in Japanese
    '.vNKgIf',
  ].join(', '),
  block: '.nMcdL',
  speaker: '.NWpY1d',
  text: '.ygicle',
};

const blocks = new Map(); // Element -> { speaker, committed, lastText, lastChange, pendingSince }
const outbox = [];
let sending = false;
let mode = null;
let lastError = null;
let timer = null;

function log(...args) {
  console.log('[meet-captions]', ...args);
}

function normalize(text) {
  return (text || '').replace(/\s+/g, ' ').trim();
}

function setMode(next) {
  if (mode !== next) {
    mode = next;
    log(`reading captions using ${next === 'selectors' ? 'block selectors' : 'fallback (whole region)'}`);
  }
}

function readBlocks(region) {
  const els = region.querySelectorAll(SELECTORS.block);
  if (els.length) {
    setMode('selectors');
    return [...els].map((el) => {
      const speaker = normalize(el.querySelector(SELECTORS.speaker)?.innerText) || 'Unknown';
      const textEl = el.querySelector(SELECTORS.text);
      // Without a text element, assume the first line of the block is the speaker name.
      const text = textEl ? textEl.innerText : el.innerText.split('\n').slice(1).join(' ');
      return { key: el, speaker, text: normalize(text) };
    });
  }

  setMode('fallback');
  // Skip the "Jump to bottom" button container.
  const text = [...region.children]
    .filter((child) => !child.querySelector('button'))
    .map((child) => child.innerText)
    .join(' ');
  return [{ key: region, speaker: 'Unknown', text: normalize(text) }];
}

function commonPrefixLength(a, b) {
  let i = 0;
  while (i < a.length && i < b.length && a[i] === b[i]) i++;
  return i;
}

// Returns the part of `current` that hasn't been sent yet, given that
// `committed` is the block text as of the last commit.
function newPortion(committed, current) {
  if (current.startsWith(committed)) return current.slice(committed.length);

  // Meet trimmed old text off the front of the block: re-anchor on the tail of
  // what we already sent.
  for (let n = Math.min(40, committed.length); n >= 8; n--) {
    const idx = current.lastIndexOf(committed.slice(-n));
    if (idx !== -1) return current.slice(idx + n);
  }

  // ASR revised text we already sent. Resend the revised sentence (up to 30
  // chars back) so the translator sees it whole; a little duplication is
  // better than dropping words.
  const diverge = commonPrefixLength(committed, current);
  if (diverge >= current.length) return '';
  const sentenceStart = Math.max(...SENTENCE_ENDS.map((c) => current.lastIndexOf(c, diverge - 1))) + 1;
  return current.slice(Math.max(sentenceStart, diverge - 30, 0));
}

const SENTENCE_ENDS = ['。', '？', '！', '?', '!'];

function commit(state, text) {
  const fresh = normalize(newPortion(state.committed, text)).replace(/^[。、，,.?？!！\s]+/, '');
  if (fresh) {
    outbox.push({ ts: Date.now(), speaker: state.speaker, text: fresh });
    if (CONFIG.debug) log('commit', state.speaker, fresh);
  }
  state.committed = text;
  state.pendingSince = text === state.lastText ? null : Date.now();
}

function tick() {
  const now = Date.now();
  const seen = new Set();
  const region = document.querySelector(SELECTORS.region);

  if (region) {
    for (const { key, speaker, text } of readBlocks(region)) {
      seen.add(key);
      let state = blocks.get(key);
      if (!state) {
        state = { speaker, committed: '', lastText: '', lastChange: now, pendingSince: null };
        blocks.set(key, state);
      }
      state.speaker = speaker;

      if (text !== state.lastText) {
        state.lastText = text;
        state.lastChange = now;
        state.pendingSince ??= now;
      }
      if (state.pendingSince === null) continue;

      if (now - state.lastChange >= CONFIG.stableMs) {
        commit(state, text);
      } else if (now - state.pendingSince >= CONFIG.maxWaitMs) {
        commit(state, text.slice(0, -CONFIG.volatileTailChars));
      }
    }
  }

  // Blocks Meet removed from the panel: flush whatever they last showed.
  for (const [key, state] of blocks) {
    if (seen.has(key)) continue;
    if (state.pendingSince !== null) commit(state, state.lastText);
    blocks.delete(key);
  }

  flush();
}

async function flush() {
  if (sending || !outbox.length) return;
  sending = true;
  const batch = outbox.slice();
  try {
    const res = await chrome.runtime.sendMessage({
      type: 'captions',
      meeting: location.pathname.replace(/^\//, ''),
      lines: batch,
    });
    if (res?.ok) {
      outbox.splice(0, batch.length);
      if (lastError) log('server reachable again');
      lastError = null;
    } else if (res?.error !== lastError) {
      lastError = res?.error;
      log(`server error (will retry): ${lastError}. Is server.py running?`);
    }
  } catch (err) {
    if (String(err).includes('Extension context invalidated')) {
      log('extension was reloaded; refresh this tab to resume capturing');
      clearInterval(timer);
    } else {
      log('send failed', err);
    }
  } finally {
    if (outbox.length > CONFIG.maxOutbox) outbox.splice(0, outbox.length - CONFIG.maxOutbox);
    sending = false;
  }
}

log('loaded; turn on captions (CC) in Meet to start capturing');
timer = setInterval(tick, CONFIG.pollMs);
