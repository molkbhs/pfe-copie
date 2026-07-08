/**
 * Authentication JavaScript - centralized frontend auth helpers
 * Uses UICore when available to keep storage/auth behavior consistent.
 */

function escapeHtml(value) {
    const div = document.createElement('div');
    div.textContent = value == null ? '' : String(value);
    return div.innerHTML;
}

function setMessage(msgEl, text, toneClass, iconClass) {
    if (!msgEl) return;
    msgEl.className = `message ${toneClass}`;
    msgEl.innerHTML = `<i class="bi ${iconClass} me-1"></i>${escapeHtml(text)}`;
}

function apiBase() {
    return window.API_BASE || 'http://127.0.0.1:5000';
}

function saveUser(user, options = {}) {
    if (!user || typeof user !== 'object') return null;

    if (window.UICore?.saveUser) {
        return window.UICore.saveUser(user, options);
    }

    try {
        sessionStorage.setItem('user', JSON.stringify(user));
        localStorage.removeItem('user');
    } catch (_) {
        // noop
    }
    return user;
}

function getUser(options = {}) {
    if (window.UICore?.getUser) {
        return window.UICore.getUser(options);
    }
    try {
        return JSON.parse(sessionStorage.getItem('user') || localStorage.getItem('user') || 'null');
    } catch (_) {
        return null;
    }
}

function logout(redirectTo = 'index.html') {
    if (window.UICore?.logout) {
        window.UICore.logout(redirectTo);
        return;
    }
    try {
        sessionStorage.removeItem('user');
        localStorage.removeItem('user');
    } catch (_) {
        // noop
    }
    window.location.replace(redirectTo);
}

function checkAuth(options = {}) {
    const redirectIfAuthenticated = options.redirectIfAuthenticated !== false;
    const adminPath = options.adminPath || 'admin.html';
    const userPath = options.userPath || 'dash.html';

    const user = window.UICore?.checkAuth
        ? window.UICore.checkAuth({ redirect: false, migrate: true })
        : getUser({ migrate: true });

    const isAuthed = window.UICore?.isAuthenticated
        ? window.UICore.isAuthenticated(user)
        : Boolean(user && user.token);

    if (!isAuthed) return null;

    if (redirectIfAuthenticated) {
        window.location.replace(user.role === 'admin' ? adminPath : userPath);
    }
    return user;
}

function login() {
    const emailInput = document.getElementById('email');
    const passwordInput = document.getElementById('password');
    const msgEl = document.getElementById('msg');
    const loginBtn = document.getElementById('loginBtn');

    if (!emailInput?.value.trim() || !passwordInput?.value.trim()) {
        setMessage(msgEl, 'Veuillez remplir tous les champs', 'text-info', 'bi-exclamation-triangle-fill');
        return;
    }

    loginBtn.disabled = true;
    loginBtn.innerHTML = '<span class="loading"></span> Connexion...';
    msgEl.className = 'message';
    msgEl.innerText = '';

    fetch(`${apiBase()}/api/login`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            email: emailInput.value.trim(),
            password: passwordInput.value
        })
    })
    .then((res) => res.json().then((body) => ({ status: res.status, body })))
    .then(({ status, body }) => {
        if (status === 200 && body.user) {
            const authUser = {
                ...body.user,
                token: body.token || body.user.token || ''
            };
            if (!authUser.token) {
                throw new Error('Token JWT manquant dans la réponse de connexion.');
            }
            setMessage(msgEl, 'Connexion reussie ! Redirection...', 'text-success', 'bi-check-circle-fill');
            saveUser(authUser);
            window.setTimeout(() => {
                window.location.href = authUser.role === 'admin' ? 'admin.html' : 'dash.html';
            }, 700);
            return;
        }
        setMessage(msgEl, body.error || 'Erreur de connexion', 'text-danger', 'bi-x-circle-fill');
        loginBtn.disabled = false;
        loginBtn.innerText = 'Se connecter';
    })
    .catch((err) => {
        console.error('Login error:', err);
        setMessage(msgEl, 'Impossible de joindre le serveur. Verifiez que le backend est demarre.', 'text-danger', 'bi-x-circle-fill');
        loginBtn.disabled = false;
        loginBtn.innerText = 'Se connecter';
    });
}

function toggleLoginPassword() {
    const input = document.getElementById('password');
    if (!input) return;
    input.type = input.type === 'password' ? 'text' : 'password';
}
