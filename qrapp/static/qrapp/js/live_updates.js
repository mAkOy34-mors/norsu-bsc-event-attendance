/* live_updates.js
 * Silently polls for new attendance rows and appends them to the
 * dashboard table without any user action. Rings the bell (chime.js)
 * for new check-ins unless the Live Logs widget already rang for them.
 */

(function () {
  'use strict';

  const POLL_MS = 4000;                // every 4 seconds
  const MAX_ROWS = 200;                // keep the DOM from growing forever

  const tableBody = document.getElementById('dashboardTableBody');
  const endpoint  = (window.appUrls && window.appUrls.scansSince) || null;

  if (!tableBody || !endpoint) return;

  // Cursor: highest attendance id we've already rendered.
  // Seed it from the rows the server already put in the DOM.
  let cursor = 0;
  function seedCursorFromDom() {
    tableBody.querySelectorAll('tr[data-attendance-id]').forEach(tr => {
      const id = parseInt(tr.getAttribute('data-attendance-id'), 10);
      if (!isNaN(id) && id > cursor) cursor = id;
    });
  }
  seedCursorFromDom();

  let timer = null;

  function currentFilters() {
    const params = new URLSearchParams();
    const pick = {
      year:    'dashboardYear',
      college: 'dashboardCollege',
      program: 'dashboardProgram',
      status:  'dashboardStatus',
      date:    'dashboardDate',
      search:  'dashboardSearch',
    };
    Object.keys(pick).forEach(key => {
      const el = document.getElementById(pick[key]);
      if (el && el.value) params.append(key, el.value);
    });
    return params;
  }

  function escapeHtml(s) {
    const div = document.createElement('div');
    div.textContent = s == null ? '' : s;
    return div.innerHTML;
  }

  function buildRow(row) {
    const tr = document.createElement('tr');
    tr.setAttribute('data-attendance-id', row.id);
    tr.classList.add('row-new');   // for the flash animation

    const badgeClass = row.status === 'IN' ? 'info' : 'warning';

    tr.innerHTML = `
      <td>${escapeHtml(row.student_id)}</td>
      <td>${escapeHtml(row.name)}</td>
      <td>${escapeHtml(row.college)}</td>
      <td>${escapeHtml(row.program)}</td>
      <td>${escapeHtml(row.year)}</td>
      <td>${escapeHtml(row.major)}</td>
      <td><span class="badge badge-${badgeClass}">${escapeHtml(row.status)}</span></td>
      <td>${escapeHtml(row.date)}</td>
      <td>${escapeHtml(row.time)}</td>
    `;
    return tr;
  }

  function removeEmptyState() {
    const empty = tableBody.querySelector('tr.empty-row');
    if (empty) empty.remove();
  }

  function pruneOldRows() {
    const rows = tableBody.querySelectorAll('tr[data-attendance-id]');
    const extra = rows.length - MAX_ROWS;
    for (let i = 0; i < extra; i++) rows[i].remove();
  }

  function updateCounter(delta) {
    const info = document.querySelector('#dashboardPagination .pagination-info');
    if (!info || !delta) return;
    const match = info.textContent.match(/of\s+(\d+)\s+records/i);
    if (!match) return;
    const total = parseInt(match[1], 10) + delta;
    info.textContent = `Showing 1-10 of ${total} records`;
  }

  async function tick() {
    // Fallback: if cursor is still 0 (e.g. rows hadn't rendered when we
    // seeded), re-derive from the DOM before asking the server.
    if (cursor === 0) seedCursorFromDom();

    try {
      const params = currentFilters();
      params.append('since', cursor);
      const res = await fetch(endpoint + '?' + params.toString(), {
        headers: { 'X-Requested-With': 'XMLHttpRequest' },
        cache: 'no-store',
      });
      if (!res.ok) return;
      const data = await res.json();
      if (!data.success) return;

      if (data.rows && data.rows.length) {
        removeEmptyState();
        // Prepend newest first (matches 'order_by("-timestamp")' in the view)
        for (let i = data.rows.length - 1; i >= 0; i--) {
          const tr = buildRow(data.rows[i]);
          tableBody.insertBefore(tr, tableBody.firstChild);
          setTimeout(() => tr.classList.remove('row-new'), 1500);
        }
        pruneOldRows();
        updateCounter(data.rows.length);

        // Ring the bell for brand-new check-ins. The sidebar Live Logs
        // widget polls the same records; don't ring twice for one scan.
        if (!window.liveSidebarLogsActive) {
          const checkIns = data.rows.filter((r) => r.status === 'IN').length;
          if (window.chime) window.chime.onNewCheckIns(checkIns || data.rows.length);
        }
      }

      if (data.latest_id && data.latest_id > cursor) {
        cursor = data.latest_id;
      }
    } catch (err) {
      // Silent — transient network errors shouldn't spam the console
    }
  }

  function start() {
    if (timer) return;
    tick();
    timer = setInterval(tick, POLL_MS);
  }

  function stop() {
    if (timer) {
      clearInterval(timer);
      timer = null;
    }
  }

  document.addEventListener('visibilitychange', () => {
    if (document.hidden) stop();
    else start();
  });

  // Expose resetCursor for future AJAX-based table reloads
  window.liveUpdates = {
    resetCursor() {
      cursor = 0;
      seedCursorFromDom();
    }
  };

  const dashboardTab = document.getElementById('dashboard');
  if (dashboardTab && !dashboardTab.classList.contains('active')) {
    const observer = new MutationObserver(() => {
      if (dashboardTab.classList.contains('active')) {
        start();
        observer.disconnect();
      }
    });
    observer.observe(dashboardTab, { attributes: true, attributeFilter: ['class'] });
  } else {
    start();
  }
})();