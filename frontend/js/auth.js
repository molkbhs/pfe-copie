/**
 * Authentication JavaScript - Login functionality
 * Handles both email/password and Google OAuth login
 */

function escapeHtml(value) {
    const div = document.createElement("div");
    div.textContent = value == null ? "" : String(value);
    return div.innerHTML;
}

function setMessage(msgEl, text, toneClass, iconClass) {
    if (!msgEl) return;
    msgEl.className = `message ${toneClass}`;
    msgEl.innerHTML = `<i class="bi ${iconClass} me-1"></i>${escapeHtml(text)}`;
}

function login() {
    const emailInput = document.getElementById("email");
    const passwordInput = document.getElementById("password");
    const msgEl = document.getElementById("msg");
    const loginBtn = document.getElementById("loginBtn");

    // Validation
    if (!emailInput.value.trim() || !passwordInput.value.trim()) {
        setMessage(msgEl, "Veuillez remplir tous les champs", "text-info", "bi-exclamation-triangle-fill");
        return;
    }

    // Disable button during request
    loginBtn.disabled = true;
    loginBtn.innerHTML = '<span class="loading"></span> Connexion...';
    msgEl.className = "message";
    msgEl.innerText = "";

    fetch("http://127.0.0.1:5000/api/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
            email: emailInput.value,
            password: passwordInput.value
        })
    })
    .then(res => res.json().then(body => ({status: res.status, body})))
    .then(({status, body}) => {
        if (status === 200 && body.user) {
            setMessage(msgEl, "Connexion réussie ! Redirection...", "text-success", "bi-check-circle-fill");
            
            // Store user data in localStorage
            localStorage.setItem("user", JSON.stringify(body.user));
            
            // Redirect to dashboard
            setTimeout(() => {
                window.location.href = body.user.role === 'admin' ? 'admin.html' : 'dash.html';
            }, 1500);
        } else {
            setMessage(msgEl, body.error || "Erreur de connexion", "text-danger", "bi-x-circle-fill");
            loginBtn.disabled = false;
            loginBtn.innerText = "Se connecter";
        }
    })
    .catch(err => {
        console.error("Login error:", err);
        setMessage(msgEl, "Impossible de joindre le serveur. Vérifiez que le backend est démarré.", "text-danger", "bi-x-circle-fill");
        loginBtn.disabled = false;
        loginBtn.innerText = "Se connecter";
    });
}

/**
 * Initiate Google OAuth login flow
 * Redirects user to Google's authorization page
 */
function loginGoogle() {
    // Show loading message
    const msgEl = document.getElementById("msg");
    if (msgEl) {
        setMessage(msgEl, "Redirection vers Google...", "text-info", "bi-arrow-repeat");
    }
    
    // Redirect to backend OAuth endpoint
    // The backend will handle the OAuth flow and redirect back with user data
    window.location.href = "http://127.0.0.1:5000/api/auth/google";
}

/**
 * Check if user is already logged in
 * Redirect to dashboard if authenticated
 */
function checkAuth() {
    const user = localStorage.getItem("user");
    if (user) {
        try {
            const userData = JSON.parse(user);
            if (userData.id && userData.email) {
                // User is authenticated, redirect to dashboard
                window.location.href = userData.role === 'admin' ? 'admin.html' : 'dash.html';
            }
        } catch (e) {
            // Invalid user data, clear it
            localStorage.removeItem("user");
        }
    }
}

/**
 * Logout function
 * Clears user data and redirects to login
 */
function logout() {
    localStorage.clear();
    sessionStorage.clear();
    const url = (window.location.origin && window.location.origin !== "null") ? window.location.origin + "/index.html" : "index.html";
    window.location.replace(url);
}

/**
 * Toggle password visibility on login form
 */
function toggleLoginPassword() {
    const input = document.getElementById("password");
    if (!input) return;
    input.type = input.type === "password" ? "text" : "password";
}

// Auto-check authentication status when page loads
// Uncomment if you want to auto-redirect authenticated users
// window.addEventListener('DOMContentLoaded', checkAuth);

