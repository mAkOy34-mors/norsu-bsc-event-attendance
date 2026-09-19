/**
 * Offline attendance queue — stores failed/offline scans in localStorage
 * and posts them when the connection returns.
 */
(function (window) {
    'use strict';

    var STORAGE_KEY = 'qrOfflineScanQueue_v1';
    var MAX_QUEUE = 500;

    function loadQueue() {
        try {
            var raw = localStorage.getItem(STORAGE_KEY);
            var list = raw ? JSON.parse(raw) : [];
            return Array.isArray(list) ? list : [];
        } catch (e) {
            return [];
        }
    }

    function saveQueue(list) {
        localStorage.setItem(STORAGE_KEY, JSON.stringify(list.slice(0, MAX_QUEUE)));
    }

    function isNetworkFailure(status, errorThrown) {
        // jQuery: status 0 / "error" / "timeout" typically means offline or unreachable
        if (status === 0) return true;
        if (errorThrown === 'timeout') return true;
        if (typeof navigator !== 'undefined' && navigator.onLine === false) return true;
        return false;
    }

    var OfflineScanSync = {
        saveUrl: '',
        csrfToken: '',
        onQueueChange: null,
        onItemSynced: null,
        onFlushDone: null,
        onOfflineQueued: null,
        isFlushing: false,
        _flushTimer: null,
        _retryTimer: null,

        init: function (opts) {
            opts = opts || {};
            this.saveUrl = opts.saveUrl || '';
            this.csrfToken = opts.csrfToken || '';
            this.onQueueChange = opts.onQueueChange || null;
            this.onItemSynced = opts.onItemSynced || null;
            this.onFlushDone = opts.onFlushDone || null;
            this.onOfflineQueued = opts.onOfflineQueued || null;

            var self = this;
            window.addEventListener('online', function () {
                self.scheduleFlush(300);
            });
            window.addEventListener('offline', function () {
                self._notify();
            });

            // Retry periodically in case "online" fired before the network was usable
            this._retryTimer = setInterval(function () {
                if (navigator.onLine && self.count() > 0) {
                    self.flush();
                }
            }, 15000);

            this._notify();
            if (navigator.onLine && this.count() > 0) {
                this.scheduleFlush(800);
            }
            return this;
        },

        count: function () {
            return loadQueue().length;
        },

        isOnline: function () {
            return typeof navigator === 'undefined' || navigator.onLine !== false;
        },

        _notify: function () {
            if (typeof this.onQueueChange === 'function') {
                this.onQueueChange(this.count(), this.isOnline());
            }
        },

        enqueue: function (payload) {
            var q = loadQueue();
            var item = {
                id: 'scan_' + Date.now() + '_' + Math.random().toString(36).slice(2, 9),
                createdAt: new Date().toISOString(),
                payload: payload
            };
            q.push(item);
            saveQueue(q);
            this._notify();
            if (typeof this.onOfflineQueued === 'function') {
                this.onOfflineQueued(item, q.length);
            }
            this.scheduleFlush(1200);
            return item;
        },

        scheduleFlush: function (delayMs) {
            var self = this;
            clearTimeout(this._flushTimer);
            this._flushTimer = setTimeout(function () {
                self.flush();
            }, typeof delayMs === 'number' ? delayMs : 500);
        },

        /**
         * POST a scan. On network failure, queue it and resolve as queued.
         * Returns a jQuery Deferred: { mode: 'live'|'queued', response?, item? }
         */
        postOrQueue: function (payload) {
            var self = this;
            var deferred = $.Deferred();

            if (!this.isOnline()) {
                var offlineItem = this.enqueue(payload);
                deferred.resolve({ mode: 'queued', item: offlineItem, reason: 'offline' });
                return deferred.promise();
            }

            $.ajax({
                url: this.saveUrl,
                method: 'POST',
                data: payload,
                timeout: 12000
            }).done(function (response) {
                deferred.resolve({ mode: 'live', response: response });
            }).fail(function (xhr, textStatus, errorThrown) {
                if (isNetworkFailure(xhr.status, textStatus) || textStatus === 'timeout') {
                    var item = self.enqueue(payload);
                    deferred.resolve({ mode: 'queued', item: item, reason: textStatus || 'network' });
                } else {
                    deferred.reject(xhr, textStatus, errorThrown);
                }
            });

            return deferred.promise();
        },

        flush: function () {
            var self = this;
            if (this.isFlushing) return;
            if (!this.isOnline()) return;
            if (!this.saveUrl) return;

            var queue = loadQueue();
            if (!queue.length) return;

            this.isFlushing = true;

            function finish() {
                self.isFlushing = false;
                self._notify();
                if (typeof self.onFlushDone === 'function') {
                    self.onFlushDone(self.count());
                }
            }

            function postNext() {
                var q = loadQueue();
                if (!q.length || !self.isOnline()) {
                    finish();
                    return;
                }

                var item = q[0];
                var data = Object.assign({}, item.payload);
                if (self.csrfToken) {
                    data.csrfmiddlewaretoken = self.csrfToken;
                }

                $.ajax({
                    url: self.saveUrl,
                    method: 'POST',
                    data: data,
                    timeout: 15000
                }).done(function (response) {
                    // Drop item whether success or business rejection — server received it
                    var remaining = loadQueue().filter(function (x) { return x.id !== item.id; });
                    saveQueue(remaining);
                    if (typeof self.onItemSynced === 'function') {
                        self.onItemSynced(item, response, remaining.length);
                    }
                    self._notify();
                    setTimeout(postNext, 200);
                }).fail(function (xhr, textStatus) {
                    var status = xhr.status;
                    // 429 = rate limited, 5xx = server hiccup. Both clear up on
                    // their own, so the scan MUST stay queued: dropping it here
                    // silently destroyed attendance records (the old code
                    // treated every response-bearing error as permanent).
                    var transient = isNetworkFailure(status, textStatus)
                        || textStatus === 'timeout'
                        || status === 429
                        || status >= 500;
                    if (transient) {
                        // Keep item; the periodic retry picks it up later.
                        finish();
                        return;
                    }
                    // Permanent rejection (400/403/404 …) — retrying can never
                    // succeed, so drop it and move on.
                    var remaining = loadQueue().filter(function (x) { return x.id !== item.id; });
                    saveQueue(remaining);
                    self._notify();
                    setTimeout(postNext, 200);
                });
            }

            postNext();
        }
    };

    window.OfflineScanSync = OfflineScanSync;
})(window);
