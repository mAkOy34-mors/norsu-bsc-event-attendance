/**
 * SweetAlert2 notification bridge.
 *
 * Provides the `notify` helper (toast + modal feedback) and `confirmAction`
 * (Promise-based confirm). Loaded in <head> (deferred) on every base.html
 * page so all page scripts can use it. Also renders Django messages.
 *
 * Swal must be loaded BEFORE this file.
 */
(function () {
    'use strict';

    if (typeof window.Swal === 'undefined') {
        // SweetAlert2 failed to load: fail soft, fall back to native dialogs.
        window.notify = {
            success: function (title, text) { alert(text ? title + '\n' + text : title); },
            error: function (title, text) { alert(text ? title + '\n' + text : title); },
            warning: function (title, text) { alert(text ? title + '\n' + text : title); },
            info: function (title, text) { alert(title + (text ? '\n' + text : '')); },
            toast: function (message, icon) { alert(message); },
            handleAjax: function (data, fallback) {
                if (data && data.success === false) {
                    alert((data && (data.error || data.message)) || fallback || 'Something went wrong.');
                } else {
                    alert((data && (data.message || data.error)) || fallback || 'Done.');
                }
            },
        };
        window.confirmAction = function (opts) {
            var msg = (opts && (opts.text || opts.title)) || 'Are you sure?';
            return Promise.resolve(window.confirm(msg));
        };
        return;
    }

    var baseToast = {
        toast: true,
        position: 'top-end',
        showConfirmButton: false,
        timer: 3200,
        timerProgressBar: true,
        didOpen: function (toast) {
            toast.addEventListener('mouseenter', Swal.stopTimer);
            toast.addEventListener('mouseleave', Swal.fire, Swal.resumeTimer);
        },
    };

    window.notify = {
        /** Small corner toast for non-blocking feedback. */
        toast: function (message, icon) {
            Swal.fire(Object.assign({}, baseToast, {
                icon: icon || 'success',
                title: message,
            }));
        },

        /** Success modal (or toast via opts.toast). */
        success: function (title, text, opts) {
            return Swal.fire(Object.assign({
                icon: 'success',
                title: title,
                text: text || '',
                confirmButtonColor: '#0984e3',
                timer: text ? 0 : 2600,
                timerProgressBar: !text,
            }, opts || {}));
        },

        /** Error modal. */
        error: function (title, text, opts) {
            return Swal.fire(Object.assign({
                icon: 'error',
                title: title,
                text: text || '',
                confirmButtonColor: '#d63031',
            }, opts || {}));
        },

        /** Warning modal. */
        warning: function (title, text, opts) {
            return Swal.fire(Object.assign({
                icon: 'warning',
                title: title,
                text: text || '',
                confirmButtonColor: '#b8860b',
            }, opts || {}));
        },

        /** Info modal. */
        info: function (title, text, opts) {
            return Swal.fire(Object.assign({
                icon: 'info',
                title: title,
                text: text || '',
                confirmButtonColor: '#0984e3',
            }, opts || {}));
        },

        /**
         * Handle a JSON AJAX response with the right dialog.
         * Expects {success, message|error} like qrapp's AJAX views return.
         */
        handleAjax: function (data, fallbackTitle) {
            if (data && data.success) {
                return this.success(data.message || fallbackTitle || 'Success');
            }
            if (data && data.color === 'warning') {
                return this.warning(fallbackTitle || 'Note', data.message || data.error);
            }
            return this.error(
                fallbackTitle || 'Error',
                (data && (data.error || data.message)) || 'Something went wrong.'
            );
        },
    };

    /**
     * Promise-based destructive-action confirm.
     * Returns Promise<boolean> so callers can `if (!(await confirmAction()))`.
     */
    window.confirmAction = function (opts) {
        opts = opts || {};
        return Swal.fire({
            icon: 'warning',
            title: opts.title || 'Are you sure?',
            text: opts.text || 'This action cannot be undone.',
            showCancelButton: true,
            confirmButtonText: opts.confirmText || 'Yes, continue',
            confirmButtonColor: opts.danger === false ? '#0984e3' : '#d63030',
            cancelButtonColor: '#636e72',
            reverseButtons: true,
            focusCancel: true,
        }).then(function (result) {
            return result.isConfirmed;
        });
    };

    /**
     * Render Django messages (passed via data attributes on a hidden
     * .django-messages div by partials/notifications.html) as toasts.
     */
    document.addEventListener('DOMContentLoaded', function () {
        var holder = document.querySelector('.django-messages');
        if (!holder) return;
        var iconFor = { error: 'error', success: 'success', warning: 'warning', debug: 'info' };
        Array.prototype.forEach.call(holder.attributes, function (attr) {
            var m = attr.name.match(/^data-tag-(\d+)$/);
            if (!m) return;
            var tag = (attr.value || 'info').split(' ')[0];
            var text = holder.getAttribute('data-text-' + m[1]) || '';
            window.notify.toast(text, iconFor[tag] || 'info');
        });
    });
})();
