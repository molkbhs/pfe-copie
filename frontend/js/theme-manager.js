// theme-manager.js - Global light/dark theme manager
window.ThemeManager = {
    STORAGE_KEY: 'app_theme',

    resolveTheme: function() {
        const saved = localStorage.getItem(this.STORAGE_KEY);
        if (saved === 'light' || saved === 'dark') return saved;
        return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
    },

    applyTheme: function(theme, options = {}) {
        const nextTheme = theme === 'dark' ? 'dark' : 'light';
        const previousTheme = document.documentElement.getAttribute('data-theme');

        document.documentElement.setAttribute('data-theme', nextTheme);
        localStorage.setItem(this.STORAGE_KEY, nextTheme);

        const icon = document.getElementById(options.iconId || 'themeIcon');
        if (icon) {
            icon.innerHTML = nextTheme === 'dark'
                ? '<i class="bi bi-sun-fill"></i>'
                : '<i class="bi bi-moon-stars-fill"></i>';
        }

        const label = document.getElementById(options.labelId || 'themeLabel');
        if (label) {
            const darkLabel = options.darkLabel || 'Clair';
            const lightLabel = options.lightLabel || 'Sombre';
            label.textContent = nextTheme === 'dark' ? darkLabel : lightLabel;
        }

        document.dispatchEvent(new CustomEvent('themeChanged', {
            detail: { theme: nextTheme, previousTheme: previousTheme || null }
        }));
    },

    toggle: function(options = {}) {
        const current = this.resolveTheme();
        const next = current === 'dark' ? 'light' : 'dark';
        this.applyTheme(next, options);
        return next;
    },

    initThemeToggle: function(options = {}) {
        const buttonId = options.buttonId || 'themeToggle';
        const btn = document.getElementById(buttonId);

        if (btn && btn.dataset.themeBound !== '1') {
            btn.dataset.themeBound = '1';
            btn.addEventListener('click', () => this.toggle(options));
        }

        const initial = this.resolveTheme();
        this.applyTheme(initial, options);
        return initial;
    }
};

document.addEventListener('DOMContentLoaded', () => {
    if (document.getElementById('themeToggle')) {
        window.ThemeManager.initThemeToggle({
            buttonId: 'themeToggle',
            iconId: 'themeIcon',
            labelId: 'themeLabel'
        });
    } else {
        window.ThemeManager.applyTheme(window.ThemeManager.resolveTheme());
    }
});
