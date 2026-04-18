/* ═══════════════════════════════════════════════════════════════════════════
   SmartTag — AI Toll Gate Monitor v3  |  app.js
   ═══════════════════════════════════════════════════════════════════════════ */

const getApiBase = () => {
  const { protocol, hostname, port } = window.location;
  if (port && port !== "8000" && !hostname.includes("ngrok"))
    return `${protocol}//${hostname}:8000/api`;
  return "/api";
};
const API = getApiBase();

// ── Role Hierarchy ─────────────────────────────────────────────────────────
const ROLE_LEVELS = { viewer: 1, operator: 2, analyst: 3, admin: 4, superadmin: 5 };
function hasRole(required) {
  const level = ROLE_LEVELS[appState.user?.role] || 0;
  const req = ROLE_LEVELS[required] || 0;
  return level >= req;
}

// ── App State ──────────────────────────────────────────────────────────────
const appState = {
  user: null,
  currentPage: "dashboard",
  activeGate: 1,
  autoRefresh: true,
  liveTimer: null,
  healthTimer: null,
  clockTimer: null,
  charts: {},
  txPage: 1,
  mediaMode: "image",
  imageB64: null,
  videoFile: null,
};

// ── Init ───────────────────────────────────────────────────────────────────
document.addEventListener("DOMContentLoaded", () => {
  updateThemePills();
  const saved = localStorage.getItem("smarttag_user");
  if (saved) {
    try {
      appState.user = JSON.parse(saved);
      showApp();
    } catch { localStorage.removeItem("smarttag_user"); }
  }
  document.getElementById("login-password")?.addEventListener("keydown", e => {
    if (e.key === "Enter") doLogin();
  });
  document.getElementById("login-username")?.addEventListener("keydown", e => {
    if (e.key === "Enter") document.getElementById("login-password")?.focus();
  });
});

// ── Theme ──────────────────────────────────────────────────────────────────
function setTheme(t) {
  document.documentElement.setAttribute("data-theme", t);
  localStorage.setItem("theme", t);
  updateThemePills();
  if (appState.currentPage === "dashboard") setTimeout(loadDashboard, 50);
}
function toggleTheme() {
  const cur = document.documentElement.getAttribute("data-theme");
  setTheme(cur === "dark" ? "light" : "dark");
}
function updateThemePills() {
  const cur = document.documentElement.getAttribute("data-theme") || "dark";
  document.getElementById("theme-dark-btn")?.classList.toggle("active", cur === "dark");
  document.getElementById("theme-light-btn")?.classList.toggle("active", cur === "light");
}

// ── Auth ───────────────────────────────────────────────────────────────────
async function doLogin() {
  const username = document.getElementById("login-username").value.trim();
  const password = document.getElementById("login-password").value;
  const errEl = document.getElementById("login-error");
  const btn = document.getElementById("login-btn");
  errEl.classList.remove("show");
  if (!username || !password) { showAuthError("login", "Please enter username and password"); return; }
  btn.disabled = true; btn.textContent = "Signing in…";
  try {
    const res = await fetch(`${API}/auth/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Login failed");
    appState.user = data;
    localStorage.setItem("smarttag_user", JSON.stringify(data));
    showApp();
  } catch (e) {
    showAuthError("login", e.message);
  } finally {
    btn.disabled = false; btn.textContent = "Sign In";
  }
}

async function doRegister() {
  const fullname = document.getElementById("reg-fullname").value.trim();
  const username = document.getElementById("reg-username").value.trim();
  const email = document.getElementById("reg-email").value.trim();
  const phone = document.getElementById("reg-phone").value.trim();
  const role = document.getElementById("reg-role").value;
  const password = document.getElementById("reg-password").value;
  if (!fullname || !username || !password) { showAuthError("register", "Please fill required fields"); return; }
  if (password.length < 6) { showAuthError("register", "Password must be at least 6 characters"); return; }
  try {
    const res = await fetch(`${API}/auth/register`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password, full_name: fullname, email, phone, role }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Registration failed");
    toast(`Account created for ${username}. Please sign in.`, "success");
    showLogin();
  } catch (e) {
    showAuthError("register", e.message);
  }
}

function showAuthError(form, msg) {
  const el = document.getElementById(`${form}-error`);
  el.textContent = msg;
  el.classList.add("show");
}

function showRegister() {
  document.getElementById("login-card").style.display = "none";
  document.getElementById("register-card").style.display = "block";
}
function showLogin() {
  document.getElementById("register-card").style.display = "none";
  document.getElementById("login-card").style.display = "block";
}

function showApp() {
  const u = appState.user;
  document.getElementById("auth-screen").classList.add("hidden");
  document.getElementById("app-shell").classList.add("visible");

  // Populate sidebar user info
  setText("user-avatar-sidebar", u.avatar_initials || u.full_name?.slice(0, 2)?.toUpperCase() || "U");
  setText("user-name-sidebar", u.full_name || u.username);
  setText("user-role-sidebar", u.role);

  // Apply role-based nav visibility
  applyRoleNav();

  // Start app
  startClock();
  loadGates();
  navigateTo("dashboard");
  startAutoRefresh();
  loadFraudCount();
}

function applyRoleNav() {
  document.querySelectorAll(".nav-link[data-min-role]").forEach(link => {
    const required = link.dataset.minRole;
    link.style.display = hasRole(required) ? "" : "none";
  });
  // Admin-only items
  document.querySelectorAll("[data-admin-only]").forEach(el => {
    el.style.display = hasRole("admin") ? "" : "none";
  });
}

function logout() {
  if (!confirm("Log out of SmartTag Monitor?")) return;
  appState.user = null;
  localStorage.removeItem("smarttag_user");
  clearInterval(appState.clockTimer);
  clearTimeout(appState.liveTimer);
  clearInterval(appState.healthTimer);
  document.getElementById("app-shell").classList.remove("visible");
  document.getElementById("auth-screen").classList.remove("hidden");
  document.getElementById("login-username").value = "";
  document.getElementById("login-password").value = "";
  document.getElementById("login-error").classList.remove("show");
}

// ── Clock ──────────────────────────────────────────────────────────────────
function startClock() {
  const tick = () => {
    const t = new Date().toLocaleTimeString("en-IN", { hour12: false });
    setText("time-display", t);
    setText("topbar-clock", t);
  };
  tick();
  appState.clockTimer = setInterval(tick, 1000);
}

// ── Navigation ─────────────────────────────────────────────────────────────
function initNavigation() {
  document.querySelectorAll(".nav-link").forEach(link => {
    link.addEventListener("click", e => {
      e.preventDefault();
      navigateTo(link.dataset.page);
      if (window.innerWidth < 768) closeSidebar();
    });
  });
}

function navigateTo(page) {
  // Check role access for page
  const pageRoleMap = {
    "admin-users": "admin",
    "process": "operator",
    "fraud": "operator",
    "vehicles": "operator",
    "fastag": "operator",
  };
  const required = pageRoleMap[page];
  const contentEl = document.getElementById(`page-${page}`);
  if (!contentEl) return;

  if (required && !hasRole(required)) {
    // Show access denied inside the page
    document.querySelectorAll(".page").forEach(p => p.classList.remove("active"));
    contentEl.classList.add("active");
    contentEl.innerHTML = `
      <div class="access-denied">
        <div class="access-denied-icon">🔒</div>
        <h3>Access Restricted</h3>
        <p>Your role (<strong>${appState.user?.role}</strong>) does not have permission to view this section. 
        Contact your administrator for access.</p>
      </div>`;
    document.querySelectorAll(".nav-link").forEach(i => i.classList.toggle("active", i.dataset.page === page));
    setText("topbar-title", pageTitles[page] || page);
    appState.currentPage = page;
    return;
  }

  document.querySelectorAll(".nav-link").forEach(i => i.classList.toggle("active", i.dataset.page === page));
  document.querySelectorAll(".page").forEach(p => p.classList.toggle("active", p.id === `page-${page}`));
  setText("topbar-title", pageTitles[page] || page);
  appState.currentPage = page;

  const loaders = {
    dashboard: loadDashboard, live: loadLiveFeed,
    fraud: loadAlerts, transactions: () => loadTransactions(1),
    vehicles: loadVehicles, fastag: loadFASTag,
    gates: loadGates, notifications: loadNotifications,
    "admin-users": loadAdminUsers, profile: loadProfile,
  };
  loaders[page]?.();
}

const pageTitles = {
  dashboard: "Dashboard", process: "Process Vehicle", live: "Live Feed",
  fraud: "Fraud Alerts", transactions: "Transactions", vehicles: "Vehicle Registry",
  fastag: "FASTag Management", gates: "Toll Gates", notifications: "Notifications",
  "admin-users": "User Management", profile: "My Profile",
};

// ── Sidebar Toggle ─────────────────────────────────────────────────────────
function toggleSidebar() {
  const sb = document.getElementById("sidebar");
  const ov = document.getElementById("sidebar-overlay");
  const open = sb.classList.toggle("open");
  ov.classList.toggle("show", open);
  document.body.style.overflow = open && window.innerWidth < 768 ? "hidden" : "";
}
function closeSidebar() {
  document.getElementById("sidebar").classList.remove("open");
  document.getElementById("sidebar-overlay").classList.remove("show");
  document.body.style.overflow = "";
}

// Init nav after DOM ready
document.addEventListener("DOMContentLoaded", initNavigation);

// ── API Helper ─────────────────────────────────────────────────────────────
async function api(path, options = {}) {
  try {
    options.headers = options.headers || {};
    if (!options.headers["Content-Type"] && options.body && typeof options.body === "string")
      options.headers["Content-Type"] = "application/json";
    const res = await fetch(`${API}${path}`, options);
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `HTTP ${res.status}`);
    }
    return await res.json();
  } catch (e) {
    console.error(`API error ${path}:`, e.message);
    return null;
  }
}

async function loadFraudCount() {
  const data = await api("/fraud/alerts?resolved=false&limit=200");
  if (data) setText("fraud-count", data.count > 0 ? data.count : "");
}

// ── Gates ──────────────────────────────────────────────────────────────────
async function loadGates() {
  const data = await api("/gates");
  if (!data) return;
  const opts = data.gates.map(g => `<option value="${g.id}">${g.gate_name}</option>`).join("");
  ["active-gate", "process-gate"].forEach(id => {
    const el = document.getElementById(id);
    if (el && el.children.length <= 1) el.innerHTML = opts;
  });
  const grid = document.getElementById("gates-grid");
  if (!grid) return;
  const vt = [["Car", "toll_car"], ["Truck", "toll_truck"], ["Bus", "toll_bus"], ["Bike", "toll_bike"], ["Van", "toll_van"]];
  grid.innerHTML = data.gates.map(g => `
    <div class="gate-card">
      <div class="gate-card-header"><div class="gate-card-name">${g.gate_name}</div><div class="gate-highway">${g.highway}</div></div>
      <div class="gate-location">📍 ${g.location}</div>
      <div class="gate-tolls">${vt.map(([l, c]) => `<div class="gate-toll-item"><div class="gate-toll-type">${l}</div><div class="gate-toll-amount">₹${g[c]}</div></div>`).join("")}</div>
    </div>`).join("");
}

function onGateChange(val) {
  appState.activeGate = parseInt(val) || 1;
  if (appState.currentPage === "dashboard") loadDashboard();
}

// ── Dashboard ──────────────────────────────────────────────────────────────
let isFetchingDashboard = false;
async function loadDashboard() {
  if (isFetchingDashboard) return;
  isFetchingDashboard = true;
  const gId = appState.activeGate > 0 ? `?gate_id=${appState.activeGate}` : "";
  const [stats, live] = await Promise.all([api(`/dashboard/stats${gId}`), api("/dashboard/live?limit=8")]);
  isFetchingDashboard = false;
  if (!stats) return;

  setText("stat-today-total", fmt(stats.today?.total));
  setText("stat-today-rev", "₹" + fmt(stats.today?.revenue));
  setText("stat-today-fraud", fmt(stats.today?.fraud));
  setText("stat-open-alerts", fmt(stats.open_alerts));
  setText("fraud-count", stats.open_alerts > 0 ? stats.open_alerts : "");

  if (stats.weekly) renderWeeklyChart(stats.weekly);
  if (stats.vehicle_types) renderTypesChart(stats.vehicle_types);
  if (stats.top_fraud_types) renderFraudTypesList(stats.top_fraud_types);

  if (live) {
    const tbody = document.querySelector("#dash-transactions tbody");
    if (tbody) tbody.innerHTML = (live.transactions || []).map(t => `
      <tr><td>${fmtTime(t.processed_at)}</td><td class="mono">${t.plate_number}</td>
      <td>${capitalize(t.vehicle_type)}</td><td>₹${t.toll_amount}</td><td>${statusPill(t.status)}</td></tr>`).join("");
  }
}

function getChartColors() {
  const isDark = document.documentElement.getAttribute("data-theme") === "dark";
  return {
    grid: isDark ? "rgba(255,255,255,0.05)" : "rgba(0,0,0,0.06)",
    tick: isDark ? "#8b949e" : "#64748b",
    bg: isDark ? "#1c2230" : "#ffffff",
  };
}

function renderWeeklyChart(weekly) {
  const ctx = document.getElementById("chart-weekly"); if (!ctx) return;
  const c = getChartColors();
  const labels = weekly.map(r => new Date(r.day + "T00:00:00").toLocaleDateString("en-IN", { weekday: "short", day: "numeric" }));
  const ds = [
    { label: "Vehicles", data: weekly.map(r => r.count), backgroundColor: "rgba(245,158,11,0.55)", borderColor: "#f59e0b", borderWidth: 1, borderRadius: 4, yAxisID: "y1" },
    { label: "Revenue (₹)", data: weekly.map(r => Math.round(r.revenue)), type: "line", borderColor: "#10b981", backgroundColor: "rgba(16,185,129,0.1)", borderWidth: 2, pointRadius: 3, fill: true, tension: 0.4, yAxisID: "y2" },
  ];
  if (appState.charts.weekly) {
    appState.charts.weekly.data.labels = labels;
    appState.charts.weekly.data.datasets = ds;
    appState.charts.weekly.update("none");
  } else {
    appState.charts.weekly = new Chart(ctx, {
      type: "bar", data: { labels, datasets: ds },
      options: {
        responsive: true, animation: { duration: 750 }, plugins: { legend: { display: false } },
        scales: {
          y1: { position: "left", grid: { color: c.grid }, ticks: { color: c.tick, font: { size: 10 } } },
          y2: { position: "right", grid: { display: false }, ticks: { color: c.tick, font: { size: 10 } } },
          x: { grid: { color: c.grid }, ticks: { color: c.tick, font: { size: 10 } } }
        }
      }
    });
  }
}

function renderTypesChart(types) {
  const ctx = document.getElementById("chart-types"); if (!ctx) return;
  const COLORS = ["#f59e0b", "#10b981", "#3b82f6", "#8b5cf6", "#f97316"];
  const c = getChartColors();
  if (appState.charts.types) appState.charts.types.destroy();
  appState.charts.types = new Chart(ctx, {
    type: "doughnut",
    data: { labels: types.map(t => capitalize(t.vehicle_type)), datasets: [{ data: types.map(t => t.count), backgroundColor: COLORS, borderWidth: 0, hoverOffset: 6, borderColor: c.bg }] },
    options: { responsive: true, cutout: "62%", plugins: { legend: { position: "bottom", labels: { color: c.tick, font: { size: 11 }, padding: 10 } } } }
  });
}

function renderFraudTypesList(types) {
  const el = document.getElementById("fraud-types-list"); if (!el) return;
  if (!types.length) { el.innerHTML = `<p style="color:var(--text-tertiary);font-size:12px;text-align:center;padding:20px">No fraud data</p>`; return; }
  const max = Math.max(...types.map(t => t.count), 1);
  el.innerHTML = types.slice(0, 6).map(t => `
    <div class="ft-row">
      <div class="ft-label" title="${t.fraud_type}">${t.fraud_type || "Unknown"}</div>
      <div class="ft-bar-wrap"><div class="ft-bar" style="width:${(t.count / max) * 100}%"></div></div>
      <div class="ft-count">${t.count}</div>
    </div>`).join("");
}

// ── Auto Refresh ───────────────────────────────────────────────────────────
function startAutoRefresh() {
  if (appState.liveTimer) clearTimeout(appState.liveTimer);
  if (appState.healthTimer) clearInterval(appState.healthTimer);
  appState.healthTimer = setInterval(() => api("/health"), 30000);
  const refresh = async () => {
    if (appState.autoRefresh && !document.hidden) {
      if (appState.currentPage === "live") await loadLiveFeed();
      else if (appState.currentPage === "dashboard") await loadDashboard();
    }
    appState.liveTimer = setTimeout(refresh, appState.currentPage === "live" ? 5000 : 10000);
  };
  appState.liveTimer = setTimeout(refresh, 10000);
}
function toggleAutoRefresh(cb) { appState.autoRefresh = cb.checked; }

// ── Live Feed ──────────────────────────────────────────────────────────────
async function loadLiveFeed() {
  const data = await api("/dashboard/live?limit=30");
  if (!data) return;
  const tbody = document.getElementById("live-tbody"); if (!tbody) return;
  setText("live-count", `${data.count} recorded events`);
  const existing = new Set(Array.from(tbody.querySelectorAll("tr")).map(r => r.dataset.tid));
  data.transactions.filter(t => !existing.has(t.transaction_id)).forEach(t => {
    const tr = document.createElement("tr");
    tr.dataset.tid = t.transaction_id; tr.classList.add("fade-in");
    tr.innerHTML = `<td class="mono" style="font-size:11px">${t.transaction_id}</td><td class="mono">${t.plate_number}</td>
      <td>${t.gate_name || "—"}</td><td>${capitalize(t.vehicle_type)}</td><td>₹${t.toll_amount}</td>
      <td class="mono">₹${fmtMoney(t.balance_before)}</td><td>${statusPill(t.status)}</td>
      <td style="color:${(t.fraud_score || 0) > 0.5 ? "var(--danger)" : "var(--success)"}">${Math.round((t.fraud_score || 0) * 100)}%</td>
      <td style="font-size:11px">${fmtDateTime(t.processed_at)}</td>`;
    tbody.prepend(tr);
  });
  while (tbody.children.length > 30) tbody.removeChild(tbody.lastChild);
}

// ── Fraud Alerts ───────────────────────────────────────────────────────────
async function loadAlerts() {
  const severity = document.getElementById("alert-severity")?.value || "";
  const resolved = document.getElementById("alert-resolved")?.value;
  let q = `/fraud/alerts?limit=60`;
  if (severity) q += `&severity=${severity}`;
  if (resolved) q += `&resolved=${resolved}`;
  const data = await api(q);
  const grid = document.getElementById("alerts-grid"); if (!data || !grid) return;
  if (!data.alerts.length) { grid.innerHTML = `<div class="card" style="grid-column:1/-1;text-align:center;color:var(--text-tertiary);padding:40px">No alerts found</div>`; return; }
  grid.innerHTML = data.alerts.map(a => `
    <div class="alert-card ${a.severity} ${a.is_resolved ? "resolved" : ""}">
      <div class="alert-card-header"><div class="alert-plate">${a.plate_number || "UNKNOWN"}</div><div class="alert-time">${fmtDateTime(a.created_at)}</div></div>
      <div class="alert-type">${a.alert_type || "—"}</div>
      <div class="alert-desc">${a.description || ""}</div>
      <div class="alert-card-footer">
        ${severityPill(a.severity)}
        ${a.is_resolved ? `<span class="resolved-tag">✓ Resolved by ${a.resolved_by || "admin"}</span>`
      : `<button class="resolve-btn" onclick="resolveAlert(${a.id},this)">✓ Resolve</button>`}
      </div>
    </div>`).join("");
}

async function resolveAlert(id, btn) {
  const u = appState.user?.username || "admin";
  const res = await api(`/fraud/alerts/${id}/resolve?resolved_by=${u}`, { method: "PATCH" });
  if (res?.success) {
    btn.closest(".alert-card").classList.add("resolved");
    btn.outerHTML = `<span class="resolved-tag">✓ Resolved</span>`;
    toast("Alert resolved", "success");
    loadFraudCount();
  } else toast("Failed to resolve alert", "error");
}

// ── Transactions ───────────────────────────────────────────────────────────
async function loadTransactions(page = 1) {
  appState.txPage = page;
  const plate = document.getElementById("tx-plate-search")?.value || "";
  const status = document.getElementById("tx-status")?.value || "";
  const dfrom = document.getElementById("tx-date-from")?.value || "";
  const dto = document.getElementById("tx-date-to")?.value || "";
  let q = `/transactions?page=${page}&limit=20`;
  if (plate) q += `&plate=${plate}`;
  if (status) q += `&status=${status}`;
  if (dfrom) q += `&date_from=${dfrom}`;
  if (dto) q += `&date_to=${dto}`;
  const data = await api(q);
  const tbody = document.getElementById("tx-tbody"); if (!data || !tbody) return;
  if (!data.transactions.length) { tbody.innerHTML = `<tr><td colspan="10" style="text-align:center;color:var(--text-tertiary);padding:30px">No transactions found</td></tr>`; return; }
  tbody.innerHTML = data.transactions.map(t => `
    <tr>
      <td class="mono" style="font-size:11px">${t.transaction_id}</td>
      <td class="mono">${t.plate_number}</td>
      <td class="mono" style="font-size:11px">${t.fastag_id || "—"}</td>
      <td>${t.gate_name || "—"}</td><td>${capitalize(t.vehicle_type)}</td>
      <td>₹${t.toll_amount}</td>
      <td class="mono" style="font-size:11px">₹${fmtMoney(t.balance_before)}→₹${fmtMoney(t.balance_after)}</td>
      <td>${statusPill(t.status)}</td>
      <td style="color:${(t.fraud_score || 0) > 0.5 ? "var(--danger)" : "var(--success)"}">${Math.round((t.fraud_score || 0) * 100)}%</td>
      <td style="font-size:11px">${fmtDateTime(t.processed_at)}</td>
    </tr>`).join("");
  renderPagination("tx-pagination", data.page, data.pages, loadTransactions);
}

// ── Vehicles ───────────────────────────────────────────────────────────────
async function loadVehicles() {
  const search = document.getElementById("veh-search")?.value || "";
  const bl = document.getElementById("veh-blacklist")?.value || "";
  let q = "/vehicles?";
  if (search) q += `search=${encodeURIComponent(search)}&`;
  if (bl === "true") q += "blacklisted=true";
  const data = await api(q);
  const tbody = document.getElementById("veh-tbody"); if (!data || !tbody) return;
  if (!data.vehicles.length) { tbody.innerHTML = `<tr><td colspan="9" style="text-align:center;color:var(--text-tertiary);padding:30px">No vehicles found</td></tr>`; return; }
  tbody.innerHTML = data.vehicles.map(v => `
    <tr>
      <td class="mono"><strong>${v.plate_number}</strong></td>
      <td>${v.owner_name}</td><td>${v.owner_phone || "—"}</td>
      <td>${capitalize(v.vehicle_type)}</td>
      <td><span class="chip">${v.fuel_type || "—"}</span></td>
      <td>${v.state_code || "—"}</td>
      <td>${v.is_blacklisted ? `<span class="pill pill-danger">Blacklisted</span>` : `<span class="pill pill-success">Active</span>`}</td>
      <td style="font-size:11px">${fmtDate(v.registered_at)}</td>
      <td style="display:flex;gap:4px;flex-wrap:wrap">
        <button class="chip" onclick="showEditVehicle('${v.plate_number}','${v.owner_name}','${v.owner_phone || ""}','${v.vehicle_type}','${v.fuel_type || ""}')">Edit</button>
        ${!v.is_blacklisted ? `<button class="chip danger" onclick="blacklistVehicle('${v.plate_number}')">Blacklist</button>` : ""}
        ${hasRole("admin") ? `<button class="chip danger" onclick="deleteVehicle('${v.plate_number}')">Del</button>` : ""}
      </td>
    </tr>`).join("");
}

function showEditVehicle(plate, name, phone, type, fuel) {
  document.getElementById("edit-veh-plate").value = plate;
  document.getElementById("edit-veh-name").value = name;
  document.getElementById("edit-veh-phone").value = phone;
  document.getElementById("edit-veh-type").value = type;
  document.getElementById("edit-veh-fuel").value = fuel;
  showModal("modal-edit-vehicle");
}

async function doEditVehicle() {
  const plate = document.getElementById("edit-veh-plate").value;
  const payload = {
    owner_name: document.getElementById("edit-veh-name").value,
    owner_phone: document.getElementById("edit-veh-phone").value,
    vehicle_type: document.getElementById("edit-veh-type").value,
    fuel_type: document.getElementById("edit-veh-fuel").value,
  };
  const res = await api(`/vehicles/${plate}`, { method: "PUT", body: JSON.stringify(payload) });
  if (res?.success) { toast("Vehicle updated", "success"); closeModal(); loadVehicles(); }
  else toast("Update failed", "error");
}

async function doAddVehicle() {
  const plate = document.getElementById("av-plate").value.trim().toUpperCase();
  const name = document.getElementById("av-name").value.trim();
  if (!plate || !name) { toast("Plate and owner name required", "warn"); return; }
  const res = await api("/vehicles", {
    method: "POST", body: JSON.stringify({
      plate_number: plate, owner_name: name,
      owner_phone: document.getElementById("av-phone").value || null,
      owner_email: document.getElementById("av-email").value || null,
      vehicle_type: document.getElementById("av-type").value,
      fuel_type: document.getElementById("av-fuel").value || null,
    })
  });
  if (res?.success) { toast(`Vehicle ${plate} registered`, "success"); closeModal(); loadVehicles(); }
  else toast("Failed (plate may exist)", "error");
}

async function blacklistVehicle(plate) {
  const reason = prompt(`Reason for blacklisting ${plate}:`);
  if (!reason) return;
  const res = await api("/vehicles/blacklist", { method: "POST", body: JSON.stringify({ plate_number: plate, reason }) });
  if (res?.success) { toast(`${plate} blacklisted`, "success"); loadVehicles(); }
  else toast("Failed", "error");
}

async function deleteVehicle(plate) {
  if (!confirm(`Delete vehicle ${plate}? This cannot be undone.`)) return;
  const res = await api(`/vehicles/${plate}`, { method: "DELETE" });
  if (res?.success) { toast(`${plate} deleted`, "success"); loadVehicles(); }
  else toast("Delete failed", "error");
}

// ── FASTag ─────────────────────────────────────────────────────────────────
async function loadFASTag() {
  const search = document.getElementById("ft-search")?.value || "";
  const data = await api(`/fastag${search ? "?search=" + encodeURIComponent(search) : ""}`);
  const tbody = document.getElementById("ft-tbody"); if (!data || !tbody) return;
  if (!data.fastags.length) { tbody.innerHTML = `<tr><td colspan="8" style="text-align:center;color:var(--text-tertiary);padding:30px">No FASTag accounts found</td></tr>`; return; }
  tbody.innerHTML = data.fastags.map(f => `
    <tr>
      <td class="mono" style="font-size:11px">${f.fastag_id}</td>
      <td class="mono">${f.plate_number}</td>
      <td>${f.owner_name || "—"}</td><td>${f.bank_name || "—"}</td>
      <td class="mono" style="color:${f.balance < 100 ? "var(--danger)" : "var(--success)"};font-weight:600">₹${fmtMoney(f.balance)}</td>
      <td>${capitalize(f.vehicle_type)}</td>
      <td>${f.is_active ? `<span class="pill pill-success">Active</span>` : `<span class="pill pill-danger">Inactive</span>`}</td>
      <td style="display:flex;gap:4px">
        <button class="chip" onclick="quickTopUp('${f.fastag_id}')">Top Up</button>
        ${hasRole("admin") ? `<button class="chip danger" onclick="deleteFASTag('${f.fastag_id}')">Del</button>` : ""}
      </td>
    </tr>`).join("");
}

async function doAddFASTag() {
  const id = document.getElementById("ft-id").value.trim();
  const plate = document.getElementById("ft-plate").value.trim().toUpperCase();
  const bank = document.getElementById("ft-bank").value.trim();
  if (!id || !plate || !bank) { toast("FASTag ID, Plate and Bank required", "warn"); return; }
  const res = await api("/fastag", {
    method: "POST", body: JSON.stringify({
      fastag_id: id, plate_number: plate, bank_name: bank,
      balance: parseFloat(document.getElementById("ft-balance").value) || 0,
      vehicle_type: document.getElementById("ft-type").value,
      owner_name: document.getElementById("ft-owner").value || null,
    })
  });
  if (res?.success) { toast("FASTag added", "success"); closeModal(); loadFASTag(); }
  else toast("Failed (may already exist)", "error");
}

async function deleteFASTag(id) {
  if (!confirm(`Delete FASTag ${id}?`)) return;
  const res = await api(`/fastag/${id}`, { method: "DELETE" });
  if (res?.success) { toast("FASTag deleted", "success"); loadFASTag(); }
  else toast("Delete failed", "error");
}

function quickTopUp(fastag_id) {
  document.getElementById("topup-id").value = fastag_id;
  showModal("modal-topup");
}

async function doTopUp() {
  const id = document.getElementById("topup-id").value.trim();
  const amt = parseFloat(document.getElementById("topup-amount").value);
  if (!id || isNaN(amt) || amt <= 0) { toast("Enter valid FASTag ID and amount", "warn"); return; }
  const res = await api("/fastag/topup", { method: "POST", body: JSON.stringify({ fastag_id: id, amount: amt }) });
  if (res?.success) { toast(`₹${amt} added. New balance: ₹${fmtMoney(res.new_balance)}`, "success"); closeModal(); loadFASTag(); }
  else toast("Top-up failed", "error");
}

// ── Admin Users ────────────────────────────────────────────────────────────
async function loadAdminUsers() {
  if (!hasRole("admin")) return;
  const data = await api("/users");
  const grid = document.getElementById("admin-users-grid"); if (!data || !grid) return;
  const roleColors = { superadmin: "#ef4444", admin: "#f59e0b", analyst: "#3b82f6", operator: "#10b981", viewer: "#8b5cf6" };
  grid.innerHTML = data.users.map(u => `
    <div class="user-card ${u.is_active ? '' : 'user-inactive'}">
      <div class="user-card-header">
        <div class="user-card-avatar" style="background:${roleColors[u.role] || "#666"}">${u.avatar_initials || u.full_name?.slice(0, 2)?.toUpperCase() || "U"}</div>
        <div class="user-card-info">
          <h4>${u.full_name || u.username}</h4>
          <p>@${u.username}</p>
        </div>
      </div>
      <div class="user-card-meta">
        <div class="user-meta-item"><div class="k">ROLE</div><div class="v"><span class="role-chip role-${u.role}" style="background:${roleColors[u.role] || "#666"}22;color:${roleColors[u.role] || "#666"}">${u.role}</span></div></div>
        <div class="user-meta-item"><div class="k">STATUS</div><div class="v">${u.is_active ? '✅ Active' : '❌ Inactive'}</div></div>
        <div class="user-meta-item"><div class="k">EMAIL</div><div class="v" style="font-size:11px">${u.email || "—"}</div></div>
        <div class="user-meta-item"><div class="k">LAST LOGIN</div><div class="v" style="font-size:11px">${u.last_login ? fmtDateTime(u.last_login) : "Never"}</div></div>
      </div>
      <div class="user-card-actions">
        <button class="btn btn-outline" style="flex:1;font-size:12px" onclick="showEditUser(${JSON.stringify(u).replace(/"/g, '&quot;')})">✏️ Edit</button>
        ${u.id !== appState.user?.id ? `<button class="btn btn-outline" style="font-size:12px;color:var(--danger);border-color:var(--danger)" onclick="deleteUser(${u.id},'${u.username}')">🗑️</button>` : `<button class="btn btn-outline" style="font-size:12px;opacity:0.4" disabled>🗑️</button>`}
      </div>
    </div>`).join("");
}

function showEditUser(u) {
  document.getElementById("edit-user-id").value = u.id;
  document.getElementById("eu-fullname").value = u.full_name || "";
  document.getElementById("eu-email").value = u.email || "";
  document.getElementById("eu-phone").value = u.phone || "";
  document.getElementById("eu-role").value = u.role;
  document.getElementById("eu-active").value = u.is_active ?? "1";
  document.getElementById("eu-password").value = "";
  showModal("modal-edit-user");
}

async function doEditUser() {
  const id = document.getElementById("edit-user-id").value;
  const payload = {
    full_name: document.getElementById("eu-fullname").value,
    email: document.getElementById("eu-email").value,
    phone: document.getElementById("eu-phone").value,
    role: document.getElementById("eu-role").value,
    is_active: parseInt(document.getElementById("eu-active").value),
  };
  const pw = document.getElementById("eu-password").value;
  if (pw) payload.password = pw;
  const res = await api(`/users/${id}`, { method: "PUT", body: JSON.stringify(payload) });
  if (res?.success) { toast("User updated", "success"); closeModal(); loadAdminUsers(); }
  else toast("Update failed", "error");
}

async function doAddUser() {
  const username = document.getElementById("au-username").value.trim();
  const fullname = document.getElementById("au-fullname").value.trim();
  const password = document.getElementById("au-password").value;
  const role = document.getElementById("au-role").value;
  if (!username || !fullname || !password) { toast("Fill required fields", "warn"); return; }
  const res = await api("/auth/register", {
    method: "POST", body: JSON.stringify({
      username, full_name: fullname, password, role,
      email: document.getElementById("au-email").value || null,
      phone: document.getElementById("au-phone").value || null,
    })
  });
  if (res?.success) { toast(`User ${username} created`, "success"); closeModal(); loadAdminUsers(); }
  else toast("Failed (username may exist)", "error");
}

async function deleteUser(id, username) {
  if (!confirm(`Delete user "${username}"? This cannot be undone.`)) return;
  const res = await api(`/users/${id}`, { method: "DELETE" });
  if (res?.success) { toast(`User ${username} deleted`, "success"); loadAdminUsers(); }
  else toast("Delete failed", "error");
}

// ── Profile ────────────────────────────────────────────────────────────────
async function loadProfile() {
  const u = appState.user; if (!u) return;
  const data = await api(`/users/${u.id}`);
  const src = data || u;
  const initials = src.avatar_initials || src.full_name?.slice(0, 2)?.toUpperCase() || "U";
  setText("profile-avatar", initials);
  setText("profile-fullname", src.full_name || "—");
  setText("profile-username", "@" + src.username);
  setText("profile-email", src.email || "—");
  setText("profile-phone", src.phone || "—");
  setText("profile-last-login", src.last_login ? "Last login: " + fmtDateTime(src.last_login) : "First session");
  const badge = document.getElementById("profile-role-badge");
  if (badge) { badge.textContent = src.role; badge.className = `profile-role-badge role-${src.role}`; }
  document.getElementById("pf-fullname").value = src.full_name || "";
  document.getElementById("pf-email").value = src.email || "";
  document.getElementById("pf-phone").value = src.phone || "";
  document.getElementById("pf-role").value = src.role;
  // Stats
  const stats = await api(`/dashboard/stats`);
  const mini = document.getElementById("profile-stats-mini");
  if (mini && stats) {
    mini.innerHTML = `
      <div class="profile-stat-mini"><div class="val">${fmt(stats.all_time?.total)}</div><div class="lbl">Total Txns</div></div>
      <div class="profile-stat-mini"><div class="val">${fmt(stats.open_alerts)}</div><div class="lbl">Open Alerts</div></div>`;
  }
}

async function saveProfile() {
  const u = appState.user; if (!u) return;
  const payload = {
    full_name: document.getElementById("pf-fullname").value,
    email: document.getElementById("pf-email").value,
    phone: document.getElementById("pf-phone").value,
  };
  const res = await api(`/users/${u.id}`, { method: "PUT", body: JSON.stringify(payload) });
  if (res?.success) {
    appState.user = { ...u, ...payload };
    localStorage.setItem("smarttag_user", JSON.stringify(appState.user));
    setText("user-name-sidebar", payload.full_name || u.full_name);
    toast("Profile updated", "success");
  } else toast("Update failed", "error");
}

async function changePassword() {
  const pw = document.getElementById("pf-newpw").value;
  const conf = document.getElementById("pf-confirmpw").value;
  if (!pw || pw.length < 6) { toast("Password must be at least 6 characters", "warn"); return; }
  if (pw !== conf) { toast("Passwords do not match", "warn"); return; }
  const res = await api(`/users/${appState.user.id}`, { method: "PUT", body: JSON.stringify({ password: pw }) });
  if (res?.success) { toast("Password changed successfully", "success"); document.getElementById("pf-newpw").value = ""; document.getElementById("pf-confirmpw").value = ""; }
  else toast("Failed to change password", "error");
}

// ── Notifications ──────────────────────────────────────────────────────────
async function loadNotifications() {
  const data = await api("/notifications?limit=50");
  const tbody = document.getElementById("notif-tbody"); if (!data || !tbody) return;
  if (!data.notifications.length) { tbody.innerHTML = `<tr><td colspan="5" style="text-align:center;color:var(--text-tertiary);padding:30px">No notifications</td></tr>`; return; }
  tbody.innerHTML = data.notifications.map(n => `
    <tr>
      <td class="mono">${n.recipient_phone || "—"}</td>
      <td>${n.recipient_type === "admin" ? `<span class="pill pill-danger">Admin</span>` : `<span class="pill pill-success">Owner</span>`}</td>
      <td style="max-width:380px;white-space:normal;font-size:12px;font-family:var(--mono)">${n.message || ""}</td>
      <td>${statusPill(n.status || "sent")}</td>
      <td style="font-size:11px">${fmtDateTime(n.sent_at)}</td>
    </tr>`).join("");
}

// ── Process Vehicle ────────────────────────────────────────────────────────
function switchMediaTab(mode, btn) {
  appState.mediaMode = mode;
  document.querySelectorAll(".media-tab").forEach(b => b.classList.toggle("active", b === btn));
  document.getElementById("media-tab-image").style.display = mode === "image" ? "" : "none";
  document.getElementById("media-tab-video").style.display = mode === "video" ? "" : "none";
}

function handleDragOver(e) { e.preventDefault(); e.currentTarget.classList.add("drag-over"); }
function handleDragLeave(e) { e.currentTarget.classList.remove("drag-over"); }
function handleDrop(e, type) {
  e.preventDefault(); e.currentTarget.classList.remove("drag-over");
  const file = e.dataTransfer?.files[0]; if (!file) return;
  const fakeEvent = { target: { files: [file] } };
  previewMedia(fakeEvent, type);
}

function previewMedia(event, type) {
  const file = event.target.files[0]; if (!file) return;
  if (type === "image") {
    const reader = new FileReader();
    reader.onload = e => {
      appState.imageB64 = e.target.result;
      const img = document.getElementById("image-preview");
      img.src = appState.imageB64; img.style.display = "block";
      const wrap = document.getElementById("img-preview-wrap");
      wrap.classList.add("has-media");
    };
    reader.readAsDataURL(file);
  } else {
    appState.videoFile = file;
    const vid = document.getElementById("video-preview");
    vid.src = URL.createObjectURL(file);
    document.getElementById("vid-preview-wrap").classList.add("has-media");
  }
}

function clearMedia(type) {
  if (type === "image") {
    appState.imageB64 = null;
    document.getElementById("image-preview").src = "";
    document.getElementById("img-preview-wrap").classList.remove("has-media");
    document.getElementById("image-input").value = "";
  } else {
    appState.videoFile = null;
    const vid = document.getElementById("video-preview");
    URL.revokeObjectURL(vid.src); vid.src = "";
    document.getElementById("vid-preview-wrap").classList.remove("has-media");
    document.getElementById("video-input").value = "";
  }
}

function captureCamera() {
  navigator.mediaDevices.getUserMedia({ video: { facingMode: "environment" } })
    .then(stream => {
      const v = document.createElement("video");
      v.autoplay = true; v.srcObject = stream;
      document.body.appendChild(v);
      v.style.cssText = "position:fixed;top:-9999px;left:-9999px";
      setTimeout(() => {
        const c = document.createElement("canvas");
        c.width = v.videoWidth || 640; c.height = v.videoHeight || 480;
        c.getContext("2d").drawImage(v, 0, 0);
        appState.imageB64 = c.toDataURL("image/jpeg");
        const img = document.getElementById("image-preview");
        img.src = appState.imageB64;
        document.getElementById("img-preview-wrap").classList.add("has-media");
        stream.getTracks().forEach(t => t.stop()); document.body.removeChild(v);
      }, 800);
    })
    .catch(() => toast("Camera not available. Use file upload.", "warn"));
}

function demoPlate(plate, type = null) {
  document.getElementById("manual-plate").value = plate;
  if (type) document.getElementById("manual-type").value = type;
  toast(`Loaded demo: ${plate}`, "info");
}

async function simulateLive() {
  const plate = document.getElementById("manual-plate").value.trim().toUpperCase();
  const vtype = document.getElementById("manual-type").value;
  const gateId = parseInt(document.getElementById("process-gate")?.value) || appState.activeGate;
  const btn = document.getElementById("simulate-btn");
  btn.disabled = true;
  const res = await api(`/simulate?plate=${plate}&gate_id=${gateId}${vtype ? "&vtype=" + vtype : ""}`, { method: "POST" });
  btn.disabled = false;
  if (res) renderResult(res);
}

async function processVehicle() {
  const plate = document.getElementById("manual-plate").value.trim().toUpperCase();
  const vtype = document.getElementById("manual-type").value;
  const gateId = parseInt(document.getElementById("process-gate")?.value) || appState.activeGate;
  const btn = document.getElementById("process-btn");
  btn.disabled = true;
  btn.innerHTML = `<span class="spin">⚙</span> Processing…`;

  let result = null;

  if (appState.mediaMode === "video" && appState.videoFile) {
    // Video processing via FormData upload
    const fd = new FormData();
    fd.append("file", appState.videoFile);
    fd.append("gate_id", gateId);
    try {
      const res = await fetch(`${API}/process/video`, { method: "POST", body: fd });
      if (res.ok) result = await res.json();
      else { const e = await res.json(); toast(e.detail || "Video processing failed", "error"); }
    } catch (e) { toast("Video upload failed: " + e.message, "error"); }
  } else if (appState.imageB64) {
    // Image with base64
    const payload = {
      image_base64: appState.imageB64.includes(",") ? appState.imageB64.split(",")[1] : appState.imageB64,
      gate_id: gateId, manual_plate: plate || null, manual_type: vtype || null
    };
    result = await api("/process", { method: "POST", body: JSON.stringify(payload) });
  } else {
    // Manual plate only
    const payload = { gate_id: gateId, manual_plate: plate || null, manual_type: vtype || null };
    result = await api("/process", { method: "POST", body: JSON.stringify(payload) });
  }

  btn.disabled = false;
  btn.innerHTML = `⚡ Process Vehicle`;
  if (!result) { toast("Processing failed — check backend", "error"); return; }
  renderResult(result);
  if (result.video_info) toast(`Video: ${result.video_info.frames_analyzed} frames analyzed`, "info");
}

function renderResult(r) {
  document.getElementById("idle-card").style.display = "none";
  document.getElementById("result-card").style.display = "block";
  const isSuccess = r.transaction?.status === "success";
  setText("result-txn", `${r.transaction_id}  ·  ${r.toll?.gate_name || ""}`);
  setText("result-plate", r.plate_number || "—");
  setText("result-ocr-conf", `OCR: ${Math.round((r.ocr?.confidence || 0) * 100)}%  ·  mode: ${r.ocr?.mode || "—"}`);
  animateGate(isSuccess, r.vehicle?.vehicle_type || "car");

  const fastag = r.fastag || {}, vehicle = r.vehicle || {}, toll = r.toll || {};
  document.getElementById("result-grid").innerHTML = `
    <div class="rg-item"><div class="rg-label">Owner</div><div class="rg-value">${vehicle.owner_name || "Unknown"}</div></div>
    <div class="rg-item"><div class="rg-label">Vehicle Type</div><div class="rg-value orange">${capitalize(vehicle.vehicle_type)}</div></div>
    <div class="rg-item"><div class="rg-label">Fuel Type</div><div class="rg-value blue">${vehicle.fuel_type || "UNKNOWN"}</div></div>
    <div class="rg-item"><div class="rg-label">State</div><div class="rg-value">${vehicle.state_code || "—"}</div></div>
    <div class="rg-item"><div class="rg-label">FASTag Bank</div><div class="rg-value">${fastag.bank || "—"}</div></div>
    <div class="rg-item"><div class="rg-label">Balance Before</div><div class="rg-value">₹${fmtMoney(fastag.balance_before)}</div></div>
    <div class="rg-item"><div class="rg-label">Balance After</div><div class="rg-value ${isSuccess ? "green" : "red"}">₹${fmtMoney(fastag.balance_after)}</div></div>
    <div class="rg-item"><div class="rg-label">Toll Amount</div><div class="rg-value orange">₹${toll.amount || 0}</div></div>
    <div class="rg-item"><div class="rg-label">Barrier</div><div class="rg-value ${isSuccess ? "green" : "red"}">${r.transaction?.barrier || "—"}</div></div>
    <div class="rg-item"><div class="rg-label">FASTag</div><div class="rg-value ${fastag.found && fastag.active ? "green" : "red"}">${fastag.found ? (fastag.active ? "Active" : "Inactive") : "Not Found"}</div></div>`;

  const fraud = r.fraud || {};
  const score = fraud.score_pct || 0;
  const fill = document.getElementById("fraud-meter-fill");
  fill.style.width = `${score}%`;
  fill.style.background = score >= 75 ? "var(--danger)" : score >= 50 ? "var(--warn)" : "var(--success)";
  setText("fraud-score-pct", `${score}%`);
  setText("fraud-severity", `Severity: ${(fraud.severity || "low").toUpperCase()}`);
  const fl = document.getElementById("fraud-details-list");
  fl.innerHTML = fraud.descriptions?.length ? `<div class="fraud-details-list">${fraud.descriptions.map(d => `<div class="fraud-item"><span class="fraud-item-icon">⚠</span><span>${d}</span></div>`).join("")}</div>` : "";

  const actions = document.getElementById("result-actions");
  window.lastResultData = r; // Store for printing
  if (isSuccess) {
    actions.innerHTML = `<div style="display:flex;gap:8px"><button class="btn btn-primary" onclick="printReceipt()">🖨 Print Receipt</button><button class="btn btn-outline" onclick="navigateTo('transactions')">View Log</button></div>`;
    toast(`Toll collected ₹${toll.amount} — ${r.plate_number}`, "success");
  } else {
    actions.innerHTML = `<div style="display:flex;gap:8px"><button class="btn btn-danger" onclick="navigateTo('fraud')">🚨 View Alert</button><button class="btn btn-outline" onclick="navigateTo('transactions')">View Log</button></div>`;
    toast(`Fraud detected: ${r.plate_number} — ${(fraud.severity || "").toUpperCase()}`, "error");
  }
  setTimeout(loadDashboard, 1000);
  loadFraudCount();
}

function animateGate(open, type) {
  const bar = document.getElementById("gate-bar");
  const car = document.getElementById("gate-icon-car");
  const badge = document.getElementById("gate-status-badge");
  if (!bar || !car || !badge) return;
  const icons = { car: "🚗", truck: "🚛", bus: "🚌", bike: "🏍️", van: "🚐" };
  car.textContent = icons[type] || "🚗";
  car.style.left = "16px";
  car.classList.remove("drive-through");
  bar.classList.remove("open", "closed");
  badge.className = "gate-badge"; badge.textContent = "—";
  setTimeout(() => {
    if (open) {
      bar.classList.add("open"); badge.textContent = "BARRIER OPEN"; badge.className = "gate-badge open";
      setTimeout(() => car.classList.add("drive-through"), 600);
    } else {
      bar.classList.add("closed"); badge.textContent = "BARRIER CLOSED"; badge.className = "gate-badge closed";
    }
  }, 100);
}

// ── PDF Export ─────────────────────────────────────────────────────────────
async function exportToPDF(type) {
  toast(`Generating ${type} PDF…`, "info");
  const url = `${API}/${type === "users" ? "users" : type}/export/pdf`;
  try {
    const res = await fetch(url);
    if (!res.ok) { toast("Export failed", "error"); return; }
    const blob = await res.blob();
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = `smarttag-${type}-${new Date().toISOString().slice(0, 10)}.pdf`;
    document.body.appendChild(link); link.click(); document.body.removeChild(link);
    toast("PDF downloaded", "success");
  } catch (e) {
    toast("Export failed: " + e.message, "error");
  }
}

// ── Modals ─────────────────────────────────────────────────────────────────
function showModal(id) {
  document.getElementById("modal-backdrop").classList.add("visible");
  document.getElementById(id)?.classList.add("visible");
}
function closeModal() {
  document.getElementById("modal-backdrop").classList.remove("visible");
  document.querySelectorAll(".modal").forEach(m => m.classList.remove("visible"));
}

// ── Password strength ──────────────────────────────────────────────────────
function checkPwStrength(pw) {
  const bar = document.getElementById("pw-bar"); if (!bar) return;
  let score = 0;
  if (pw.length >= 6) score++;
  if (pw.length >= 10) score++;
  if (/[A-Z]/.test(pw)) score++;
  if (/[0-9]/.test(pw)) score++;
  if (/[^A-Za-z0-9]/.test(pw)) score++;
  const pct = (score / 5) * 100;
  const color = score <= 1 ? "#ef4444" : score <= 3 ? "#f59e0b" : "#10b981";
  bar.style.width = pct + "%";
  bar.style.background = color;
}

// ── Pagination ─────────────────────────────────────────────────────────────
function renderPagination(elId, current, total, fn) {
  const el = document.getElementById(elId);
  if (!el || total <= 1) { if (el) el.innerHTML = ""; return; }
  let html = "";
  if (current > 1) html += `<button class="page-btn" onclick="${fn.name}(${current - 1})">← Prev</button>`;
  const start = Math.max(1, current - 2), end = Math.min(total, current + 2);
  for (let p = start; p <= end; p++) html += `<button class="page-btn ${p === current ? "active" : ""}" onclick="${fn.name}(${p})">${p}</button>`;
  if (current < total) html += `<button class="page-btn" onclick="${fn.name}(${current + 1})">Next →</button>`;
  el.innerHTML = html;
}

// ── Toasts ─────────────────────────────────────────────────────────────────
function toast(msg, type = "success") {
  const ct = document.getElementById("toast-container");
  const el = document.createElement("div");
  el.className = `toast ${type}`;
  const icons = { success: "✅", error: "❌", warn: "⚠️", info: "ℹ️" };
  el.innerHTML = `<span>${icons[type] || "ℹ️"}</span><span>${msg}</span>`;
  ct.appendChild(el);
  setTimeout(() => el?.remove(), 4200);
}

// ── Helpers ────────────────────────────────────────────────────────────────
function setText(id, val) { const el = document.getElementById(id); if (el) el.textContent = val; }
function fmt(n) { return Number(n || 0).toLocaleString("en-IN"); }
function fmtMoney(n) { return Number(n || 0).toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 }); }
function fmtTime(ts) { if (!ts) return "—"; return new Date(ts).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit" }); }
function fmtDate(ts) { if (!ts) return "—"; return new Date(ts).toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "numeric" }); }
function fmtDateTime(ts) { if (!ts) return "—"; const d = new Date(ts); return `${d.toLocaleDateString("en-IN", { day: "2-digit", month: "short" })} ${d.toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit" })}`; }
function capitalize(s) { return s ? s.charAt(0).toUpperCase() + s.slice(1) : "—"; }
function statusPill(s) { const m = { success: "pill-success", fraud: "pill-danger", failed: "pill-warn", pending: "pill-info", sent: "pill-success" }; return `<span class="pill ${m[s] || "pill-gray"}">${s}</span>`; }
function severityPill(s) { const m = { critical: "pill-danger", high: "pill-danger", medium: "pill-warn", low: "pill-info" }; return `<span class="pill ${m[s] || "pill-gray"}">${s}</span>`; }

// ── Printing ───────────────────────────────────────────────────────────────
function printReceipt() {
  const r = window.lastResultData;
  if (!r) { toast("No transaction data available", "error"); return; }
  
  const w = window.open("", "_blank", "width=400,height=600");
  const html = `
    <!DOCTYPE html>
    <html>
    <head>
      <title>Toll Receipt - ${r.transaction_id}</title>
      <style>
        body { font-family: 'Courier New', Courier, monospace; width: 300px; margin: 0 auto; color: #000; padding: 20px 0; }
        .center { text-align: center; }
        .bold { font-weight: bold; }
        h2, h3 { margin: 5px 0; }
        hr { border: 0; border-top: 1px dashed #000; margin: 10px 0; }
        .row { display: flex; justify-content: space-between; margin-bottom: 5px; font-size: 14px; }
        .footer { font-size: 12px; margin-top: 20px; }
        @media print {
            body { width: 100%; padding: 0; }
        }
      </style>
    </head>
    <body onload="window.print(); window.close();">
      <div class="center">
        <h2>SmartTag Toll</h2>
        <h3>${r.toll?.gate_name || 'Toll Plaza'}</h3>
        <p style="font-size:12px">${fmtDateTime(r.transaction?.processed_at || new Date())}</p>
      </div>
      <hr>
      <div class="row"><span>Txn ID:</span> <span class="bold">${r.transaction_id}</span></div>
      <div class="row"><span>Plate:</span> <span class="bold">${r.plate_number}</span></div>
      <div class="row"><span>Type:</span> <span>${capitalize(r.vehicle?.vehicle_type)}</span></div>
      <div class="row"><span>Status:</span> <span>Paid via FASTag</span></div>
      <hr>
      <div class="row bold"><span>Toll Fee:</span> <span>INR ${r.toll?.amount || 0}</span></div>
      <hr>
      <div class="center footer">
        <p>Thank you for a safe journey!</p>
        <p>Drive safely.</p>
      </div>
    </body>
    </html>
  `;
  w.document.open();
  w.document.write(html);
  w.document.close();
}
