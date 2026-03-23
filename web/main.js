// ── Theme management ──────────────────────────────────────────────

function getTheme() {
    return localStorage.getItem('oto-theme') || 'dark';
}

function setTheme(theme) {
    localStorage.setItem('oto-theme', theme);
    document.body.classList.remove('theme-dark', 'theme-light');
    document.body.classList.add(`theme-${theme}`);

    const sunIcon = document.getElementById('icon-sun');
    const moonIcon = document.getElementById('icon-moon');
    if (sunIcon && moonIcon) {
        sunIcon.classList.toggle('hidden', theme === 'dark');
        moonIcon.classList.toggle('hidden', theme === 'light');
    }

    // Update map tiles if map exists
    if (window._map && window._tileLayer) {
        window._tileLayer.setUrl(getTileUrl(theme));
    }
}

function getTileUrl(theme) {
    return theme === 'dark'
        ? 'https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png'
        : 'https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png';
}

// ── Line color mapping ───────────────────────────────────────────

const LINE_COLORS = {
    'red': '#c00004',
    'blue': '#2563eb',
    'green': '#16a34a',
    'yellow': '#eab308',
    'orange': '#ea580c',
    'purple': '#9333ea',
    'pink': '#ec4899',
    'brown': '#92400e',
    'central': '#dc2626',
    'district': '#16a34a',
    'circle': '#eab308',
    'metropolitan': '#9333ea',
    'northern': '#1a1a1a',
    'jubilee': '#737373',
    'piccadilly': '#1d4ed8',
    'victoria': '#2563eb',
    'bakerloo': '#92400e',
    'hammersmith': '#ec4899',
    'elizabeth': '#6d28d9',
    'overground': '#ea580c',
    'dlr': '#0891b2',
};

const FALLBACK_COLORS = [
    '#c00004', '#2563eb', '#16a34a', '#eab308', '#9333ea',
    '#ea580c', '#ec4899', '#0891b2', '#737373', '#92400e',
];

let colorIndex = 0;

function getLineColor(lineName) {
    if (!lineName) return '#c00004';
    const key = lineName.toLowerCase().trim();
    if (LINE_COLORS[key]) return LINE_COLORS[key];
    if (!LINE_COLORS[key]) {
        LINE_COLORS[key] = FALLBACK_COLORS[colorIndex % FALLBACK_COLORS.length];
        colorIndex++;
    }
    return LINE_COLORS[key];
}

// ── Init ─────────────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', () => {
    setTheme(getTheme());

    // Theme toggle
    const themeBtn = document.getElementById('theme-toggle');
    if (themeBtn) {
        themeBtn.addEventListener('click', () => {
            setTheme(getTheme() === 'dark' ? 'light' : 'dark');
        });
    }

    // Mobile menu toggle (index page)
    const menuToggle = document.getElementById('menu-toggle');
    const mobileMenu = document.getElementById('mobile-menu');
    if (menuToggle && mobileMenu) {
        menuToggle.addEventListener('click', () => mobileMenu.classList.toggle('hidden'));
        mobileMenu.querySelectorAll('a').forEach(a =>
            a.addEventListener('click', () => mobileMenu.classList.add('hidden'))
        );
    }

    // File input (results page)
    const fileInput = document.getElementById('file-input');
    if (fileInput) {
        fileInput.addEventListener('change', (e) => {
            const file = e.target.files[0];
            if (!file) return;
            const reader = new FileReader();
            reader.onload = (ev) => {
                try {
                    const data = JSON.parse(ev.target.result);
                    renderResults(data);
                } catch (err) {
                    alert('Invalid JSON: ' + err.message);
                }
            };
            reader.readAsText(file);
        });
    }

    // Load sample button
    const loadSample = document.getElementById('load-sample');
    if (loadSample) {
        loadSample.addEventListener('click', () => {
            fetch('sample.json')
                .then(r => r.json())
                .then(data => renderResults(data))
                .catch(err => alert('Could not load sample: ' + err.message));
        });
    }

    // Auto-load sample.json if ?sample is in the URL
    if (window.location.search.includes('sample')) {
        fetch('sample.json')
            .then(r => r.json())
            .then(data => renderResults(data))
            .catch(() => {});
    }

    // Export button
    const exportBtn = document.getElementById('export-btn');
    if (exportBtn) {
        exportBtn.addEventListener('click', exportPNG);
    }
});

// ── Render results ───────────────────────────────────────────────

function renderResults(data) {
    document.getElementById('loader').classList.add('hidden');
    document.getElementById('results').classList.remove('hidden');
    document.getElementById('export-btn').classList.remove('hidden');

    // Stats
    document.getElementById('stat-time').textContent =
        data.total_time_formatted || formatTime(data.total_time_seconds);
    document.getElementById('stat-stations').textContent =
        data.stations_visited + (data.stations_required ? '/' + data.stations_required : '');
    document.getElementById('stat-gap').textContent =
        data.optimality_gap_pct != null ? data.optimality_gap_pct.toFixed(1) + '%' : 'N/A';
    document.getElementById('stat-bound').textContent =
        data.lp_lower_bound_seconds ? formatTime(data.lp_lower_bound_seconds) : 'N/A';

    // Meta
    document.getElementById('meta-city').textContent = data.city || '--';
    document.getElementById('meta-date').textContent = data.date || '--';
    if (data.graph_stats) {
        document.getElementById('meta-graph').textContent =
            (data.graph_stats.teg_nodes || 0).toLocaleString() + ' nodes';
    }
    if (data.solver_params) {
        document.getElementById('meta-start').textContent = data.solver_params.start_station || '--';
    }

    // Watermark
    document.getElementById('wm-city').textContent = data.city || '';
    document.getElementById('wm-date').textContent = data.date || '';

    // Build map (sets up segments but doesn't draw them yet)
    buildMap(data);

    // Build timeline
    buildTimeline(data);
}

// ── Map ──────────────────────────────────────────────────────────

function buildMap(data) {
    const theme = getTheme();

    // Destroy existing map and animation
    if (window._anim) {
        window._anim.stop();
        window._anim = null;
    }
    if (window._map) {
        window._map.remove();
        window._map = null;
    }

    const map = L.map('map', {
        zoomControl: false,
        attributionControl: true,
    });

    window._map = map;
    window._tileLayer = L.tileLayer(getTileUrl(theme), {
        maxZoom: 18,
        attribution: '&copy; <a href="https://carto.com/">CARTO</a>',
    }).addTo(map);

    L.control.zoom({ position: 'topright' }).addTo(map);

    // Filter to visits with coordinates (skip waits)
    const visits = (data.route || []).filter(v => v.lat && v.lon && v.type !== 'wait');

    if (visits.length === 0) return;

    // Build segments
    const segments = [];
    for (let i = 1; i < visits.length; i++) {
        segments.push({
            from: visits[i - 1],
            to: visits[i],
            line: visits[i].line || '',
            type: visits[i].type || 'transit',
        });
    }

    // Draw station dots as background layer (visible from start)
    const seenStations = new Set();
    visits.forEach((v, i) => {
        if (seenStations.has(v.station_id)) return;
        seenStations.add(v.station_id);

        const isStart = i === 0;
        const isEnd = i === visits.length - 1;

        if (isStart || isEnd) {
            const className = isStart ? 'station-start' : 'station-end';
            L.marker([v.lat, v.lon], {
                icon: L.divIcon({
                    className: className,
                    iconSize: [14, 14],
                    iconAnchor: [7, 7],
                }),
            }).addTo(map).bindTooltip(
                `${isStart ? 'START' : 'END'}: ${v.station_name}`,
                { permanent: false, direction: 'top', offset: [0, -10] }
            );
        } else {
            L.circleMarker([v.lat, v.lon], {
                radius: 3,
                color: 'transparent',
                fillColor: theme === 'dark' ? '#444' : '#ccc',
                fillOpacity: 0.5,
                weight: 0,
            }).addTo(map).bindTooltip(
                v.station_name,
                { permanent: false, direction: 'top', offset: [0, -6] }
            );
        }
    });

    // Fit bounds with generous padding
    const bounds = L.latLngBounds(visits.map(v => [v.lat, v.lon]));
    map.fitBounds(bounds, { padding: [60, 60], maxZoom: 13 });

    // Store segments and start animation
    window._segments = segments;
    window._visits = visits;

    // Show animation controls
    const controls = document.getElementById('animation-controls');
    if (controls) controls.classList.remove('hidden');

    // Create and auto-play animation
    window._anim = new AnimationController(map, segments, visits);
    window._anim.play();
}

// ── Animation Controller ─────────────────────────────────────────

class AnimationController {
    constructor(map, segments, visits) {
        this.map = map;
        this.segments = segments;
        this.visits = visits;
        this.drawnLayers = [];
        this.headMarker = null;
        this.currentIndex = 0;
        this.playing = false;
        this._intervalId = null;
        this._speedHandler = null;
        this.speed = 5;
        this.stationsReached = new Set();

        // Count first station
        if (visits.length > 0) {
            this.stationsReached.add(visits[0].station_id);
        }

        // Create head marker
        this.headMarker = L.marker([visits[0].lat, visits[0].lon], {
            icon: L.divIcon({
                className: 'anim-head',
                iconSize: [14, 14],
                iconAnchor: [7, 7],
            }),
            zIndexOffset: 1000,
        }).addTo(map);

        // Bind controls
        this._bindControls();
        this._updateProgress();
    }

    _bindControls() {
        const playBtn = document.getElementById('anim-play');
        const speedSlider = document.getElementById('anim-speed');
        const speedLabel = document.getElementById('anim-speed-label');

        if (playBtn) {
            playBtn.addEventListener('click', () => {
                if (this.currentIndex >= this.segments.length) {
                    this.reset();
                    this.play();
                } else if (this.playing) {
                    this.pause();
                } else {
                    this.play();
                }
            });
        }

        if (speedSlider) {
            speedSlider.value = this.speed;
            speedSlider.addEventListener('input', (e) => {
                this.speed = parseInt(e.target.value);
                if (speedLabel) speedLabel.textContent = this.speed + 'x';
            });
        }
    }

    _updateProgress() {
        const el = document.getElementById('anim-progress');
        if (el) {
            el.textContent = `${this.stationsReached.size} / ${new Set(this.visits.map(v => v.station_id)).size} stations`;
        }
    }

    play() {
        if (this.playing) return;
        this.playing = true;

        const playBtn = document.getElementById('anim-play');
        if (playBtn) playBtn.textContent = 'Pause';

        this._intervalId = setInterval(() => {
            if (!this.playing) return;

            if (this.currentIndex < this.segments.length) {
                this._drawSegment(this.currentIndex);
                this.currentIndex++;
                this._updateProgress();
                this._syncTimeline();
            }

            if (this.currentIndex >= this.segments.length) {
                this._onComplete();
            }
        }, this._msPerSegment());

        // Update interval when speed changes
        const speedSlider = document.getElementById('anim-speed');
        this._speedHandler = () => {
            if (this._intervalId) {
                clearInterval(this._intervalId);
                this._intervalId = setInterval(() => {
                    if (!this.playing) return;
                    if (this.currentIndex < this.segments.length) {
                        this._drawSegment(this.currentIndex);
                        this.currentIndex++;
                        this._updateProgress();
                        this._syncTimeline();
                    }
                    if (this.currentIndex >= this.segments.length) {
                        this._onComplete();
                    }
                }, this._msPerSegment());
            }
        };
        if (speedSlider) speedSlider.addEventListener('input', this._speedHandler);
    }

    _msPerSegment() {
        return Math.max(20, 400 / this.speed);
    }

    pause() {
        this.playing = false;
        if (this._intervalId) {
            clearInterval(this._intervalId);
            this._intervalId = null;
        }

        const playBtn = document.getElementById('anim-play');
        if (playBtn) playBtn.textContent = 'Play';
    }

    stop() {
        this.pause();
        const speedSlider = document.getElementById('anim-speed');
        if (speedSlider && this._speedHandler) {
            speedSlider.removeEventListener('input', this._speedHandler);
        }
        if (this.headMarker) {
            this.map.removeLayer(this.headMarker);
            this.headMarker = null;
        }
    }

    reset() {
        this.pause();
        // Remove drawn polylines
        this.drawnLayers.forEach(l => this.map.removeLayer(l));
        this.drawnLayers = [];
        this.currentIndex = 0;
        this.stationsReached = new Set();
        if (this.visits.length > 0) {
            this.stationsReached.add(this.visits[0].station_id);
        }

        // Reset head marker position
        if (this.headMarker && this.visits.length > 0) {
            this.headMarker.setLatLng([this.visits[0].lat, this.visits[0].lon]);
        } else if (this.visits.length > 0) {
            this.headMarker = L.marker([this.visits[0].lat, this.visits[0].lon], {
                icon: L.divIcon({
                    className: 'anim-head',
                    iconSize: [14, 14],
                    iconAnchor: [7, 7],
                }),
                zIndexOffset: 1000,
            }).addTo(this.map);
        }

        // Reset timeline highlights
        document.querySelectorAll('.timeline-item.active').forEach(el => el.classList.remove('active'));
        this._updateProgress();

        const playBtn = document.getElementById('anim-play');
        if (playBtn) playBtn.textContent = 'Play';
    }

    _drawSegment(idx) {
        const seg = this.segments[idx];
        const coords = [[seg.from.lat, seg.from.lon], [seg.to.lat, seg.to.lon]];
        const color = getLineColor(seg.line);
        const isWalk = seg.type === 'walk';

        const polyline = L.polyline(coords, {
            color: isWalk ? '#f59e0b' : color,
            weight: isWalk ? 3 : 3.5,
            opacity: isWalk ? 0.8 : 0.85,
            dashArray: isWalk ? '8 6' : null,
            lineCap: 'round',
            lineJoin: 'round',
        }).addTo(this.map);

        this.drawnLayers.push(polyline);

        // Track station
        this.stationsReached.add(seg.to.station_id);

        // Color the station dot now that we've reached it
        L.circleMarker([seg.to.lat, seg.to.lon], {
            radius: 3.5,
            color: 'transparent',
            fillColor: isWalk ? '#f59e0b' : color,
            fillOpacity: 0.9,
            weight: 0,
        }).addTo(this.map);

        // Move head marker
        if (this.headMarker) {
            this.headMarker.setLatLng([seg.to.lat, seg.to.lon]);
        }
    }

    _syncTimeline() {
        // Highlight the corresponding timeline item
        const items = document.querySelectorAll('.timeline-item');
        items.forEach(el => el.classList.remove('active'));

        const targetIdx = Math.min(this.currentIndex, items.length - 1);
        if (items[targetIdx]) {
            items[targetIdx].classList.add('active');
            // Only scroll within the timeline container, not the whole page
            const container = document.getElementById('timeline');
            if (container) {
                const itemTop = items[targetIdx].offsetTop - container.offsetTop;
                container.scrollTop = itemTop - container.clientHeight / 2;
            }
        }
    }

    _onComplete() {
        this.playing = false;
        if (this.animFrameId) {
            cancelAnimationFrame(this.animFrameId);
            this.animFrameId = null;
        }

        // Remove head marker
        if (this.headMarker) {
            this.map.removeLayer(this.headMarker);
            this.headMarker = null;
        }

        const playBtn = document.getElementById('anim-play');
        if (playBtn) playBtn.textContent = 'Replay';

        this._updateProgress();
    }

    // Skip to end — draw all remaining segments instantly
    skipToEnd() {
        this.pause();
        while (this.currentIndex < this.segments.length) {
            this._drawSegment(this.currentIndex);
            this.currentIndex++;
        }
        this._onComplete();
        this._updateProgress();
    }
}

// ── Timeline ─────────────────────────────────────────────────────

function buildTimeline(data) {
    const container = document.getElementById('timeline');
    container.innerHTML = '';

    const visits = (data.route || []).filter(v => v.type !== 'wait');
    let num = 0;

    visits.forEach((v, i) => {
        // Walking transfer indicator
        if (v.type === 'walk' && i > 0) {
            const walkDiv = document.createElement('div');
            walkDiv.className = 'timeline-walk';
            const prev = visits[i - 1];
            walkDiv.textContent = `Walk from ${prev.station_name}`;
            container.appendChild(walkDiv);
        }

        num++;
        const color = getLineColor(v.line);
        const isWalk = v.type === 'walk';

        const item = document.createElement('div');
        item.className = 'timeline-item';
        item.innerHTML = `
            <span class="timeline-num">${num}</span>
            <span class="timeline-dot" style="background: ${v.type === 'start' ? '#c00004' : isWalk ? '#f59e0b' : color};"></span>
            <span class="timeline-name" style="color: var(--text);">${v.station_name || v.station_id}</span>
            <span class="timeline-line">${isWalk ? 'WALK' : (v.line || '')}</span>
            <span class="timeline-time">${v.arrival || ''}</span>
        `;

        // Hover to highlight on map
        if (v.lat && v.lon && window._map) {
            item.addEventListener('mouseenter', () => {
                if (window._highlight) window._map.removeLayer(window._highlight);
                window._highlight = L.circleMarker([v.lat, v.lon], {
                    radius: 8,
                    color: '#c00004',
                    fillColor: '#c00004',
                    fillOpacity: 0.4,
                    weight: 2,
                }).addTo(window._map);
            });
            item.addEventListener('mouseleave', () => {
                if (window._highlight) {
                    window._map.removeLayer(window._highlight);
                    window._highlight = null;
                }
            });
        }

        container.appendChild(item);
    });
}

// ── Export PNG ────────────────────────────────────────────────────

async function exportPNG() {
    // Complete animation first if in progress
    if (window._anim && window._anim.currentIndex < window._anim.segments.length) {
        window._anim.skipToEnd();
    }

    const card = document.getElementById('export-card');
    const watermark = document.getElementById('watermark');

    watermark.classList.remove('hidden');

    try {
        const dataUrl = await htmlToImage.toPng(card, {
            pixelRatio: 2,
            backgroundColor: getComputedStyle(document.body).getPropertyValue('--bg').trim(),
        });

        const link = document.createElement('a');
        const city = (document.getElementById('meta-city').textContent || 'route').toLowerCase().replace(/\s+/g, '-');
        const date = document.getElementById('meta-date').textContent || 'result';
        link.download = `route-${city}-${date}.png`;
        link.href = dataUrl;
        link.click();
    } catch (err) {
        console.error('Export failed:', err);
        alert('Export failed. Try a smaller window or different browser.');
    } finally {
        watermark.classList.add('hidden');
    }
}

// ── Helpers ───────────────────────────────────────────────────────

function formatTime(seconds) {
    if (!seconds && seconds !== 0) return '--';
    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    return `${h}h${m.toString().padStart(2, '0')}m`;
}
