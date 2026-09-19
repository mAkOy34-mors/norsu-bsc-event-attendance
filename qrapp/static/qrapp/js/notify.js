/**
 * SweetAlert2 notification bridge — the single notification system for the
 * whole app. Every dialog/toast renders through this module so styling stays
 * identical everywhere (see the "SweetAlert theme" section in style.css).
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

    // ---- shared theme -------------------------------------------------
    // buttonsStyling:false + customClass let style.css own the button look
    // (tokens: --primary / --danger / --warning), so every dialog in the
    // app matches instead of Swal's default per-icon coloring.
    var BTN = {
        primary: 'swal-btn swal-btn-primary',
        danger: 'swal-btn swal-btn-danger',
        warn: 'swal-btn swal-btn-warn',
        neutral: 'swal-btn swal-btn-neutral',
    };

    var theme = {
        buttonsStyling: false,
        customClass: {
            popup: 'qr-swal-popup',
            title: 'qr-swal-title',
            htmlContainer: 'qr-swal-text',
            confirmButton: BTN.primary,
        },
    };

    var baseToast = Object.assign({}, theme, {
        toast: true,
        position: 'top-end',
        showConfirmButton: false,
        timer: 3200,
        timerProgressBar: true,
        customClass: {
            popup: 'qr-swal-popup qr-swal-toast',
            title: 'qr-swal-title',
        },
        didOpen: function (toast) {
            toast.addEventListener('mouseenter', Swal.stopTimer);
            toast.addEventListener('mouseleave', Swal.fire, Swal.resumeTimer);
        },
    });

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
            return Swal.fire(Object.assign({}, theme, {
                icon: 'success',
                title: title,
                text: text || '',
                confirmButtonText: 'OK',
                timer: text ? 0 : 2600,
                timerProgressBar: !text,
            }, opts || {}, {
                customClass: Object.assign({}, theme.customClass, {
                    confirmButton: BTN.primary,
                }, (opts && opts.customClass) || {}),
            }));
        },

        /** Error modal. */
        error: function (title, text, opts) {
            return Swal.fire(Object.assign({}, theme, {
                icon: 'error',
                title: title,
                text: text || '',
                confirmButtonText: 'Try again',
            }, opts || {}, {
                customClass: Object.assign({}, theme.customClass, {
                    confirmButton: BTN.danger,
                }, (opts && opts.customClass) || {}),
            }));
        },

        /** Warning modal. */
        warning: function (title, text, opts) {
            return Swal.fire(Object.assign({}, theme, {
                icon: 'warning',
                title: title,
                text: text || '',
                confirmButtonText: 'Understood',
            }, opts || {}, {
                customClass: Object.assign({}, theme.customClass, {
                    confirmButton: BTN.warn,
                }, (opts && opts.customClass) || {}),
            }));
        },

        /** Info modal. */
        info: function (title, text, opts) {
            return Swal.fire(Object.assign({}, theme, {
                icon: 'info',
                title: title,
                text: text || '',
                confirmButtonText: 'OK',
            }, opts || {}, {
                customClass: Object.assign({}, theme.customClass, {
                    confirmButton: BTN.primary,
                }, (opts && opts.customClass) || {}),
            }));
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
     * Danger confirm + neutral cancel unless opts.danger === false.
     */
    window.confirmAction = function (opts) {
        opts = opts || {};
        return Swal.fire(Object.assign({}, theme, {
            icon: 'warning',
            title: opts.title || 'Are you sure?',
            text: opts.text || 'This action cannot be undone.',
            showCancelButton: true,
            confirmButtonText: opts.confirmText || 'Yes, continue',
            cancelButtonText: 'Cancel',
            reverseButtons: true,
            focusCancel: true,
            customClass: Object.assign({}, theme.customClass, {
                confirmButton: opts.danger === false ? BTN.primary : BTN.danger,
                cancelButton: BTN.neutral,
            }),
        })).then(function (result) {
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
