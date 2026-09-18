/* live_sidebar_logs.js
 * Realtime sidebar "Live Logs" widget.
 * Polls for new attendance records (time-in / time-out) every few seconds and
 * prepends them without any page refresh. Also requests a Screen Wake Lock so
 * the display stays active while the dashboard is monitored on a kiosk/screen.
 * Rings the bell (chime.js) whenever a brand-new check-in arrives.
 */

(function () {
  'use strict';

  const POLL_MS = 4000;      // poll every 4 seconds
  const MAX_ITEMS = 20;      // keep the DOM from growing forever

  const widget = document.getElementById('sidebarLogs');
  if (!widget) return;

  const INITIAL_LIMIT = parseInt(widget.getAttribute('data-initial-limit'), 10) || 12;

  const listEl = document.getElementById('liveLogsList');
  const emptyEl = document.getElementById('liveLogsEmpty');
  const updatedEl = document.getElementById('liveLogsUpdated');
  const dotEl = document.getElementById('liveLogsDot');
  const endpoint = widget.getAttribute('data-logs-url');
  if (!listEl || !endpoint) return;

  let timer = null;
  let pollInFlight = false;
  let cursor = 0;            // highest attendance id rendered

  /* ---------------- screen wake lock (screen always active) ---------------- */
  let wakeLock = null;

  async function requestWakeLock() {
    try {
      if ('wakeLock' in navigator && navigator.wakeLock.request) {
        wakeLock = await navigator.wakeLock.request('screen');
        wakeLock.addEventListener('release', () => {
          wakeLock = null;
          updateDot();
        });
        updateDot();
      }
    } catch (err) {
      // Wake Lock unsupported/denied (needs HTTPS or localhost) — polling
      // continues regardless; silently ignore.
    }
  }

  function releaseWakeLock() {
    if (wakeLock) {
      try { wakeLock.release(); } catch (e) { /* noop */ }
      wakeLock = null;
    }
  }

  document.addEventListener('visibilitychange', () => {
    if (document.hidden) {
      stopPolling();
      releaseWakeLock();
    } else {
      startPolling();
      requestWakeLock();  // re-acquire after tab becomes visible again
    }
  });

  /* ------------------------------ helpers ------------------------------ */

  function escapeHtml(s) {
    const div = document.createElement('div');
    div.textContent = s == null ? '' : String(s);
    return div.innerHTML;
  }

  function buildItem(row) {
    const li = document.createElement('li');
    li.className = 'sidebar-log-item is-new';
    li.setAttribute('data-attendance-id', row.id);

    const isIn = row.status === 'IN';
    const badgeClass = isIn ? 'log-in' : 'log-out';
    const icon = isIn ? 'fa-sign-in-alt' : 'fa-sign-out-alt';

    li.innerHTML = `
      <div class="log-row-main">
        <span class="log-status ${badgeClass}" title="${escapeHtml(row.status)}">
          <i class="fas ${icon}"></i>
        </span>
        <span class="log-name">${escapeHtml(row.name)}</span>
        <span class="log-time">${escapeHtml(row.time)}</span>
      </div>
      <div class="log-row-sub">
        <span class="log-id">${escapeHtml(row.student_id)}</span>
        ${row.event ? `<span class="log-event" title="${escapeHtml(row.event)}">${escapeHtml(row.event)}</span>` : ''}
      </div>
    `;
    return li;
  }

  function prependItems(rows) {
    if (!rows.length) return;
    if (emptyEl && emptyEl.parentNode) emptyEl.remove();

    const frag = document.createDocumentFragment();
    for (let i = rows.length - 1; i >= 0; i--) {
      const item = buildItem(rows[i]);
      frag.appendChild(item);
    }
    listEl.insertBefore(frag, listEl.firstChild);
    pruneOldItems();

    // Clear the flash highlight after a moment
    listEl.querySelectorAll('.sidebar-log-item.is-new').forEach((el) => {
      setTimeout(() => el.classList.remove('is-new'), 2500);
    });
  }

  function pruneOldItems() {
    const items = listEl.querySelectorAll('.sidebar-log-item');
    const extra = items.length - MAX_ITEMS;
    for (let i = 0; i < extra; i++) items[items.length - 1 - i].remove();
  }

  function updateDot() {
    if (!dotEl) return;
    dotEl.classList.toggle('is-live', !!wakeLock && !document.hidden);
    dotEl.classList.toggle('is-polling', !wakeLock);
  }

  function setUpdatedLabel(serverTime) {
    if (!updatedEl) return;
    const now = serverTime || new Date().toLocaleTimeString([], {
      hour: 'numeric', minute: '2-digit', second: '2-digit'
    });
    updatedEl.textContent = `Updated ${now}`;
  }

  /* ------------------------------- polling ------------------------------- */

  async function poll() {
    if (pollInFlight) return;
    pollInFlight = true;
    try {
      const params = new URLSearchParams();
      params.append('limit', INITIAL_LIMIT);
      params.append('since_id', cursor);
      const res = await fetch(`${endpoint}?${params.toString()}`, {
        headers: { 'X-Requested-With': 'XMLHttpRequest' },
        cache: 'no-store',
      });
      if (!res.ok) return;
      const data = await res.json();
      if (!data.success) return;

      // On the very first poll (cursor === 0) the server returns the newest
      // rows; render them as a seed list, not as "new" flashes.
      const isSeed = cursor === 0 && !listEl.querySelector('.sidebar-log-item');
      if (isSeed && data.rows.length) {
        const frag = document.createDocumentFragment();
        for (const row of data.rows) {
          const item = buildItem(row);
          item.classList.remove('is-new');
          frag.appendChild(item);
        }
        listEl.appendChild(frag);
        if (emptyEl && emptyEl.parentNode) emptyEl.remove();
        data.rows.forEach((row) => { if (row.id > cursor) cursor = row.id; });
        setUpdatedLabel(data.server_time);
        updateDot();
        return;
      }

      const fresh = data.rows.filter((row) => row.id > cursor);
      if (fresh.length) {
        prependItems(fresh);
        fresh.forEach((row) => { if (row.id > cursor) cursor = row.id; });
        // Ring the bell for brand-new scans (only true check-ins).
        const checkIns = fresh.filter((row) => row.status === 'IN').length;
        if (window.chime) window.chime.onNewCheckIns(checkIns || fresh.length);
      }
      setUpdatedLabel(data.server_time);
    } catch (err) {
      // Silent — transient network errors shouldn't spam the console
    } finally {
      pollInFlight = false;
    }
  }

  function startPolling() {
    if (timer) return;
    poll();
    timer = setInterval(poll, POLL_MS);
  }

  function stopPolling() {
    if (timer) {
      clearInterval(timer);
      timer = null;
    }
  }

  /* ------------------------------- start ------------------------------- */

  startPolling();
  requestWakeLock();

  // Tell other pollers (live_updates.js) that this widget already rings
  // the bell for new scans, so the same scan never rings twice.
  window.liveSidebarLogsActive = true;
})();
