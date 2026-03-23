// ── Line color mapping ───────────────────────────────────────────

const LINE_COLORS = {
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
    'hammersmith & city': '#ec4899',
    'elizabeth': '#6d28d9',
    'overground': '#ea580c',
    'dlr': '#0891b2',
    'waterloo & city': '#76c8b0',
};

const FALLBACK_COLORS = [
    '#dc2626', '#2563eb', '#16a34a', '#eab308', '#9333ea',
    '#ea580c', '#ec4899', '#0891b2', '#737373', '#92400e',
];

let colorIndex = 0;

function getLineColor(lineName) {
    if (!lineName) return '#f05a28';
    const key = lineName.toLowerCase().trim();
    if (LINE_COLORS[key]) return LINE_COLORS[key];
    // Auto-assign a color for unknown lines
    LINE_COLORS[key] = FALLBACK_COLORS[colorIndex % FALLBACK_COLORS.length];
    colorIndex++;
    return LINE_COLORS[key];
}

// ── Init ─────────────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', () => {
    // File input (results page)
    const fileInput = document.getElementById('file-input');
    if (fileInput) {
        fileInput.addEventListener('change', (e) => {
            const file = e.target.files[0];
            if (!file) return;
            const reader = new FileReader();
            reader.onload = (ev) => {
                try {
                    renderResults(JSON.parse(ev.target.result));
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

    // Auto-load if ?sample in URL
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
        data.stations_visited + (data.stations_required ? ' / ' + data.stations_required : '');
    document.getElementById('stat-gap').textContent =
        data.optimality_gap_pct != null ? data.optimality_gap_pct.toFixed(1) + '%' : 'N/A';
    document.getElementById('stat-bound').textContent =
        data.lp_lower_bound_seconds ? formatTime(data.lp_lower_bound_seconds) : 'N/A';

    // Nav meta
    document.getElementById('meta-city').textContent = data.city || '--';
    document.getElementById('meta-date').textContent = data.date || '--';

    // Meta bar
    if (data.graph_stats) {
        document.getElementById('meta-graph').textContent =
            (data.graph_stats.teg_nodes || 0).toLocaleString() + ' nodes / ' +
            (data.graph_stats.teg_edges || 0).toLocaleString() + ' edges';
    }
    if (data.solver_params) {
        const stationName = data.stations?.[data.solver_params.start_station]?.name || data.solver_params.start_station;
        document.getElementById('meta-start').textContent = stationName || '--';
    }

    // Watermark
    document.getElementById('wm-city').textContent = data.city || '';
    document.getElementById('wm-date').textContent = data.date || '';

    buildMap(data);
    buildTimeline(data);
}

// ── Map ──────────────────────────────────────────────────────────

function buildMap(data) {
    // Destroy existing
    if (window._anim) { window._anim.stop(); window._anim = null; }
    if (window._map) { window._map.remove(); window._map = null; }

    const map = L.map('map', { zoomControl: false, attributionControl: true });
    window._map = map;

    // Light tiles always
    L.tileLayer('https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png', {
        maxZoom: 18,
        attribution: '&copy; <a href="https://carto.com/">CARTO</a>',
    }).addTo(map);

    L.control.zoom({ position: 'topright' }).addTo(map);

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

    // Station dots (background)
    const seenStations = new Set();
    visits.forEach((v, i) => {
        if (seenStations.has(v.station_id)) return;
        seenStations.add(v.station_id);

        const isStart = i === 0;
        const isEnd = i === visits.length - 1;

        if (isStart || isEnd) {
            L.marker([v.lat, v.lon], {
                icon: L.divIcon({
                    className: isStart ? 'station-start' : 'station-end',
                    iconSize: [10, 10],
                    iconAnchor: [5, 5],
                }),
            }).addTo(map).bindTooltip(
                `${isStart ? 'START' : 'END'}: ${v.station_name}`,
                { permanent: false, direction: 'top', offset: [0, -8] }
            );
        } else {
            L.circleMarker([v.lat, v.lon], {
                radius: 2.5,
                color: 'transparent',
                fillColor: '#ccc',
                fillOpacity: 0.5,
                weight: 0,
            }).addTo(map).bindTooltip(
                v.station_name,
                { permanent: false, direction: 'top', offset: [0, -4] }
            );
        }
    });

    const bounds = L.latLngBounds(visits.map(v => [v.lat, v.lon]));
    map.fitBounds(bounds, { padding: [60, 60], maxZoom: 13 });

    window._segments = segments;
    window._visits = visits;

    const controls = document.getElementById('animation-controls');
    if (controls) controls.classList.remove('hidden');

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

        if (visits.length > 0) {
            this.stationsReached.add(visits[0].station_id);
            this.headMarker = L.marker([visits[0].lat, visits[0].lon], {
                icon: L.divIcon({
                    className: 'anim-head',
                    iconSize: [10, 10],
                    iconAnchor: [5, 5],
                }),
                zIndexOffset: 1000,
            }).addTo(map);
        }

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
            const total = new Set(this.visits.map(v => v.station_id)).size;
            el.textContent = `${this.stationsReached.size} / ${total} stations`;
        }
    }

    play() {
        if (this.playing) return;
        this.playing = true;

        const playBtn = document.getElementById('anim-play');
        if (playBtn) playBtn.textContent = 'Pause';

        const tick = () => {
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
        };

        this._intervalId = setInterval(tick, this._msPerSegment());

        // Update interval on speed change
        const speedSlider = document.getElementById('anim-speed');
        this._speedHandler = () => {
            if (this._intervalId) clearInterval(this._intervalId);
            this._intervalId = setInterval(tick, this._msPerSegment());
        };
        if (speedSlider) speedSlider.addEventListener('input', this._speedHandler);
    }

    _msPerSegment() {
        return Math.max(20, 400 / this.speed);
    }

    pause() {
        this.playing = false;
        if (this._intervalId) { clearInterval(this._intervalId); this._intervalId = null; }
        const playBtn = document.getElementById('anim-play');
        if (playBtn) playBtn.textContent = 'Play';
    }

    stop() {
        this.pause();
        const speedSlider = document.getElementById('anim-speed');
        if (speedSlider && this._speedHandler) speedSlider.removeEventListener('input', this._speedHandler);
        if (this.headMarker) { this.map.removeLayer(this.headMarker); this.headMarker = null; }
    }

    reset() {
        this.pause();
        this.drawnLayers.forEach(l => this.map.removeLayer(l));
        this.drawnLayers = [];
        this.currentIndex = 0;
        this.stationsReached = new Set();
        if (this.visits.length > 0) this.stationsReached.add(this.visits[0].station_id);

        if (this.headMarker && this.visits.length > 0) {
            this.headMarker.setLatLng([this.visits[0].lat, this.visits[0].lon]);
        } else if (this.visits.length > 0) {
            this.headMarker = L.marker([this.visits[0].lat, this.visits[0].lon], {
                icon: L.divIcon({ className: 'anim-head', iconSize: [10, 10], iconAnchor: [5, 5] }),
                zIndexOffset: 1000,
            }).addTo(this.map);
        }

        document.querySelectorAll('.timeline-row.active').forEach(el => el.classList.remove('active'));
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
            weight: isWalk ? 2.5 : 3,
            opacity: isWalk ? 0.7 : 0.85,
            dashArray: isWalk ? '6 5' : null,
            lineCap: 'round',
            lineJoin: 'round',
        }).addTo(this.map);

        this.drawnLayers.push(polyline);
        this.stationsReached.add(seg.to.station_id);

        // Color station dot
        const dot = L.circleMarker([seg.to.lat, seg.to.lon], {
            radius: 3,
            color: 'transparent',
            fillColor: isWalk ? '#f59e0b' : color,
            fillOpacity: 0.9,
            weight: 0,
        }).addTo(this.map);
        this.drawnLayers.push(dot);

        if (this.headMarker) this.headMarker.setLatLng([seg.to.lat, seg.to.lon]);
    }

    _syncTimeline() {
        const rows = document.querySelectorAll('.timeline-row');
        rows.forEach(el => el.classList.remove('active'));

        const targetIdx = Math.min(this.currentIndex, rows.length - 1);
        if (rows[targetIdx]) {
            rows[targetIdx].classList.add('active');
            // Scroll within timeline container only
            const container = document.getElementById('timeline');
            if (container) {
                const rowTop = rows[targetIdx].offsetTop - container.offsetTop;
                container.scrollTop = rowTop - container.clientHeight / 2;
            }
        }
    }

    _onComplete() {
        this.playing = false;
        if (this._intervalId) { clearInterval(this._intervalId); this._intervalId = null; }
        if (this.headMarker) { this.map.removeLayer(this.headMarker); this.headMarker = null; }
        const playBtn = document.getElementById('anim-play');
        if (playBtn) playBtn.textContent = 'Replay';
        this._updateProgress();
    }

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
        num++;
        const color = getLineColor(v.line);
        const isWalk = v.type === 'walk';

        const row = document.createElement('div');
        row.className = 'timeline-row' + (isWalk ? ' walk-row' : '');
        row.innerHTML = `
            <span class="tr-num">${num}</span>
            <span class="tr-time">${v.arrival || ''}</span>
            <span class="tr-station">${v.station_name || v.station_id}</span>
            <span class="tr-line"><span class="line-dot" style="background:${isWalk ? '#f59e0b' : color}"></span>${isWalk ? 'Walk' : (v.line || '')}</span>
        `;

        // Hover highlight on map
        if (v.lat && v.lon && window._map) {
            row.addEventListener('mouseenter', () => {
                if (window._highlight) window._map.removeLayer(window._highlight);
                window._highlight = L.circleMarker([v.lat, v.lon], {
                    radius: 8,
                    color: '#f05a28',
                    fillColor: '#f05a28',
                    fillOpacity: 0.3,
                    weight: 2,
                }).addTo(window._map);
            });
            row.addEventListener('mouseleave', () => {
                if (window._highlight) { window._map.removeLayer(window._highlight); window._highlight = null; }
            });
        }

        container.appendChild(row);
    });
}

// ── Export PNG ────────────────────────────────────────────────────

async function exportPNG() {
    if (window._anim && window._anim.currentIndex < window._anim.segments.length) {
        window._anim.skipToEnd();
    }

    const card = document.getElementById('export-card');
    const watermark = document.getElementById('watermark');
    watermark.classList.remove('hidden');

    try {
        const dataUrl = await htmlToImage.toPng(card, {
            pixelRatio: 2,
            backgroundColor: '#fafafa',
        });

        const link = document.createElement('a');
        const city = (document.getElementById('meta-city').textContent || 'route').toLowerCase().replace(/\s+/g, '-');
        const date = document.getElementById('meta-date').textContent || 'result';
        link.download = `route-${city}-${date}.png`;
        link.href = dataUrl;
        link.click();
    } catch (err) {
        console.error('Export failed:', err);
        alert('Export failed. Try a smaller window.');
    } finally {
        watermark.classList.add('hidden');
    }
}

// ── Helpers ──────────────────────────────────────────────────────

function formatTime(seconds) {
    if (!seconds && seconds !== 0) return '--';
    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    return `${h}h${m.toString().padStart(2, '0')}m`;
}
