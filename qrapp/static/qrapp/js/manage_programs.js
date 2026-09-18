// Program/major stats injected by the template via json_script.
// Student counts themselves are rendered server-side directly into the
// table (so a program/major with zero students always shows "0 students"
// instead of getting stuck on "Loading..."); these dicts are only used
// here to pre-fill the delete-confirmation warnings.
const programStats = JSON.parse(document.getElementById('program-stats').textContent);
const majorStats = JSON.parse(document.getElementById('major-stats').textContent);

document.addEventListener('DOMContentLoaded', () => {
    // Setup delete button handlers (programs)
    document.querySelectorAll('.delete-btn').forEach(btn => {
        btn.addEventListener('click', function() {
            const id = this.getAttribute('data-program-id');
            const code = this.getAttribute('data-program-code');
            const count = programStats[code] || 0;
            confirmDelete(id, code, count);
        });
    });

    // Setup delete button handlers (majors)
    document.querySelectorAll('.delete-major-btn').forEach(btn => {
        btn.addEventListener('click', function() {
            const id = this.getAttribute('data-major-id');
            const code = this.getAttribute('data-major-code');
            const count = majorStats[code] || 0;
            confirmDeleteMajor(id, code, count);
        });
    });
});

function openAddModal() {
    document.getElementById('addProgramModal').classList.add('active');
}

function openEditModal(id, code, name, isActive) {
    document.getElementById('editProgramId').value = id;
    document.getElementById('editCode').value = code;
    document.getElementById('editName').value = name;
    document.getElementById('editIsActive').checked = isActive;
    document.getElementById('editProgramModal').classList.add('active');
}

function confirmDelete(id, code, studentCount) {
    document.getElementById('deleteProgramId').value = id;
    const message = studentCount > 0
        ? 'Cannot delete <strong>' + code + '</strong> because <strong>' + studentCount + ' students</strong> are enrolled in this program. Please reassign them first.'
        : 'Are you sure you want to delete program <strong>' + code + '</strong>?';
    document.getElementById('deleteMessage').innerHTML = message;

    // Disable delete button if students exist
    const deleteBtn = document.getElementById('confirmDeleteBtn');
    if (studentCount > 0) {
        deleteBtn.disabled = true;
        deleteBtn.style.opacity = '0.5';
        deleteBtn.style.cursor = 'not-allowed';
    } else {
        deleteBtn.disabled = false;
        deleteBtn.style.opacity = '1';
        deleteBtn.style.cursor = 'pointer';
    }

    document.getElementById('deleteProgramModal').classList.add('active');
}

function openAddMajorModal(programId, programCode) {
    document.getElementById('addMajorProgramId').value = programId;
    document.getElementById('addMajorProgramLabel').textContent = 'Adding a major under ' + programCode;
    document.getElementById('addMajorModal').classList.add('active');
}

function openEditMajorModal(id, code, name, isActive) {
    document.getElementById('editMajorId').value = id;
    document.getElementById('editMajorCode').value = code;
    document.getElementById('editMajorName').value = name;
    document.getElementById('editMajorIsActive').checked = isActive;
    document.getElementById('editMajorModal').classList.add('active');
}

function confirmDeleteMajor(id, code, studentCount) {
    document.getElementById('deleteMajorId').value = id;
    const message = studentCount > 0
        ? 'Cannot delete <strong>' + code + '</strong> because <strong>' + studentCount + ' students</strong> have this major. Please reassign them first.'
        : 'Are you sure you want to delete major <strong>' + code + '</strong>?';
    document.getElementById('deleteMajorMessage').innerHTML = message;

    const deleteBtn = document.getElementById('confirmDeleteMajorBtn');
    if (studentCount > 0) {
        deleteBtn.disabled = true;
        deleteBtn.style.opacity = '0.5';
        deleteBtn.style.cursor = 'not-allowed';
    } else {
        deleteBtn.disabled = false;
        deleteBtn.style.opacity = '1';
        deleteBtn.style.cursor = 'pointer';
    }

    document.getElementById('deleteMajorModal').classList.add('active');
}

function closeModal(modalId) {
    document.getElementById(modalId).classList.remove('active');
}

// Close modal on outside click
window.addEventListener('click', (e) => {
    if (e.target.classList.contains('modal')) {
        e.target.classList.remove('active');
    }
});

// Auto-hide alerts after 5 seconds
document.addEventListener('DOMContentLoaded', () => {
    const alerts = document.querySelectorAll('.alert');
    alerts.forEach(alert => {
        setTimeout(() => {
            alert.style.transition = 'opacity 0.3s';
            alert.style.opacity = '0';
            setTimeout(() => alert.remove(), 300);
        }, 5000);
    });
});