/**
 * Shared UI actions: modal helpers + declarative [data-action] dispatcher.
 *
 * Replaces inline onclick/style attributes. Buttons declare intent via
 * data attributes, e.g.:
 *   <button data-action="open-modal" data-target="addUserModal">
 *   <button data-action="close-modal" data-target="addUserModal">
 *   <button data-action="open-edit-student" data-payload='{"id":1,...}'>
 *   <button data-action="approve-user" data-url="/approve/1/" data-name="jo">
 *   <a data-action="confirm-nav" data-confirm="Delete ALL students?">
 */
(function () {
    'use strict';

    // ---- Modal helpers (defined once; page scripts may reuse them) ----
    if (typeof window.openModal !== 'function') {
        window.openModal = function (modalId) {
            var modal = document.getElementById(modalId);
            if (modal) modal.classList.add('active');
        };
    }
    if (typeof window.closeModal !== 'function') {
        window.closeModal = function (modalId) {
            var modal = document.getElementById(modalId);
            if (modal) modal.classList.remove('active');
        };
    }

    // ---- Declarative dispatcher ----
    function readPayload(el) {
        var raw = el.getAttribute('data-payload');
        if (!raw) return {};
        try { return JSON.parse(raw); } catch (e) { return {}; }
    }

    document.addEventListener('click', function (e) {
        var el = e.target.closest('[data-action]');
        if (!el) return;

        var action = el.getAttribute('data-action');
        var target = el.getAttribute('data-target');

        switch (action) {
            // --- Modals ---
            case 'open-modal':
                e.preventDefault();
                window.openModal(target);
                break;
            case 'close-modal':
                e.preventDefault();
                window.closeModal(target);
                break;

            // --- Users ---
            case 'open-add-user':
                e.preventDefault();
                window.openAddUserModal();
                break;
            case 'open-edit-user':
                e.preventDefault();
                var u = readPayload(el);
                window.openEditUserModal(u.id, u.username, u.email, u.isStaff, u.isActive);
                break;
            case 'approve-user':
                e.preventDefault();
                window.approveUser(el.getAttribute('data-user-id'), el.getAttribute('data-user-name'));
                break;
            case 'delete-user':
                e.preventDefault();
                window.deleteUser(el.getAttribute('data-user-id'), el.getAttribute('data-user-name'));
                break;

            // --- Students ---
            case 'open-add-student':
                e.preventDefault();
                window.openModal('addStudentModal');
                break;
            case 'open-upload-students':
                e.preventDefault();
                window.openModal('uploadStudentsModal');
                break;
            case 'open-export-students':
                e.preventDefault();
                window.openExportStudentsModal();
                break;
            case 'open-edit-student':
                e.preventDefault();
                var s = readPayload(el);
                window.openEditStudentModal(s.id, s.studentId, s.name, s.sex, s.college, s.program, s.year, s.major);
                break;

            // --- QR ---
            case 'open-qr-codes':
                e.preventDefault();
                window.openQrCodesModal();
                break;
            case 'load-qr-codes':
                e.preventDefault();
                window.loadQrCodes();
                break;
            case 'export-qr':
                e.preventDefault();
                window.exportQrCodes(el.getAttribute('data-format'));
                break;

            // --- Reports / uploads ---
            case 'submit-add-user':
                e.preventDefault();
                window.submitAddUser();
                break;
            case 'submit-edit-user':
                e.preventDefault();
                window.submitEditUser();
                break;
            case 'submit-add-student':
                e.preventDefault();
                window.submitAddStudent();
                break;
            case 'submit-upload-students':
                e.preventDefault();
                window.submitUploadStudents.call(el, e);
                break;
            case 'submit-edit-student':
                e.preventDefault();
                window.submitEditStudent();
                break;
            case 'apply-print':
                e.preventDefault();
                window.applyPrintOptions();
                break;
            case 'apply-export':
                e.preventDefault();
                window.applyExportOptions();
                break;
            case 'apply-export-students':
                e.preventDefault();
                window.applyExportStudents();
                break;

            // --- Generic page-function call ---
            // <button data-action="call" data-fn="openEditModal" data-args="3|CCJE|Name|true">
            // Args are pipe-separated and URL-encoded (via Django's urlencode
            // filter) so pipes/quotes inside values are safe. After decoding,
            // "true"/"false" become booleans, numeric tokens become numbers.
            case 'call': {
                e.preventDefault();
                var fn = window[el.getAttribute('data-fn')];
                if (typeof fn !== 'function') break;
                var rawArgs = el.getAttribute('data-args') || '';
                var args = rawArgs === '' ? [] : rawArgs.split('|').map(function (v) {
                    var value;
                    try { value = decodeURIComponent(v); } catch (err) { value = v; }
                    if (value === 'true') return true;
                    if (value === 'false') return false;
                    var n = Number(value);
                    return (value !== '' && !isNaN(n)) ? n : value;
                });
                fn.apply(null, args);
                break;
            }

            case 'print-page':
                e.preventDefault();
                window.print();
                break;

            // --- Sidebar section links (admin dashboard tabs) ---
            case 'section-nav':
                if (typeof window.navigateDashboardSection === 'function' &&
                    !window.navigateDashboardSection(el)) {
                    e.preventDefault();
                }
                break;

            // --- Confirm before navigating (destructive GET links) ---
            case 'confirm-nav':
                if (!window.confirm(el.getAttribute('data-confirm') || 'Are you sure?')) {
                    e.preventDefault();
                }
                break;
        }
    });

    // Close any open modal when clicking the dark backdrop
    window.addEventListener('click', function (e) {
        if (e.target.classList && e.target.classList.contains('modal')) {
            e.target.classList.remove('active');
        }
    });
})();
