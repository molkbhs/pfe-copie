// ui-core.js - Shared UI/Auth helpers
window.API_BASE = window.location.origin.includes('localhost') || window.location.origin.includes('127.0.0.1')
    ? 'http://127.0.0.1:5000'
    : window.location.origin;

window.UICore = {
    _menuGlobalBound: false,

    getUser: function() {
        try {
            return JSON.parse(localStorage.getItem('user') || 'null');
        } catch (_) {
            return null;
        }
    },

    setUser: function(user) {
        if (!user) return;
        localStorage.setItem('user', JSON.stringify(user));
    },

    getAuthToken: function(user) {
        const resolved = user || this.getUser();
        if (!resolved) return '';
        return resolved.token || resolved.id || '';
    },

    isAuthenticated: function(user) {
        const resolved = user || this.getUser();
        if (!resolved || typeof resolved !== 'object') return false;
        const hasId = Boolean(resolved.id || resolved.token);
        const hasIdentity = Boolean(resolved.email || resolved.username || resolved.firstname);
        return hasId && hasIdentity;
    },

    authHeaders: function(options = {}) {
        const json = options.json === true;
        const user = options.user || this.getUser();
        const token = this.getAuthToken(user);
        const headers = {};
        if (token) headers['Authorization'] = `Bearer ${token}`;
        if (json) headers['Content-Type'] = 'application/json';
        if (options.extra && typeof options.extra === 'object') {
            Object.assign(headers, options.extra);
        }
        return headers;
    },

    requireUser: function(redirectTo = 'index.html') {
        const user = this.getUser();
        if (!this.isAuthenticated(user)) {
            window.location.replace(redirectTo);
            return null;
        }
        return user;
    },

    clearAuthState: function() {
        const theme = localStorage.getItem('theme');
        localStorage.clear();
        sessionStorage.clear();
        if (theme) {
            localStorage.setItem('theme', theme);
        }
    },

    logout: function(redirectTo = 'index.html', options = {}) {
        try {
            document.dispatchEvent(new CustomEvent('app:logout', {
                detail: { reason: options.reason || 'manual', redirectTo }
            }));
        } catch (_) {}
        this.clearAuthState();
        window.location.replace(redirectTo);
    },

    isUnauthorizedResponse: function(response, payload) {
        if (response && (response.status === 401 || response.status === 403)) return true;
        const text = String(
            payload?.error || payload?.message || payload?.detail || ''
        ).toLowerCase();
        return text.includes('auth') || text.includes('autoris');
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

        return user;
    },

    refreshUserProfile: async function(options = {}) {
        const user = this.getUser();
        if (!user) return null;

        const token = this.getAuthToken(user);
        if (!token) return user;

        try {
            const res = await fetch(`${window.API_BASE}/api/profile`, {
                headers: { 'Authorization': `Bearer ${token}` }
            });
            if (res.status === 401 || res.status === 403) {
                this.logout(options.redirectTo || 'index.html', { reason: 'unauthorized' });
                return null;
            }
            if (!res.ok) return user;
            const payload = await res.json();
            const fresh = payload?.data || payload || {};
            const updated = { ...user, ...fresh };
            this.setUser(updated);
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

document.addEventListener('DOMContentLoaded', () => {
    if (window.UICore.getUser()) {
        window.UICore.bootstrapAuthenticatedPage({
            requireAuth: false,
            refreshProfile: false
        });
    }
});
