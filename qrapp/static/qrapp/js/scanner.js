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

      // Prefer rear camera on mobile
      let cameraId = state.currentCameraId;
      if (!cameraId && state.cameras.length) {
        const rear = state.cameras.find((c) =>
          /back|rear|environment/i.test(c.label || '')
        );
        cameraId = rear ? rear.id : state.cameras[state.cameras.length - 1].id;
      }

      if (!cameraId) {
        throw new Error('No camera available');
      }

      state.html5QrCode = new Html5Qrcode('reader', {
        verbose: false,
      });

      await state.html5QrCode.start(
        cameraId,
        {
          fps: 10,
          qrbox: { width: 250, height: 250 },
          aspectRatio: 1.0,
        },
        onScanSuccess,
        onScanFailure
      );

      // Track the active video track for cleanup
      const video = document.querySelector('#reader video');
      if (video && video.srcObject) {
        const tracks = video.srcObject.getVideoTracks();
        if (tracks.length) state.videoTrack = tracks[0];
      }

      state.isScanning = true;
      state.currentCameraId = cameraId;
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