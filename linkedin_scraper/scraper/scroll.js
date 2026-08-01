// scraper/scroll.js — load the feed, then save the page.
//
// Produces ONE saved HTML page and nothing else. Extraction is parsing/'s job and runs offline
// against that file. The single exception to "reads nothing" is the relative age ("3h", "2d") of
// the last few posts, used only to decide when to stop.
//
// Pasting this file only installs window.SCR. Start it with the line printed by start_cmd.py:
//   await SCR.start({...})   begin   |  SCR.status() counts  |  SCR.probe() scroll diagnostics
//   await SCR.wait(sec)      observe, sec < 38
//   SCR.stop()               finish the cycle, then save
//   SCR.save()               re-download if Chrome swallowed it

;(function () {
'use strict';

// Set by start(cfg): the page cannot read config.json, so the values arrive as an argument.
var CFG = null;

// Every key required, none defaulted — a half-specified run would use settings nobody chose.
var REQ = {
  scrollFrac:    'pair',  // fraction of the container per step; <= 1 so nothing is skipped
  delayMs:       'pair',  // ms between steps
  settleMs:      'num',
  loadWaitMs:    'num',   // base wait at the bottom; grows with each stuck cycle
  stuckLimit:    'num',
  maxCycles:     'num',
  maxMinutes:    'num',
  maxPosts:      'num',   // DOM post count, not records — this file counts nothing else
  stopBeforeMs:  'num',   // absolute instant to stop at; computed in start_cmd.py
  stopStreak:    'num',   // consecutive posts past the boundary before stopping
  sortWaitMs:    'num',   // how long to give the feed to render after switching to Recent
  hiddenGraceMs: 'num'    // how long a hidden tab may make no progress before giving up
};

// The only per-post hook in the feed DOM that is not a hashed, rotating class name.
var BTN_SEL = 'button[aria-label^="Open control menu for post"]';

var S = {
  cyc: 0, lm: 0, stuck: 0, modals: 0, halt: false, done: false,
  hidSince: null, hidN: 0,
  why: null, err: null, t0: Date.now(), file: null, bytes: 0, oldest: null
};

function sleep(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }
function rnd(p) { return Math.round(p[0] + Math.random() * (p[1] - p[0])); }
function buttons() { return document.querySelectorAll(BTN_SEL); }
function postCount() { return buttons().length; }

// LinkedIn scrolls an inner container: window.scrollBy() is a silent no-op and scrollY stays 0.
// Climbing from a real post button survives an A/B test that renames <main>.
function scroller() {
  var b = document.querySelector(BTN_SEL);
  if (b) {
    for (var node = b.parentElement; node; node = node.parentElement) {
      if (node.scrollHeight > node.clientHeight + 200 && node.clientHeight > 300) return node;
    }
  }
  var m = document.querySelector('main');
  if (m && m.scrollHeight > m.clientHeight + 200) return m;
  return document.scrollingElement || document.documentElement;
}

// Two digits, never more — LinkedIn rolls 60m -> 1h, 24h -> 1d, 7d -> 1w.
var AGE_RE = /^(\d{1,2})([mhdw])(?:\s*•|\s*$)/;
var AGE_S  = { m: 60, h: 3600, d: 86400, w: 604800 };

// Read the age off the element that RENDERS it: textContent glues adjacent nodes together and
// "23h" becomes "5023h". The 40-char cap rejects wrappers, the element guard bounds the climb.
function ageOf(btn) {
  for (var n = btn.parentElement, i = 0; n && i < 6; n = n.parentElement, i++) {
    var els = n.querySelectorAll('span, time');
    if (els.length > 400) break;
    for (var j = 0; j < els.length; j++) {
      var t = (els[j].textContent || '').trim();
      if (t.length > 40) continue;
      var m = AGE_RE.exec(t);
      if (m) return { s: parseInt(m[1], 10) * AGE_S[m[2]], txt: m[1] + m[2] };
    }
  }
  return null;
}

// Tail only — the page reaches 15MB. Paid "Promoted" slots carry no age and are skipped, with a
// bounded look-back so a run of them cannot walk the whole feed.
function lastAges(bs, n) {
  var out = [], floor = Math.max(0, bs.length - 3 * n - 1);
  for (var i = bs.length - 1; i >= floor && out.length < n; i--) {
    var a = ageOf(bs[i]);
    if (a) out.push(a);
  }
  return out;
}

// Promotional modals render the feed behind them EMPTY, so postCount() reads 0 and the loop
// counts stuck cycles against a feed that is merely covered.
// Matched on aria-label only, never text: nothing that accepts, subscribes or confirms qualifies.
function dismissModals() {
  var n = 0, dialogs = document.querySelectorAll('[role="dialog"]');
  for (var i = 0; i < dialogs.length; i++) {
    if (!dialogs[i].getClientRects().length) continue;
    var bs = dialogs[i].querySelectorAll('button');
    for (var j = 0; j < bs.length; j++) {
      var label = (bs[j].getAttribute('aria-label') || '').trim();
      if (/^(dismiss|close)\b/i.test(label) && bs[j].getClientRects().length) {
        bs[j].click(); n++; break;
      }
    }
  }
  return n;
}

// Exact-text allowlist, deliberately EXCLUDING the "New posts" pill — clicking that jumps the
// feed back to the top and resets the run.
function findLoadMore() {
  var ok = ['load more', 'show more feed updates'];
  var bs = document.querySelectorAll('button');
  for (var i = 0; i < bs.length; i++) {
    var b = bs[i], t = (b.textContent || '').trim().toLowerCase();
    if (ok.indexOf(t) !== -1 && b.offsetParent && !b.closest('[role="dialog"]')) return b;
  }
  return null;
}

// The control is a <div role="button">, not a <button>; the visibility filter drops an
// off-screen duplicate. The post-switch wait is WALL CLOCK — summing requested sleeps instead
// made a "15 second" wait run three minutes.
async function sortByRecent() {
  var btn = Array.prototype.slice.call(document.querySelectorAll('button, [role="button"]'))
    .filter(function (b) { return /^Sort by:/i.test((b.textContent || '').trim()) && b.offsetParent; })[0];
  if (!btn) return { ok: false, reason: 'sort control not found' };

  dismissModals();
  var already = /Recent/i.test(btn.textContent);
  if (!already) {
    btn.click();
    await sleep(700);
    var item = Array.prototype.slice.call(document.querySelectorAll('[role="menuitem"]'))
      .filter(function (el) { return (el.textContent || '').trim() === 'Recent'; })[0];
    if (!item) return { ok: false, reason: 'Recent menuitem missing' };
    item.click();
    await sleep(2500);
  }

  var t0 = Date.now();
  while (postCount() === 0 && Date.now() - t0 < CFG.sortWaitMs) {
    dismissModals();
    await sleep(500);
  }
  if (postCount() === 0) {
    return { ok: false, empty: true,
             reason: 'feed rendered 0 posts ' + Math.round(CFG.sortWaitMs / 1000) +
                     's after sorting by Recent — reload the tab and paste again' };
  }
  return { ok: true, already: already };
}

// A Blob plus a synthetic <a download> is the only way bulk data leaves the page. Called ONCE
// per run — Chrome blocks the second automatic download from a page load.
function save() {
  var html = '<!DOCTYPE html>\n' + document.documentElement.outerHTML;
  var name = 'linkedin_feed_' + new Date().toISOString().replace(/[:.]/g, '-') + '.html';
  var u = URL.createObjectURL(new Blob([html], { type: 'text/html' }));
  var a = document.createElement('a');
  a.href = u; a.download = name; a.style.display = 'none';
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(function () { URL.revokeObjectURL(u); }, 30000);
  S.file = name; S.bytes = html.length;
}

function status() {
  return {
    done: S.done, why: S.why, err: S.err, posts: postCount(), oldest: S.oldest,
    cyc: S.cyc, lm: S.lm, stuck: S.stuck, modals: S.modals, file: S.file, bytes: S.bytes,
    secs: Math.round((Date.now() - S.t0) / 1000), hidden: document.hidden
  };
}

// A hidden tab is often served an empty feed, which reads exactly like a broken sort. Grace
// rather than an instant halt: a hidden tab that is still growing is doing its job.
function hiddenTooLong(n) {
  if (!document.hidden) { S.hidSince = null; return false; }
  if (S.hidSince === null || n > S.hidN) { S.hidSince = Date.now(); S.hidN = n; return false; }
  return Date.now() - S.hidSince > CFG.hiddenGraceMs;
}

// Sitting at the bottom unable to make the container grow is the real end-of-feed signal; "no
// new posts" alone is not. Returns a halt reason, or null to keep going.
// Modals are checked here and not every cycle: this is the moment a covered feed and a finished
// one become indistinguishable, and querying the whole DOM 400 times is not free.
async function handleBottom(h0, n0) {
  if (dismissModals()) { S.modals++; S.stuck = 0; return null; }
  var more = findLoadMore();
  if (more) { more.click(); S.lm++; }
  // Escalating backoff separates "the feed has ended" from "the feed is being slow".
  await sleep(CFG.loadWaitMs * (S.stuck + 1));
  if (scroller().scrollHeight > h0 + 50 || postCount() > n0) { S.stuck = 0; return null; }
  S.stuck++;
  return S.stuck >= CFG.stuckLimit ? (more ? 'load_more_dead' : 'end_of_feed') : null;
}

async function loop() {
  try {
    for (var i = 0; i < CFG.maxCycles; i++) {
      S.cyc = i + 1;
      var n0 = postCount();

      if (S.halt) { S.why = 'stopped'; break; }
      if (Date.now() - S.t0 > CFG.maxMinutes * 60000) { S.why = 'max_runtime'; break; }
      if (n0 >= CFG.maxPosts) { S.why = 'max_posts'; break; }
      if (!/^\/feed/.test(location.pathname)) { S.why = 'navigated'; break; }
      if (hiddenTooLong(n0)) { S.why = 'tab_hidden'; break; }

      var M = scroller();
      if (!M) { S.why = 'no_scroller'; break; }
      var h0 = M.scrollHeight;

      M.scrollBy(0, Math.round(M.clientHeight * (CFG.scrollFrac[0] +
        Math.random() * (CFG.scrollFrac[1] - CFG.scrollFrac[0]))));
      await sleep(CFG.settleMs);

      // A STREAK past the boundary, not one post: the feed interleaves the odd older item.
      var bs = buttons();
      var ages = lastAges(bs, CFG.stopStreak);
      if (ages.length) S.oldest = ages[0].txt;
      if (ages.length === CFG.stopStreak) {
        var past = 0;
        for (var k = 0; k < ages.length; k++) {
          if (Date.now() - ages[k].s * 1000 < CFG.stopBeforeMs) past++;
        }
        if (past === ages.length) { S.why = 'reached_date'; break; }
      }

      document.title = '> ' + bs.length + ' @' + (S.oldest || '?') + ' | li';

      if (M.scrollTop + M.clientHeight >= M.scrollHeight - 700 && M.scrollHeight <= h0 + 50) {
        var halt = await handleBottom(h0, n0);
        if (halt) { S.why = halt; break; }
      } else S.stuck = 0;

      await sleep(rnd(CFG.delayMs));
    }
    if (!S.why) S.why = 'max_cycles';
  } catch (e) {
    S.err = String((e && e.message) || e); S.why = 'error';
  }
  try { save(); } catch (e) { S.err = 'save failed: ' + String((e && e.message) || e); }
  S.done = true;
  document.title = 'DONE ' + postCount() + ' | li';
}

async function start(cfg) {
  // Checked first, before the config and before the sort control: only a human can fix it.
  if (document.hidden) {
    S.done = true; S.why = 'tab_hidden';
    return { ok: false, why: 'tab_hidden', retry: false, ask_user: true,
             reason: 'the LinkedIn tab is not visible — bring the window and this tab to the ' +
                     'front, leave them there for the whole run, then start again' };
  }

  var bad = [];
  for (var key in REQ) {
    var v = cfg ? cfg[key] : undefined;
    var ok = REQ[key] === 'pair'
      ? (Array.isArray(v) && v.length === 2 && typeof v[0] === 'number' && typeof v[1] === 'number')
      : typeof v === 'number';
    if (!ok) bad.push(key);
  }
  if (bad.length) {
    S.done = true; S.why = 'bad_config';
    return { ok: false, why: 'bad_config', bad: bad,
             reason: 'run scraper/start_cmd.py and paste the line it prints' };
  }
  CFG = cfg;
  S.t0 = Date.now();   // the clock starts here, not at paste time

  // An unsorted feed makes the run's ordering meaningless, so refuse rather than save an
  // unusable page. feed_empty is retryable by reloading; sort_failed means the markup moved.
  var sort = await sortByRecent();
  if (!sort.ok) {
    S.done = true; S.why = sort.empty ? 'feed_empty' : 'sort_failed'; S.err = sort.reason;
    return { ok: false, why: S.why, retry: !!sort.empty, reason: sort.reason };
  }

  await sleep(1000);
  var M = scroller();
  loop();   // fire and forget: the loop must keep scrolling between the caller's round trips
  return {
    ok: true, sorted: sort.already ? 'already' : 'switched',
    scroller: M.tagName, scrollable: M.scrollHeight > M.clientHeight + 200,
    posts: postCount(), cycles: CFG.maxCycles,
    stopBefore: new Date(CFG.stopBeforeMs).toISOString()
  };
}

// Keep `sec` under 40: CDP Runtime.evaluate times out at 45s. That kills only the response
// channel — page code runs on — but a timed-out call tells you nothing.
function wait(sec) {
  var end = Date.now() + Math.min(sec || 30, 38) * 1000;
  return new Promise(function (res) {
    (function tick() {
      if (S.done || Date.now() >= end) return res(status());
      setTimeout(tick, 500);
    })();
  });
}

window.SCR = {
  start: start,
  wait: wait,
  status: status,
  save: save,   // the loop always saves on exit; this is for a download Chrome swallowed
  stop: function () { S.halt = true; return status(); },
  cfg: function () { return CFG; },
  probe: function () {
    var M = scroller();
    return { posts: postCount(), lm: !!findLoadMore(),
             dialogs: document.querySelectorAll('[role="dialog"]').length, tag: M.tagName,
             sh: M.scrollHeight, top: Math.round(M.scrollTop), ch: M.clientHeight,
             path: location.pathname.slice(0, 40), hidden: document.hidden };
  }
};

})();
