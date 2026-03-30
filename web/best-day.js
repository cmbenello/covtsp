/* best-day.js — Best Day to Attempt feature */

(function () {
  'use strict';

  const DATA_FILES = {
    london: 'best-day-london.json',
    nyc: 'best-day-nyc.json',
    berlin: 'best-day-berlin.json',
  };

  let currentCity = 'london';
  let expandedDay = null;

  // ── Init ────────────────────────────────────────────────────────

  function init() {
    const tabs = document.querySelectorAll('.bd-tab');
    if (!tabs.length) return; // not on index page

    tabs.forEach(tab => {
      tab.addEventListener('click', () => {
        tabs.forEach(t => t.classList.remove('active'));
        tab.classList.add('active');
        currentCity = tab.dataset.city;
        expandedDay = null;
        loadCity(currentCity);
      });
    });

    loadCity(currentCity);
  }

  function loadCity(city) {
    const file = DATA_FILES[city];
    if (!file) return;

    fetch(file)
      .then(r => {
        if (!r.ok) throw new Error(r.status);
        return r.json();
      })
      .then(data => render(data))
      .catch(() => renderEmpty());
  }

  // ── Render ──────────────────────────────────────────────────────

  function render(data) {
    const days = data.days || [];
    if (!days.length) { renderEmpty(); return; }

    // Sort by date for calendar display
    const sorted = [...days].sort((a, b) => a.date.localeCompare(b.date));
    // Best day is first in original (score-sorted) array
    const best = days[0];

    renderHero(best);
    renderCalendar(sorted, best.date);
    renderFootnote(data.generated_at);
  }

  function renderHero(best) {
    const d = new Date(best.date + 'T00:00:00');
    const fmt = d.toLocaleDateString('en-US', {
      weekday: 'long', month: 'long', day: 'numeric',
    });

    setText('bd-best-date', fmt);
    setText('bd-best-score', best.overall_score.toFixed(1));
    setText('bd-recommendation', best.recommendation);

    setBar('bd-fill-service', 'bd-val-service', best.service.score);
    setBar('bd-fill-weather', 'bd-val-weather', best.weather.score);
    setBar('bd-fill-disruption', 'bd-val-disruption', best.disruptions.score);
  }

  function renderCalendar(days, bestDate) {
    const cal = document.getElementById('bd-calendar');
    if (!cal) return;
    cal.innerHTML = '';

    days.forEach(day => {
      const el = document.createElement('div');
      el.className = 'bd-day' + (day.date === bestDate ? ' best' : '');

      const d = new Date(day.date + 'T00:00:00');
      const dayName = d.toLocaleDateString('en-US', { weekday: 'short' });
      const dayNum = d.getDate();

      const scoreClass = day.overall_score >= 80 ? 'score-high' :
                         day.overall_score >= 60 ? 'score-mid' : 'score-low';

      el.innerHTML =
        '<div class="bd-day-name">' + dayName + '</div>' +
        '<div class="bd-day-num">' + dayNum + '</div>' +
        '<div class="bd-day-score ' + scoreClass + '">' + day.overall_score.toFixed(0) + '</div>' +
        '<div class="bd-day-detail">' + buildDetail(day) + '</div>';

      el.addEventListener('click', () => {
        if (expandedDay === el) {
          el.classList.remove('expanded');
          expandedDay = null;
        } else {
          if (expandedDay) expandedDay.classList.remove('expanded');
          el.classList.add('expanded');
          expandedDay = el;
        }
      });

      cal.appendChild(el);
    });
  }

  function buildDetail(day) {
    var parts = [];
    parts.push('<strong>Service:</strong> ' + day.service.score.toFixed(0) + ' — ' + day.service.day_type);
    parts.push('<strong>Weather:</strong> ' + day.weather.score.toFixed(0) + ' — ' + day.weather.summary);

    if (day.disruptions.count > 0) {
      var lines = day.disruptions.items.map(function(i) { return i.line; }).join(', ');
      parts.push('<strong>Disruptions:</strong> ' + day.disruptions.count + ' (' + lines + ')');
    } else {
      parts.push('<strong>Disruptions:</strong> None');
    }

    return parts.join('<br>');
  }

  function renderEmpty() {
    const hero = document.getElementById('bd-hero');
    const cal = document.getElementById('bd-calendar');
    if (hero) {
      hero.innerHTML =
        '<div class="bd-empty">' +
        'No data available for this city yet.<br>' +
        '<code>python cli.py best-day -c configs/' + currentCity + '.yaml</code>' +
        '</div>';
    }
    if (cal) cal.innerHTML = '';
  }

  function renderFootnote(generatedAt) {
    const el = document.getElementById('bd-updated');
    if (!el || !generatedAt) return;
    var d = new Date(generatedAt);
    el.textContent = d.toLocaleDateString('en-US', {
      month: 'short', day: 'numeric', year: 'numeric',
    });
  }

  // ── Helpers ─────────────────────────────────────────────────────

  function setText(id, text) {
    var el = document.getElementById(id);
    if (el) el.textContent = text;
  }

  function setBar(fillId, valId, score) {
    var fill = document.getElementById(fillId);
    var val = document.getElementById(valId);
    if (fill) {
      fill.style.width = Math.max(0, Math.min(100, score)) + '%';
      fill.className = 'bd-factor-fill ' + (score >= 80 ? 'good' : score >= 60 ? 'ok' : 'bad');
    }
    if (val) val.textContent = score.toFixed(0);
  }

  // ── Start ───────────────────────────────────────────────────────

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
