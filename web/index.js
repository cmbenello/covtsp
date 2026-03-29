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

    // Nav scroll effect
    initNavScroll();

    // Scroll reveal with stagger
    initScrollReveal();

    // Record bar animation
    initRecordBars();
});

// ── Nav Scroll ──────────────────────────────────────────────

function initNavScroll() {
    const nav = document.getElementById('main-nav');
    if (!nav) return;

    const onScroll = () => {
        if (window.scrollY > 20) {
            nav.classList.add('nav-scrolled');
        } else {
            nav.classList.remove('nav-scrolled');
        }
    };

    window.addEventListener('scroll', onScroll, { passive: true });
    onScroll();
}

// ── Scroll Reveal with Stagger ──────────────────────────────

function initScrollReveal() {
    const prefersReduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    const elements = document.querySelectorAll('.reveal');
    if (!elements.length) return;

    if (prefersReduced) {
        elements.forEach(el => el.classList.add('visible'));
        return;
    }

    const observer = new IntersectionObserver((entries) => {
        entries.forEach(entry => {
            if (entry.isIntersecting) {
                entry.target.classList.add('visible');

                // Stagger children if any
                const children = entry.target.querySelectorAll('.stagger-child');
                children.forEach((child, i) => {
                    setTimeout(() => {
                        child.style.transitionDelay = '0ms';
                        child.classList.add('visible');
                    }, i * 60);
                });

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
                const section = entry.target.closest('.record-bars');
                if (section) {
                    const fills = section.querySelectorAll('.record-bar-fill');
                    fills.forEach((fill, i) => {
                        setTimeout(() => {
                            fill.style.width = fill.dataset.width + '%';
                        }, i * 250);
                    });
                }
                observer.unobserve(entry.target);
            }
        });
    }, {
        threshold: 0.3,
    });

    observer.observe(bars[0]);
}
