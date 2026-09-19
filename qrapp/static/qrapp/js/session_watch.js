/* session_watch.js
 * Detects session expiry on AJAX calls.
 *
 * When a Django session ages out, protected endpoints redirect fetch() to
 * the login page (fetch follows silently) and the caller's res.json() fails
 * with a confusing parse error. This wrapper watches every fetch response:
 * one that was redirected to the login URL means the session is gone, so we
 * tell the admin what happened and send the browser to the login page
 * instead of leaving a page of broken buttons behind.
 */
(function () {
    'use strict';

    if (typeof window.fetch !== 'function') return;

    var LOGIN_PATHS = ['/qrapp/login/', '/login/'];
    var handled = false;

    function isLoginRedirect(res) {
        if (!res.redirected) return false;
        try {
            var path = new URL(res.url, window.location.origin).pathname;
            return LOGIN_PATHS.indexOf(path) !== -1;
        } catch (e) {
            return false;
        }
    }

    function expireNow(loginUrl) {
        if (handled) return;
        handled = true;
        var go = function () { window.location.href = loginUrl; };
        if (window.Swal && window.notify && typeof window.notify.warning === 'function') {
            window.notify.warning(
                'Session expired',
                'Your session has ended. Please log in again to continue.'
            ).then(go);
            // Don't strand the user if the dialog is dismissed slowly.
            setTimeout(go, 6000);
        } else if (window.confirm) {
            window.confirm('Your session has expired. You will now be returned to the login page.');
            go();
        } else {
            go();
        }
    }

    var originalFetch = window.fetch.bind(window);
    window.fetch = function () {
        return originalFetch.apply(null, arguments).then(function (res) {
            if (isLoginRedirect(res)) {
                expireNow(res.url);
                // Return the response anyway so existing .json() handlers
                // fail softly into their own catch paths while we redirect.
            }
            return res;
        });
    };
})();
