/* chime.js
 * Bell notification sound for new student check-ins.
 *
 * Usage:
 *   window.chime.onNewCheckIns(count)  // rings once per batch of new rows
 *   window.chime.ring()                // ring manually
 *
 * The sound is synthesized with the Web Audio API (no audio files needed),
 * so it works offline and needs no extra HTTP requests.
 */

(function () {
  'use strict';

  // Browsers block audio until the user interacts with the page; the first
  // click/keypress unlocks it. Polls may also see their "seed" rows on the
  // very first tick — those are existing records, not fresh check-ins, so
  // callers use seen() to mark them without ringing.
  let audioCtx = null;
  let unlocked = false;

  function ensureContext() {
    if (!audioCtx) {
      var AC = window.AudioContext || window.webkitAudioContext;
      if (!AC) return null;
      audioCtx = new AC();
    }
    if (audioCtx.state === 'suspended') audioCtx.resume();
    return audioCtx;
  }

  function unlock() {
    unlocked = true;
    ensureContext();
    document.removeEventListener('click', unlock);
    document.removeEventListener('keydown', unlock);
    document.removeEventListener('touchstart', unlock);
  }

  document.addEventListener('click', unlock);
  document.addEventListener('keydown', unlock);
  document.addEventListener('touchstart', unlock);

  /**
   * Two-tone "ding-dong" bell: an upper strike followed by a lower,
   * slightly longer one, each with a quick decay like a real bell.
   */
  function ring() {
    var ctx = ensureContext();
    if (!ctx) return;

    var now = ctx.currentTime;

    function strike(freq, start, dur, gain) {
      var osc = ctx.createOscillator();
      var g = ctx.createGain();
      osc.type = 'sine';
      osc.frequency.setValueAtTime(freq, start);
      g.gain.setValueAtTime(0, start);
      g.gain.linearRampToValueAtTime(gain, start + 0.015);
      g.gain.exponentialRampToValueAtTime(0.0001, start + dur);
      osc.connect(g).connect(ctx.destination);
      osc.start(start);
      osc.stop(start + dur + 0.05);
    }

    // Ding (E6) then dong (C6)
    strike(1318.51, now, 0.55, 0.22);
    strike(1046.50, now + 0.22, 0.85, 0.18);
  }

  /**
   * Called by the live pollers when fresh attendance rows arrive.
   * No-ops when the page hasn't been interacted with yet (browser
   * autoplay policy), so the very first load stays silent.
   */
  function onNewCheckIns(count) {
    if (!count || count <= 0) return;
    if (!unlocked) return; // autoplay policy: stay silent until user interacts
    ring();
  }

  /**
   * Mark rows as "seen" without ringing (used for the initial seed rows
   * so existing records aren't announced as new on page load).
   */
  function markSeen() { /* kept for API symmetry / future use */ }

  window.chime = {
    ring: ring,
    onNewCheckIns: onNewCheckIns,
    markSeen: markSeen,
  };
})();
