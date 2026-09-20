// Config read from the json_script data islands rendered by the template
const appUrls = JSON.parse(document.getElementById('app-urls').textContent);
// Expose for shared scripts: dashboard.js resolves its AJAX endpoints from
// window.appUrls (ajaxStudentList/ajaxDashboardData/ajaxReportsData).
window.appUrls = appUrls;
const csrfToken = appUrls.csrfToken || '';
const collegeProgramsMap = JSON.parse(document.getElementById('college-programs-map').textContent);
const studentProgramsByCollege = JSON.parse(document.getElementById('student-programs-by-college').textContent);

// Function to update program dropdown based on selected college
function updateProgramDropdown(collegeSelectId, programSelectId) {
    const collegeSelect = document.getElementById(collegeSelectId);
    const programSelect = document.getElementById(programSelectId);
    
    if (!collegeSelect || !programSelect) return;

    const previousValue = programSelect.value;
    const selectedCollege = collegeSelect.value;
    const added = new Set();

    function addProgram(code, label) {
        if (!code || added.has(code)) return;
        added.add(code);
        const option = document.createElement('option');
        option.value = code;
        option.textContent = label || code;
        programSelect.appendChild(option);
    }

    programSelect.innerHTML = '<option value="">All Programs</option>';

    if (selectedCollege) {
        if (collegeProgramsMap[selectedCollege]) {
            collegeProgramsMap[selectedCollege].forEach(program => {
                addProgram(program.code, program.code + ' - ' + program.name);
            });
        }
        if (studentProgramsByCollege[selectedCollege]) {
            studentProgramsByCollege[selectedCollege].forEach(code => {
                addProgram(code, code);
            });
        }
    } else {
        Object.keys(collegeProgramsMap).forEach(college => {
            collegeProgramsMap[college].forEach(program => {
                addProgram(program.code, program.code + ' - ' + program.name);
            });
        });
        Object.keys(studentProgramsByCollege).forEach(college => {
            studentProgramsByCollege[college].forEach(code => {
                addProgram(code, code);
            });
        });
    }

    if (previousValue && Array.from(programSelect.options).some(opt => opt.value === previousValue)) {
        programSelect.value = previousValue;
    }
}

// Show/hide + populate a Major dropdown based on the selected program.
// Used by the Export Report, QR Codes and Export Students modals: the field
// stays hidden unless the program actually has majors.
async function updateProgramMajorDropdown(programCode, groupId, selectId) {
    const majorGroup = document.getElementById(groupId);
    const majorSelect = document.getElementById(selectId);
    if (!majorGroup || !majorSelect) return;

    // Reset selection whenever the program changes
    majorSelect.value = '';

    if (!programCode) {
        majorGroup.classList.add('is-hidden');
        majorSelect.innerHTML = '<option value="">All Majors</option>';
        return;
    }

    try {
        const response = await fetch(appUrls.getMajors.replace('TEMPLATE', encodeURIComponent(programCode)));
        const data = await response.json();
        const majors = (data.success && data.majors) || [];

        if (majors.length === 0) {
            // Program has no majors — hide the field entirely
            majorGroup.classList.add('is-hidden');
            majorSelect.innerHTML = '<option value="">All Majors</option>';
            return;
        }

        majorSelect.innerHTML = '<option value="">All Majors</option>';
        majors.forEach(major => {
            const option = document.createElement('option');
            option.value = major.name;
            option.textContent = major.code + ' - ' + major.name;
            majorSelect.appendChild(option);
        });
        majorGroup.classList.remove('is-hidden');
    } catch (err) {
        console.error('Failed to load majors for ' + groupId + ':', err);
        majorGroup.classList.add('is-hidden');
    }
}

// Backward-compatible wrapper for the Export Report modal
async function updateExportMajorDropdown(programCode) {
    await updateProgramMajorDropdown(programCode, 'exportMajorGroup', 'exportMajor');
}

// Setup event listeners when page loads
document.addEventListener('DOMContentLoaded', () => {
    // Print / Export modals
    const printCollegeSelect = document.getElementById('printCollege');
    if (printCollegeSelect) {
        printCollegeSelect.addEventListener('change', () => {
            updateProgramDropdown('printCollege', 'printProgram');
        });
    }

    const exportCollegeSelect = document.getElementById('exportCollege');
    if (exportCollegeSelect) {
        exportCollegeSelect.addEventListener('change', () => {
            updateProgramDropdown('exportCollege', 'exportProgram');
        });
    }

    // Export report modal: show the Major dropdown only when the selected
    // program actually has majors, and populate it from the server
    const exportProgramSelect = document.getElementById('exportProgram');
    if (exportProgramSelect) {
        exportProgramSelect.addEventListener('change', () => {
            updateExportMajorDropdown(exportProgramSelect.value);
        });
    }

    // QR Codes modal: major auto-appears when the chosen program has majors
    const qrProgramSelect = document.getElementById('qrProgram');
    if (qrProgramSelect) {
        qrProgramSelect.addEventListener('change', () => {
            updateProgramMajorDropdown(qrProgramSelect.value, 'qrMajorGroup', 'qrMajor');
        });
    }

    // Export Students modal: same auto-major behavior
    const exportStudentsProgramSelect = document.getElementById('exportStudentsProgram');
    if (exportStudentsProgramSelect) {
        exportStudentsProgramSelect.addEventListener('change', () => {
            updateProgramMajorDropdown(exportStudentsProgramSelect.value, 'exportStudentsMajorGroup', 'exportStudentsMajor');
        });
    }

    // Reports section: majors appear only when the picked program has them,
    // and reset when the college (thus program list) changes.
    const reportsProgramSelect = document.getElementById('reportsProgram');
    if (reportsProgramSelect) {
        reportsProgramSelect.addEventListener('change', () => {
            updateProgramMajorDropdown(reportsProgramSelect.value, 'reportsMajorGroup', 'reportsMajor');
        });
    }
    const reportsCollegeSelect = document.getElementById('reportsCollege');
    if (reportsCollegeSelect) {
        reportsCollegeSelect.addEventListener('change', () => {
            const group = document.getElementById('reportsMajorGroup');
            const major = document.getElementById('reportsMajor');
            if (major) major.value = '';
            if (group) group.classList.add('is-hidden');
        });
    }

    // Export Report modal: majors appear only when the picked program has them
    const exportReportProgramSelect = document.getElementById('exportProgram');
    if (exportReportProgramSelect) {
        exportReportProgramSelect.addEventListener('change', () => {
            updateProgramMajorDropdown(exportReportProgramSelect.value, 'exportMajorGroup', 'exportMajor');
        });
    }

    // "Load more" batches in the QR modal
    const qrMoreBtn = document.getElementById('qrCodesMore');
    if (qrMoreBtn) {
        qrMoreBtn.addEventListener('click', () => loadQrCodes({ append: true }));
    }

    // Export report modal: the event filter alone selects that event's scans
    // (their real timestamps). Dates are applied only when the user types
    // them -- auto-filling the event's nominal date here used to blank out
    // Time In/Out when scans happened on a different day.

    // Per-menu college → program filters
    document.querySelectorAll('.filter-college').forEach(collegeSelect => {
        collegeSelect.addEventListener('change', () => {
            const programId = collegeSelect.dataset.programTarget;
            if (programId) {
                updateProgramDropdown(collegeSelect.id, programId);
            }
        });
    });
});

// ==================== USER MANAGEMENT FUNCTIONS ====================
function openAddUserModal() {
    document.getElementById('addUserModal').classList.add('active');
}

function openEditUserModal(id, username, email, isStaff, isActive) {
    document.getElementById('editUserId').value = id;
    document.getElementById('editUsername').value = username;
    document.getElementById('editEmail').value = email;
    document.getElementById('editIsStaff').checked = isStaff;
    document.getElementById('editIsActive').checked = isActive;
    document.getElementById('editUserModal').classList.add('active');
}

function submitAddUser() {
    const username = document.getElementById('addUsername').value.trim();
    const email = document.getElementById('addEmail').value.trim();
    const password = document.getElementById('addPassword').value;
    const isStaff = document.getElementById('addIsStaff').checked;
    const isActive = document.getElementById('addIsActive').checked;
    
    if (!username || !password) {
        notify.warning('Missing fields', 'Username and password are required');
        return;
    }
    
    $.post(appUrls.manageUsers, {
        action: 'add',
        username: username,
        email: email,
        password: password,
        is_staff: isStaff,
        is_active: isActive,
        csrfmiddlewaretoken: csrfToken
    }, function(response) {
        if (response.success) {
            notify.success('User created', response.message);
            setTimeout(() => location.reload(), 900);
        } else {
            notify.error('Could not create user', response.error);
        }
    }).fail(function() {
        notify.error('Network error', 'Please try again.');
    });
}

function submitEditUser() {
    const userId = document.getElementById('editUserId').value;
    const email = document.getElementById('editEmail').value.trim();
    const newPassword = document.getElementById('editNewPassword').value;
    const isStaff = document.getElementById('editIsStaff').checked;
    const isActive = document.getElementById('editIsActive').checked;
    
    $.post(appUrls.manageUsers, {
        action: 'edit',
        user_id: userId,
        email: email,
        new_password: newPassword,
        is_staff: isStaff,
        is_active: isActive,
        csrfmiddlewaretoken: csrfToken
    }, function(response) {
        if (response.success) {
            notify.success('User updated', response.message);
            setTimeout(() => location.reload(), 900);
        } else {
            notify.error('Could not update user', response.error);
        }
    }).fail(function() {
        notify.error('Network error', 'Please try again.');
    });
}

function deleteUser(userId, username) {
    confirmAction({
        title: 'Delete user "' + username + '"?',
        text: 'This action cannot be undone.',
        confirmText: 'Yes, delete user',
    }).then(function (ok) {
        if (!ok) return;
        $.post(appUrls.manageUsers, {
            action: 'delete',
            user_id: userId,
            csrfmiddlewaretoken: csrfToken
        }, function(response) {
            if (response.success) {
                notify.success('User deleted', response.message);
                setTimeout(() => location.reload(), 900);
            } else {
                notify.error('Could not delete user', response.error);
            }
        }).fail(function() {
            notify.error('Network error', 'Please try again.');
        });
    });
}

function approveUser(userId, username) {
    $.post(appUrls.manageUsers, {
        action: 'approve',
        user_id: userId,
        csrfmiddlewaretoken: csrfToken
    }, function(response) {
        if (response.success) {
            notify.success('User approved', response.message);
            // Inline update — no page reload. Remove the pending chip,
            // bump the counters, and flip the row's status badge.
            const chip = document.querySelector(`.pending-chip [data-user-id="${userId}"]`);
            const chipCard = chip ? chip.closest('.pending-chip') : null;
            if (chipCard) chipCard.remove();
            const stat = document.querySelector('#approve .stat-warning .stat-value');
            if (stat) stat.textContent = Math.max(parseInt(stat.textContent, 10) - 1, 0);
            const row = document.querySelector(`#usersTableBody tr[data-user-id="${userId}"]`);
            if (row) {
                const statusCell = row.cells[3];
                if (statusCell) {
                    statusCell.innerHTML = '<span class="badge badge-success">Active</span>';
                }
            }
            hideEmptyPendingNotice();
        } else {
            notify.error('Could not approve user', response.error);
        }
    }).fail(function() {
        notify.error('Network error', 'Please try again.');
    });
}

// The approve tab polls for new registrations so the admin sees them
// without refreshing. Polling runs only while the tab is visible.
(function () {
    const approveTab = document.getElementById('approve');
    if (!approveTab) return;

    const POLL_MS = 4000;
    const list = approveTab.querySelector('.pending-list');
    if (!list) {
        // No pending users were server-rendered; create the containers so
        // live updates still have somewhere to land.
        const section = approveTab.querySelector('.card-body');
        if (!section) return;
        const wrap = document.createElement('div');
        wrap.className = 'pending-approvals';
        wrap.innerHTML = '<h4 class="pending-approvals-title"><i class="fas fa-exclamation-triangle"></i> Pending Approvals (<span id="pendingApprovalsCount">0</span>)</h4><div class="pending-list"></div>';
        const tableWrap = section.querySelector('.table-container');
        section.insertBefore(wrap, tableWrap || null);
    }

    let timer = null;
    let seenIds = null;   // null = first poll seeds silently
    let lastSignature = null;   // skip DOM rewrites while nothing changed

    function escapeHtml(s) {
        const div = document.createElement('div');
        div.textContent = s == null ? '' : String(s);
        return div.innerHTML;
    }

    function chipHtml(user) {
        return `
        <div class="pending-chip">
            <div class="user-avatar user-avatar-sm user-avatar-pending">
                ${escapeHtml(user.username.slice(0, 2).toUpperCase())}
            </div>
            <div>
                <div class="user-name">${escapeHtml(user.username)}</div>
                <div class="form-hint">${escapeHtml(user.email || '-')}</div>
            </div>
            <button type="button" class="btn btn-success btn-sm" data-action="approve-user"
                    data-user-id="${user.id}" data-user-name="${escapeHtml(user.username)}">
                <i class="fas fa-check"></i> Approve
            </button>
        </div>`;
    }

    function refresh(data) {
        const wrap = approveTab.querySelector('.pending-approvals');
        const list = approveTab.querySelector('.pending-list');
        const stat = approveTab.querySelector('.stat-warning .stat-value');
        const active = approveTab.querySelector('.stat-success .stat-value');
        const total = approveTab.querySelector('.stat-info .stat-value');
        const title = approveTab.querySelector('.pending-approvals-title');

        if (stat) stat.textContent = data.count;
        if (active) active.textContent = data.active_users;
        if (total) total.textContent = data.total_users;

        if (data.count === 0) {
            if (wrap) wrap.style.display = 'none';
            if (list) list.innerHTML = '';
        } else {
            if (wrap) wrap.style.display = '';
            if (title) {
                const t = title.querySelector('i');
                title.innerHTML = '';
                if (t) title.appendChild(t);
                title.appendChild(document.createTextNode(` Pending Approvals (${data.count})`));
            }
            if (list) list.innerHTML = data.pending.map(chipHtml).join('');
        }
    }

    async function poll() {
        if (document.hidden || !approveTab.classList.contains('active')) return;
        try {
            const res = await fetch(appUrls.pendingUsers, {
                headers: { 'X-Requested-With': 'XMLHttpRequest' },
                cache: 'no-store',
            });
            if (!res.ok) return;
            const data = await res.json();
            if (!data.success) return;

            // Rewrite the chip list only when the pending set actually
            // changed — an unchanged re-render would wipe Approve buttons
            // mid-click every 4 seconds.
            const signature = data.pending.map(u => u.id).join(',');
            if (signature !== lastSignature) {
                refresh(data);
                lastSignature = signature;
            }

            if (seenIds !== null) {
                const freshIds = data.pending.map(u => u.id).filter(id => !seenIds.has(id));
                if (freshIds.length && window.chime) window.chime.ring();
            }
            seenIds = new Set(data.pending.map(u => u.id));
        } catch (err) {
            /* transient — next poll retries */
        }
    }

    timer = setInterval(poll, POLL_MS);
})();

function hideEmptyPendingNotice() {
    // After the last chip is approved inline, collapse the empty section.
    const wrap = document.querySelector('#approve .pending-approvals');
    if (wrap && !wrap.querySelector('.pending-chip')) {
        wrap.style.display = 'none';
        const stat = document.querySelector('#approve .stat-warning .stat-value');
        if (stat) stat.textContent = '0';
    }
}

// ==================== STUDENT MANAGEMENT FUNCTIONS ====================
// Setup college-program relationship for Add Student modal
document.addEventListener('DOMContentLoaded', () => {
    const addStudentCollege = document.getElementById('addStudentCollege');
    if (addStudentCollege) {
        addStudentCollege.addEventListener('change', () => {
            updateProgramDropdown('addStudentCollege', 'addStudentProgram');
        });
    }
    
    const editStudentCollege = document.getElementById('editStudentCollege');
    if (editStudentCollege) {
        editStudentCollege.addEventListener('change', () => {
            updateProgramDropdown('editStudentCollege', 'editStudentProgram');
        });
    }

    // Major depends on the selected program
    const addStudentProgram = document.getElementById('addStudentProgram');
    if (addStudentProgram) {
        addStudentProgram.addEventListener('change', () => {
            updateMajorDropdown(addStudentProgram.value, 'addStudentMajor');
        });
    }

    const editStudentProgram = document.getElementById('editStudentProgram');
    if (editStudentProgram) {
        editStudentProgram.addEventListener('change', () => {
            updateMajorDropdown(editStudentProgram.value, 'editStudentMajor');
        });
    }
});

// Load majors for a program into a major dropdown
async function updateMajorDropdown(programCode, majorSelectId, selectedMajor) {
    const majorSelect = document.getElementById(majorSelectId);
    if (!majorSelect) return;

    majorSelect.innerHTML = '<option value="">-- No Major --</option>';

    if (!programCode) return;

    try {
        const response = await fetch(appUrls.getMajors.replace('TEMPLATE', encodeURIComponent(programCode)));
        const data = await response.json();
        if (data.success && data.majors) {
            data.majors.forEach(major => {
                const option = document.createElement('option');
                option.value = major.name;
                option.textContent = major.code + ' - ' + major.name;
                majorSelect.appendChild(option);
            });
        }
    } catch (err) {
        console.error('Failed to load majors:', err);
    }

    if (selectedMajor) {
        const match = Array.from(majorSelect.options).find(opt => opt.value === selectedMajor || opt.textContent === selectedMajor);
        if (match) majorSelect.value = match.value;
    }
}

function submitAddStudent() {
    const studentId = document.getElementById('addStudentId').value.trim();
    const studentName = document.getElementById('addStudentName').value.trim();
    const gender = document.getElementById('addStudentGender').value;
    const college = document.getElementById('addStudentCollege').value;
    const program = document.getElementById('addStudentProgram').value;
    const year = document.getElementById('addStudentYear').value;
    const major = document.getElementById('addStudentMajor').value.trim();
    
    // Validation
    if (!studentId || !studentName || !gender || !college || !program || !year) {
        notify.warning('Missing fields', 'Please fill in all required fields');
        return;
    }
    
    // Submit via AJAX
    $.post(appUrls.addStudent, {
        student_id: studentId,
        name: studentName,
        sex: gender,
        college: college,
        program: program,
        year: year,
        major: major,
        csrfmiddlewaretoken: csrfToken
    }, function(response) {
        if (response.success) {
            notify.success('Student added', response.message || 'The student was added successfully.');
            if (response.qr_page_url) {
                // Show the new student's QR code right away (auto-downloads once).
                setTimeout(() => { window.location.href = response.qr_page_url; }, 900);
            } else {
                setTimeout(() => location.reload(), 900);
            }
        } else {
            notify.error('Could not add student', response.error || 'Failed to add student');
        }
    }).fail(function(xhr) {
        if (xhr.responseJSON && xhr.responseJSON.error) {
            notify.error('Could not add student', xhr.responseJSON.error);
        } else {
            notify.error('Network error', 'Please try again.');
        }
    });
}

function submitUploadStudents(event) {
    const fileInput = document.getElementById('studentFile');
    const file = fileInput.files[0];
    const allowedExtensions = ['.pdf', '.csv', '.xlsx', '.xlsm'];
    const allowedTypes = [
        'application/pdf',
        'text/csv',
        'application/vnd.ms-excel',
        'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    ];
    
    if (!file) {
        notify.warning('No file selected', 'Please select a file');
        return;
    }

    const extension = file.name.slice(file.name.lastIndexOf('.')).toLowerCase();
    if (!allowedExtensions.includes(extension) && !allowedTypes.includes(file.type)) {
        notify.warning('Unsupported file', 'Please select a PDF, CSV, or Excel (.xlsx) file');
        return;
    }
    
    if (file.size > 10 * 1024 * 1024) {
        notify.warning('File too large', 'File size must be less than 10MB');
        return;
    }
    
    const formData = new FormData();
    formData.append('student_file', file);
    formData.append('csrfmiddlewaretoken', csrfToken);

    const allowGeneratedIds = document.getElementById('allowGeneratedIds');
    if (allowGeneratedIds && allowGeneratedIds.checked) {
        formData.append('allow_generated_ids', 'on');
    }
    
    const submitBtn = event.target;
    const originalText = submitBtn.innerHTML;
    submitBtn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Processing...';
    submitBtn.disabled = true;
    
    $.ajax({
        url: appUrls.uploadPdf,
        type: 'POST',
        data: formData,
        processData: false,
        contentType: false,
        success: function(response) {
            if (response.success) {
                notify.success('Import complete', response.message || 'File uploaded and processed successfully.');
                setTimeout(() => location.reload(), 1100);
            } else {
                notify.error('Import failed', response.error || 'Failed to process file');
            }
        },
        error: function(xhr) {
            if (xhr.responseJSON && xhr.responseJSON.error) {
                notify.error('Import failed', xhr.responseJSON.error);
            } else {
                notify.error('Network error', 'Please try again.');
            }
        },
        complete: function() {
            submitBtn.innerHTML = originalText;
            submitBtn.disabled = false;
        }
    });
}

function getQrFilterParams() {
    const params = new URLSearchParams();
    const college = document.getElementById('qrCollege')?.value || '';
    const program = document.getElementById('qrProgram')?.value || '';
    const year = document.getElementById('qrYear')?.value || '';
    const major = document.getElementById('qrMajor')?.value || '';
    const gender = document.getElementById('qrGender')?.value || '';

    if (college) params.append('college', college);
    if (program) params.append('program', program);
    if (year) params.append('year', year);
    if (major) params.append('major', major);
    if (gender) params.append('gender', gender);
    return params;
}

// QR previews are streamed in batches so the modal opens instantly even for
// the whole roster. State tracks the in-flight/next batch; runId lets a fresh
// load cancel the auto-load run of a superseded filter set.
const qrBatchState = { page: 0, pageSize: 60, total: 0, loading: false, filterKey: '', runId: 0 };

function currentQrFilterKey() {
    return getQrFilterParams().toString();
}

function renderQrBatch(qrList, append) {
    const grid = document.getElementById('qrCodesGrid');
    const empty = document.getElementById('qrCodesEmpty');
    const count = document.getElementById('qrCodesCount');
    const more = document.getElementById('qrCodesMore');

    if (!grid || !empty || !count) return;

    const escapeHtml = (text) => {
        const div = document.createElement('div');
        div.textContent = text ?? '';
        return div.innerHTML;
    };

    if (!append) grid.innerHTML = '';

    if (!qrList.length && !append) {
        empty.style.display = 'block';
        count.textContent = '0 QR codes loaded';
        if (more) more.classList.add('is-hidden');

        const program = document.getElementById('qrProgram')?.value || '';
        if (program) {
            empty.textContent = `No students found for ${program}. Try "All Programs" or pick a program that exists in your student records (e.g. BSINT, BSCS).`;
        } else {
            empty.textContent = 'No students match the selected filters.';
        }
        return;
    }

    empty.style.display = 'none';
    grid.insertAdjacentHTML('beforeend', qrList.map(item => `
        <div class="qr-modal-card">
            <strong>${escapeHtml(item.name)}</strong>
            <small>${escapeHtml(item.student_id)}</small>
            <small>${escapeHtml(item.college)} · ${escapeHtml(item.program)}</small>
            <small>Year ${escapeHtml(String(item.year))} · ${escapeHtml(item.major || '-')}</small>
            <img src="data:image/png;base64,${item.qr_img}" alt="QR for ${escapeHtml(item.student_id)}">
        </div>
    `).join(''));

    const loaded = grid.querySelectorAll('.qr-modal-card').length;
    const remaining = Math.max((qrBatchState.total || loaded) - loaded, 0);
    count.textContent = `${loaded} of ${qrBatchState.total || loaded} QR code${(qrBatchState.total || loaded) === 1 ? '' : 's'} loaded`;
    if (more) {
        if (remaining > 0) {
            more.classList.remove('is-hidden');
            more.textContent = `Load ${Math.min(remaining, qrBatchState.pageSize)} more (${remaining} remaining)`;
        } else {
            more.classList.add('is-hidden');
        }
    }
}

function renderQrCodesGrid(qrList) {
    // Kept for compatibility: renders a single full list.
    qrBatchState.total = qrList.length;
    renderQrBatch(qrList, false);
}

function openQrCodesModal() {
    const college = document.getElementById('studentsCollege');
    const program = document.getElementById('studentsProgram');
    const year = document.getElementById('studentsYear');
    const qrProgram = document.getElementById('qrProgram');

    if (college) {
        document.getElementById('qrCollege').value = college.value;
    }
    updateProgramDropdown('qrCollege', 'qrProgram');

    if (program && qrProgram) {
        qrProgram.value = program.value;
        if (program.value && qrProgram.value !== program.value) {
            qrProgram.value = '';
        }
    }
    if (year) {
        document.getElementById('qrYear').value = year.value;
    }

    // Major auto-dropdown: show options only when the program has majors
    updateProgramMajorDropdown(qrProgram ? qrProgram.value : '', 'qrMajorGroup', 'qrMajor');

    document.getElementById('qrGender').value = '';
    document.getElementById('qrCodesGrid').innerHTML = '';
    document.getElementById('qrCodesEmpty').style.display = 'none';
    document.getElementById('qrCodesLoading').style.display = 'none';
    document.getElementById('qrCodesCount').textContent = 'Load QR codes using the filters above.';

    openModal('qrCodesModal');
    loadQrCodes();
}

function qrModalOpen() {
    const modal = document.getElementById('qrCodesModal');
    return !!(modal && modal.classList.contains('active'));
}

// Keep fetching the next batch while rows remain, the modal is open and the
// run has not been superseded -- a long roster should load fully on its own
// instead of making the user click "Load more" over and over. Batches stay
// small so the page paints between requests, and the button below remains as
// a fallback if a request fails mid-run.
function scheduleQrAutoLoad(runId) {
    const grid = document.getElementById('qrCodesGrid');
    if (!grid) return;
    const loaded = grid.querySelectorAll('.qr-modal-card').length;
    const remaining = Math.max((qrBatchState.total || 0) - loaded, 0);
    if (remaining <= 0 || qrBatchState.loading) return;
    if (runId !== qrBatchState.runId || !qrModalOpen()) return;
    setTimeout(() => {
        if (runId !== qrBatchState.runId || !qrModalOpen() || qrBatchState.loading) return;
        loadQrCodes({ append: true });
    }, 30);
}

function loadQrCodes(options) {
    const opts = options || {};
    const loading = document.getElementById('qrCodesLoading');
    const loadBtn = document.getElementById('qrLoadBtn');
    const moreBtn = document.getElementById('qrCodesMore');

    if (qrBatchState.loading) return;

    // Fresh load (vs "load more") resets the batch cursor and starts a new
    // auto-load run; any in-flight run for older filters is dropped via runId.
    if (!opts.append) {
        qrBatchState.page = 0;
        qrBatchState.filterKey = currentQrFilterKey();
        qrBatchState.runId += 1;
    }
    const runId = qrBatchState.runId;

    const nextPage = qrBatchState.page + 1;
    const params = new URLSearchParams(qrBatchState.filterKey);
    params.set('page', String(nextPage));
    params.set('page_size', String(qrBatchState.pageSize));

    qrBatchState.loading = true;
    loading.style.display = 'block';
    document.getElementById('qrCodesEmpty').style.display = 'none';
    if (loadBtn) loadBtn.disabled = true;
    if (moreBtn) moreBtn.disabled = true;

    fetch(appUrls.ajaxQrCodes + "?" + params.toString(), {
        headers: { 'X-Requested-With': 'XMLHttpRequest' }
    })
        .then(response => response.json())
        .then(data => {
            if (runId !== qrBatchState.runId) return;  // superseded run
            if (!data.success) {
                throw new Error(data.error || 'Failed to load QR codes');
            }
            qrBatchState.page = nextPage;
            qrBatchState.total = data.total_count ?? qrBatchState.total;
            renderQrBatch(data.qr_list || [], !!opts.append);
            scheduleQrAutoLoad(runId);
        })
        .catch(error => {
            if (runId !== qrBatchState.runId) return;
            notify.error('QR codes unavailable', error.message || 'Could not load QR codes.');
            if (!opts.append) renderQrCodesGrid([]);
        })
        .finally(() => {
            qrBatchState.loading = false;
            loading.style.display = 'none';
            if (loadBtn) loadBtn.disabled = false;
            if (moreBtn) moreBtn.disabled = false;
        });
}

function exportQrCodes(format) {
    const params = getQrFilterParams();
    params.append('format', format);

    const pdfBtn = document.getElementById('qrExportPdfBtn');
    const zipBtn = document.getElementById('qrExportZipBtn');
    const activeBtn = format === 'zip' ? zipBtn : pdfBtn;
    const originalText = activeBtn.innerHTML;

    activeBtn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Exporting...';
    activeBtn.disabled = true;
    if (pdfBtn && pdfBtn !== activeBtn) pdfBtn.disabled = true;
    if (zipBtn && zipBtn !== activeBtn) zipBtn.disabled = true;

    fetch(appUrls.exportQrCodes + "?" + params.toString())
        .then(response => {
            if (!response.ok) {
                return response.text().then(text => {
                    throw new Error(text || 'Export failed');
                });
            }
            const disposition = response.headers.get('Content-Disposition') || '';
            const match = disposition.match(/filename="?([^"]+)"?/);
            const filename = match ? match[1] : (`qrcodes.${format === 'zip' ? 'zip' : 'pdf'}`);
            return response.blob().then(blob => ({ blob, filename }));
        })
        .then(({ blob, filename }) => {
            const url = URL.createObjectURL(blob);
            const link = document.createElement('a');
            link.href = url;
            link.download = filename;
            document.body.appendChild(link);
            link.click();
            document.body.removeChild(link);
            URL.revokeObjectURL(url);
        })
        .catch(error => {
            notify.error('Export failed', error.message || 'Could not export QR codes.');
        })
        .finally(() => {
            activeBtn.innerHTML = originalText;
            activeBtn.disabled = false;
            if (pdfBtn) pdfBtn.disabled = false;
            if (zipBtn) zipBtn.disabled = false;
        });
}

function openExportStudentsModal() {
    const college = document.getElementById('studentsCollege');
    const program = document.getElementById('studentsProgram');
    const year = document.getElementById('studentsYear');

    if (college) {
        document.getElementById('exportStudentsCollege').value = college.value;
        updateProgramDropdown('exportStudentsCollege', 'exportStudentsProgram');
    }
    if (program) {
        document.getElementById('exportStudentsProgram').value = program.value;
    }
    if (year) {
        document.getElementById('exportStudentsYear').value = year.value;
    }

    // Major auto-dropdown for the inherited program selection
    const exportStudentsProgram = document.getElementById('exportStudentsProgram');
    updateProgramMajorDropdown(
        exportStudentsProgram ? exportStudentsProgram.value : '',
        'exportStudentsMajorGroup',
        'exportStudentsMajor'
    );

    openModal('exportStudentsModal');
}

function applyExportStudents() {
    const params = new URLSearchParams();
    const college = document.getElementById('exportStudentsCollege')?.value || '';
    const program = document.getElementById('exportStudentsProgram')?.value || '';
    const year = document.getElementById('exportStudentsYear')?.value || '';
    const major = document.getElementById('exportStudentsMajor')?.value || '';
    const gender = document.getElementById('exportStudentsGender')?.value || '';
    const format = document.getElementById('exportStudentsFormat')?.value || 'csv';
    const includeHeaders = document.getElementById('exportStudentsIncludeHeaders')?.checked;

    if (college) params.append('college', college);
    if (program) params.append('program', program);
    if (year) params.append('year', year);
    if (major) params.append('major', major);
    if (gender) params.append('gender', gender);
    params.append('format', format);
    params.append('include_headers', includeHeaders ? '1' : '0');

    const submitBtn = document.getElementById('exportStudentsSubmitBtn');
    const originalText = submitBtn.innerHTML;
    submitBtn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Exporting...';
    submitBtn.disabled = true;

    fetch(appUrls.exportStudents + "?" + params.toString())
        .then(response => {
            if (!response.ok) {
                return response.text().then(text => {
                    throw new Error(text || 'Export failed');
                });
            }
            const disposition = response.headers.get('Content-Disposition') || '';
            const match = disposition.match(/filename="?([^"]+)"?/);
            const filename = match ? match[1] : ('students.' + format);
            return response.blob().then(blob => ({ blob, filename }));
        })
        .then(({ blob, filename }) => {
            const url = URL.createObjectURL(blob);
            const link = document.createElement('a');
            link.href = url;
            link.download = filename;
            document.body.appendChild(link);
            link.click();
            document.body.removeChild(link);
            URL.revokeObjectURL(url);
            closeModal('exportStudentsModal');
        })
        .catch(error => {
            notify.error('Export failed', error.message || 'Could not export students.');
        })
        .finally(() => {
            submitBtn.innerHTML = originalText;
            submitBtn.disabled = false;
        });
}

function openEditStudentModal(id, studentId, name, sex, college, program, year, major) {
    document.getElementById('editStudentDbId').value = id;
    document.getElementById('editStudentId').value = studentId;
    document.getElementById('editStudentName').value = name;
    document.getElementById('editStudentGender').value = sex;
    document.getElementById('editStudentCollege').value = college;

    // Update program dropdown and set value
    updateProgramDropdown('editStudentCollege', 'editStudentProgram');
    // Select the stored program, or prepend it when the college has changed
    // in the DB and it is no longer among the options (keeps the value
    // visible instead of silently resetting to "-- Select Program --").
    const programSelect = document.getElementById('editStudentProgram');
    const setProgram = () => {
        if (program && programSelect.value !== program &&
            !Array.from(programSelect.options).some(o => o.value === program)) {
            const opt = new Option(program, program, true, true);
            programSelect.add(opt, programSelect.options[1] || null);
        }
        programSelect.value = program;
    };
    if (programSelect.options.length > 1) {
        setProgram();
    } else {
        setTimeout(setProgram, 100);
    }

    document.getElementById('editStudentYear').value = year;
    updateMajorDropdown(program, 'editStudentMajor', major);

    document.getElementById('editStudentModal').classList.add('active');
}

function submitEditStudent() {
    const id = document.getElementById('editStudentDbId').value;
    const studentId = document.getElementById('editStudentId').value.trim();
    const name = document.getElementById('editStudentName').value.trim();
    const sex = document.getElementById('editStudentGender').value;
    const college = document.getElementById('editStudentCollege').value;
    const program = document.getElementById('editStudentProgram').value;
    const year = document.getElementById('editStudentYear').value;
    const major = document.getElementById('editStudentMajor').value.trim();

    if (!studentId || !name || !sex || !college || !program || !year) {
        notify.warning('Missing fields', 'Please fill in all required fields');
        return;
    }

    $.post(appUrls.editStudent.replace('/0/', '/' + id + '/'), {
        student_id: studentId,
        name: name,
        sex: sex,
        college: college,
        program: program,
        year: year,
        major: major,
        csrfmiddlewaretoken: csrfToken
    }, function(response) {
        if (response.success) {
            notify.success('Student updated', response.message || 'The student was updated successfully.');
            setTimeout(() => location.reload(), 900);
        } else {
            notify.error('Could not update student', response.error || 'Failed to update student');
        }
    }).fail(function(xhr) {
        if (xhr.responseJSON && xhr.responseJSON.error) {
            notify.error('Could not update student', xhr.responseJSON.error);
        } else {
            notify.error('Network error', 'Please try again.');
        }
    });
}
// ==================== HASH-BASED TAB ACTIVATION ====================
// Lets URLs like /qrapp/admin_dashboard/#students open the Students tab
// directly on page load. Also handles back/forward navigation and any
// future tab links.

(function () {
    'use strict';

    function activateTabFromHash() {
        const hash = (window.location.hash || '').replace('#', '').trim();
        if (!hash) return;

        const targetTab = document.getElementById(hash);
        if (!targetTab || !targetTab.classList.contains('tabs')) return;

        // Hide all tab panels, show the one matching the hash
        document.querySelectorAll('.tabs').forEach(t => t.classList.remove('active'));
        targetTab.classList.add('active');

        // Sync any nav buttons/links that carry data-tab="..."
        document.querySelectorAll('[data-tab]').forEach(btn => {
            btn.classList.toggle('active', btn.dataset.tab === hash);
        });

        // If the tab exposes a nav link with a matching href, sync it too
        document.querySelectorAll('a[href^="#"]').forEach(a => {
            a.classList.toggle('active', a.getAttribute('href') === '#' + hash);
        });
    }

    document.addEventListener('DOMContentLoaded', activateTabFromHash);
    window.addEventListener('hashchange', activateTabFromHash);
})();