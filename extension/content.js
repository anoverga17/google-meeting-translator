// Reads Google Meet live captions and sends settled text to the local
// transcript server (via background.js) as "[HH:MM:SS] Speaker: text" lines.
//
// Meet rewrites a caption in place while someone is speaking (and speech
// recognition revises earlier words a lot, especially in Japanese), so text is
// only sent once it has settled:
//   - completed sentences (ending in 。？！. etc. with more text after) once unchanged for SENTENCE_MS,
//   - the whole caption once unchanged for PAUSE_MS (the speaker paused), or
//   - after MAX_WAIT_MS with no sentence end, all but the still-changing tail.
// Cutting mid-sentence hurts translation (e.g. Japanese puts the verb last), so the
// last rule is a backstop, not the normal path.
const POLL_MS = 250;
const SENTENCE_MS = 1000;
const PAUSE_MS = 1500;
const MAX_WAIT_MS = 6000;
const TAIL_CHARS = 12;

// Meet's class names are obfuscated and change from time to time. If captions
// stop arriving, inspect the captions panel and update these.
const REGION = '[role="region"][aria-label="Captions"], [role="region"][aria-label="字幕"], .vNKgIf';
const BLOCK = '.nMcdL'; // one speaker's caption
const SPEAKER = '.NWpY1d';
const TEXT = '.ygicle';

// Caption element -> { speaker, text, sent, sentences, changedAt, sentencesAt, pendingSince }
const blocks = new Map();
const outbox = [];
let sending = false;

const clean = (s) => (s || '').replace(/\s+/g, ' ').trim();

// The text up to and including the last sentence-ending mark ('' if none).
// Meet puts a provisional 。 on the end of text that is still being spoken, so
// only a mark with more text after it counts. A "." only counts before a space,
// so numbers like 2.0.1 don't split.
const completed = (text) => (text.match(/.*(?:[。？！?!؟।]|\.(?=\s))(?=\s*\S)/s) || [''])[0];

function readCaptions() {
  const region = document.querySelector(REGION);
  if (!region) return [];
  const els = [...region.querySelectorAll(BLOCK)];
  if (els.length) {
    return els.map((el) => ({
      el,
      speaker: clean(el.querySelector(SPEAKER)?.innerText) || 'Unknown',
      text: clean((el.querySelector(TEXT) || el).innerText),
    }));
  }
  // BLOCK is out of date: read the whole panel as one speaker, skipping the
  // "Jump to bottom" button.
  const text = [...region.children].filter((c) => !c.querySelector('button')).map((c) => c.innerText);
  return [{ el: region, speaker: 'Unknown', text: clean(text.join(' ')) }];
}

// The part of `text` that hasn't been sent, given `sent` is the caption as of the last send.
function unsent(sent, text) {
  if (sent.startsWith(text)) return '';
  if (text.startsWith(sent)) return text.slice(sent.length);

  // Meet trimmed old text off the front: re-anchor on the tail of what was sent.
  for (let n = Math.min(40, sent.length); n >= 8; n--) {
    const i = text.lastIndexOf(sent.slice(-n));
    if (i !== -1) return text.slice(i + n);
  }

  // ASR revised text that was already sent. Resend the revised sentence (up to
  // 30 chars back) so the translator sees it whole; a little duplication is
  // better than dropping words.
  let i = 0;
  while (text[i] === sent[i]) i++;
  return text.slice(Math.max(completed(text.slice(0, i)).length, i - 30));
}

function send(state, text) {
  const fresh = clean(unsent(state.sent, text)).replace(/^[。、，,.?？!！\s]+/, '');
  if (fresh) {
    const time = new Date(state.pendingSince).toTimeString().slice(0, 8);
    outbox.push(`[${time}] ${state.speaker}: ${fresh}\n`);
  }
  state.sent = text;
  state.pendingSince = text === state.text ? null : Date.now();
}

function tick() {
  const now = Date.now();
  const seen = new Set();

  for (const { el, speaker, text } of readCaptions()) {
    seen.add(el);
    if (!blocks.has(el)) blocks.set(el, { text: '', sent: '', sentences: '', pendingSince: null });
    const state = blocks.get(el);
    state.speaker = speaker;

    if (text !== state.text) {
      state.text = text;
      state.changedAt = now;
      state.pendingSince ??= now;
    }
    const sentences = completed(text);
    if (sentences !== state.sentences) {
      state.sentences = sentences;
      state.sentencesAt = now;
    }
    if (state.pendingSince === null) continue;

    // The startsWith checks skip cuts with nothing new in them; sending one
    // would rewind `sent` and the text after it would go out twice.
    const settled = text.slice(0, -TAIL_CHARS);
    if (now - state.changedAt >= PAUSE_MS) {
      send(state, text);
    } else if (now - state.sentencesAt >= SENTENCE_MS && !state.sent.startsWith(sentences)) {
      send(state, sentences);
    } else if (now - state.pendingSince >= MAX_WAIT_MS && !state.sent.startsWith(settled)) {
      send(state, settled);
    }
  }

  // Captions Meet removed from the panel: send whatever they last showed.
  for (const [el, state] of blocks) {
    if (seen.has(el)) continue;
    if (state.pendingSince !== null) send(state, state.text);
    blocks.delete(el);
  }

  flush();
}

// Lines stay in the outbox until the server has them, so nothing is lost while it's down.
async function flush() {
  if (sending || !outbox.length) return;
  sending = true;
  const count = outbox.length;
  try {
    if (await chrome.runtime.sendMessage(outbox.join(''))) outbox.splice(0, count);
  } catch (err) {
    console.warn('[meet-captions] send failed:', err);
    if (String(err).includes('Extension context invalidated')) {
      console.warn('[meet-captions] extension was reloaded; refresh this tab to resume capturing');
      clearInterval(timer);
    }
  }
  sending = false;
}

console.log('[meet-captions] loaded; turn on captions (CC) in Meet to start capturing');
const timer = setInterval(tick, POLL_MS);
