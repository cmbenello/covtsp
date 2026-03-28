// ── Theme toggle ─────────────────────────────────────────────

(function initTheme() {
    const saved = localStorage.getItem('theme');
    if (saved) {
        document.body.setAttribute('data-theme', saved);
    }
})();

document.addEventListener('DOMContentLoaded', () => {
    // Theme toggle
    const toggle = document.getElementById('theme-toggle');
    if (toggle) {
        toggle.addEventListener('click', () => {
            const current = document.body.getAttribute('data-theme');
            const next = current === 'dark' ? 'light' : 'dark';
            document.body.setAttribute('data-theme', next);
            localStorage.setItem('theme', next);
        });
    }

    // Scroll reveal
    initScrollReveal();

    // Record bar animation
    initRecordBars();
});

// ── Scroll Reveal ────────────────────────────────────────────

function initScrollReveal() {
    const elements = document.querySelectorAll('.reveal');
    if (!elements.length) return;

    const observer = new IntersectionObserver((entries) => {
        entries.forEach(entry => {
            if (entry.isIntersecting) {
                entry.target.classList.add('visible');
                observer.unobserve(entry.target);
            }
        });
    }, {
        threshold: 0.1,
        rootMargin: '0px 0px -40px 0px',
    });

    elements.forEach(el => observer.observe(el));
}

// ── Record Bar Animation ─────────────────────────────────────

function initRecordBars() {
    const bars = document.querySelectorAll('.record-bar-fill');
    if (!bars.length) return;

    const observer = new IntersectionObserver((entries) => {
        entries.forEach(entry => {
            if (entry.isIntersecting) {
                // Animate all bars in the section with stagger
                const section = entry.target.closest('.record-bars');
                if (section) {
                    const fills = section.querySelectorAll('.record-bar-fill');
                    fills.forEach((fill, i) => {
                        setTimeout(() => {
                            fill.style.width = fill.dataset.width + '%';
                        }, i * 200);
                    });
                }
                observer.unobserve(entry.target);
            }
        });
    }, {
        threshold: 0.3,
    });

    // Observe the first bar only (triggers all)
    observer.observe(bars[0]);
}
