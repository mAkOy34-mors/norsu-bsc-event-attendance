// College stats injected by the template via json_script. Student counts
// are rendered server-side directly into the table (so a college with zero
// students always shows "0 students" instead of getting stuck on
// "Loading..."); this dict is only used here to pre-fill the
// delete-confirmation warning.
const collegeStats = JSON.parse(document.getElementById('college-stats').textContent);

document.addEventListener('DOMContentLoaded', () => {
    // Setup delete button handlers
    document.querySelectorAll('.delete-btn').forEach(btn => {
        btn.addEventListener('click', function() {
            const id = this.getAttribute('data-college-id');
            const code = this.getAttribute('data-college-code');
            const count = collegeStats[code] || 0;
            confirmDelete(id, code, count);
        });
    });
});

function openAddModal() {
    document.getElementById('addCollegeModal').classList.add('active');
}

function openEditModal(id, code, name, isActive) {
    document.getElementById('editCollegeId').value = id;
    document.getElementById('editCode').value = code;
    document.getElementById('editName').value = name;
    document.getElementById('editIsActive').checked = isActive;
    document.getElementById('editCollegeModal').classList.add('active');
}

function confirmDelete(id, code, studentCount) {
    document.getElementById('deleteCollegeId').value = id;
    const message = studentCount > 0
        ? 'Cannot delete <strong>' + code + '</strong> because <strong>' + studentCount + ' students</strong> are still enrolled in this college. Please reassign them first.'
        : 'Are you sure you want to delete college <strong>' + code + '</strong>?';
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

    document.getElementById('deleteCollegeModal').classList.add('active');
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