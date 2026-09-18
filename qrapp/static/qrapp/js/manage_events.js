const eventStats = JSON.parse(document.getElementById('event-stats').textContent);

function openModal(id) {
    document.getElementById(id).classList.add('active');
}
function closeModal(id) {
    document.getElementById(id).classList.remove('active');
}
function openAddModal() {
    openModal('addEventModal');
}
function openEditModal(id, title, description, date, start, end, location, isActive, isCurrent, pictureUrl) {
    document.getElementById('editEventId').value = id;
    document.getElementById('editTitle').value = title;
    document.getElementById('editDescription').value = description || '';
    document.getElementById('editDate').value = date;
    document.getElementById('editStart').value = start || '';
    document.getElementById('editEnd').value = end || '';
    document.getElementById('editLocation').value = location || '';
    document.getElementById('editIsActive').checked = !!isActive;
    document.getElementById('editIsCurrent').checked = !!isCurrent;
    document.getElementById('editPicture').value = '';
    document.getElementById('editClearPicture').checked = false;
    const previewWrap = document.getElementById('editPicturePreviewWrap');
    const preview = document.getElementById('editPicturePreview');
    if (pictureUrl) {
        preview.src = pictureUrl;
        previewWrap.style.display = 'block';
    } else {
        preview.src = '';
        previewWrap.style.display = 'none';
    }
    openModal('editEventModal');
}
function openDeleteModal(id, title) {
    document.getElementById('deleteEventId').value = id;
    document.getElementById('deleteEventTitle').textContent = title;
    openModal('deleteEventModal');
}

document.querySelectorAll('.scan-count').forEach(el => {
    const id = el.dataset.eventId;
    el.textContent = eventStats[id] ?? 0;
});

window.addEventListener('click', (e) => {
    ['addEventModal', 'editEventModal', 'deleteEventModal'].forEach(id => {
        const modal = document.getElementById(id);
        if (e.target === modal) closeModal(id);
    });
});
