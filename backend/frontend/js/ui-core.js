// ui-core.js - Shared UI/Auth helpers
window.API_BASE = window.location.origin.includes('localhost') || window.location.origin.includes('127.0.0.1')
    ? 'http://127.0.0.1:5000'
    : window.location.origin;

window.AUTH_STORAGE_MODE = (window.AUTH_STORAGE_MODE || 'session').toLowerCase();
window.AUTH_STORAGE_KEY = window.AUTH_STORAGE_KEY || 'user';
window.AUTH_CLEAR_ON_RELOAD = window.AUTH_CLEAR_ON_RELOAD === true;

window.UICore = {
    _menuGlobalBound: false,
    _authStorageMode: ['session', 'local'].includes(window.AUTH_STORAGE_MODE) ? window.AUTH_STORAGE_MODE : 'session',
    _authStorageKey: window.AUTH_STORAGE_KEY || 'user',

    _safeStorage: function(kind) {
        try {
            if (kind === 'local') return window.localStorage;
            if (kind === 'session') return window.sessionStorage;
            return null;
        } catch (_) {
            return null;
        }
    },

    _getStorage: function(mode) {
        if (String(mode || '').toLowerCase() === 'local') {
            return this._safeStorage('local') || this._safeStorage('session');
        }
        return this._safeStorage('session') || this._safeStorage('local');
    },

    _readUserFromStorage: function(storage) {
        if (!storage) return null;
        try {
            return JSON.parse(storage.getItem(this._authStorageKey) || 'null');
        } catch (_) {
            return null;
        }
    },

    _writeUserToStorage: function(storage, user) {
        if (!storage || !user) return;
        try {
            storage.setItem(this._authStorageKey, JSON.stringify(user));
        } catch (_) {
            // noop
        }
    },

    _removeUserFromStorage: function(storage) {
        if (!storage) return;
        try {
            storage.removeItem(this._authStorageKey);
        } catch (_) {
            // noop
        }
    },

    getUser: function(options = {}) {
        const mode = options.mode || this._authStorageMode;
        const primary = this._getStorage(mode);
        const secondary = this._getStorage(mode === 'local' ? 'session' : 'local');

        const direct = this._readUserFromStorage(primary);
        if (direct) return direct;

        const fallback = this._readUserFromStorage(secondary);
        const shouldMigrate = options.migrate !== false;
        if (fallback && shouldMigrate && primary && secondary && primary !== secondary) {
            this._writeUserToStorage(primary, fallback);
            this._removeUserFromStorage(secondary);
        }
        return fallback;
    },

    saveUser: function(user, options = {}) {
        if (!user || typeof user !== 'object') return null;

        const mode = options.mode || this._authStorageMode;
        const primary = this._getStorage(mode);
        const secondary = this._getStorage(mode === 'local' ? 'session' : 'local');

        this._writeUserToStorage(primary, user);
        if (secondary && secondary !== primary) this._removeUserFromStorage(secondary);
        return user;
    },

    setUser: function(user, options = {}) {
        return this.saveUser(user, options);
    },

    removeUser: function() {
        this._removeUserFromStorage(this._safeStorage('local'));
        this._removeUserFromStorage(this._safeStorage('session'));
    },

    getAuthToken: function(user) {
        const resolved = user || this.getUser();
        if (!resolved) return '';
        return resolved.token || '';
    },

    decodeJwtPayload: function(token) {
        if (!token || typeof token !== 'string') return null;
        const parts = token.split('.');
        if (parts.length !== 3) return null;
        const base64 = parts[1].replace(/-/g, '+').replace(/_/g, '/');
        const padded = base64 + '='.repeat((4 - (base64.length % 4)) % 4);
        try {
            return JSON.parse(atob(padded));
        } catch (_) {
            return null;
        }
    },

    isAuthenticated: function(user) {
        const resolved = user || this.getUser();
        if (!resolved || typeof resolved !== 'object') return false;
        const hasToken = Boolean(resolved.token);
        const hasIdentity = Boolean(resolved.email || resolved.username || resolved.firstname);
        return hasToken && hasIdentity;
    },

    checkAuth: function(options = {}) {
        const redirectTo = options.redirectTo || 'index.html';
        const shouldRedirect = options.redirect !== false;
        const user = this.getUser(options);

        if (this.isAuthenticated(user)) return user;
        if (shouldRedirect) window.location.replace(redirectTo);
        return null;
    },

    authHeaders: function(options = {}) {
        const json = options.json === true;
        const user = options.user || this.getUser();
        const token = this.getAuthToken(user);
        const headers = {};
        if (token) headers.Authorization = `Bearer ${token}`;
        if (json) headers['Content-Type'] = 'application/json';
        if (options.extra && typeof options.extra === 'object') {
            Object.assign(headers, options.extra);
        }
        return headers;
    },

    ensureValidSession: function(options = {}) {
        const redirectTo = options.redirectTo || 'index.html';
        const shouldRedirect = options.redirect !== false;
        const user = options.user || this.getUser(options);
        const token = this.getAuthToken(user);

        if (!user || !token) {
            if (shouldRedirect) this.logout(redirectTo, { reason: 'missing_token' });
            return null;
        }

        const payload = this.decodeJwtPayload(token);
        if (payload && typeof payload.exp === 'number') {
            const now = Math.floor(Date.now() / 1000);
            if (payload.exp <= now) {
                this.clearAuthState();
                if (shouldRedirect) window.location.replace(redirectTo);
                return null;
            }
        }
        return user;
    },

    requireUser: function(redirectTo = 'index.html') {
        return this.checkAuth({ redirectTo });
    },

    clearAuthState: function() {
        this.removeUser();
    },

    logout: function(redirectTo = 'index.html', options = {}) {
        try {
            document.dispatchEvent(new CustomEvent('app:logout', {
                detail: { reason: options.reason || 'manual', redirectTo }
            }));
        } catch (_) {
            // noop
        }
        this.clearAuthState();
        if (options.redirect !== false) {
            window.location.replace(redirectTo);
        }
    },

    isUnauthorizedResponse: function(response, payload) {
        if (response && (response.status === 401 || response.status === 403)) return true;
        const code = String(payload?.code || payload?.error_code || '').toLowerCase().trim();
        if (code && ['auth_required', 'unauthorized', 'forbidden', 'invalid_token', 'token_expired'].includes(code)) {
            return true;
        }
        const text = String(payload?.error || payload?.message || payload?.detail || '').toLowerCase();
        return (
            text.includes('auth requis') ||
            text.includes('non autoris') ||
            text.includes('token invalide') ||
            text.includes('token expire') ||
            text.includes('session expir') ||
            text.includes('unauthorized') ||
            text.includes('forbidden')
        );
    },

    bindLogout: function(options = {}) {
        const buttonId = options.buttonId || 'logoutNav';
        const redirectTo = options.redirectTo || 'index.html';
        const logoutBtn = document.getElementById(buttonId);
        if (!logoutBtn || logoutBtn.dataset.uicoreBound === '1') return;
        logoutBtn.dataset.uicoreBound = '1';
        logoutBtn.addEventListener('click', (event) => {
            event.preventDefault();
            this.logout(redirectTo);
        });
    },

    bindSidebarToggle: function(options = {}) {
        const btnId = options.buttonId || 'histToggle';
        const sidebarId = options.sidebarId || 'histSidebar';
        const btn = document.getElementById(btnId);
        const sidebar = document.getElementById(sidebarId);
        if (!btn || !sidebar) return;
        if (btn.dataset.uicoreBound === '1') return;
        btn.dataset.uicoreBound = '1';
        btn.addEventListener('click', (event) => {
            event.preventDefault();
            sidebar.classList.toggle('collapsed');
        });
    },

    getDisplayName: function(user) {
        if (!user) return 'Utilisateur';
        const full = `${user.firstname || ''} ${user.lastname || ''}`.trim();
        return full || user.username || user.email || 'Utilisateur';
    },

    getInitials: function(user) {
        if (!user) return 'U';
        const first = (user.firstname || '')[0] || '';
        const second = (user.lastname || '')[0] || (user.username || '')[0] || (user.email || '')[0] || '';
        const initials = `${first}${second}`.toUpperCase().trim();
        return initials || 'U';
    },

    hydrateUserPill: function(user, options = {}) {
        if (!user) return;

        const ids = {
            navUser: options.nameId || 'navUser',
            avatar: options.avatarId || 'userAvatar',
            menuAvatar: options.menuAvatarId || 'menuUserAvatarLg',
            menuName: options.menuNameId || 'menuUserName',
            menuEmail: options.menuEmailId || 'menuUserEmail',
            welcome: options.welcomeId || 'welcome'
        };

        const displayName = this.getDisplayName(user);
        const initials = this.getInitials(user);
        const email = user.email || user.username || '';

        const navUser = document.getElementById(ids.navUser);
        if (navUser) navUser.textContent = displayName;

        const avatar = document.getElementById(ids.avatar);
        if (avatar) avatar.textContent = initials;

        const menuAvatar = document.getElementById(ids.menuAvatar);
        if (menuAvatar) menuAvatar.textContent = initials;

        const menuName = document.getElementById(ids.menuName);
        if (menuName) menuName.textContent = displayName;

        const menuEmail = document.getElementById(ids.menuEmail);
        if (menuEmail) menuEmail.textContent = email;

        const welcome = document.getElementById(ids.welcome);
        if (welcome) {
            const firstName = user.firstname || user.username || 'utilisateur';
            welcome.textContent = `Bienvenue, ${firstName} !`;
        }
    },

    initAdminLink: function(role, options = {}) {
        const adminItem = document.getElementById(options.adminItemId || 'navAdminItem');
        if (!adminItem) return;
        adminItem.style.display = role === 'admin' ? '' : 'none';
    },

    closeUserMenu: function(options = {}) {
        const menu = document.getElementById(options.menuId || 'userMenu');
        if (menu) menu.classList.remove('show');
    },

    initUserPill: function(options = {}) {
        const pill = document.getElementById(options.pillId || 'userPill');
        const menu = document.getElementById(options.menuId || 'userMenu');
        if (!pill || !menu) return;

        if (pill.dataset.uicoreBound !== '1') {
            pill.dataset.uicoreBound = '1';
            pill.addEventListener('click', (event) => {
                event.stopPropagation();
                if (menu.contains(event.target)) return;
                menu.classList.toggle('show');
            });
        }

        if (!this._menuGlobalBound) {
            this._menuGlobalBound = true;

            document.addEventListener('click', () => {
                document.querySelectorAll('.udrop.show').forEach((openMenu) => {
                    openMenu.classList.remove('show');
                });
            });

            document.addEventListener('keydown', (event) => {
                if (event.key === 'Escape') {
                    document.querySelectorAll('.udrop.show').forEach((openMenu) => {
                        openMenu.classList.remove('show');
                    });
                }
            });
        }
    },

    bootstrapAuthenticatedPage: function(options = {}) {
        const requireAuth = options.requireAuth !== false;
        const refreshProfile = options.refreshProfile === true;
        const redirectTo = options.redirectTo || 'index.html';

        const user = requireAuth ? this.requireUser(redirectTo) : this.getUser();
        if (!user) return null;

        this.hydrateUserPill(user, options.hydrateOptions || {});
        this.initAdminLink(user.role, options.adminOptions || {});
        this.initUserPill(options.menuOptions || {});
        this.bindLogout(options.logoutOptions || {});

        if (refreshProfile) {
            this.refreshUserProfile({ silent: true }).then((fresh) => {
                if (!fresh) return;
                this.hydrateUserPill(fresh, options.hydrateOptions || {});
                this.initAdminLink(fresh.role, options.adminOptions || {});
            }).catch(() => {});
        }

        // Enable sidebar toggle binding for small screens
        try { this.bindSidebarToggle(); } catch (_) { /* noop */ }

        return user;
    },

    refreshUserProfile: async function(options = {}) {
        const user = this.getUser();
        if (!user) return null;

        const token = this.getAuthToken(user);
        if (!token) return user;

        try {
            const res = await fetch(`${window.API_BASE}/api/profile`, {
                cache: 'no-store',
                headers: { Authorization: `Bearer ${token}` }
            });
            if (res.status === 401 || res.status === 403) {
                this.logout(options.redirectTo || 'index.html', { reason: 'unauthorized' });
                return null;
            }
            if (!res.ok) return user;
            const payload = await res.json();
            const fresh = payload?.data || payload || {};
            const updated = { ...user, ...fresh };
            this.saveUser(updated);
            this.hydrateUserPill(updated);
            this.initAdminLink(updated.role);
            return updated;
        } catch (error) {
            if (!options.silent) {
                console.error('[UICore] Failed to refresh profile', error);
            }
            return user;
        }
    }
};

(function initAuthRuntime() {
    if (!window.AUTH_CLEAR_ON_RELOAD) return;
    try {
        const nav = window.performance?.getEntriesByType?.('navigation')?.[0];
        if (nav && nav.type === 'reload') {
            window.UICore.clearAuthState();
        }
    } catch (_) {
        // noop
    }
})();

document.addEventListener('DOMContentLoaded', () => {
    if (window.UICore.getUser()) {
        window.UICore.bootstrapAuthenticatedPage({
            requireAuth: false,
            refreshProfile: false
        });
    }
});
