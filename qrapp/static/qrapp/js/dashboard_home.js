        const chartData = JSON.parse(document.getElementById('dashboard-chart-data').textContent);

        // Attendance Line Chart (last 7 days)
        try {
        const ctx1 = document.getElementById('attendanceChart').getContext('2d');
        new Chart(ctx1, {
            type: 'line',
            data: {
                labels: chartData.week_labels,
                datasets: [{
                    label: 'Check In',
                    data: chartData.week_check_in,
                    borderColor: '#0984e3',
                    backgroundColor: 'rgba(9, 132, 227, 0.1)',
                    tension: 0.4,
                    fill: true
                },
                {
                    label: 'Check Out',
                    data: chartData.week_check_out,
                    borderColor: '#0a4d7a',
                    backgroundColor: 'rgba(10, 77, 122, 0.08)',
                    tension: 0.4,
                    fill: true
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: {
                        position: 'top',
                    }
                },
                scales: {
                    y: {
                        beginAtZero: true,
                        ticks: { precision: 0 }
                    }
                }
            }
        });

        // College Pie Chart (today's attendees, or roster fallback)
        const ctx2 = document.getElementById('collegeChart').getContext('2d');
        const collegeColors = [
            '#0984e3',
            '#0a4d7a',
            '#3ea0ee',
            '#00b894',
            '#0770c4',
            '#74b9ff',
            '#b8860b',
            '#0f2f4f'
        ];
        new Chart(ctx2, {
            type: 'doughnut',
            data: {
                labels: chartData.college_labels.length ? chartData.college_labels : ['No data'],
                datasets: [{
                    data: chartData.college_counts.length ? chartData.college_counts : [1],
                    backgroundColor: collegeColors
                }]
            },
            options: {
                responsive: true,
                plugins: {
                    legend: {
                        position: 'bottom'
                    }
                }
            }
        });
        } catch (e) {
            console.warn('Chart render skipped', e);
        }
