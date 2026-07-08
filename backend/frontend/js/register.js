function register() {
    const firstnameInput = document.getElementById("firstname");
    const lastnameInput = document.getElementById("lastname");
    const emailInput = document.getElementById("email");
    const passwordInput = document.getElementById("password");
    const confirmPassInput = document.getElementById("confirmpass");

    const msgEl = document.getElementById("msg");
    const registerBtn = document.getElementById("registerBtn");

    const escapeHtml = (value) => {
        const div = document.createElement("div");
        div.textContent = value == null ? "" : String(value);
        return div.innerHTML;
    };

    const setMessage = (text, toneClass, iconClass) => {
        msgEl.className = `message ${toneClass}`;
        msgEl.innerHTML = `<i class="bi ${iconClass} me-1"></i>${escapeHtml(text)}`;
    };

    // Validation
    if (
        !firstnameInput.value.trim() ||
        !lastnameInput.value.trim() ||
        !emailInput.value.trim() ||
        !passwordInput.value.trim()
    ) {
        setMessage("Veuillez remplir tous les champs", "text-info", "bi-exclamation-triangle-fill");
        return;
    }

    if (passwordInput.value.length < 6) {
        setMessage("Le mot de passe doit contenir au moins 6 caractères", "text-info", "bi-exclamation-triangle-fill");
        return;
    }

    if (passwordInput.value !== confirmPassInput.value) {
        setMessage("Les mots de passe ne correspondent pas", "text-info", "bi-exclamation-triangle-fill");
        return;
    }

    // Désactiver bouton pendant la requête
    registerBtn.disabled = true;
    registerBtn.innerHTML = '<span class="loading"></span> Création du compte...';
    msgEl.className = "message";
    msgEl.innerText = "";

    const payload = {
        firstname: firstnameInput.value.trim(),
        lastname: lastnameInput.value.trim(),
        email: emailInput.value.trim(),
        password: passwordInput.value
    };

    fetch("http://127.0.0.1:5000/api/register", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
    })
    .then(res => res.json().then(body => ({ status: res.status, body })))
    .then(({ status, body }) => {
        if (status === 201 && body.user) {
            setMessage("Inscription réussie ! Redirection vers connexion...", "text-success", "bi-check-circle-fill");

            setTimeout(() => {
                window.location.href = "index.html";
            }, 1500);
        } else {
            setMessage(body.error || "Erreur lors de l'inscription", "text-danger", "bi-x-circle-fill");
            registerBtn.disabled = false;
            registerBtn.innerText = "Créer un compte";
        }
    })
    .catch(() => {
        setMessage("Impossible de joindre le serveur", "text-danger", "bi-x-circle-fill");
        registerBtn.disabled = false;
        registerBtn.innerText = "Créer un compte";
    });
}

/**
 * Toggle visibility of password and confirmation fields on register form
 */
function toggleRegisterPasswords() {
    ["password", "confirmpass"].forEach(id => {
        const input = document.getElementById(id);
        if (!input) return;
        input.type = input.type === "password" ? "text" : "password";
    });
}
