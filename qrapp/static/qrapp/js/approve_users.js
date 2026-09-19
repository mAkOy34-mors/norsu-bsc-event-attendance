/* approve_users.js
 * Realtime Approve Users page.
 * Polls ajax_pending_users every few seconds so newly registered accounts
 * appear without a page refresh (rings the bell when someone new arrives),
 * and approves accounts via fetch so an approval doesn't reload the page.
 */
(function () {
    'use strict';

    const POLL_MS = 4000;

    const tbody = document.getElementById('pendingTableBody');
    const pendingBadge = document.getElementById('pendingCountBadge');
    const totalBadge = document.getElementById('totalUsersBadge');
    const dot = document.getElementById('approveLiveDot');
    if (!tbody) return;

    const endpoint = (window.appUrls && window.appUrls.pendingUsers) || '/qrapp/ajax/pending-users/';
    const approveUrl = (window.appUrls && window.appUrls.manageUsers) || '/qrapp/ajax/manage-users/';

    function csrfToken() {
        const el = document.getElementById('csrfTokenValue');
        return (el && el.getAttribute('data-csrf')) || getCookie('csrftoken');
    }

    function getCookie(name) {
        const value = `; ${document.cookie}`;
        const parts = value.split(`; ${name}=`);
        if (parts.length === 2) return parts.pop().split(';').shift();
        return '';
    }

    function escapeHtml(s) {
        const div = document.createElement('div');
        div.textContent = s == null ? '' : String(s);
        return div.innerHTML;
    }

    function rowHtml(user) {
        const initials = escapeHtml(user.username.slice(0, 2).toUpperCase());
        return `
            <tr data-user-id="${user.id}">
                <td>
                    <div class="user-info">
                        <div class="avatar">${initials}</div>
                        <div class="user-details">
                            <div class="username">${escapeHtml(user.username)}</div>
                            <div class="email">Registered: ${escapeHtml(user.date_joined)}</div>
                        </div>
                    </div>
                </td>
                <td>${escapeHtml(user.email || '-')}</td>
                <td class="cell-actions">
                    <button type="button" class="btn btn-success btn-sm approve-btn"
                            data-user-id="${user.id}" data-user-name="${escapeHtml(user.username)}">
                        <i class="fas fa-check"></i> Approve
                    </button>
                </td>
            </tr>`;
    }

    function emptyRow() {
        return `
            <tr class="pending-empty-row">
                <td colspan="3">
                    <div class="no-users" style="text-align:center; padding: 36px; color: var(--gray);">
                        <i class="fas fa-users" style="font-size: 42px; margin-bottom: 12px; color: var(--gray-light); display:block;"></i>
                        <p>No pending users to approve — all registrations have been reviewed.</p>
                    </div>
                </td>
            </tr>`;
    }

    function render(data) {
        tbody.innerHTML = data.pending.length
            ? data.pending.map(rowHtml).join('')
            : emptyRow();
        if (pendingBadge) pendingBadge.textContent = `Pending: ${data.count}`;
        if (totalBadge) totalBadge.textContent = `Total Users: ${data.total_users}`;
    }

    function setDot(state) {
        if (!dot) return;
        dot.classList.toggle('is-live', state === 'live');
        dot.classList.toggle('is-polling', state === 'polling');
    }

    let seenCount = null;   // first poll seeds silently
    let timer = null;
    let inFlight = false;

    async function poll() {
        if (inFlight || document.hidden) return;
        inFlight = true;
        try {
            const res = await fetch(endpoint, {
                headers: { 'X-Requested-With': 'XMLHttpRequest' },
                cache: 'no-store',
            });
            if (!res.ok) return;
            const data = await res.json();
            if (!data.success) return;

            render(data);
            setDot('live');

            if (seenCount !== null && data.count > seenCount) {
                // New registration arrived while watching
                if (window.chime) window.chime.ring();
                const fresh = data.count - seenCount;
                if (window.notify) {
                    window.notify.toast(
                        `${fresh} new registration${fresh === 1 ? '' : 's'} waiting for approval`,
                        'info'
                    );
                }
            }
            seenCount = data.count;
        } catch (err) {
            setDot('polling');  // transient network hiccup
        } finally {
            inFlight = false;
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
        setDot('');
    }

    document.addEventListener('visibilitychange', () => {
        if (document.hidden) stopPolling();
        else startPolling();
    });

    // Approve via AJAX — no page reload; the next poll (or direct removal
    // below) refreshes the list and the counters.
    tbody.addEventListener('click', async (e) => {
        const btn = e.target.closest('.approve-btn');
        if (!btn || btn.disabled) return;
        btn.disabled = true;
        btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> ...';

        try {
            const body = new URLSearchParams({
                action: 'approve',
                user_id: btn.getAttribute('data-user-id'),
                csrfmiddlewaretoken: csrfToken(),
            });
            const res = await fetch(approveUrl, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/x-www-form-urlencoded',
                    'X-CSRFToken': csrfToken(),
                    'X-Requested-With': 'XMLHttpRequest',
                },
                body: body.toString(),
                cache: 'no-store',
            });
            const data = await res.json();
            if (data.success) {
                if (window.notify) window.notify.success('User approved', data.message);
                const row = btn.closest('tr');
                if (row) row.remove();
                if (pendingBadge) {
                    const m = pendingBadge.textContent.match(/\d+/);
                    if (m) pendingBadge.textContent = `Pending: ${Math.max(parseInt(m[0], 10) - 1, 0)}`;
                }
                if (!tbody.querySelector('.approve-btn') && !tbody.querySelector('.pending-empty-row')) {
                    tbody.innerHTML = emptyRow();
                }
            } else {
                if (window.notify) window.notify.error('Could not approve user', data.error || 'Please try again.');
                btn.disabled = false;
                btn.innerHTML = '<i class="fas fa-check"></i> Approve';
            }
        } catch (err) {
            if (window.notify) window.notify.error('Network error', 'Please try again.');
            btn.disabled = false;
            btn.innerHTML = '<i class="fas fa-check"></i> Approve';
        }
    });

    startPolling();
})();
