// Allow submitting form with Enter key
document.getElementById('adminPassword').addEventListener('keypress', function(e) {
    if (e.key === 'Enter') {
        e.preventDefault();
        document.getElementById('deleteForm').submit();
    }
});

// Show password error injected by the template (falls back to a toast)
(function () {
    const errorEl = document.getElementById('passwordError');
    const errorText = errorEl ? errorEl.dataset.errorText : '';
    if (errorEl && errorText) {
        errorEl.textContent = errorText;
        errorEl.style.display = 'block';
        if (window.notify) {
            notify.toast(errorText, 'error');
        }
    }
})();
