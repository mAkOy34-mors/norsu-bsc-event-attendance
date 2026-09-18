/* auth_password_toggle.js
 * Eye show/hide toggles for password fields on the login and register pages.
 * A button .password-toggle with data-target="<input id>" flips the input
 * between password/text and swaps the eye / eye-slash icon.
 */
(function () {
    'use strict';

    document.addEventListener('click', function (e) {
        var btn = e.target.closest('.password-toggle');
        if (!btn) return;
        e.preventDefault();

        var input = document.getElementById(btn.getAttribute('data-target'));
        if (!input) return;

        var show = input.type === 'password';
        input.type = show ? 'text' : 'password';

        var icon = btn.querySelector('i');
        if (icon) {
            icon.classList.toggle('fa-eye', !show);
            icon.classList.toggle('fa-eye-slash', show);
        }
        btn.setAttribute('aria-label', show ? 'Hide password' : 'Show password');
        input.focus({ preventScroll: true });
    });
})();
