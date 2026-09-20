/* scanner.js
 * QR Scanner logic with camera cleanup, deferred library loading,
 * and fast navigation handling.
 */

(function () {
  'use strict';

  // ------------------------------------------------------------------
  // Configuration
  // ------------------------------------------------------------------
  const configEl = document.getElementById('scanner-config');
  const CONFIG = configEl ? JSON.parse(configEl.textContent) : {};

  const state = {
    html5QrCode: null,
    currentCameraId: null,
    cameras: [],
    mode: 'camera', // 'camera' | 'device'
    isScanning: false,
    processing: false, // a scan request is in flight; further decodes wait
    lastScanText: null,
    lastScanTime: 0,
    SCAN_COOLDOWN_MS: 2500,
    eventId: null,
    eventDate: null, // YYYY-MM-DD of the selected event, for the day check
    videoTrack: null,
  };

  // ------------------------------------------------------------------
  // DOM references
  // ------------------------------------------------------------------
  const $ = (sel) => document.querySelector(sel);

  const els = {
    reader: $('#reader'),
    result: $('#result'),
    cameraSelect: $('#cameraSelect'),
    switchCameraBtn: $('#switchCameraBtn'),
    modeCameraBtn: $('#modeCameraBtn'),
    modeDeviceBtn: $('#modeDeviceBtn'),
    cameraControls: $('#cameraControls'),
    eventControls: $('#eventControls'),
    deviceScannerPanel: $('#deviceScannerPanel'),
    deviceScanInput: $('#deviceScanInput'),
    startDeviceScanBtn: $('#startDeviceScanBtn'),
    scannerHintText: $('#scannerHintText'),
    cameraError: $('#cameraError'),
    cameraErrorMsg: $('#cameraErrorMsg'),
    retryCameraBtn: $('#retryCameraBtn'),
    eventSelect: $('#eventSelect'),
    eventPreviewTitle: $('#eventPreviewTitle'),
    eventPreviewSub: $('#eventPreviewSub'),
    eventPreviewDesc: $('#eventPreviewDesc'),
    eventPicture: $('#eventPicture'),
    eventPicturePlaceholder: $('#eventPicturePlaceholder'),
    notificationCenter: $('#notificationCenter'),
    dashboardBtn: $('#dashboardBtn'),
    logoutBtn: $('#logoutBtn'),
    statusIndicator: $('#scannerStatus'),
    statusText: $('#scannerStatusText'),
    manualInput: $('#manualInput'),
    manualStatus: $('#manualStatus'),
    manualTime: $('#manualTime'),
    manualSubmit: $('#manualSubmit'),
    addModal: $('#addStudentModal'),
    addForm: $('#addStudentForm'),
    addId: $('#addStudentId'),
    addName: $('#addStudentName'),
    addSex: $('#addStudentSex'),
    addYear: $('#addStudentYear'),
    addCollege: $('#addStudentCollege'),
    addProgram: $('#addStudentProgram'),
    addMajorGroup: $('#addStudentMajorGroup'),
    addMajor: $('#addStudentMajor'),
    addError: $('#addStudentError'),
    addSubmit: $('#addStudentSubmit'),
    addClose: $('#addStudentClose'),
    addCancel: $('#addStudentCancel'),
    openRegisterBtn: $('#openRegisterBtn'),
  };

  // ------------------------------------------------------------------
  // Status line ("Scanning…" while the camera hunts / a scan is in flight)
  // ------------------------------------------------------------------
  function setStatusText(text, scanning) {
    if (els.statusText) els.statusText.textContent = text;
    if (els.statusIndicator) {
      els.statusIndicator.classList.toggle('is-scanning', !!scanning);
    }
  }

  function idleStatusText() {
    if (state.mode === 'camera' && state.isScanning) return 'Scanning…';
    if (state.mode === 'device') return 'Device scanner ready';
    return 'Scanner ready';
  }

  // ------------------------------------------------------------------
  // Utilities
  // ------------------------------------------------------------------
  function setResult(message, type) {
    if (!els.result) return;
    els.result.textContent = message;
    els.result.className = '';
    if (type) els.result.classList.add(type);
  }

  function showNotification(message, type = 'info', title = '') {
    if (!els.notificationCenter) return;

    const icons = {
      in: 'fa-check-circle',
      out: 'fa-sign-out-alt',
      error: 'fa-exclamation-circle',
      warning: 'fa-exclamation-triangle',
      info: 'fa-info-circle',
    };

    const titles = {
      in: title || 'Check-in',
      out: title || 'Check-out',
      error: title || 'Error',
      warning: title || 'Warning',
      info: title || 'Info',
    };

    const notification = document.createElement('div');
    notification.className = `notification ${type}`;
    notification.innerHTML = `
      <div class="notification-icon"><i class="fas ${icons[type] || 'fa-info-circle'}"></i></div>
      <div class="notification-content">
        <div class="notification-title">${titles[type] || 'Notice'}</div>
        <div class="notification-message">${message}</div>
        <div class="notification-time">
          <i class="far fa-clock"></i>
          <span>${new Date().toLocaleTimeString()}</span>
        </div>
      </div>
      <button class="notification-close" aria-label="Close">
        <i class="fas fa-times"></i>
      </button>
    `;

    const closeBtn = notification.querySelector('.notification-close');
    closeBtn.addEventListener('click', () => removeNotification(notification));

    els.notificationCenter.appendChild(notification);

    // Auto-dismiss after 5s
    setTimeout(() => removeNotification(notification), 5000);
  }

  function removeNotification(el) {
    if (!el || !el.parentNode) return;
    el.classList.add('hiding');
    setTimeout(() => {
      if (el.parentNode) el.parentNode.removeChild(el);
    }, 300);
  }

  // ------------------------------------------------------------------
  // Camera cleanup — crucial for fast navigation
  // ------------------------------------------------------------------
  function stopCameraStream() {
    // Stop html5-qrcode instance
    if (state.html5QrCode) {
      try {
        const stopPromise = state.html5QrCode.stop();
        if (stopPromise && typeof stopPromise.catch === 'function') {
          stopPromise.catch(() => {});
        }
      } catch (e) {
        // ignore
      }
      try {
        const clearPromise = state.html5QrCode.clear();
        if (clearPromise && typeof clearPromise.catch === 'function') {
          clearPromise.catch(() => {});
        }
      } catch (e) {
        // ignore
      }
      state.html5QrCode = null;
    }

    // Stop any lingering MediaStream tracks on the video element
    const videos = document.querySelectorAll('#reader video');
    videos.forEach((video) => {
      if (video.srcObject) {
        try {
          video.srcObject.getTracks().forEach((track) => track.stop());
        } catch (e) {
          // ignore
        }
        video.srcObject = null;
      }
    });

    // Also stop the tracked video track if we have one
    if (state.videoTrack) {
      try {
        state.videoTrack.stop();
      } catch (e) {
        // ignore
      }
      state.videoTrack = null;
    }

    state.isScanning = false;
    if (!state.processing) setStatusText('Scanner ready', false);
  }

  // ------------------------------------------------------------------
  // Deferred QR library loader
  // ------------------------------------------------------------------
  function loadQrLibrary() {
  if (window.Html5Qrcode) {
    return Promise.resolve(window.Html5Qrcode);
  }

  return Promise.reject(
    new Error('QR scanner library was not loaded.')
  );
}

  // ------------------------------------------------------------------
  // Camera scanning
  // ------------------------------------------------------------------
  // Touch devices are far more likely to be phones/tablets where picking
  // the rear camera by default matters; desktops keep the old behaviour.
  function isMobileCameraPreferred() {
    return ('ontouchstart' in window) ||
      (navigator.maxTouchPoints > 0 && window.matchMedia('(pointer: coarse)').matches);
  }

  async function startCameraScan() {
    if (state.isScanning) return;

    try {
      setStatusText('Starting camera…', true);
      const Html5Qrcode = await loadQrLibrary();
      if (!Html5Qrcode) throw new Error('QR library failed to load');

      // Populate cameras if needed
      if (!state.cameras.length) {
        const devices = await Html5Qrcode.getCameras();
        state.cameras = devices || [];
        populateCameraSelect(devices);
      }

      // Prefer the rear ("environment") camera on mobile: match the device
      // label first, then fall back to a facingMode constraint, which works
      // even when Android labels are generic (e.g. "camera 1, facing front").
      // Desktop keeps the previous behaviour (last listed camera).
      let cameraConfig = state.currentCameraId || null;
      if (!cameraConfig) {
        const rear = state.cameras.find((c) =>
          /back|rear|environment/i.test(c.label || '')
        );
        if (rear) {
          cameraConfig = rear.id;
        } else if (state.cameras.length && isMobileCameraPreferred()) {
          cameraConfig = { facingMode: 'environment' };
        } else if (state.cameras.length) {
          cameraConfig = state.cameras[state.cameras.length - 1].id;
        }
      }

      if (!cameraConfig) {
        throw new Error('No camera available');
      }

      state.html5QrCode = new Html5Qrcode('reader', {
        verbose: false,
      });

      // Scan box sizing: hand the library a FUNCTION instead of a fixed size.
      // html5-qrcode calls it with the video element's real rendered size, so
      // the square can never overflow the preview (a hard-coded box did, which
      // is what used to stretch the video and mis-align the shaded region).
      // 90% of the shorter side keeps a slim dark margin while giving the
      // operator the biggest possible aiming frame - the library only decodes
      // inside this box, so bigger also means far fewer missed scans.
      const startOpts = {
        fps: 10,
        qrbox: (viewfinderWidth, viewfinderHeight) => {
          const shorter = Math.min(viewfinderWidth, viewfinderHeight);
          // Never below the library's 50px minimum, never above the preview.
          const side = Math.max(50, Math.min(Math.floor(shorter * 0.9), shorter));
          return { width: side, height: side };
        },
      };

      try {
        await state.html5QrCode.start(
          cameraConfig,
          startOpts,
          onScanSuccess,
          onScanFailure
        );
      } catch (startErr) {
        // A facingMode request can fail (some devices report "environment"
        // but expose a single camera). Retry once with an explicit id.
        if (typeof cameraConfig === 'object' && state.cameras.length) {
          cameraConfig = state.cameras[state.cameras.length - 1].id;
          // Drop any half-created preview element from the failed attempt
          // and release whatever stream it may already hold.
          const readerEl = document.getElementById('reader');
          if (readerEl) {
            readerEl.querySelectorAll('video').forEach((v) => {
              if (v.srcObject) {
                try {
                  v.srcObject.getTracks().forEach((t) => t.stop());
                } catch (e) { /* ignore */ }
                v.srcObject = null;
              }
              v.remove();
            });
          }
          try { state.html5QrCode.clear(); } catch (e) { /* ignore */ }
          state.html5QrCode = new Html5Qrcode('reader', { verbose: false });
          await state.html5QrCode.start(
            cameraConfig,
            startOpts,
            onScanSuccess,
            onScanFailure
          );
        } else {
          throw startErr;
        }
      }

      // Track the active video track for cleanup
      const video = document.querySelector('#reader video');
      if (video && video.srcObject) {
        const tracks = video.srcObject.getVideoTracks();
        if (tracks.length) {
          state.videoTrack = tracks[0];
          // When started via facingMode, remember the concrete device so the
          // dropdown and "Switch" cycling stay in sync with reality.
          const settings = state.videoTrack.getSettings
            ? state.videoTrack.getSettings()
            : {};
          if (settings.deviceId) {
            state.currentCameraId = settings.deviceId;
            if (els.cameraSelect) {
              const exists = Array.from(els.cameraSelect.options)
                .some((o) => o.value === state.currentCameraId);
              if (exists) els.cameraSelect.value = state.currentCameraId;
            }
          }
        }
      }

      state.isScanning = true;
      if (typeof cameraConfig === 'string') {
        state.currentCameraId = cameraConfig;
      }
      hideCameraError();
      setStatusText('Scanning…', true);
    } catch (err) {
      console.error('Camera start error:', err);
      showCameraError(err.message || 'Unable to access camera');
      setStatusText('Camera unavailable', false);
    }
  }

  function populateCameraSelect(cameras) {
    if (!els.cameraSelect) return;
    els.cameraSelect.innerHTML = '';
    if (!cameras || !cameras.length) {
      els.cameraSelect.innerHTML = '<option value="">No camera found</option>';
      return;
    }
    cameras.forEach((cam, idx) => {
      const option = document.createElement('option');
      option.value = cam.id;
      option.textContent = cam.label || `Camera ${idx + 1}`;
      els.cameraSelect.appendChild(option);
    });
    if (state.currentCameraId) {
      els.cameraSelect.value = state.currentCameraId;
    }
  }

  function onScanSuccess(decodedText, decodedResult) {
    const now = Date.now();
    if (
      decodedText === state.lastScanText &&
      now - state.lastScanTime < state.SCAN_COOLDOWN_MS
    ) {
      return;
    }
    state.lastScanText = decodedText;
    state.lastScanTime = now;
    processScan(decodedText);
  }

  function onScanFailure() {
    // Silently ignore per-frame decode failures
  }

  function showCameraError(message) {
    if (!els.cameraError) return;

    // On plain HTTP from another device (e.g. http://192.168.x.x:8001),
    // browsers block getUserMedia entirely — surface an actionable message
    // instead of the raw error text.
    let friendly = message;
    if (window.isSecureContext === false) {
      const host = window.location.hostname;
      const isLocal = ['localhost', '127.0.0.1', '[::1]'].includes(host);
      if (!isLocal) {
        friendly =
          'Camera blocked: this page is not served over HTTPS. ' +
          'Open the scanner from https:// (recommended) or from this machine via http://localhost:' +
          (window.location.port || '8000') + '/, or use the "Scanner device" mode on this device.';
      }
    }

    els.cameraError.classList.remove('is-hidden');
    if (els.cameraErrorMsg) els.cameraErrorMsg.textContent = friendly;
  }

  function hideCameraError() {
    if (!els.cameraError) return;
    els.cameraError.classList.add('is-hidden');
  }

 // ------------------------------------------------------------------
// Scan processing (camera + device + manual all funnel here)
// ------------------------------------------------------------------
function processScan(rawText, overrides = {}) {
    if (!state.eventId) {
        if (els.eventSelect) {
            els.eventSelect.classList.add('event-select-required');
            els.eventSelect.focus();
        }
        showNotification(
            'Please select an event before scanning.',
            'warning',
            'Event required'
        );
        setResult('Select an event first', 'error');
        return;
    }

    // One scan at a time: while a scan is being stored, further decodes wait.
    // This keeps the success/failure feedback honest -- no second request can
    // race ahead and toggle IN/OUT before the first one is confirmed.
    if (state.processing) {
        setResult('Scanning…', 'info');
        return;
    }

    let payload;
    try {
        payload = typeof rawText === 'string' ? JSON.parse(rawText) : rawText;
    } catch (e) {
        // Treat as raw student ID string
        payload = { student_id: String(rawText).trim() };
    }

    const studentId = payload.student_id || payload.id || payload.code || '';
    const status = overrides.status || payload.status || '';
    const time = overrides.time || payload.time || '';
    const qrToken = payload.qr_token || '';
    const qrPayload = typeof rawText === 'string' ? rawText : '';

    if (!studentId && !qrPayload) {
        showNotification('No student ID found in scan.', 'error');
        setResult('Invalid QR code', 'error');
        return;
    }

    // Get CSRF token from cookie
    const csrfToken = getCookie('csrftoken');

    // Build a FormData payload — Django's request.POST can read this
    const formData = new FormData();
    formData.append('event_id', state.eventId);
    if (studentId) formData.append('student_id', studentId);
    if (qrPayload) formData.append('qr_payload', qrPayload);
    if (qrToken) formData.append('qr_token', qrToken);
    if (status) formData.append('manual_status', status);
    if (time) formData.append('local_time', time);

    // "Scanning…" stays on screen until the server confirms the attendance
    // row was actually stored -- only then does the success bell ring.
    setResult('Scanning…', 'info');
    setStatusText('Scanning…', true);
    state.processing = true;
    // Captured before the fetch: the response handler's own `status` const
    // shadows these, and the walk-in path needs the original values.
    const scanOverrides = { status: status, time: time };

    const scanUrl = CONFIG.scan_url || '/qrapp/save_scan/';

    fetch(scanUrl, {
        method: 'POST',
        headers: {
            'X-CSRFToken': csrfToken,
            // NOTE: do NOT set Content-Type — the browser sets the multipart boundary
        },
        body: formData,
    })
        .then((res) => res.json().then((data) => ({ ok: res.ok, data })))
        .then(({ ok, data }) => {
            if (!ok || data.success === false || data.error) {
                // Walk-in path: the scanned ID is not registered — offer
                // on-the-spot registration, then auto check-in.
                if (data && data.error === 'student_not_found' && data.student_id) {
                    const sid = data.student_id;
                    setResult('Not registered — register this student', 'warning');
                    showNotification(
                        `Student ${sid} is not registered. Register them to check in.`,
                        'warning',
                        'Not registered'
                    );
                    openAddStudentModal(sid, scanOverrides);
                    return;
                }
                const msg = data.error || data.message || 'Scan failed';
                const warning = data.color === 'warning';
                showNotification(msg, warning ? 'warning' : 'error');
                setResult(msg, warning ? 'warning' : 'error');
                return;
            }

            const status = (data.status || status || '').toUpperCase();
            const name = data.student_name || studentId;
            const type = status === 'IN' ? 'in' : status === 'OUT' ? 'out' : 'info';
            const label = status === 'IN' ? 'Check-in' : status === 'OUT' ? 'Check-out' : 'Recorded';

            // The server only answers success after the attendance record is
            // saved, so this is the one place the bell is allowed to ring.
            if (window.chime && typeof window.chime.ring === 'function') {
                window.chime.ring();
            }
            setResult(`${label} saved: ${name}`, 'success');
            showNotification(`${name} — ${label}`, type, label);
        })
        .catch((err) => {
            console.error('Scan request failed:', err);
            showNotification('Network error, scan queued offline.', 'warning');
            setResult('Queued offline', 'info');
            if (window.offlineSync && typeof window.offlineSync.queue === 'function') {
                window.offlineSync.queue({ student_id: studentId, event_id: state.eventId, status, time });
            }
        })
        .finally(() => {
            state.processing = false;
            setStatusText(idleStatusText(), state.mode === 'camera' && state.isScanning);
        });
}

  function getCookie(name) {
    const value = `; ${document.cookie}`;
    const parts = value.split(`; ${name}=`);
    if (parts.length === 2) return parts.pop().split(';').shift();
    return '';
  }

  // ------------------------------------------------------------------
  // Walk-in registration: scan an unregistered student -> modal ->
  // register -> auto check-in.
  // ------------------------------------------------------------------
  let pendingWalkIn = null; // overrides to check in with after saving

  function addAddError(msg) {
    if (!els.addError) return;
    els.addError.textContent = msg || '';
    els.addError.hidden = !msg;
  }

  // The catalog (colleges/programs/majors) ships inside the page, so the
  // dropdowns populate instantly and can never fail to load.
  const CATALOG = CONFIG.catalog || { colleges: [], programs: [], majors: [] };

  function escapeHtml(s) {
    const div = document.createElement('div');
    div.textContent = s == null ? '' : String(s);
    return div.innerHTML;
  }

  function optionList(valueLabel, items) {
    return `<option value="">${valueLabel}</option>` +
      items.map((it) => `<option value="${escapeHtml(it.value)}">${escapeHtml(it.label)}</option>`).join('');
  }

  function loadCollegeOptions() {
    if (!els.addCollege) return;
    els.addCollege.innerHTML = optionList(
      '— Select college —',
      CATALOG.colleges.map((c) => ({ value: c.code, label: `${c.code} — ${c.name}` }))
    );
  }

  function loadProgramOptions() {
    if (!els.addProgram) return;
    const college = els.addCollege.value;
    const programs = CATALOG.programs.filter((p) => p.college === college);
    els.addProgram.disabled = !college;
    if (!college) {
      els.addProgram.innerHTML = '<option value="">Select college first</option>';
    } else {
      els.addProgram.innerHTML = optionList(
        '— Select program —',
        programs.map((p) => ({ value: p.code, label: `${p.code} — ${p.name}` }))
      );
    }
    hideMajorGroup();
  }

  function loadMajorOptions() {
    if (!els.addMajor || !els.addMajorGroup) return;
    const program = els.addProgram.value;
    const majors = CATALOG.majors.filter((m) => m.program === program);
    els.addMajor.innerHTML = '<option value="">None</option>' +
      majors.map((m) => `<option value="${escapeHtml(m.name)}">${escapeHtml(m.code)} — ${escapeHtml(m.name)}</option>`).join('');
    // Programs like BSA/BSAS/BSF have no majors: hide the field entirely.
    els.addMajorGroup.hidden = !program || !majors.length;
  }

  function hideMajorGroup() {
    if (els.addMajorGroup) els.addMajorGroup.hidden = true;
    if (els.addMajor) els.addMajor.innerHTML = '<option value="">None</option>';
  }

  function openAddStudentModal(studentId, overrides) {
    pendingWalkIn = { studentId: studentId, overrides: overrides || {} };
    if (els.addId) els.addId.value = studentId || '';
    if (els.addName) els.addName.value = '';
    if (els.addSex) els.addSex.value = '';
    if (els.addYear) els.addYear.value = '';
    if (els.addCollege) els.addCollege.value = '';
    if (els.addProgram) {
      els.addProgram.innerHTML = '<option value="">Select college first</option>';
      els.addProgram.disabled = true;
    }
    hideMajorGroup();
    addAddError('');
    if (els.addModal) {
      els.addModal.classList.add('is-open');
      els.addModal.setAttribute('aria-hidden', 'false');
    }
    // Refresh the college list on every open: cheap (cached endpoint) and
    // it self-heals after any earlier network hiccup.
    loadCollegeOptions();
    if (els.addName) setTimeout(() => els.addName.focus(), 60);
  }

  function closeAddStudentModal() {
    if (!els.addModal) return;
    els.addModal.classList.remove('is-open');
    els.addModal.setAttribute('aria-hidden', 'true');
    pendingWalkIn = null;
  }

  function bindAddStudentModal() {
    if (!els.addModal) return;

    els.addCollege.addEventListener('change', loadProgramOptions);
    els.addProgram.addEventListener('change', loadMajorOptions);

    const close = () => closeAddStudentModal();
    if (els.addClose) els.addClose.addEventListener('click', close);
    if (els.addCancel) els.addCancel.addEventListener('click', close);
    els.addModal.addEventListener('click', (e) => {
      if (e.target === els.addModal) close();
    });
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && els.addModal.classList.contains('is-open')) close();
    });

    els.addForm.addEventListener('submit', async (e) => {
      e.preventDefault();
      const studentId = (els.addId.value || '').trim();
      const name = (els.addName.value || '').trim();
      if (!studentId || !name || !els.addSex.value || !els.addYear.value || !els.addCollege.value || !els.addProgram.value) {
        addAddError('Please fill in all required fields.');
        return;
      }

      els.addSubmit.disabled = true;
      els.addSubmit.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Saving…';
      addAddError('');

      try {
        const body = new FormData();
        body.append('student_id', studentId);
        body.append('name', name);
        body.append('sex', els.addSex.value);
        body.append('year', els.addYear.value);
        body.append('college', els.addCollege.value);
        body.append('program', els.addProgram.value);
        body.append('major', els.addMajor ? els.addMajor.value : '');

        const res = await fetch(CONFIG.add_student_url, {
          method: 'POST',
          headers: {
            'X-CSRFToken': getCookie('csrftoken') || CONFIG.csrfToken || '',
            'X-Requested-With': 'XMLHttpRequest',
          },
          body: body,
        });
        const data = await res.json();

        if (data.success) {
          closeAddStudentModal();
          showNotification(data.message || 'Student registered.', 'success', 'Registered');
          // Auto check-in: the student exists now, so the normal scan
          // flow runs — including the bell for a successful check-in.
          processScan(studentId, pendingWalkIn ? pendingWalkIn.overrides : {});
          pendingWalkIn = null;
        } else if (data.error === 'already_registered' && data.student) {
          // Registered elsewhere meanwhile: just check them in.
          closeAddStudentModal();
          showNotification(data.message || 'Already registered.', 'info', 'Already registered');
          processScan(data.student.student_id, pendingWalkIn ? pendingWalkIn.overrides : {});
          pendingWalkIn = null;
        } else {
          addAddError(data.error || 'Could not register the student.');
        }
      } catch (err) {
        addAddError('Network error — please try again.');
      } finally {
        els.addSubmit.disabled = false;
        els.addSubmit.innerHTML = '<i class="fas fa-user-plus"></i> Register &amp; Check In';
      }
    });
  }

  // ------------------------------------------------------------------
  // Mode switching
  // ------------------------------------------------------------------
  function switchMode(mode) {
    state.mode = mode;

    if (mode === 'camera') {
      els.modeCameraBtn.classList.add('active');
      els.modeDeviceBtn.classList.remove('active');
      els.cameraControls.style.display = '';
      els.deviceScannerPanel.classList.remove('active');
      els.reader.style.display = '';
      els.scannerHintText.textContent = 'Position the QR code within the frame';
      startCameraScan();
    } else {
      els.modeDeviceBtn.classList.add('active');
      els.modeCameraBtn.classList.remove('active');
      els.cameraControls.style.display = 'none';
      els.reader.style.display = 'none';
      els.deviceScannerPanel.classList.add('active');
      els.scannerHintText.textContent = 'Scan with your USB / Bluetooth scanner';
      stopCameraStream();
      setStatusText('Device scanner ready', false);
      setTimeout(() => els.deviceScanInput.focus(), 100);
    }
  }

  // ------------------------------------------------------------------
  // Event selector
  // ------------------------------------------------------------------
  function syncEventPreview() {
    if (!els.eventSelect) return;
    const opt = els.eventSelect.options[els.eventSelect.selectedIndex];
    const picture = opt.getAttribute('data-picture') || '';
    const title = opt.getAttribute('data-title') || 'Select an event';
    const sub = opt.getAttribute('data-sub') || 'Required before scanning';
    const desc = opt.getAttribute('data-description') || '';
    const value = opt.value;
    const eventDate = opt.getAttribute('data-date') || '';

    state.eventId = value || null;
    state.eventDate = eventDate || null;

    // Warning sign: the selected event is not scheduled for today. The
    // server refuses such scans outright; this surfaces it the moment the
    // event is picked so the operator is not surprised at scan time.
    if (value && eventDate) {
      const today = new Date();
      const todayStr = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, '0')}-${String(today.getDate()).padStart(2, '0')}`;
      if (eventDate !== todayStr) {
        showNotification(
          `${title} is scheduled for ${sub || eventDate}, not today. Scanning is only allowed on the event day.`,
          'warning',
          'Event is not today'
        );
      }
    }

    if (els.eventPreviewTitle) els.eventPreviewTitle.textContent = title;
    if (els.eventPreviewSub) els.eventPreviewSub.textContent = sub;
    if (els.eventPreviewDesc) els.eventPreviewDesc.textContent = desc;

    if (picture && els.eventPicture) {
      els.eventPicture.src = picture;
      els.eventPicture.classList.add('is-visible');
      if (els.eventPicturePlaceholder) els.eventPicturePlaceholder.classList.add('is-hidden');
    } else {
      if (els.eventPicture) {
        els.eventPicture.classList.remove('is-visible');
        els.eventPicture.removeAttribute('src');
      }
      if (els.eventPicturePlaceholder) els.eventPicturePlaceholder.classList.remove('is-hidden');
    }

    if (value) {
      els.eventSelect.classList.remove('event-select-required');
    }
  }

  // ------------------------------------------------------------------
  // Cleanup handlers (beforeunload + dashboard/logout click)
  // ------------------------------------------------------------------
  function attachNavigationCleanup() {
    // beforeunload — last chance to stop camera
    window.addEventListener('beforeunload', () => {
      stopCameraStream();
    });

    // pagehide covers bfcache in some browsers
    window.addEventListener('pagehide', () => {
      stopCameraStream();
    });

    // When the user clicks Dashboard or Logout, stop the camera first
    // so the browser can release the device immediately.
    [els.dashboardBtn, els.logoutBtn].forEach((btn) => {
      if (!btn) return;
      btn.addEventListener('click', (e) => {
        stopCameraStream();
        // Don't preventDefault — let navigation proceed.
      });
    });
  }

  // ------------------------------------------------------------------
  // Bind UI events
  // ------------------------------------------------------------------
  function bindEvents() {
    if (els.modeCameraBtn) {
      els.modeCameraBtn.addEventListener('click', () => switchMode('camera'));
    }
    if (els.modeDeviceBtn) {
      els.modeDeviceBtn.addEventListener('click', () => switchMode('device'));
    }

    if (els.switchCameraBtn) {
      els.switchCameraBtn.addEventListener('click', () => {
        if (!state.cameras.length) return;
        const idx = state.cameras.findIndex((c) => c.id === state.currentCameraId);
        const next = state.cameras[(idx + 1) % state.cameras.length];
        state.currentCameraId = next.id;
        if (els.cameraSelect) els.cameraSelect.value = next.id;
        stopCameraStream();
        setTimeout(startCameraScan, 200);
      });
    }

    if (els.cameraSelect) {
      els.cameraSelect.addEventListener('change', () => {
        state.currentCameraId = els.cameraSelect.value;
        stopCameraStream();
        setTimeout(startCameraScan, 200);
      });
    }

    if (els.eventSelect) {
      els.eventSelect.addEventListener('change', syncEventPreview);
    }

    if (els.retryCameraBtn) {
      els.retryCameraBtn.addEventListener('click', () => {
        hideCameraError();
        startCameraScan();
      });
    }

    // Device scanner: Enter key submits, also keep focus
    if (els.deviceScanInput) {
      els.deviceScanInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') {
          e.preventDefault();
          const value = els.deviceScanInput.value.trim();
          if (value) {
            processScan(value);
            els.deviceScanInput.value = '';
          }
        }
      });
    }

    if (els.startDeviceScanBtn) {
      els.startDeviceScanBtn.addEventListener('click', () => {
        els.deviceScanInput.focus();
      });
    }

    // Manual entry: auto-fill the time as soon as IN or OUT is picked, so
    // the operator rarely types it. A time already typed is never clobbered.
    if (els.manualStatus) {
      els.manualStatus.addEventListener('change', () => {
        if (!els.manualStatus.value) return;
        if (els.manualTime && !els.manualTime.value) {
          const now = new Date();
          const hh = String(now.getHours()).padStart(2, '0');
          const mm = String(now.getMinutes()).padStart(2, '0');
          els.manualTime.value = `${hh}:${mm}`;
        }
      });
    }

    bindAddStudentModal();
    if (els.openRegisterBtn) {
        els.openRegisterBtn.addEventListener('click', () => openAddStudentModal('', {}));
    }

    // Manual entry
    if (els.manualSubmit) {
      els.manualSubmit.addEventListener('click', () => {
        const studentId = els.manualInput ? els.manualInput.value.trim() : '';
        const status = els.manualStatus ? els.manualStatus.value : '';
        const time = els.manualTime ? els.manualTime.value : '';

        if (!studentId) {
          showNotification('Please enter a student ID.', 'warning');
          return;
        }
        processScan(studentId, { status, time });
      });
    }
  }

  // ------------------------------------------------------------------
  // Init
  // ------------------------------------------------------------------
  function init() {
    bindEvents();
    attachNavigationCleanup();
    syncEventPreview();

    // Start camera only after a short idle so the page paints first
    if (state.mode === 'camera') {
      if ('requestIdleCallback' in window) {
        requestIdleCallback(() => startCameraScan(), { timeout: 1500 });
      } else {
        setTimeout(startCameraScan, 400);
      }
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();