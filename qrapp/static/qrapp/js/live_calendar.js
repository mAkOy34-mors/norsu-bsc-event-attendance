(function () {
    const widget = document.getElementById('liveCalendarWidget');
    const calendarData = JSON.parse(document.getElementById('live-calendar-data').textContent);
    if (!calendarData || !calendarData.today) {
        document.getElementById('liveClockDate').textContent = 'Calendar data unavailable';
        return;
    }

    function updateLiveClock() {
        const now = new Date();
        document.getElementById('liveClockTime').textContent = now.toLocaleTimeString([], {
            hour: '2-digit', minute: '2-digit', second: '2-digit'
        });
        document.getElementById('liveClockDate').textContent = now.toLocaleDateString([], {
            weekday: 'long', year: 'numeric', month: 'long', day: 'numeric'
        });
    }
    updateLiveClock();
    setInterval(updateLiveClock, 1000);

    let allEvents = (calendarData.events || []).slice();
    const eventsByDate = {};
    function rebuildEventsIndex() {
        Object.keys(eventsByDate).forEach(k => delete eventsByDate[k]);
        allEvents.forEach(ev => {
            if (!eventsByDate[ev.date]) eventsByDate[ev.date] = [];
            eventsByDate[ev.date].push(ev);
        });
    }
    rebuildEventsIndex();

    const attendanceByDate = calendarData.attendance_by_date || {};
    const todayIso = calendarData.today;
    let viewDate = new Date(todayIso + 'T00:00:00');
    let selectedIso = null;
    let filterDate = null;

    const monthNames = [
        'January', 'February', 'March', 'April', 'May', 'June',
        'July', 'August', 'September', 'October', 'November', 'December'
    ];

    function toIso(d) {
        const y = d.getFullYear();
        const m = String(d.getMonth() + 1).padStart(2, '0');
        const day = String(d.getDate()).padStart(2, '0');
        return y + '-' + m + '-' + day;
    }

    function formatDateLabel(iso) {
        const d = new Date(iso + 'T00:00:00');
        return d.toLocaleDateString([], { weekday: 'short', month: 'short', day: 'numeric', year: 'numeric' });
    }

    function sortedEvents(list) {
        return list.slice().sort((a, b) => {
            if (a.is_current && !b.is_current) return -1;
            if (!a.is_current && b.is_current) return 1;
            if (a.date !== b.date) return a.date < b.date ? -1 : 1;
            return (a.start_time || '').localeCompare(b.start_time || '');
        });
    }

    function renderEventsList() {
        const list = document.getElementById('eventsList');
        const subtitle = document.getElementById('eventsListSubtitle');
        const clearBtn = document.getElementById('clearEventFilter');
        let events = allEvents;

        if (filterDate) {
            events = eventsByDate[filterDate] || [];
            subtitle.textContent = 'Events on ' + formatDateLabel(filterDate);
            clearBtn.hidden = false;
        } else {
            subtitle.textContent = 'Upcoming & active events';
            clearBtn.hidden = true;
            // Prefer current + today/future, but still show recent past if few upcoming
            const upcoming = allEvents.filter(ev => ev.date >= todayIso || ev.is_current);
            events = upcoming.length ? upcoming : allEvents;
        }

        events = sortedEvents(events);

        if (!events.length) {
            list.innerHTML = filterDate
                ? '<div class="event-empty">No events on this day</div>'
                : '<div class="event-empty">No events yet. Create one in Manage Events.</div>';
            return;
        }

        list.innerHTML = events.map(ev => {
            const time = [ev.start_time, ev.end_time].filter(Boolean).join(' - ');
            const dateLabel = formatDateLabel(ev.date);
            return '<div class="event-item' + (ev.is_current ? ' current' : '') + '" data-date="' + ev.date + '">' +
                '<div class="title">' + (ev.is_current ? '★ ' : '') + ev.title +
                (ev.is_current ? ' <span class="event-current-badge">Current</span>' : '') +
                '</div>' +
                '<div class="meta">' + dateLabel +
                (time ? ' · ' + time : '') +
                (ev.location ? ' · ' + ev.location : '') +
                '</div></div>';
        }).join('');
    }

    function selectDay(iso) {
        selectedIso = iso;
        filterDate = iso;
        renderEventsList();
        renderCalendar();
    }

    function renderCalendar() {
        const year = viewDate.getFullYear();
        const month = viewDate.getMonth();
        document.getElementById('calMonthLabel').textContent = monthNames[month] + ' ' + year;

        const dow = document.getElementById('calDow');
        if (!dow.dataset.ready) {
            ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'].forEach(d => {
                const el = document.createElement('div');
                el.className = 'cal-dow';
                el.textContent = d;
                dow.appendChild(el);
            });
            dow.dataset.ready = '1';
        }

        const first = new Date(year, month, 1);
        const startPad = first.getDay();
        const daysInMonth = new Date(year, month + 1, 0).getDate();
        const prevDays = new Date(year, month, 0).getDate();
        const grid = document.getElementById('calDays');
        grid.innerHTML = '';

        for (let i = 0; i < 42; i++) {
            const btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'cal-day';

            let dayNum, cellDate, muted = false;
            if (i < startPad) {
                dayNum = prevDays - startPad + i + 1;
                cellDate = new Date(year, month - 1, dayNum);
                muted = true;
            } else if (i >= startPad + daysInMonth) {
                dayNum = i - (startPad + daysInMonth) + 1;
                cellDate = new Date(year, month + 1, dayNum);
                muted = true;
            } else {
                dayNum = i - startPad + 1;
                cellDate = new Date(year, month, dayNum);
            }

            const iso = toIso(cellDate);
            btn.textContent = String(dayNum);
            if (muted) btn.classList.add('muted');
            if (iso === todayIso) btn.classList.add('today');
            if (iso === selectedIso) btn.classList.add('selected');
            if (eventsByDate[iso] && eventsByDate[iso].length) btn.classList.add('has-event');
            if (attendanceByDate[iso]) btn.classList.add('has-scans');
            btn.addEventListener('click', () => selectDay(iso));
            grid.appendChild(btn);
        }
    }

    document.getElementById('calPrev').addEventListener('click', () => {
        viewDate = new Date(viewDate.getFullYear(), viewDate.getMonth() - 1, 1);
        renderCalendar();
    });
    document.getElementById('calNext').addEventListener('click', () => {
        viewDate = new Date(viewDate.getFullYear(), viewDate.getMonth() + 1, 1);
        renderCalendar();
    });
    document.getElementById('clearEventFilter').addEventListener('click', () => {
        filterDate = null;
        selectedIso = null;
        renderEventsList();
        renderCalendar();
    });

    renderCalendar();
    renderEventsList();

    setInterval(async () => {
        try {
            const res = await fetch(widget.dataset.eventsUrl + "?active_only=true", {
                headers: { 'X-Requested-With': 'XMLHttpRequest' }
            });
            const data = await res.json();
            if (!data.success) return;
            allEvents = (data.events || []).map(ev => ({
                id: ev.id,
                title: ev.title,
                date: ev.event_date,
                start_time: '',
                end_time: '',
                location: ev.location || '',
                is_current: ev.is_current,
                display_window: ev.display_window || ''
            }));
            rebuildEventsIndex();
            renderEventsList();
            renderCalendar();
        } catch (e) {}
    }, 60000);
})();
