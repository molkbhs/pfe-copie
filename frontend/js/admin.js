/**
 * Admin Dashboard - Chart.js, API, Animations
 */
const API = (window.location.origin || "http://127.0.0.1:5000") + "/api";

let chartReg, chartLogin, chartActivity;
let currentPage = 1;
let searchTimeout;

function authHeaders(json = false) {
  if (window.UICore?.authHeaders) {
    return window.UICore.authHeaders({ json });
  }
  let token = '';
  try {
    const user = JSON.parse(sessionStorage.getItem('user') || localStorage.getItem('user') || 'null');
    token = user?.token || '';
  } catch (_) {}
  const headers = token ? { Authorization: `Bearer ${token}` } : {};
  if (json) headers['Content-Type'] = 'application/json';
  return headers;
}

// ==================== INIT ====================
document.addEventListener("DOMContentLoaded", () => {
  updateDateTime();
  setInterval(updateDateTime, 1000);
  loadStats();
  loadChartData();
  loadUsers();
  document.getElementById("searchUsers").addEventListener("input", debounce(onSearch, 300));

  // Déconnexion admin
  const adminLogout = document.getElementById("adminLogout");
  if (adminLogout) {
    adminLogout.addEventListener("click", (e) => {
      e.preventDefault();
      if (window.UICore?.logout) {
        window.UICore.logout("index.html");
        return;
      }
      try {
        localStorage.removeItem("user");
        sessionStorage.removeItem("user");
      } catch (_) {}
      window.location.replace("index.html");
    });
  }
});

function updateDateTime() {
  const el = document.getElementById("currentDateTime");
  if (el) el.textContent = new Date().toLocaleString("fr-FR", {
    weekday: "long",
    day: "numeric",
    month: "long",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit"
  });
}

function debounce(fn, ms) {
  return function (...args) {
    clearTimeout(searchTimeout);
    searchTimeout = setTimeout(() => fn.apply(this, args), ms);
  };
}

// ==================== STATS ====================
async function loadStats() {
  try {
    const res = await fetch(`${API}/stats`, { headers: authHeaders() });
    const data = await res.json();
    if (data.error) throw new Error(data.error);

    animateValue("statTotal", 0, data.total_users, 800);
    animateValue("statNewToday", 0, data.new_today, 600);
    animateValue("statActive", 0, data.active_week, 700);
    animateValue("statGoogle", 0, data.google_users, 500);
    animateValue("statEmail", 0, data.email_users, 600);

    const sub = data.total_users > 0 ? `${data.email_users} email + ${data.google_users} Google` : "—";
    document.getElementById("statTotalSub").textContent = sub;
    if (data.new_today > 0) document.getElementById("statNewSub").textContent = `+${data.new_today} aujourd'hui`;
    if (data.active_week > 0) document.getElementById("statActiveSub").textContent = `Sur 7 jours`;
  } catch (err) {
    console.error("Stats:", err);
    ["statTotal", "statNewToday", "statActive", "statGoogle", "statEmail"].forEach(id => {
      const el = document.getElementById(id);
      if (el) el.textContent = "—";
    });
  }
}

function animateValue(id, start, end, duration) {
  const el = document.getElementById(id);
  if (!el) return;
  const startTime = performance.now();
  function update(now) {
    const elapsed = now - startTime;
    const progress = Math.min(elapsed / duration, 1);
    const eased = 1 - Math.pow(1 - progress, 3);
    el.textContent = Math.floor(start + (end - start) * eased);
    if (progress < 1) requestAnimationFrame(update);
  }
  requestAnimationFrame(update);
}

// ==================== CHARTS ====================
async function loadChartData() {
  try {
    const res = await fetch(`${API}/users/chart-data`, { headers: authHeaders() });
    const data = await res.json();
    if (data.error) throw new Error(data.error);

    renderChartRegistrations(data.registrations);
    renderChartLoginType(data.login_type);
    renderChartActivity(data.activity_week);
  } catch (err) {
    console.error("Charts:", err);
    renderChartRegistrations([]);
    renderChartLoginType([]);
    renderChartActivity([]);
  }
}

function renderChartRegistrations(registrations) {
  const ctx = document.getElementById("chartRegistrations")?.getContext("2d");
  if (!ctx) return;

  if (chartReg) chartReg.destroy();

  const labels = registrations.map(r => r.date?.slice(5) || "");
  const values = registrations.map(r => r.count || 0);

  chartReg = new Chart(ctx, {
    type: "line",
    data: {
      labels,
      datasets: [{
        label: "Inscriptions",
        data: values,
        borderColor: "#0D9488",
        backgroundColor: "rgba(13, 148, 136, 0.12)",
        fill: true,
        tension: 0.4,
        borderWidth: 2,
        pointBackgroundColor: "#0D9488",
        pointBorderColor: "#fff",
        pointBorderWidth: 2
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: { duration: 800 },
      plugins: { legend: { display: false } },
      scales: {
        y: { beginAtZero: true, grid: { color: "#E2E8F0" }, ticks: { font: { size: 12 } } },
        x: { grid: { display: false }, ticks: { font: { size: 11 }, maxRotation: 45 } }
      }
    }
  });
}

function renderChartLoginType(loginType) {
  const ctx = document.getElementById("chartLoginType")?.getContext("2d");
  if (!ctx) return;

  if (chartLogin) chartLogin.destroy();

  const labels = loginType.map(r => r.type === "google" ? "Google" : "Email");
  const values = loginType.map(r => r.count);
  const colors = ["#0D9488", "#F59E0B"];

  chartLogin = new Chart(ctx, {
    type: "doughnut",
    data: {
      labels,
      datasets: [{
        data: values,
        backgroundColor: colors,
        borderWidth: 0
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: { duration: 800 },
      plugins: {
        legend: { position: "bottom", labels: { padding: 20, usePointStyle: true } }
      }
    }
  });
}

function renderChartActivity(activityWeek) {
  const ctx = document.getElementById("chartActivity")?.getContext("2d");
  if (!ctx) return;

  if (chartActivity) chartActivity.destroy();

  const labels = activityWeek.map((r, i) => `S${i + 1}`);
  const values = activityWeek.map(r => r.count || 0);

  chartActivity = new Chart(ctx, {
    type: "bar",
    data: {
      labels,
      datasets: [{
        label: "Utilisateurs",
        data: values,
        backgroundColor: "rgba(13, 148, 136, 0.7)",
        borderColor: "#0D9488",
        borderWidth: 1,
        borderRadius: 6
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: { duration: 800 },
      plugins: { legend: { display: false } },
      scales: {
        y: { beginAtZero: true, grid: { color: "#E2E8F0" }, ticks: { font: { size: 12 } } },
        x: { grid: { display: false }, ticks: { font: { size: 11 } } }
      }
    }
  });
}

// ==================== USERS TABLE ====================
function onSearch() {
  currentPage = 1;
  loadUsers();
}

async function loadUsers() {
  const search = document.getElementById("searchUsers")?.value || "";
  try {
    const res = await fetch(`${API}/users/recent?page=${currentPage}&search=${encodeURIComponent(search)}`, {
      headers: authHeaders()
    });
    const data = await res.json();
    if (data.error) throw new Error(data.error);

    renderUsers(data.users);
    renderPagination(data);
  } catch (err) {
    console.error("Users:", err);
    document.getElementById("usersTableBody").innerHTML =
      '<tr><td colspan="7" class="text-center py-4 text-danger">Erreur de chargement</td></tr>';
  }
}

function renderUsers(users) {
  const tbody = document.getElementById("usersTableBody");
  if (!tbody) return;

  if (!users || users.length === 0) {
    tbody.innerHTML = '<tr><td colspan="7" class="text-center py-4">Aucun utilisateur</td></tr>';
    return;
  }

  tbody.innerHTML = users.map(u => `
    <tr>
      <td>${u.id}</td>
      <td>${escapeHtml(u.username)}</td>
      <td>${escapeHtml(u.email)}</td>
      <td>${u.created_at || "—"}</td>
      <td><span class="badge-${u.login_type === "google" ? "google" : "email"}">${u.login_type === "google" ? "Google" : "Email"}</span></td>
      <td><span class="badge-${u.status === "active" ? "active" : "inactive"}">${u.status === "active" ? "Actif" : "Inactif"}</span></td>
      <td>${u.last_login || "—"}</td>
    </tr>
  `).join("");
}

function escapeHtml(s) {
  if (!s) return "";
  const div = document.createElement("div");
  div.textContent = s;
  return div.innerHTML;
}

function renderPagination(data) {
  const total = data.total || 0;
  const page = data.page || 1;
  const perPage = data.per_page || 10;
  const pages = data.pages || 1;

  document.getElementById("tableInfo").textContent =
    `${total} utilisateur(s) · Page ${page}/${pages || 1}`;

  const pagEl = document.getElementById("pagination");
  if (!pagEl) return;

  let html = "";
  html += `<button ${page <= 1 ? "disabled" : ""} onclick="goPage(${page - 1})">Précédent</button>`;
  for (let i = 1; i <= Math.min(pages, 5); i++) {
    html += `<button class="${i === page ? "active" : ""}" onclick="goPage(${i})">${i}</button>`;
  }
  html += `<button ${page >= pages ? "disabled" : ""} onclick="goPage(${page + 1})">Suivant</button>`;
  pagEl.innerHTML = html;
}

function goPage(p) {
  currentPage = p;
  loadUsers();
}

// ==================== QUICK ACTIONS ====================
function addUserModal() {
  alert("Fonctionnalité à implémenter : formulaire d'ajout d'utilisateur.");
}

function deleteInactive() {
  if (!confirm("Supprimer tous les utilisateurs inactifs ?")) return;
  alert("Endpoint à implémenter côté backend.");
}

function sendNotification() {
  alert("Fonctionnalité à implémenter : envoi d'email de notification.");
}
// ── Add User Modal ─────────────────────────────────────────
function openAddUserModal() {
  document.getElementById('newFirstname').value = '';
  document.getElementById('newLastname').value  = '';
  document.getElementById('newEmail').value     = '';
  document.getElementById('newPassword').value  = '';
  document.getElementById('newRole').value      = 'user';
  document.getElementById('addUserError').style.display = 'none';
  document.getElementById('addUserModal').classList.add('open');
}

function closeAddUserModal() {
  document.getElementById('addUserModal').classList.remove('open');
}

// Fermer en cliquant sur l'overlay
document.getElementById('addUserModal').addEventListener('click', function(e) {
  if (e.target === this) closeAddUserModal();
});

async function submitAddUser() {
  const firstname = document.getElementById('newFirstname').value.trim();
  const lastname  = document.getElementById('newLastname').value.trim();
  const email     = document.getElementById('newEmail').value.trim();
  const password  = document.getElementById('newPassword').value;
  const role      = document.getElementById('newRole').value;
  const errEl     = document.getElementById('addUserError');
  const btn       = document.getElementById('btnConfirmAdd');

  // Validation simple
  errEl.style.display = 'none';
  if (!firstname || !lastname || !email || !password) {
    errEl.innerHTML = '<i class="bi bi-exclamation-triangle-fill me-1"></i>Tous les champs sont obligatoires.';
    errEl.style.display = 'block';
    return;
  }
  if (password.length < 6) {
    errEl.innerHTML = '<i class="bi bi-exclamation-triangle-fill me-1"></i>Le mot de passe doit contenir au moins 6 caractères.';
    errEl.style.display = 'block';
    return;
  }

  btn.disabled = true;
  btn.textContent = 'Création...';

  try {
    const r = await fetch('/api/admin/users', {
      method: 'POST',
      headers: authHeaders(true),
      body: JSON.stringify({ firstname, lastname, email, password, role })
    });
    const d = await r.json();

    if (d.error) {
      errEl.innerHTML = `<i class="bi bi-x-circle-fill me-1"></i>${escapeHtml(d.error)}`;
      errEl.style.display = 'block';
      return;
    }

    toast(`Compte de ${firstname} ${lastname} créé avec succès !`);
    closeAddUserModal();
    loadUsers();
    loadStats();

  } catch(e) {
    errEl.innerHTML = '<i class="bi bi-x-circle-fill me-1"></i>Erreur réseau.';
    errEl.style.display = 'block';
  } finally {
    btn.disabled = false;
    btn.innerHTML = '<i class="bi bi-person-plus-fill me-1"></i>Créer le compte';
  }
}
function addUserToTable(user) {
  const tableBody = document.querySelector("#usersTable tbody");

  const tr = document.createElement("tr");
  tr.innerHTML = `
    <td>${user.id}</td>
    <td>${user.firstname} ${user.lastname}</td>
    <td>${user.email}</td>
    <td>
      <span class="badge ${user.role === 'admin' ? 'bg-danger' : 'bg-primary'}">
        ${user.role}
      </span>
    </td>
  `;

  // Ajouter en haut du tableau
  tableBody.prepend(tr);
}
