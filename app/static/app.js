/* Pinnacle Intelligence System - single-page front end (no build step). */
"use strict";

/* Self-repair: a browser that kept an old copy of the page (from before versioned
   assets) has no version tag. Reload it once, fetching a fresh page. */
const PAGE_VERSION = document.querySelector('meta[name="pis-version"]')?.content || null;
(() => {
  let reloaded = null;
  try { reloaded = sessionStorage.getItem("pis_reloaded"); } catch (_) { /* storage blocked */ }
  if (!PAGE_VERSION && !reloaded) {
    try { sessionStorage.setItem("pis_reloaded", "1"); } catch (_) {}
    location.replace(`${location.pathname}?fresh=${Date.now()}${location.hash}`);
    throw new Error("Reloading to get the latest version");
  }
  if (location.search.includes("fresh=")) history.replaceState(null, "", location.pathname + location.hash);
})();

const ROLE_LEVEL = { viewer: 0, junior: 1, senior: 2, admin: 3 };
const ROLE_NAMES = { viewer: "Viewer", junior: "Junior Surveyor", senior: "Senior Surveyor", admin: "Admin" };
const state = { token: null, user: null, health: null, recordsPage: 1, charts: {}, facets: null };

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
/* Amount boxes show commas (1,500,000) while typing; values are saved as plain numbers. */
const toNum = (s) => { const t = String(s ?? "").replace(/[,\s]/g, ""); return t === "" ? null : Number(t); };
function withCommas(v) {
  if (v === null || v === undefined || v === "") return "";
  const t = String(v).replace(/[,\s]/g, "");
  const m = t.match(/^(-?)(\d*)(\.\d*)?$/);
  if (!m) return String(v);
  const int = m[2] ? m[2].replace(/^0+(?=\d)/, "").replace(/\B(?=(\d{3})+(?!\d))/g, ",") : (m[3] ? "0" : "");
  return m[1] + int + (m[3] || "");
}
document.addEventListener("input", (e) => {
  const el = e.target;
  if (!(el instanceof HTMLInputElement) || !el.dataset.num) return;
  const clean = el.value.replace(/[^\d.,\-\s]/g, "");
  const pos = el.selectionStart ?? el.value.length;
  const keep = el.value.slice(0, pos).replace(/[^\d.\-]/g, "").length;
  const out = withCommas(clean);
  if (out === el.value) return;
  el.value = out;
  let i = 0;
  for (let n = 0; i < out.length && n < keep; i++) if (/[\d.\-]/.test(out[i])) n++;
  try { el.setSelectionRange(i, i); } catch (_) {}
}, true);
const NUM_ATTR = 'type="text" inputmode="decimal" autocomplete="off" data-num="1"';
const fmt = (v, digits = 0) => (v === null || v === undefined || v === "" ? "—" : Number(v).toLocaleString(undefined, { maximumFractionDigits: digits, minimumFractionDigits: 0 }));
const can = (role) => state.user && ROLE_LEVEL[state.user.role] >= ROLE_LEVEL[role];

/* All 261 MMDAs (embedded in the page): [name, region, capital]. */
const MMDAS = window.PIS_MMDAS || [];
const MMDA_REGION = Object.fromEntries(MMDAS.map(([n, r]) => [n, r]));
const MMDA_REGIONS = [...new Set(MMDAS.map(([, r]) => r))].sort((a, b) =>
  (a === "Greater Accra" ? -1 : b === "Greater Accra" ? 1 : a.localeCompare(b)));
/* <option>s for an MMDA drop-down, grouped by region. Keeps an unlisted current value. */
function mmdaOptions(value, blank = "— Select MMDA —") {
  const v = value || "";
  const extra = v && !MMDA_REGION[v] ? `<option value="${esc(v)}" selected>${esc(v)} (not in official list)</option>` : "";
  return `<option value="">${esc(blank)}</option>${extra}` + MMDA_REGIONS.map((region) =>
    `<optgroup label="${esc(region)} Region">${MMDAS.filter(([, r]) => r === region)
      .map(([n]) => `<option value="${esc(n)}"${n === v ? " selected" : ""}>${esc(n)}</option>`).join("")}</optgroup>`).join("");
}

function toast(msg, isErr = false) {
  const t = $("#toast");
  t.textContent = msg;
  t.className = "toast" + (isErr ? " err" : "");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => t.classList.add("hidden"), isErr ? 6000 : 3000);
}

/* ---------------- API ---------------- */
/* Loading state: the button the user just tapped shows a spinner and is disabled while its
   requests to the server run (so nothing looks "dead" and nothing is sent twice). */
let tapped = null, tappedAt = 0;
const noteTap = (b) => { if (b && b.tagName === "BUTTON") { tapped = b; tappedAt = Date.now(); } };
document.addEventListener("click", (e) => noteTap(e.target.closest && e.target.closest("button")), true);
document.addEventListener("submit", (e) => noteTap(e.submitter || e.target.querySelector('button[type=submit], button:not([type])')), true);
function busyStart(btn) {
  const n = Number(btn.dataset.busy || 0) + 1;
  btn.dataset.busy = n;
  if (n === 1) { btn.classList.add("busy"); btn.setAttribute("aria-busy", "true"); btn.dataset.wasDisabled = btn.disabled ? "1" : ""; btn.disabled = true; }
}
function busyEnd(btn) {
  const n = Math.max(0, Number(btn.dataset.busy || 1) - 1);
  btn.dataset.busy = n;
  if (n === 0) { btn.classList.remove("busy"); btn.removeAttribute("aria-busy"); if (!btn.dataset.wasDisabled) btn.disabled = false; }
}

async function api(path, opts = {}) {
  const btn = !opts.quiet && tapped && Date.now() - tappedAt < 600 && document.contains(tapped) ? tapped : null;
  if (btn) busyStart(btn);
  try { return await apiCall(path, opts); } finally { if (btn) busyEnd(btn); }
}

async function apiCall(path, opts = {}) {
  const headers = { ...(opts.headers || {}) };
  if (state.token) headers.Authorization = `Bearer ${state.token}`;
  let body = opts.body;
  if (body && !(body instanceof FormData) && typeof body !== "string") {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(body);
  }
  const res = await fetch(path, { ...opts, headers, body });
  if (res.status === 401 && state.token) { logout(); throw new Error("Session expired - please sign in again"); }
  if (!res.ok) {
    let msg = res.statusText;
    try {
      const j = await res.json();
      msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail);
    } catch (_) { /* not json */ }
    throw new Error(msg);
  }
  if (opts.raw) return res;
  if (res.status === 204) return null;
  return res.json();
}

async function download(path, fallbackName, opts = {}) {
  const res = await api(path, { ...opts, raw: true });
  const blob = await res.blob();
  const cd = res.headers.get("Content-Disposition") || "";
  const name = (cd.match(/filename="([^"]+)"/) || [])[1] || fallbackName;
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 2000);
}

const qs = (obj) => {
  const p = new URLSearchParams();
  Object.entries(obj).forEach(([k, v]) => { if (v !== null && v !== undefined && v !== "") p.set(k, v); });
  return p.toString();
};
const formData = (form) => {
  const nums = new Set([...form.querySelectorAll("input[data-num]")].map((i) => i.name));
  return Object.fromEntries([...new FormData(form).entries()].filter(([, v]) => v !== "")
    .map(([k, v]) => [k, nums.has(k) ? String(toNum(v)) : v]));
};

/* ---------------- Auth ---------------- */
function saveSession(token, user) {
  state.token = token; state.user = user;
  try { sessionStorage.setItem("pis_token", token); } catch (_) { /* storage blocked */ }
}
function logout() {
  state.token = null; state.user = null;
  try { sessionStorage.removeItem("pis_token"); } catch (_) {}
  $("#app-view").classList.add("hidden");
  $("#login-view").classList.remove("hidden");
}

$("#login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  $("#login-error").textContent = "";
  const btn = $("#login-form button[type=submit]"), hint = $("#login-wait");
  btn.textContent = "Signing in…";
  // The free server sleeps when unused; the first request then takes up to a minute.
  const slow = setTimeout(() => { hint.hidden = false; }, 4000);
  try {
    const d = await api("/api/auth/login", { method: "POST", body: formData(e.target) });
    saveSession(d.access_token, d.user);
    e.target.reset();
    startApp();
    shareSignInLocation();
  } catch (err) {
    $("#login-error").textContent = err.message === "Failed to fetch" ? "Cannot reach PIS. Check your internet connection and try again." : err.message;
  } finally { clearTimeout(slow); hint.hidden = true; btn.textContent = "Sign in"; }
});
/* Device location at sign-in. The browser itself asks the user; nothing is sent unless they allow it. */
function shareSignInLocation() {
  const send = (body) => api("/api/auth/location", { method: "POST", body, quiet: true }).catch(() => {});
  if (!navigator.geolocation) { send({ declined: true, reason: "not supported by this browser" }); return; }
  navigator.geolocation.getCurrentPosition(
    (pos) => send({ latitude: pos.coords.latitude, longitude: pos.coords.longitude, accuracy: pos.coords.accuracy }),
    (err) => {
      send({ declined: true, reason: err.code === 1 ? "permission refused" : err.code === 3 ? "timed out" : "position unavailable" });
      if (err.code === 1) locationBlockedNotice();
    },
    { enableHighAccuracy: true, timeout: 20000, maximumAge: 0 });
}
/* The browser remembers "Never"/"Block" for this site on this device and will not ask again;
   only the user can undo it, so explain how. */
function locationBlockedNotice() {
  if ($("#loc-notice")) return;
  const box = document.createElement("div");
  box.id = "loc-notice";
  box.className = "update-bar loc-notice";
  box.innerHTML = `<div><b>Location is blocked for PIS on this device.</b> Your sign-in was recorded without a location.
    To allow it: tap the icon left of the web address (🔒 or ⚙) → <b>Permissions</b> / <b>Site settings</b> → <b>Location</b> → <b>Allow</b>,
    then sign in again.</div><button class="btn small" type="button">OK</button>`;
  box.querySelector("button").onclick = () => box.remove();
  document.body.appendChild(box);
  setTimeout(() => box.remove(), 25000);    // don't keep covering buttons
}
const mapUrl = (lat, lng) => `https://www.google.com/maps?q=${lat},${lng}`;
function mapLink(lat, lng, acc) {
  if (lat == null || lng == null) return "";
  const a = acc != null ? ` <span class="muted small">±${fmt(acc)} m</span>` : "";
  return `<a href="${mapUrl(lat, lng)}" target="_blank" rel="noopener">📍 ${lat.toFixed(6)}, ${lng.toFixed(6)}</a>${a}`;
}
const ipLink = (ip) => (ip ? `<a href="https://ipinfo.io/${encodeURIComponent(ip)}" target="_blank" rel="noopener" title="Look up this IP (approximate area and network only)">${esc(ip)}</a>` : "");

$("#logout-btn").addEventListener("click", () => {
  // tell the server (so others see "Signed out"), without waiting for it
  if (state.token) fetch("/api/auth/logout", { method: "POST", keepalive: true, headers: { Authorization: `Bearer ${state.token}` } }).catch(() => {});
  logout();
});
$("#pw-btn").addEventListener("click", () => {
  openModal(`<h3>Change password</h3><form id="pw-form" class="form-grid">
    <label>Current password<input type="password" name="current_password" required></label>
    <label>New password (min 8)<input type="password" name="new_password" minlength="8" required></label>
    <div class="wide row gap"><button class="btn primary">Update</button><button type="button" class="btn" onclick="closeModal()">Cancel</button></div></form>`);
  $("#pw-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    try { await api("/api/auth/password", { method: "POST", body: formData(e.target) }); closeModal(); toast("Password updated"); }
    catch (err) { toast(err.message, true); }
  });
});

function checkForUpdate(health) {
  if (!health || !health.asset_version || !PAGE_VERSION || health.asset_version === PAGE_VERSION) return;
  if ($("#update-bar")) return;
  const freshUrl = `${location.pathname}?fresh=${Date.now()}${location.hash}`;
  // Nothing being edited: load the new version straight away.
  let last = 0;
  try { last = Number(sessionStorage.getItem("pis_auto_reload") || 0); } catch (_) {}
  if (!$(".review-record") && !$(".sheet .dirty") && Date.now() - last > 5 * 60000) {
    try { sessionStorage.setItem("pis_auto_reload", String(Date.now())); } catch (_) {}
    location.replace(freshUrl);
    return;
  }
  const bar = document.createElement("div");
  bar.id = "update-bar";
  bar.className = "update-bar";
  bar.innerHTML = 'PIS has been updated. Save your work, then <button class="btn small primary" type="button">Reload now</button>';
  bar.querySelector("button").onclick = () => location.replace(freshUrl);
  document.body.appendChild(bar);
}
async function pollForUpdate() {
  if (document.visibilityState !== "visible") return;
  if (state.token) api("/api/auth/heartbeat", { method: "POST", quiet: true }).catch(() => {});   // "online now"
  if (state.token && location.hash === "#admin" && !$("#modal").open) loadAdmin().catch(() => {});
  try { checkForUpdate(await (await fetch("/api/health", { cache: "no-store" })).json()); } catch (_) { /* offline */ }
}
document.addEventListener("visibilitychange", pollForUpdate);
setInterval(pollForUpdate, 60000);   // also while the page stays open

async function boot() {
  let token = null;
  try { token = sessionStorage.getItem("pis_token"); } catch (_) {}
  state.health = await api("/api/health").catch(() => null);
  checkForUpdate(state.health);
  if (token) {
    state.token = token;
    try { state.user = await api("/api/auth/me"); return startApp(); } catch (_) { /* fallthrough */ }
  }
  logout();
}

function startApp() {
  $("#login-view").classList.add("hidden");
  $("#app-view").classList.remove("hidden");
  $("#who").textContent = `${state.user.full_name || state.user.email} · ${ROLE_NAMES[state.user.role]}`;
  const ai = state.health && state.health.ai_enabled;
  const pill = $("#ai-status");
  const aiName = { gemini: "Gemini", claude: "Claude" }[state.health && state.health.ai_provider] || "AI";
  const check = state.health && state.health.ai_check;
  if (ai && check && check.ok === true) {
    pill.textContent = `AI: ${aiName} ✓ working`; pill.className = "pill ok";
  } else if (ai && check && check.ok === false) {
    pill.textContent = `AI: ${aiName} problem — built-in reader in use`; pill.className = "pill warn";
  } else {
    pill.textContent = ai ? `AI: ${aiName} connected` : "AI: built-in reader";
    pill.className = "pill " + (ai ? "ok" : "warn");
  }
  pill.title = check ? `${check.model || ""}: ${check.detail || ""}` : "Reports are read by the built-in reader; add GEMINI_API_KEY to add Gemini";
  $$("[data-min-role]").forEach((el) => el.classList.toggle("hidden", !can(el.dataset.minRole)));
  if (state.health && state.health.max_upload_mb) {
    $("#upload-hint").textContent = `PDF, Word (.docx), Excel (.xlsx), text or scanned images — single or bulk · up to ${state.health.max_upload_mb} MB per file`;
  }
  loadFacets();
  route();
}

/* ---------------- Routing ---------------- */
const loaders = { dashboard: loadDashboard, ingest: loadReports, records: loadRecords, intel: loadIntel, comps: () => {}, research: loadResearch, admin: loadAdmin };
function route() {
  let view = (location.hash || "#dashboard").slice(1);
  if (!loaders[view]) view = "dashboard";
  const link = $(`nav a[data-view="${view}"]`);
  if (link && link.classList.contains("hidden")) view = "dashboard";
  $$("nav a").forEach((a) => a.classList.toggle("active", a.dataset.view === view));
  $$(".page").forEach((p) => p.classList.toggle("active", p.dataset.page === view));
  Promise.resolve().then(loaders[view]).catch((err) => toast(err.message, true));
}
window.addEventListener("hashchange", () => state.user && route());

/* ---------------- Facets & filter forms ---------------- */
async function loadFacets() {
  state.facets = await api("/api/records/facets");
  const fill = (id, arr) => { $(id).innerHTML = arr.map((v) => `<option value="${esc(v)}">`).join(""); };
  fill("#dl-locations", state.facets.locations);
  fill("#dl-cities", state.facets.cities);
  fill("#dl-districts", state.facets.districts || []);
  fill("#dl-types", state.facets.property_types);
}

function filterFormHTML(withSearch = true) {
  return `
    ${withSearch ? '<label>Search<input name="q" placeholder="address, client, notes…"></label>' : ""}
    <label>Category<select name="category"><option value="">All</option><option value="valuation">Valuation</option><option value="cost">Construction cost</option><option value="housing">Housing</option></select></label>
    <label>Location<input name="location" list="dl-locations"></label>
    <label>MMDA<select name="district">${mmdaOptions("", "All MMDAs")}</select></label>
    <label>City<input name="city" list="dl-cities"></label>
    <label>Property type<input name="property_type" list="dl-types"></label>
    <label>Basis of value<select name="basis_of_value"><option value="">All bases</option>${BASES.map((b) => `<option>${esc(b)}</option>`).join("")}<option>Not stated</option></select></label>
    <label>Purpose<input name="purpose"></label>
    <label>From<input type="date" name="date_from"></label>
    <label>To<input type="date" name="date_to"></label>
    ${withSearch ? '<label>Min value/cost<input ${NUM_ATTR} name="min_amount" placeholder="e.g. 500,000"></label><label>Max value/cost<input ${NUM_ATTR} name="max_amount" placeholder="e.g. 5,000,000"></label><label>Tag<input name="tag"></label>' : ""}
    <div class="filter-actions"><button class="btn primary" type="submit">Apply</button><button class="btn" type="reset">Clear</button></div>`;
}

/* ---------------- Dashboard ---------------- */
async function loadDashboard() {
  const [s, reports] = await Promise.all([
    api("/api/analytics/summary"),
    api("/api/reports?limit=8"),
  ]);
  const cur = s.currency || (state.health && state.health.default_currency) || "";
  const m = s.metrics;
  const pending = reports.filter((r) => r.status === "extracted").length;
  $("#kpis").innerHTML = [
    kpi("Records in database", fmt(s.record_count), Object.entries(s.by_category).map(([k, v]) => `${v} ${k}`).join(" · ") || "No data yet"),
    basisKpiHTML(),
    kpi("Avg construction cost / m² (rate)", m.cost_per_sqm ? `${cur} ${fmt(m.cost_per_sqm.mean)}` : "—", m.cost_per_sqm ? `${m.cost_per_sqm.count} record${m.cost_per_sqm.count === 1 ? "" : "s"}` : "from cost reports and rates adopted"),
    kpi("Avg yield", m.yield_pct ? `${fmt(m.yield_pct.mean, 2)}%` : "—", m.yield_pct ? `${m.yield_pct.count} records` : ""),
    kpi("Reports awaiting review", fmt(pending), "in the latest uploads"),
  ].join("");
  state.byBasis = { rows: s.by_basis || [], cur };
  wireBasisKpi();
  renderBasisTable($("#dash-bases"), s.by_basis || [], cur);
  setupBasisAnalysis(s.by_basis || []);
  const locs = Object.entries(s.top_locations);
  $("#dash-locations").innerHTML = locs.length ? table(["Location", "Records"], locs.map(([k, v]) => [esc(k), { v: fmt(v), num: true }])) : '<p class="muted">No records yet.</p>';
  $("#dash-reports").innerHTML = reports.length ? table(["Report", "Type", "Status"], reports.map((r) => [esc(r.title || r.filename), esc(r.report_type || "—"), statusPill(r.status)])) : '<p class="muted">No reports uploaded yet.</p>';
  showSuspects($("#dash-warning"), s.suspect_values, cur);
}
/* Valuations whose figure looks mis-read (e.g. 2,300 for 2,300,000): listed, kept out of averages. */
function showSuspects(box, list, cur) {
  if (!box) return;
  if (!list || !list.length) { box.hidden = true; box.innerHTML = ""; return; }
  box.hidden = false;
  box.innerHTML = `<b>⚠ ${list.length} valuation${list.length === 1 ? "" : "s"} with a value that looks too low</b> — left out of the averages until corrected:
    <ul>${list.map((x) => `<li>Ref ${x.id} ${esc(x.label)}: valuation opinion ${esc(cur)} ${fmt(x.capital_value)}${x.value_per_sqm != null ? ` (${esc(cur)} ${fmt(x.value_per_sqm, 2)} per m²)` : ""}
      <button class="btn small fix-suspect" data-id="${x.id}">Open &amp; correct</button></li>`).join("")}</ul>`;
  $$(".fix-suspect", box).forEach((b) => (b.onclick = async () => {
    try { const rec = await api(`/api/records/${b.dataset.id}`); state.lastTab = "valuation"; openRecord(rec); } catch (e) { toast(e.message, true); }
  }));
}
function checkValueOpinion(el) {
  const v = toNum($("[name=capital_value]", el)?.value) || 0, size = toNum($("[name=size_sqm]", el)?.value) || 0;
  const cur = ($("[name=currency]", el)?.value || "GHS").toUpperCase();
  const label = $("[name=capital_value]", el)?.closest("label");
  if (!label) return;
  $(".value-warn", label)?.remove();
  if (!v || cur !== "GHS") return;
  const perSqm = size ? v / size : null;
  if (v < 10000 || (perSqm !== null && perSqm < 100)) {
    label.insertAdjacentHTML("beforeend", `<span class="value-warn">⚠ ${perSqm !== null ? `This works out to GH¢${fmt(perSqm, 2)} per m², which` : "This"} looks too low. Check the figure (e.g. ${fmt(v)} vs ${fmt(v * 1000)}).</span>`);
  }
}

/* "Average <basis of value ▾>": the average opinion of value for one basis at a time. */
function savedBasis() { try { return localStorage.getItem("pis_basis") || "Market Value"; } catch (_) { return "Market Value"; } }
function basisKpiHTML() {
  return `<div class="kpi kpi-basis"><div class="label">Average <select id="basis-pick" aria-label="Basis of value"></select></div>
    <div class="value" id="basis-value">—</div><div class="sub" id="basis-sub"></div></div>`;
}
function wireBasisKpi() {
  const pick = $("#basis-pick");
  if (!pick) return;
  const rows = state.byBasis.rows, cur = state.byBasis.cur;
  const have = new Set(rows.map((r) => r.basis));
  const names = [...BASES.filter((b) => have.has(b)), ...[...have].filter((b) => !BASES.includes(b)),
    ...BASES.filter((b) => !have.has(b))];
  const want = savedBasis();
  pick.innerHTML = names.map((b) => `<option value="${esc(b)}"${b === want ? " selected" : ""}>${esc(b)}${have.has(b) ? "" : " (none yet)"}</option>`).join("");
  const show = () => {
    const r = rows.find((x) => x.basis === pick.value);
    try { localStorage.setItem("pis_basis", pick.value); } catch (_) {}
    $("#basis-value").textContent = r ? `${cur} ${fmt(r.mean)}${r.per_year ? " p.a." : ""}` : "—";
    $("#basis-sub").textContent = r
      ? `${r.count} valuation${r.count === 1 ? "" : "s"} · median ${fmt(r.median)}`
      : "No valuations on this basis yet";
  };
  pick.onchange = show;
  show();
}
function renderBasisTable(box, rows, cur) {
  if (!box) return;
  if (!rows.length) { box.innerHTML = '<p class="muted">No valuations yet.</p>'; return; }
  box.innerHTML = table(["Basis of value", { t: "Valuations", num: true }, { t: `Average (${cur})`, num: true }, { t: "Median", num: true },
    { t: "Lowest", num: true }, { t: "Highest", num: true }],
  rows.map((r) => [esc(r.basis) + (r.per_year ? ' <span class="muted small">(per year)</span>' : ""), { v: r.count, num: true },
    { v: fmt(r.mean), num: true }, { v: fmt(r.median), num: true }, { v: fmt(r.min), num: true }, { v: fmt(r.max), num: true }]));
}

/* Dashboard "Analysis by basis of value": pick Market Value, Market Rent, Fair Value ... or
   "Market Value and Market Rent" to see averages, by location, by property type and the trend. */
const BOTH = "Market Value|Market Rent";
function savedAnalysis() { try { return localStorage.getItem("pis_analysis") || "Market Value"; } catch (_) { return "Market Value"; } }
function setupBasisAnalysis(rows) {
  const pick = $("#analysis-basis");
  if (!pick) return;
  const have = new Set(rows.map((r) => r.basis));
  const opts = [];
  if (have.has("Market Value") && have.has("Market Rent")) opts.push([BOTH, "Market Value and Market Rent (with yield)"]);
  BASES.filter((b) => have.has(b)).forEach((b) => opts.push([b, `${b}`]));
  if (have.has("Not stated")) opts.push(["Not stated", "Basis not stated"]);
  BASES.filter((b) => !have.has(b)).forEach((b) => opts.push([b, `${b} (none yet)`]));
  const want = savedAnalysis();
  if (!opts.some(([v]) => v === want)) opts.unshift([want, want]);
  pick.innerHTML = opts.map(([v, t]) => `<option value="${esc(v)}"${v === want ? " selected" : ""}>${esc(t)}</option>`).join("");
  const reload = () => loadBasisAnalysis(pick.value);
  pick.onchange = () => { try { localStorage.setItem("pis_analysis", pick.value); } catch (_) {} reload(); };
  const period = $("#analysis-period");
  period.onchange = () => { $("#analysis-dates").hidden = period.value !== "custom"; if (period.value !== "custom") reload(); };
  $("#analysis-from").onchange = reload; $("#analysis-to").onchange = reload;
  $("#analysis-interval").onchange = reload;
  reload();
}
/* Period chosen on the dashboard -> date_from / date_to (valuation dates). */
function analysisPeriod() {
  const v = $("#analysis-period")?.value || "all";
  const iso = (d) => d.toISOString().slice(0, 10);
  const now = new Date(), y = now.getFullYear();
  if (v === "12m") { const d = new Date(now); d.setFullYear(y - 1); return { date_from: iso(d), date_to: iso(now), label: "the last 12 months" }; }
  if (v === "this") return { date_from: `${y}-01-01`, date_to: `${y}-12-31`, label: `${y}` };
  if (v === "last") return { date_from: `${y - 1}-01-01`, date_to: `${y - 1}-12-31`, label: `${y - 1}` };
  if (v === "3y") { const d = new Date(now); d.setFullYear(y - 3); return { date_from: iso(d), date_to: iso(now), label: "the last 3 years" }; }
  if (v === "custom") {
    const f = $("#analysis-from").value, t = $("#analysis-to").value;
    return { date_from: f || undefined, date_to: t || undefined, label: `${f || "earliest"} to ${t || "latest"}` };
  }
  return { label: "all time" };
}
const niceDate = (iso) => (iso ? new Date(iso + "T00:00:00").toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" }) : "");
async function loadBasisAnalysis(choice) {
  const box = $("#basis-analysis");
  const [basis, compare] = choice.split("|");
  box.innerHTML = '<p class="muted">Loading…</p>';
  let a;
  const per = analysisPeriod();
  const interval = $("#analysis-interval")?.value || "quarter";
  try { a = await api(`/api/analytics/basis?${qs({ basis, compare, interval, date_from: per.date_from, date_to: per.date_to })}`); } catch (e) { box.innerHTML = `<p class="muted">${esc(e.message)}</p>`; return; }
  const cur = a.currency || (state.health && state.health.default_currency) || "";
  const bases = a.bases;
  const now = new Date().toLocaleString(undefined, { day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" });
  $("#analysis-stamp").textContent = `Period: ${per.label}` +
    (a.date_range ? ` · valuations dated ${niceDate(a.date_range[0])} – ${niceDate(a.date_range[1])}` : "") +
    (a.undated && !per.date_from && !per.date_to ? ` · ${a.undated} without a valuation date` : "") + ` · figures as at ${now}`;
  const ivl = { month: "monthly", quarter: "quarterly", year: "yearly" }[a.interval] || "quarterly";
  const word = (b) => (b === "Market Rent" ? "rental opinion" : "valuation opinion");
  const yr = (b) => (b === "Market Rent" ? " p.a." : "");
  if (!bases.some((b) => a.overall[b])) {
    box.innerHTML = `<p class="muted">No valuations on the basis of ${esc(bases.join(" or "))} ${per.date_from || per.date_to ? `for ${esc(per.label)}` : "yet"}. ${per.date_from || per.date_to ? "Try a longer period." : "Upload a report or set the basis of value on a record."}</p>`;
    return;
  }
  const tiles = [];
  bases.forEach((b) => {
    const o = a.overall[b];
    tiles.push(kpi(`Average ${word(b)} — ${b}`, o ? `${cur} ${fmt(o.mean)}${yr(b)}` : "—",
      o ? `${o.count} valuation${o.count === 1 ? "" : "s"} · median ${fmt(o.median)}` : "none yet"));
    if (o) tiles.push(kpi(`Range — ${b}`, `${fmt(o.min)} – ${fmt(o.max)}`, `${cur}${yr(b)}`));
  });
  if (a.yield_pct != null) tiles.push(kpi("Implied yield", `${fmt(a.yield_pct, 2)}%`, "average rent ÷ average value"));
  const head = (first) => [first, ...bases.flatMap((b) => [{ t: bases.length > 1 ? `${b}: no.` : "Valuations", num: true },
    { t: `${bases.length > 1 ? b + ": " : ""}average (${cur})`, num: true },
    ]),
    ...(bases.length > 1 && bases.includes("Market Rent") ? [{ t: "Yield", num: true }] : [])];
  const body = (rows) => rows.map((r) => [esc(r.name), ...bases.flatMap((b) => {
    const o = r[b];
    return [{ v: o ? o.count : "—", num: true }, { v: o ? fmt(o.mean) : "—", num: true }];
  }), ...(bases.length > 1 && bases.includes("Market Rent") ? [{ v: r.yield_pct != null ? `${fmt(r.yield_pct, 2)}%` : "—", num: true }] : [])]);
  const sect = (title, first, rows) => (rows.length ? `<h4>${title}</h4>${table(head(first), body(rows))}` : "");
  box.innerHTML = `<div class="kpis">${tiles.join("")}</div>
    ${a.excluded_suspect ? `<p class="muted small">${a.excluded_suspect} valuation(s) with a figure that looks mis-read are left out.</p>` : ""}
    ${bases.length > 1
    ? sect("By location", "Location", a.by_location) + sect("By property type", "Property type", a.by_property_type)
    : `<div class="grid two"><div>${sect("By location", "Location", a.by_location)}</div><div>${sect("By property type", "Property type", a.by_property_type)}</div></div>`}
    ${sect("By district (MMDA)", "District", a.by_district)}
    <div class="${bases.length > 1 ? "grid two" : ""}">${bases.map((b, i) => `<div><h4>Trend — average ${esc(word(b))} (${esc(b)}, ${ivl})</h4>
      ${a.trend[b].length ? `<div class="chart-box"><canvas id="basis-chart-${i}"></canvas></div>` : '<p class="muted small">The trend appears once records have a valuation date.</p>'}</div>`).join("")}</div>`;
  bases.forEach((b, i) => {
    if (!a.trend[b].length) return;
    lineChart(`basis-chart-${i}`, a.trend[b].map((p) => p.period),
      [{ label: `${b} (${cur}${yr(b)})`, data: a.trend[b].map((p) => p.mean), borderColor: PALETTE[i], backgroundColor: PALETTE[i] + "33" }]);
  });
}

const kpi = (label, value, sub) => `<div class="kpi"><div class="label">${esc(label)}</div><div class="value">${esc(value)}</div><div class="sub">${esc(sub)}</div></div>`;
function statusPill(s) {
  const cls = { confirmed: "ok", extracted: "warn", failed: "bad", rejected: "bad" }[s] || "";
  const label = { extracted: "awaiting review", uploaded: "processing" }[s] || s;
  return `<span class="pill ${cls}">${esc(label)}</span>`;
}
function table(headers, rows, opts = {}) {
  const th = headers.map((h) => `<th class="${typeof h === "object" && h.num ? "num" : ""}">${esc(typeof h === "object" ? h.t : h)}</th>`).join("");
  const body = rows.map((r, i) => `<tr class="${opts.clickable ? "clickable" : ""}" data-i="${i}">${r.map((c) => (c && typeof c === "object" ? `<td class="${c.num ? "num" : ""}">${c.v}</td>` : `<td>${c ?? ""}</td>`)).join("")}</tr>`).join("");
  return `<div class="table-wrap"><table><thead><tr>${th}</tr></thead><tbody>${body}</tbody></table></div>`;
}

/* ---------------- Charts ---------------- */
const PALETTE = ["#0a2a66", "#cfa04a", "#3f7fd1", "#7a8b99"];   // Pinnacle navy & gold first
function lineChart(id, labels, datasets, opts = {}) {
  if (!window.Chart) return;
  if (state.charts[id]) state.charts[id].destroy();
  const dark = matchMedia("(prefers-color-scheme: dark)").matches;
  const grid = dark ? "#2c3542" : "#e6e9ef";
  const tick = dark ? "#98a3b1" : "#64707f";
  state.charts[id] = new Chart(document.getElementById(id), {
    type: opts.type || "line",
    data: { labels, datasets: datasets.map((d, i) => ({ borderColor: PALETTE[i], backgroundColor: PALETTE[i] + (opts.type === "bar" ? "cc" : "33"), tension: 0.25, spanGaps: true, pointRadius: 3, ...d })) },
    options: {
      responsive: true, maintainAspectRatio: false, interaction: { mode: "index", intersect: false },
      plugins: { legend: { labels: { color: tick } } },
      scales: { x: { grid: { color: grid }, ticks: { color: tick } }, y: { grid: { color: grid }, ticks: { color: tick, callback: (v) => Number(v).toLocaleString() } } },
    },
  });
}

/* ---------------- Upload & review ---------------- */
const dz = $("#dropzone");
$("#pick-files").addEventListener("click", () => $("#file-input").click());
$("#file-input").addEventListener("change", (e) => uploadFiles(e.target.files));
["dragenter", "dragover"].forEach((ev) => dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.add("drag"); }));
["dragleave", "drop"].forEach((ev) => dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.remove("drag"); }));
dz.addEventListener("drop", (e) => uploadFiles(e.dataTransfer.files));
$("#report-status-filter").addEventListener("change", loadReports);

async function uploadFiles(files) {
  if (!files || !files.length) return;
  const fd = new FormData();
  [...files].forEach((f) => fd.append("files", f));
  $("#upload-status").textContent = `Uploading ${files.length} file(s)…`;
  try {
    await api("/api/reports/upload", { method: "POST", body: fd });
    $("#upload-status").textContent = `Uploaded ${files.length} file(s). Extraction is running — reports will appear as "awaiting review".`;
    $("#file-input").value = "";
    loadReports();
    pollReports();
  } catch (err) { $("#upload-status").textContent = ""; toast(err.message, true); }
}

function pollReports() {
  clearTimeout(pollReports._t);
  pollReports._t = setTimeout(async () => {
    const pending = await api("/api/reports?status=uploaded").catch(() => []);
    if ($(".page.active")?.dataset.page === "ingest") loadReports();
    if (pending.length) pollReports();
  }, 2500);
}

async function loadReports() {
  const status = $("#report-status-filter").value;
  const reports = await api("/api/reports?" + qs({ status }));
  if (!reports.length) { $("#reports-table").innerHTML = '<p class="muted">No reports.</p>'; return; }
  $("#reports-table").innerHTML = table(["#", "File", "Detected type", "Extraction", "Status", "Records", "Uploaded"],
    reports.map((r) => [r.id, esc(r.filename), esc(r.report_type || "—"), esc(r.extraction_method || "—"), statusPill(r.status), { v: r.record_count, num: true }, new Date(r.created_at).toLocaleString()]),
    { clickable: true });
  $$("#reports-table tbody tr").forEach((tr) => tr.addEventListener("click", () => openReview(reports[tr.dataset.i].id)));
  if (reports.some((r) => r.status === "uploaded")) pollReports();
}

/* Field definitions for the record editor */
/* Record editor: tabbed like a valuation report. */
// "Very good" and "Excellent" mean the same in reports: one top grade.
const RATING = ["Excellent / Very good", "Good", "Fair", "Poor"];
const BASES = ["Market Value", "Market Rent", "Fair Value", "Equitable Value", "Investment Value", "Synergistic Value",
  "Marriage Value", "Special Value", "Liquidation Value", "Forced Sale Value", "Reinstatement Cost",
  "Depreciated Replacement Cost", "Mortgage Lending Value"];
/* One standard basis: "Market Value VALUATION OPINION" -> "Market Value". */
function basisCanonical(v) {
  if (!v) return "";
  const low = String(v).toLowerCase();
  const hits = BASES.map((b) => [low.indexOf(b.toLowerCase()), b]).filter(([i]) => i >= 0).sort((a, b) => a[0] - b[0]);
  const primary = hits.filter(([, b]) => b !== "Forced Sale Value");
  return (primary[0] || hits[0] || [0, String(v).trim()])[1];
}
const SELECTS = {
  basis: BASES,
  rating: RATING,
  repair: ["Excellent / Very good", "Good", "Fair", "Poor", "Dilapidated"],
  conformity: ["Conforming", "Partly conforming", "Non-conforming"],
};
const VALUATION_TABS = [
  ["property", "1. Property", [["property_type", "Property type", "ptype"], ["property_name", "Property name / number"],
    ["owner", "Owner"], ["use_occupancy", "Use / occupancy"], ["highest_best_use", "Highest & best use"]],
    [["client", "Client"], ["title", "Report title / project"]]],
  ["location", "2. Location", [["address", "Address", "wide"], ["gps_address", "Digital address", "gps"],
    ["gps_coordinates", "GPS coordinates", "coords"], ["land_size", "Land size"], ["accessibility", "Accessibility", "text"],
    ["location", "Neighbourhood", "loc"]],
    [["district", "MMDA", "mmda"], ["city", "City / town"], ["region", "Region", "region"]]],
  ["improvements", "3. Improvements", [["building_type", "Building type"], ["accommodation", "Accommodation", "text"],
    ["construction_details", "Construction details", "text"], ["services", "Services", "text"],
    ["floor_areas", "Floor areas", "text"], ["size_sqm", "Total floor area (m²)", "num"],
    ["ancillary_structures", "Ancillary structures", "text"]]],
  ["title", "4. Title", [["ownership", "Ownership"], ["tenure", "Tenure", "tenure"], ["source_of_title", "Source of title"],
    ["unexpired_term", "Unexpired term"], ["planning_status", "Planning status"]]],
  ["valuation", "5. Valuation", [["purpose", "Purpose", "purpose"], ["basis_of_value", "Basis of value", "basis"],
    ["inspection_date", "Date of inspection", "date"], ["record_date", "Date of valuation", "date"],
    ["method", "Valuation method", "method"], ["capital_value", "Market value", "num"],
    ["forced_sale_value", "Forced sale value", "num"]],
    [["cost_per_sqm", "Construction cost / m²: Main building", "num"],
     ["external_works_rate", "Construction cost / m²: External works", "num"],
     ["currency", "Currency"], ["annual_rent", "Market rent (annual)", "num"], ["yield_pct", "Yield %", "num"],
     ["value_per_sqm", "Value / m²", "num"], ["rent_per_sqm", "Rent / m² p.a.", "num"],

     ["valuation_basis", "Capital / rental / both"], ["valuer", "Valuer identity", "wide"],
     ["property_valued", "Property valued (summary)", "text"]]],
  ["marketability", "6. Marketability", [["location_rating", "Location rating", "rating"],
    ["accessibility_rating", "Accessibility rating", "rating"], ["state_of_repair", "State of repair", "repair"],
    ["user_conformity", "User conformity", "conformity"], ["neighbourhood_rating", "Neighbourhood rating", "rating"],
    ["overall_marketability", "Overall marketability", "rating"]]],
  ["assumptions", "7. Assumptions", [["encumbrances", "Encumbrances", "text"], ["legal_disclaimer", "Legal disclaimer", "text"],
    ["inspection_limitation", "Inspection limitation", "text"], ["purpose_restriction", "Purpose restriction", "text"],
    ["assumptions", "Valuation assumptions", "text"]]],
  ["attachments", "8. Attachments", null],
];
const [P_TAB, L_TAB, I_TAB, , , , A_TAB, ATT_TAB] = VALUATION_TABS;
const TABS_BY_CATEGORY = {
  valuation: VALUATION_TABS,
  cost: [P_TAB, L_TAB, I_TAB, ["cost", "Cost", [["purpose", "Purpose", "purpose"], ["record_date", "Date", "date"],
    ["currency", "Currency"], ["total_cost", "Total cost", "num"], ["cost_per_sqm", "Construction cost / m²: Main building", "num"],
    ["external_works_rate", "Construction cost / m²: External works", "num"],
    ["specification", "Specification"]]], A_TAB, ATT_TAB],
  housing: [L_TAB, ["housing", "Housing market", [["purpose", "Purpose", "purpose"], ["record_date", "Date", "date"],
    ["currency", "Currency"], ["property_type", "Property type", "ptype"], ["avg_price", "Average price", "num"],
    ["avg_monthly_rent", "Average monthly rent", "num"], ["supply_units", "Supply (units)", "int"],
    ["demand_level", "Demand level"], ["affordability_ratio", "Affordability ratio", "num"],
    ["stock_notes", "Housing stock notes", "text"]]], A_TAB, ATT_TAB],
};
// Kept for code that refers to "all common fields".
const COMMON_FIELDS = [...P_TAB[2], ...P_TAB[3], ...L_TAB[2], ...L_TAB[3]];
const BREAKDOWN_KEYS = ["preliminaries", "substructure", "superstructure", "finishes", "services", "external_works", "contingency", "other"];
const LISTS = { mmda: "dl-districts", loc: "dl-locations", region: "dl-regions", ptype: "dl-types", purpose: "dl-purposes",
  basis: "dl-bases", tenure: "dl-tenure", method: "dl-methods" };
const HINTS = { gps: "e.g. GA-183-8164", coords: "e.g. 5.6396, -0.1676", mmda: "e.g. Adentan Municipal",
  basis: "e.g. Market Value", purpose: "e.g. Mortgage", tenure: "e.g. Leasehold" };
const ATTACH_KINDS = [["photo", "Photographs"], ["site_plan", "Site plan"], ["title_document", "Title documents"],
  ["allocation_note", "Allocation note"], ["valuation_certificate", "Valuation certificate"], ["supporting", "Supporting documents"]];
state.lastTab = "property";

/* An empty field means the report did not state it. */
const NS = "Not stated";
function fieldHTML([key, label, kind], rec) {
  const v = rec[key] ?? "";
  if (kind === "text") return `<label class="wide">${esc(label)}<textarea name="${key}" rows="2" placeholder="${NS}">${esc(v)}</textarea></label>`;
  if (kind === "mmda") {
    return `<label>${esc(label)} <span class="muted small">(${MMDAS.length})</span><select name="${key}" class="mmda-select">${mmdaOptions(v, `${NS} — select MMDA`)}</select></label>`;
  }
  if (SELECTS[kind]) {
    const val = kind === "basis" ? basisCanonical(v) : v;
    const opts = SELECTS[kind].includes(val) || !val ? SELECTS[kind] : [val, ...SELECTS[kind]];
    return `<label>${esc(label)}<select name="${key}"><option value="">${NS}</option>${opts.map((o) => `<option ${o === val ? "selected" : ""}>${esc(o)}</option>`).join("")}</select></label>`;
  }
  if (LISTS[kind] || HINTS[kind]) {
    const list = LISTS[kind] ? ` list="${LISTS[kind]}"` : "";
    const hint = ` placeholder="${NS}${HINTS[kind] ? ` (${HINTS[kind]})` : ""}"`;
    const extra = kind === "gps" ? ' autocapitalize="characters" spellcheck="false"' : "";
    return `<label>${esc(label)}<input type="text" name="${key}" value="${esc(v)}"${list}${hint}${extra}></label>`;
  }
  if (kind === "num" || kind === "int") {
    return `<label>${esc(label)}<input ${NUM_ATTR} name="${key}" value="${esc(withCommas(v))}" data-kind="${kind}" placeholder="${NS}"></label>`;
  }
  const type = kind === "date" ? "date" : "text";
  const step = "";
  const ns = type === "date" ? `<span class="ns-tag">${NS}</span>` : "";
  return `<label class="${kind === "wide" ? "wide" : ""}">${esc(label)}<input type="${type}"${step} name="${key}" value="${esc(v)}" data-kind="${kind || ""}"${type === "date" ? "" : ` placeholder="${NS}"`}>${ns}</label>`;
}

function recordEditorHTML(rec, idx) {
  const cat = rec.category || "valuation";
  const tabs = TABS_BY_CATEGORY[cat];
  const comps = rec.comparables || [];
  const bd = rec.cost_breakdown || {};
  const grid = (fields) => `<div class="form-grid">${fields.map((f) => fieldHTML(f, rec)).join("")}</div>`;
  const panel = ([key, , fields, more]) => {
    if (key === "attachments") {
      return `<div class="attach-panel" data-record-id="${rec.id || ""}">${rec.id ? '<p class="muted small">Loading…</p>'
        : '<p class="muted">Attachments can be added once the record is saved. After approving, open it in <b>Data &amp; Search</b> (or reopen this report) and use this tab.</p>'}</div>`;
    }
    let html = grid(fields);
    if (more) html += `<p class="sub-head">Also recorded</p>${grid(more)}`;
    if (key === "valuation" || key === "cost") html += costItemsEditorHTML(rec.cost_items || []);
    if (key === "valuation") {
      html += comparablesEditorHTML(comps);
    }
    if (key === "cost") {
      html += `<p class="sub-head">Elemental cost breakdown</p><div class="form-grid">${BREAKDOWN_KEYS.map((k) => `<label>${k.replace("_", " ")}<input ${NUM_ATTR} name="bd_${k}" value="${esc(withCommas(bd[k]))}" placeholder="—"></label>`).join("")}</div>`;
    }
    return html;
  };
  return `<div class="review-record" data-idx="${idx}">
    <div class="row between"><strong>Record ${idx + 1}</strong>
      <div class="row gap"><label>Category<select name="category">${["valuation", "cost", "housing"].map((c) => `<option ${c === cat ? "selected" : ""}>${c}</option>`).join("")}</select></label>
      <button type="button" class="btn small danger rm-record">Remove</button></div></div>
    <div class="tab-bar" role="tablist">${tabs.map(([key, label]) => `<button type="button" role="tab" class="tab-btn" data-tab="${key}">${esc(label)}</button>`).join("")}</div>
    ${tabs.map((t) => `<section class="tab-panel" role="tabpanel" data-tab="${t[0]}">${panel(t)}</section>`).join("")}
    <div class="form-grid tab-foot"><label class="wide">Tags (comma separated)<input name="tags" value="${esc((rec.tags || []).join(", "))}"></label>
    <label class="wide">Notes<textarea name="notes" rows="2">${esc(rec.notes || "")}</textarea></label></div>
  </div>`;
}

/* Rates adopted (cost approach / DRC): one row per part of the building. */
const COST_FIELDS = [["item", "Item / floor"], ["part", "Part", "part"], ["area_sqm", "Area m²", "num"], ["rate", "Rate per m²", "num"], ["amount", "Amount", "num"]];
const PARTS = ["Main building", "External works"];
const MAIN_PART = /\b(?:floors?|stor(?:e)?ys?|levels?|main (?:building|house|block)|principal building|basement|mezzanine|pent ?house|attic|superstructure|substructure|(?:the )?building)\b/i;
const partOf = (item, given) => (given && PARTS.includes(given) ? given
  : MAIN_PART.test(item || "") && !EXTERNAL_WORKS.test(item || "") ? "Main building" : "External works");
const EXTERNAL_WORKS = /boundary|\bwalls?\b|fenc|\bgates?\b|external|landscap|paving|paved|driveway|car ?park|\bland\b|site works|septic|borehole|soak ?away|drain|walkway|security post|guard ?house|swimming pool|water tank|polytank|tap|compound|courtyard|yard|kerb|gutter|culvert|lawn|garden|parking|forecourt/i;
let costRowSeq = 0;
function costRowHTML(c = {}) {
  const i = costRowSeq++;
  const part = partOf(c.item, c.part);
  return `<tr data-row="${i}">${COST_FIELDS.map(([k, , kind]) => kind === "part"
    ? `<td><select name="cst_${i}_part" aria-label="part">${PARTS.map((p) => `<option ${p === part ? "selected" : ""}>${p}</option>`).join("")}</select></td>`
    : `<td><input name="cst_${i}_${k}" value="${esc(kind === "num" ? withCommas(c[k]) : c[k] ?? "")}"${kind === "num" ? ` ${NUM_ATTR} data-kind="num"` : ' placeholder="e.g. Ground floor"'} aria-label="${k}"></td>`).join("")}
    <td><button type="button" class="btn small danger cost-del" title="Remove line">✕</button></td></tr>`;
}
function costItemsEditorHTML(items) {
  return `<p class="sub-head">Rates adopted (cost approach) (<span class="cost-count">${items.length}</span>)</p>
    <div class="cost-groups"></div>
    <div class="sheet-wrap comp-wrap"><table class="sheet cost-table"><thead><tr>${COST_FIELDS.map(([, l]) => `<th>${esc(l)}</th>`).join("")}<th></th></tr></thead>
    <tbody>${items.map(costRowHTML).join("")}</tbody></table></div>
    <button type="button" class="btn small cost-add">+ Add rate</button>
    <span class="muted small">One line per item as the report lists it. <b>Main building</b> = the floors; tap area, compound, walls and the rest are <b>External works</b>. Construction cost / m² is the floor-area-weighted rate of the main building.</span>`;
}
function readCostRows(el) {
  const rows = {};
  $$(".cost-table input, .cost-table select", el).forEach((inp) => {
    const m = inp.name.match(/^cst_(\d+)_(\w+)$/);
    if (!m || inp.value.trim() === "") return;
    rows[m[1]] = rows[m[1]] || {};
    rows[m[1]][m[2]] = inp.dataset.kind === "num" ? toNum(inp.value) : inp.value.trim();
  });
  return Object.keys(rows).sort((a, b) => a - b).map((k) => rows[k]).filter((r) => r.rate || r.amount);
}
function groupRate(rows) {
  rows = rows.filter((r) => r.rate);
  if (!rows.length) return null;
  if (rows.every((r) => r.area_sqm)) {
    const area = rows.reduce((s, r) => s + r.area_sqm, 0);
    return area ? Math.round((rows.reduce((s, r) => s + r.rate * r.area_sqm, 0) / area) * 100) / 100 : null;
  }
  return Math.round((rows.reduce((s, r) => s + r.rate, 0) / rows.length) * 100) / 100;
}
function costGroups(items) {
  const rows = items.map((r) => ({ ...r, rate: r.rate || (r.amount && r.area_sqm ? r.amount / r.area_sqm : null),
    amount: r.amount || (r.rate && r.area_sqm ? r.rate * r.area_sqm : null) }));
  const pick = (part) => {
    const g = rows.filter((r) => partOf(r.item, r.part) === part);
    return { lines: g.length, rate: groupRate(g), area: g.reduce((s, r) => s + (r.area_sqm || 0), 0), amount: g.reduce((s, r) => s + (r.amount || 0), 0) };
  };
  return { main: pick("Main building"), external: pick("External works") };
}
function adoptedRate(items) {
  const rows = items.map((r) => ({ ...r, rate: r.rate || (r.amount && r.area_sqm ? r.amount / r.area_sqm : null) })).filter((r) => r.rate);
  const building = rows.filter((r) => partOf(r.item, r.part) === "Main building");
  const use = building.length ? building : rows;
  if (!use.length) return null;
  if (use.every((r) => r.area_sqm)) {
    const area = use.reduce((s, r) => s + r.area_sqm, 0);
    return area ? Math.round((use.reduce((s, r) => s + r.rate * r.area_sqm, 0) / area) * 100) / 100 : null;
  }
  return Math.round((use.reduce((s, r) => s + r.rate, 0) / use.length) * 100) / 100;
}
function initCostRows(el) {
  const tbody = $(".cost-table tbody", el);
  if (!tbody) return;
  const update = () => {
    $(".cost-count", el).textContent = $$("tr", tbody).length;
    const items = readCostRows(el);
    const rate = adoptedRate(items);
    const box = $("[name=cost_per_sqm]", el);
    if (box && rate) box.value = withCommas(rate);
    const g = costGroups(items);
    const ext = $("[name=external_works_rate]", el);
    if (ext) ext.value = g.external.rate ? withCommas(g.external.rate) : "";
    const cur = ($("[name=currency]", el)?.value || "GHS").toUpperCase();
    const line = (label, x) => `<div class="cost-group"><span class="muted small">${label}</span>
      <b>${x.rate ? `${cur} ${fmt(x.rate, 2)} / m²` : "—"}</b>
      <span class="muted small">${x.lines} line${x.lines === 1 ? "" : "s"}${x.area ? ` · ${fmt(x.area, 2)} m²` : ""}${x.amount ? ` · ${cur} ${fmt(x.amount)}` : ""}</span></div>`;
    const box2 = $(".cost-groups", el);
    if (box2) box2.innerHTML = items.length ? line("1. Main building", g.main) + line("2. External works", g.external) : "";
  };
  $$("tr", tbody).forEach((tr) => {
    $(".cost-del", tr).onclick = () => { tr.remove(); update(); };
    const sel = $("select", tr), name = $(`[name$="_item"]`, tr);
    if (sel) sel.addEventListener("change", () => { sel.dataset.chosen = "1"; update(); });
    if (name && sel) name.addEventListener("input", () => { if (!sel.dataset.chosen) sel.value = partOf(name.value); });
    $$("input", tr).forEach((inp) => inp.addEventListener("input", () => {
      const get = (k) => toNum($(`[name$="_${k}"]`, tr).value);
      const amount = $(`[name$="_amount"]`, tr);
      if (inp.name.match(/_(area_sqm|rate)$/) && get("area_sqm") && get("rate") && !amount.dataset.typed) amount.value = withCommas(Math.round(get("area_sqm") * get("rate")));
      if (inp === amount) amount.dataset.typed = "1";
      update();
    }));
  });
  update();
}

/* Comparables: an editable table (one row per comparable). */
const COMP_FIELDS = [["address", "Address / location"], ["property_type", "Type"], ["date", "Date"],
  ["price", "Price", "num"], ["rent", "Rent", "num"], ["size_sqm", "Size m²", "num"], ["price_per_sqm", "Price / m²", "num"],
  ["notes", "Remarks"]];
let compRowSeq = 0;
function compRowHTML(c = {}) {
  const i = compRowSeq++;
  return `<tr data-row="${i}">${COMP_FIELDS.map(([k, , kind]) => `<td><input name="cmp_${i}_${k}" value="${esc(kind === "num" ? withCommas(c[k]) : c[k] ?? "")}"${kind === "num" ? ` ${NUM_ATTR} data-kind="num"` : ""} aria-label="${k}"></td>`).join("")}
    <td><button type="button" class="btn small danger comp-del" title="Remove comparable">✕</button></td></tr>`;
}
function comparablesEditorHTML(comps) {
  return `<p class="sub-head">Comparables (<span class="comp-count">${comps.length}</span>)</p>
    <div class="sheet-wrap comp-wrap"><table class="sheet comp-table"><thead><tr>${COMP_FIELDS.map(([, l]) => `<th>${esc(l)}</th>`).join("")}<th></th></tr></thead>
    <tbody>${comps.map(compRowHTML).join("")}</tbody></table></div>
    <button type="button" class="btn small comp-add">+ Add comparable</button>
    <span class="muted small">Comparables found in the report are listed here; add, correct or remove them before saving.</span>`;
}

function initCompRows(el) {
  const tbody = $(".comp-table tbody", el);
  if (!tbody) return;
  const count = () => { $(".comp-count", el).textContent = $$("tr", tbody).length; };
  $$("tr", tbody).forEach((tr) => { $(".comp-del", tr).onclick = () => { tr.remove(); count(); }; });
  count();
}

/* Tabs + attachments for an editor that is now in the page. */
function initRecordEditor(el, rec = {}) {
  const show = (key) => {
    const btns = $$(".tab-btn", el);
    if (!btns.some((b) => b.dataset.tab === key)) key = btns[0].dataset.tab;
    btns.forEach((b) => { const on = b.dataset.tab === key; b.classList.toggle("active", on); b.setAttribute("aria-selected", on); });
    $$(".tab-panel", el).forEach((p) => p.classList.toggle("active", p.dataset.tab === key));
    state.lastTab = key;
  };
  $$(".tab-btn", el).forEach((b) => b.addEventListener("click", () => show(b.dataset.tab)));
  show(state.lastTab);
  const att = $(".attach-panel", el);
  if (att && att.dataset.recordId) loadAttachments(att, att.dataset.recordId);
  const tbody = $(".comp-table tbody", el);
  if (tbody) {
    initCompRows(el);
    $(".comp-add", el).onclick = () => {
      tbody.insertAdjacentHTML("beforeend", compRowHTML());
      initCompRows(el); $("input", tbody.lastElementChild).focus();
    };
    if (el.closest("form") && $$("input, select, textarea", el).some((x) => x.disabled)) {
      $$(".comp-add, .comp-del", el).forEach((b) => (b.disabled = true));
    }
  }
  const ctb = $(".cost-table tbody", el);
  if (ctb) {
    initCostRows(el);
    $(".cost-add", el).onclick = () => { ctb.insertAdjacentHTML("beforeend", costRowHTML()); initCostRows(el); $("input", ctb.lastElementChild).focus(); };
    if (el.closest("form") && $$("input, select, textarea", el).some((x) => x.disabled)) $$(".cost-add, .cost-del", el).forEach((b) => (b.disabled = true));
  }
  if (rec.suggested && Array.isArray(rec.suggested.cost_items) && ctb && !$(".cost-suggest", el)) {
    const lines = rec.suggested.cost_items.map((c) => `${esc(c.item || "item")} ${c.rate ? fmt(c.rate) : "—"}`).join("; ");
    ctb.closest(".sheet-wrap").insertAdjacentHTML("afterend", `<span class="suggest cost-suggest">New reading: <b>${lines}</b>
      <button type="button" class="btn small use-new">Use these rates</button></span>`);
    $(".cost-suggest .use-new", el).onclick = () => {
      ctb.innerHTML = rec.suggested.cost_items.map(costRowHTML).join(""); initCostRows(el);
      const r = adoptedRate(readCostRows(el)); if (r) $("[name=cost_per_sqm]", el).value = withCommas(r);
      $(".cost-suggest", el).innerHTML = "Using the new rates ✓";
    };
  }
  markAutofilled(el, rec.autofilled);
  if ((rec.warnings || []).length && !$(".rec-warn", el)) {
    $(".tab-bar", el).insertAdjacentHTML("beforebegin", `<div class="notice rec-warn">${rec.warnings.map((w) => `⚠ ${esc(w)}`).join("<br>")}</div>`);
  }
  Object.entries(rec.suggested || {}).forEach(([field, value]) => {
    const c = $(`[name="${field}"]`, el);
    const label = c && c.closest("label");
    if (!label || $(".suggest", label) || field === "comparables" || field === "cost_items") return;
    const shown = Array.isArray(value) ? value.join(", ") : typeof value === "number" ? fmt(value, 2) : String(value);
    label.insertAdjacentHTML("beforeend", `<span class="suggest">New reading: <b>${esc(shown.length > 160 ? shown.slice(0, 160) + "…" : shown)}</b>
      <button type="button" class="btn small use-new">Use this</button></span>`);
    $(".use-new", label).onclick = () => {
      setFieldValue(el, field, value);
      c.dispatchEvent(new Event("input", { bubbles: true }));
      label.classList.add("changed");
      $(".suggest", label).innerHTML = "Using the new reading ✓";
    };
  });
  Object.keys(rec.kept || {}).forEach((field) => {
    const label = $(`[name="${field}"]`, el)?.closest("label");
    if (label && !$(".kept-hint", label)) label.insertAdjacentHTML("beforeend",
      '<span class="kept-hint">Kept from the saved record: this re-run did not find it</span>');
  });
  updateGaps(el);
  checkValueOpinion(el);
  ["capital_value", "size_sqm", "currency"].forEach((n) => $(`[name=${n}]`, el)?.addEventListener("input", () => checkValueOpinion(el)));
  el.addEventListener("input", () => updateGaps(el));
  el.addEventListener("change", () => updateGaps(el));
  const mmda = $("select[name=district]", el), region = $("[name=region]", el);
  if (mmda && region) mmda.addEventListener("change", () => {
    if (!region.value.trim() && MMDA_REGION[mmda.value]) region.value = MMDA_REGION[mmda.value];
  });
}

async function loadAttachments(box, recordId) {
  let items = [];
  try { items = await api(`/api/records/${recordId}/attachments`); }
  catch (e) { box.innerHTML = `<p class="error">${esc(e.message)}</p>`; return; }
  const kb = (n) => (n > 1048576 ? `${(n / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1024))} KB`);
  box.innerHTML = ATTACH_KINDS.map(([kind, label]) => {
    const mine = items.filter((a) => a.kind === kind);
    const accept = kind === "photo" ? "image/*" : ".pdf,.doc,.docx,.xls,.xlsx,.jpg,.jpeg,.png,.heic,.webp,.tif,.tiff,.txt";
    return `<div class="attach-kind" data-kind="${kind}">
      <div class="row between"><b>${esc(label)}</b> <span class="muted small">${mine.length || "none"}</span>
        ${can("junior") ? `<span><button type="button" class="btn small attach-add">+ Add</button><input type="file" class="attach-input" multiple accept="${accept}" hidden></span>` : ""}</div>
      <div class="attach-items">${mine.map((a) => `<div class="attach-item" data-id="${a.id}">
        ${a.content_type.startsWith("image/") ? `<img class="attach-thumb" alt="${esc(a.filename)}" data-id="${a.id}">` : '<span class="attach-icon">📄</span>'}
        <span class="attach-name" title="${esc(a.filename)}">${esc(a.filename)}<br><span class="muted small">${kb(a.size)}</span></span>
        <span class="row gap"><button type="button" class="btn small attach-open">Open</button>
        ${can("senior") ? '<button type="button" class="btn small danger attach-del">Delete</button>' : ""}</span></div>`).join("")}</div>
    </div>`;
  }).join("") + `<p class="muted small">Up to 10 MB per file. Photos can be taken straight from a phone camera.</p>`;
  $$(".attach-thumb", box).forEach(async (img) => {
    try { const r = await api(`/api/attachments/${img.dataset.id}`, { raw: true }); img.src = URL.createObjectURL(await r.blob()); } catch (_) {}
  });
  $$(".attach-kind", box).forEach((k) => {
    const input = $(".attach-input", k);
    const add = $(".attach-add", k);
    if (add) add.onclick = () => input.click();
    if (input) input.onchange = async () => {
      if (!input.files.length) return;
      const fd = new FormData();
      fd.append("kind", k.dataset.kind);
      [...input.files].forEach((f) => fd.append("files", f));
      add.disabled = true; add.textContent = "Uploading…";
      try { await api(`/api/records/${recordId}/attachments`, { method: "POST", body: fd }); toast("Attachment added"); }
      catch (e) { toast(e.message, true); }
      loadAttachments(box, recordId);
    };
  });
  $$(".attach-item", box).forEach((row) => {
    const a = items.find((x) => String(x.id) === row.dataset.id);
    $(".attach-open", row).onclick = async () => {
      if (a.content_type.startsWith("image/")) return showImage(a);
      download(`/api/attachments/${a.id}?download=true`, a.filename).catch((e) => toast(e.message, true));
    };
    const del = $(".attach-del", row);
    if (del) del.onclick = async () => {
      if (!confirm(`Delete ${a.filename}?`)) return;
      try { await api(`/api/attachments/${a.id}`, { method: "DELETE" }); toast("Attachment deleted"); } catch (e) { toast(e.message, true); }
      loadAttachments(box, recordId);
    };
  });
  $$(".attach-thumb", box).forEach((img) => (img.onclick = () => showImage(items.find((x) => String(x.id) === img.dataset.id))));
}

async function showImage(a) {
  const r = await api(`/api/attachments/${a.id}`, { raw: true });
  const url = URL.createObjectURL(await r.blob());
  const box = document.createElement("div");
  box.className = "lightbox";
  box.innerHTML = `<figure><img src="${url}" alt="${esc(a.filename)}"><figcaption>${esc(a.filename)}
    <button type="button" class="btn small">Download</button> <button type="button" class="btn small primary">Close</button></figcaption></figure>`;
  const [dl, close] = $$("button", box);
  const done = () => { box.remove(); URL.revokeObjectURL(url); };
  dl.onclick = () => download(`/api/attachments/${a.id}?download=true`, a.filename);
  close.onclick = done;
  box.addEventListener("click", (e) => { if (e.target === box) done(); });
  document.body.appendChild(box);
}

function readRecordEditor(el, original) {
  const rec = { comparables: original.comparables || null };
  const comps = {};
  const hasCompEditor = !!$(".comp-table", el);
  $$("input, select, textarea", el).forEach((inp) => {
    const name = inp.name;
    if (!name) return;
    let v = inp.value.trim();
    if (name.startsWith("cst_")) return;      // rates adopted: read below
    const cm = name.match(/^cmp_(\d+)_(\w+)$/);
    if (cm) {
      if (v === "") return;
      comps[cm[1]] = comps[cm[1]] || {};
      comps[cm[1]][cm[2]] = inp.dataset.kind === "num" ? toNum(v) : v;
      return;
    }
    if (name.startsWith("bd_")) {
      if (v !== "") { rec.cost_breakdown = rec.cost_breakdown || {}; rec.cost_breakdown[name.slice(3)] = toNum(v); }
      return;
    }
    if (name === "tags") { rec.tags = v ? v.split(",").map((t) => t.trim()).filter(Boolean) : null; return; }
    if (v === "") { rec[name] = null; return; }
    if (inp.dataset.kind === "num") v = toNum(v);
    if (inp.dataset.kind === "int") v = Math.round(toNum(v));
    rec[name] = v;
  });
  if (hasCompEditor) {
    const list = Object.keys(comps).sort((a, b) => a - b).map((k) => comps[k]);
    list.forEach((c) => { if (c.price && c.size_sqm && !c.price_per_sqm) c.price_per_sqm = Math.round((c.price / c.size_sqm) * 100) / 100; });
    rec.comparables = list.length ? list : null;
  }
  if ($(".cost-table", el)) {
    const items = readCostRows(el);
    rec.cost_items = items.length ? items : null;
  }
  if (!rec.currency) rec.currency = (state.health && state.health.default_currency) || "GHS";
  return rec;
}

async function openReview(id) {
  const r = await api(`/api/reports/${id}`);
  const panel = $("#review-panel");
  panel.classList.remove("hidden");
  const draft = r.draft || { records: [] };
  const approved = r.status === "confirmed";
  const pendingUpdate = approved && !!draft.reextracted;   // new extraction waiting to be applied
  // Saved records of an approved report, for display and for "Saved: ..." comparison.
  const saved = approved ? (await api(`/api/records?${qs({ report_id: id, page_size: 200, sort: "created", order: "asc" })}`)).items : [];
  let records = (pendingUpdate || !approved ? draft.records || [] : saved).map((x) => ({ ...x }));
  const canEdit = can("junior");
  const editable = canEdit && (!approved || pendingUpdate);

  const render = () => {
    const actions = [];
    actions.push('<button class="btn" id="dl-file">Open original</button>');
    if (canEdit) actions.push('<button class="btn" id="reprocess">Re-run extraction</button>');
    if (canEdit && !approved) actions.push('<button class="btn danger" id="reject">Reject</button>');
    if (can("senior")) actions.push('<button class="btn danger" id="del-report">Delete</button>');
    actions.push('<button class="btn" id="close-review">Close</button>');
    const banner = pendingUpdate
      ? `<div class="notice">New extraction (${esc(r.extraction_method || "")}) from ${esc(new Date(draft.reextracted_at).toLocaleString())}.
         Your approved values are kept. ${suggestCount(draft) ? `<b>${suggestCount(draft)}</b> field${suggestCount(draft) === 1 ? "" : "s"} read differently this time: tap <b>Use this</b> under any you want.` : "The new reading found nothing different."}
         Fields that were empty are filled and marked <span class="changed-chip">changed</span>.
         Nothing is saved until you tap <b>Update saved records</b>.</div>`
      : approved ? `<p class="muted small">🔒 Approved ${r.confirmed_at ? esc(new Date(r.confirmed_at).toLocaleString()) : ""}, locked. Showing the saved record${saved.length === 1 ? "" : "s"}.
         The only way to change ${saved.length === 1 ? "it" : "them"} is <b>Re-run extraction</b>, then review and tap <b>Update saved records</b>.</p>` : "";
    panel.innerHTML = `
      <div class="row between"><h3>Review: ${esc(r.filename)}</h3><div class="row gap">${actions.join("")}</div></div>
      <p class="muted small">Status: ${statusPill(r.status)} · Extraction: ${esc(r.extraction_method || "—")}</p>
      ${banner}
      ${draft.summary ? `<p>${esc(draft.summary)}</p>` : ""}
      ${draft.warning ? `<div class="notice">${esc(draft.warning)}</div>` : ""}
      ${r.error ? `<div class="notice">${esc(r.error)}</div>` : ""}
      <form id="review-form">${records.map(recordEditorHTML).join("")}</form>
      ${editable ? `<div class="row gap"><button class="btn" id="add-rec">+ Add record</button>
        ${pendingUpdate
          ? '<button class="btn" id="discard">Keep saved version</button><button class="btn primary" id="apply-update">Update saved records</button>'
          : '<button class="btn primary" id="confirm">Confirm &amp; save to database</button>'}</div>` : ""}`;
    $("#close-review").onclick = () => panel.classList.add("hidden");
    $("#dl-file").onclick = () => download(`/api/reports/${id}/file`, r.filename).catch((e) => toast(e.message, true));
    if (!editable) $$("input, select, textarea, .rm-record", $("#review-form")).forEach((x) => (x.disabled = true));
    $$(".review-record", panel).forEach((el) => initRecordEditor(el, records[Number(el.dataset.idx)] || {}));
    if (editable) $$(".review-record", panel).forEach((el) => {
      const head = $(".row.gap", el);
      head.insertAdjacentHTML("afterbegin", '<button type="button" class="btn small fill-gaps">✨ Fill gaps</button>');
      $(".fill-gaps", el).onclick = (ev) => fillGaps(el, records[Number(el.dataset.idx)] || {}, id, ev.target);
    });
    if (pendingUpdate) markChanges(panel, saved);
    $$(".review-record", panel).forEach((el) => {
      const i = Number(el.dataset.idx);
      $("select[name=category]", el).addEventListener("change", () => { records = collect(); records[i].category = $("select[name=category]", el).value; render(); });
      $(".rm-record", el).addEventListener("click", () => { records = collect(); records.splice(i, 1); render(); });
    });
    const del = $("#del-report");
    if (del) del.onclick = async () => {
      if (!confirm("Delete this report and any records saved from it?")) return;
      await api(`/api/reports/${id}`, { method: "DELETE" }); panel.classList.add("hidden"); loadReports();
    };
    const re = $("#reprocess");
    if (re) re.onclick = async (ev) => {
      if (approved && !confirm("Read this report again? The saved record stays unchanged until you review the new result and tap Update.")) return;
      ev.target.disabled = true; ev.target.textContent = "Extracting…";
      try { await api(`/api/reports/${id}/reprocess`, { method: "POST" }); openReview(id); loadReports(); }
      catch (e) { toast(e.message, true); ev.target.disabled = false; ev.target.textContent = "Re-run extraction"; }
    };
    if (!editable) return;
    $("#add-rec").onclick = () => { records = collect(); records.push({ category: r.report_type || "valuation" }); render(); };
    const confirmBtn = $("#confirm");
    if (confirmBtn) confirmBtn.onclick = async () => {
      try {
        const payload = collect();
        await api(`/api/reports/${id}/confirm`, { method: "POST", body: { records: payload } });
        toast(`Saved ${payload.length} record(s) to the database`);
        panel.classList.add("hidden");
        loadReports(); loadFacets();
      } catch (e) { toast(e.message, true); }
    };
    const rej = $("#reject");
    if (rej) rej.onclick = async () => { await api(`/api/reports/${id}/reject`, { method: "POST" }); panel.classList.add("hidden"); loadReports(); };
    const apply = $("#apply-update");
    if (apply) apply.onclick = async () => {
      if (!confirm("Replace the saved record(s) with these values?")) return;
      try {
        const payload = collect();
        await api(`/api/reports/${id}/update-records`, { method: "POST", body: { records: payload } });
        toast("Saved records updated");
        loadReports(); loadFacets(); openReview(id);
      } catch (e) { toast(e.message, true); }
    };
    const discard = $("#discard");
    if (discard) discard.onclick = async () => {
      try { await api(`/api/reports/${id}/discard-reextraction`, { method: "POST" }); toast("Kept the saved version"); openReview(id); }
      catch (e) { toast(e.message, true); }
    };
  };
  const collect = () => $$(".review-record", panel).map((el) => readRecordEditor(el, records[Number(el.dataset.idx)] || {}));
  render();
  panel.scrollIntoView({ behavior: "smooth" });
}

const suggestCount = (draft) => (draft.records || []).reduce((n, x) => n + Object.keys(x.suggested || {}).filter((k) => k !== "comparables" && !(k === "cost_per_sqm" && x.suggested.cost_items)).length, 0);

/* Mark every field whose new value differs from the saved record, showing the saved value. */
function markChanges(panel, saved) {
  const norm = (v) => {
    if (v === null || v === undefined) return "";
    if (Array.isArray(v)) return v.map((x) => String(x).trim().toLowerCase()).filter(Boolean).sort().join(",");
    const n = toNum(v);
    if (v !== "" && n !== null && !Number.isNaN(n) && /^-?[\d.,\s]+$/.test(String(v))) return String(Math.round(n * 100) / 100);
    return String(v).trim().toLowerCase();
  };
  let changed = 0;
  $$(".review-record", panel).forEach((el) => {
    const old = saved[Number(el.dataset.idx)];
    if (!old) { el.insertAdjacentHTML("afterbegin", '<p class="was-hint">New record: not in the saved data</p>'); changed++; return; }
    $$("input[name], select[name], textarea[name]", el).forEach((inp) => {
      const name = inp.name;
      if (name.startsWith("bd_") || name.startsWith("cmp_") || name.startsWith("cst_")) return;
      let now = inp.value;
      if (name === "tags") now = now.split(",").map((t) => t.trim()).filter(Boolean);
      const was = old[name];
      if (norm(now) === norm(was)) return;
      changed++;
      const label = inp.closest("label");
      if (!label) return;
      label.classList.add("changed");
      const shown = was === null || was === undefined || was === "" ? "(blank)" : Array.isArray(was) ? was.join(", ") : was;
      label.insertAdjacentHTML("beforeend", `<span class="was-hint">Saved: ${esc(shown)}</span>`);
    });
  });
  $$(".review-record", panel).forEach((el) => $$(".tab-panel", el).forEach((p) => {
    if ($("label.changed", p)) $(`.tab-btn[data-tab="${p.dataset.tab}"]`, el)?.classList.add("has-change");
  }));
  if (!changed && !$(".suggest", panel)) panel.querySelector(".notice")?.insertAdjacentHTML("beforeend", "<br><b>No differences from the saved record.</b>");
}

/* ---------------- Records ---------------- */
$("#record-filters").innerHTML = filterFormHTML(true);
$("#record-filters").addEventListener("submit", (e) => { e.preventDefault(); state.recordsPage = 1; loadRecords(); });
$("#record-filters").addEventListener("reset", () => setTimeout(() => { state.recordsPage = 1; loadRecords(); }));

async function loadRecords() {
  const f = formData($("#record-filters"));
  const d = await api("/api/records?" + qs({ ...f, page: state.recordsPage, page_size: 25 }));
  if (!d.items.length) { $("#records-table").innerHTML = '<p class="muted">No records match these filters.</p>'; $("#records-pager").innerHTML = ""; return; }
  if (state.sheet.on) { state.sheet.items = d.items; renderSheet(); } else {
  $("#records-table").innerHTML = table(["Date", "Category", "Location", "Type", "Address / title", { t: "Size m²", num: true }, { t: "Key figure", num: true }, { t: "Rate / m²", num: true }, "Tags"],
    d.items.map((r) => [esc(r.record_date || "—"), esc(r.category), esc([r.location, r.district, r.city].filter(Boolean).join(", ") || "—"), esc(r.property_type || "—"),
      esc(r.address || r.title || "—"), { v: fmt(r.size_sqm), num: true }, { v: keyFigure(r), num: true }, { v: rateFigure(r), num: true }, (r.tags || []).map((t) => `<span class="tag">${esc(t)}</span>`).join("")]),
    { clickable: true });
  $$("#records-table tbody tr").forEach((tr) => tr.addEventListener("click", () => openRecord(d.items[tr.dataset.i])));
  }
  const pages = Math.max(1, Math.ceil(d.total / d.page_size));
  $("#records-pager").innerHTML = `<span class="muted small">${d.total} records</span>
    <button class="btn small" ${d.page <= 1 ? "disabled" : ""} id="pg-prev">‹ Prev</button><span>${d.page} / ${pages}</span>
    <button class="btn small" ${d.page >= pages ? "disabled" : ""} id="pg-next">Next ›</button>`;
  const leave = () => !Object.keys(state.sheet.dirty).length || confirm("Leave this page without saving your spreadsheet changes?");
  $("#pg-prev").onclick = () => { if (leave()) { state.sheet.dirty = {}; state.recordsPage--; loadRecords(); } };
  $("#pg-next").onclick = () => { if (leave()) { state.sheet.dirty = {}; state.recordsPage++; loadRecords(); } };
}
function keyFigure(r) {
  const c = r.currency || "";
  if (r.category === "valuation") return r.capital_value != null ? `${c} ${fmt(r.capital_value)}` : r.annual_rent != null ? `${c} ${fmt(r.annual_rent)} p.a.` : "—";
  if (r.category === "cost") return r.total_cost != null ? `${c} ${fmt(r.total_cost)}` : "—";
  return r.avg_price != null ? `${c} ${fmt(r.avg_price)}` : r.avg_monthly_rent != null ? `${c} ${fmt(r.avg_monthly_rent)}/mo` : "—";
}
function rateFigure(r) {
  if (r.category === "valuation") return r.value_per_sqm != null ? fmt(r.value_per_sqm) : "—";
  if (r.category === "cost") return r.cost_per_sqm != null ? fmt(r.cost_per_sqm) : "—";
  return "—";
}

function openRecord(rec) {
  const locked = !!(rec && rec.locked);
  const editable = can("junior") && !locked;
  openModal(`<div class="row between"><h3>Record #${rec && rec.id ? rec.id : "new"}</h3><button class="btn" onclick="closeModal()">Close</button></div>
    ${locked ? `<div class="notice lock-note">🔒 <b>Approved, locked.</b> This record was saved from approved report #${rec.report_id} and can't be edited here.
      To change it, open the report, tap <b>Re-run extraction</b>, check the new reading and tap <b>Update saved records</b>.
      ${can("junior") ? '<button type="button" class="btn small" id="open-source">Open the report</button>' : ""}</div>`
      : rec && rec.report_id ? `<p class="muted small">Source report #${rec.report_id}</p>` : ""}
    <form id="rec-form">${recordEditorHTML(rec || { category: "valuation", currency: (state.health && state.health.default_currency) || "GHS" }, 0)}</form>
    <div class="row gap">${editable ? '<button class="btn primary" id="rec-save">Save</button>' : ""}${rec && rec.id && can("senior") && !locked ? '<button class="btn danger" id="rec-del">Delete</button>' : ""}</div>`);
  const src = $("#open-source");
  if (src) src.onclick = () => { closeModal(); location.hash = "#ingest"; setTimeout(() => openReview(rec.report_id), 300); };
  const el = $("#rec-form .review-record");
  $(".rm-record", el).remove();
  initRecordEditor(el, rec || {});
  if (editable) {
    $(".row.gap", el).insertAdjacentHTML("afterbegin", '<button type="button" class="btn small fill-gaps">✨ Fill gaps</button>');
    $(".fill-gaps", el).onclick = (ev) => fillGaps(el, rec || {}, rec && rec.report_id, ev.target);
  }
  if (!editable) $$("#rec-form input, #rec-form select, #rec-form textarea, #rec-form .comp-add, #rec-form .comp-del").forEach((x) => (x.disabled = true));
  // Re-render with the category's fields, keeping what has been typed so far.
  $("select[name=category]", el).addEventListener("change", () => {
    const typed = readRecordEditor(el, rec || {});
    openRecord(rec && rec.id ? { ...rec, ...typed } : { ...typed, isNew: true });
  });
  const save = $("#rec-save");
  if (save) save.onclick = async () => {
    const body = readRecordEditor(el, rec || {});
    try {
      if (rec && rec.id) await api(`/api/records/${rec.id}`, { method: "PATCH", body });
      else await api("/api/records", { method: "POST", body });
      closeModal(); toast("Record saved"); loadRecords(); loadFacets();
    } catch (e) { toast(e.message, true); }
  };
  const del = $("#rec-del");
  if (del) del.onclick = async () => {
    if (!confirm("Delete this record permanently?")) return;
    await api(`/api/records/${rec.id}`, { method: "DELETE" }); closeModal(); toast("Record deleted"); loadRecords();
  };
}
$("#new-record").addEventListener("click", () => openRecord(null));

function evidenceExport(format) {
  const f = formData($("#record-filters"));
  if (!f.category && format === "docx") { toast("Choose a category (valuation, cost or housing) for the Word table", true); return; }
  download("/api/exports/evidence?" + qs({ ...f, format }), `pis-records.${format}`).catch((e) => toast(e.message, true));
}
$("#export-csv").addEventListener("click", () => evidenceExport("csv"));
$("#export-xlsx").addEventListener("click", () => evidenceExport("xlsx"));
$("#export-docx").addEventListener("click", () => evidenceExport("docx"));

/* ---------------- Gaps: count, highlight and fill empty fields ---------------- */
const GAP_SKIP = /^(cmp_|bd_|cst_)/;
function gapControls(scope) {
  // Notes and tags are optional extras, not gaps.
  return $$("input[name], select[name], textarea[name]", scope).filter((c) => !GAP_SKIP.test(c.name) && c.type !== "file"
    && !["category", "notes", "tags"].includes(c.name));
}
function isEmpty(c) { return !String(c.value || "").trim(); }
/* Tab badges with the number of empty fields; outline empties when gap mode is on. */
function updateGaps(el) {
  $$(".tab-panel", el).forEach((p) => {
    if (p.dataset.tab === "attachments") return;
    const empty = gapControls(p).filter(isEmpty);
    const btn = $(`.tab-btn[data-tab="${p.dataset.tab}"]`, el);
    if (!btn) return;
    let badge = $(".gap-badge", btn);
    if (!badge) { badge = document.createElement("span"); badge.className = "gap-badge"; btn.appendChild(badge); }
    badge.textContent = empty.length || "";
    badge.title = empty.length ? `${empty.length} empty field${empty.length === 1 ? "" : "s"}` : "";
    badge.hidden = !empty.length;
  });
  gapControls(el).forEach((c) => c.closest("label")?.classList.toggle("gap", isEmpty(c)));
}
function markAutofilled(el, reasons) {
  Object.entries(reasons || {}).forEach(([field, reason]) => {
    const c = $(`[name="${field}"]`, el);
    const label = c && c.closest("label");
    if (!label) return;
    label.classList.add("autofilled");
    $(".auto-hint", label)?.remove();
    label.insertAdjacentHTML("beforeend", `<span class="auto-hint">Auto-filled: ${esc(reason)} — please check</span>`);
  });
}
function setFieldValue(el, field, value) {
  if (field === "cost_items") {
    const tb = $(".cost-table tbody", el);
    if (!tb || !Array.isArray(value)) return false;
    tb.innerHTML = value.map(costRowHTML).join(""); initCostRows(el);
    return true;
  }
  if (field === "comparables") {
    const tbody = $(".comp-table tbody", el);
    if (!tbody || !Array.isArray(value)) return false;
    value.forEach((c) => tbody.insertAdjacentHTML("beforeend", compRowHTML(c)));
    initCompRows(el);
    return true;
  }
  if (field === "cost_breakdown" && value && typeof value === "object") {
    let any = false;
    Object.entries(value).forEach(([k, v]) => { const c = $(`[name="bd_${k}"]`, el); if (c && isEmpty(c)) { c.value = withCommas(v); any = true; } });
    return any;
  }
  const c = $(`[name="${field}"]`, el);
  if (!c) return false;
  let v = Array.isArray(value) ? value.join(", ") : value ?? "";
  if (c.tagName === "SELECT") {
    if (field === "basis_of_value") v = basisCanonical(v);
    if (![...c.options].some((o) => o.value === String(v))) c.insertAdjacentHTML("beforeend", `<option value="${esc(v)}">${esc(v)}</option>`);
  }
  c.value = c.dataset.num ? withCommas(v) : v;
  return true;
}
async function fillGaps(el, original, reportId, btn) {
  const label = btn ? btn.textContent : "";
  if (btn) { btn.disabled = true; btn.textContent = "Filling gaps…"; }
  try {
    const current = readRecordEditor(el, original || {});
    const res = await api("/api/gaps/fill", { method: "POST", body: { record: current, report_id: reportId || null } });
    const reasons = {};
    Object.entries(res.filled).forEach(([field, { value, reason }]) => { if (setFieldValue(el, field, value)) reasons[field] = reason; });
    markAutofilled(el, reasons);
    el.classList.add("show-gaps");
    updateGaps(el);
    const left = gapControls(el).filter(isEmpty).length;
    const n = Object.keys(reasons).length;
    toast(`${n ? `Filled ${n} gap${n === 1 ? "" : "s"}` : "No more gaps could be filled automatically"} · ${left} field${left === 1 ? "" : "s"} still empty (outlined) — fill them in and save${res.warning ? ". " + res.warning : ""}`);
  } catch (e) { toast(e.message, true); }
  if (btn) { btn.disabled = false; btn.textContent = label; }
}

/* Bulk: fill gaps across saved records (Data & Search). */
$("#fill-gaps-bulk").addEventListener("click", async () => {
  const f = formData($("#record-filters"));
  let plan;
  try { plan = await api("/api/gaps/bulk/preview?" + qs(f)); } catch (e) { toast(e.message, true); return; }
  const fieldLabel = (k) => (GRID_COLS.find((c) => c[0] === k) || [k, k.replace(/_/g, " ")])[1];
  const show = (v) => (Array.isArray(v) ? v.join(", ") : typeof v === "object" && v ? `${Object.keys(v).length} items` : v);
  const list = plan.records.map((r) => `<li><b>Ref ${r.id}</b> ${esc(r.label)}<ul>${Object.entries(r.filled)
    .map(([k, { value, reason }]) => `<li>${esc(fieldLabel(k))}: <b>${esc(show(value))}</b> <span class="muted small">(${esc(reason)})</span></li>`).join("")}</ul></li>`).join("");
  openModal(`<h3>Fill gaps</h3>
    <p class="muted small">Only empty fields are filled, from information each record or its report already contains. Nothing you entered is changed.</p>
    ${plan.records.length ? `<p>${plan.fields} gap${plan.fields === 1 ? "" : "s"} can be filled in ${plan.records.length} record${plan.records.length === 1 ? "" : "s"}:</p><ul class="import-list">${list}</ul>` : "<p>No gaps can be filled automatically for these records. Use the spreadsheet view to type in what's missing.</p>"}
    <div class="row gap">${plan.records.length && can("junior") ? '<button class="btn primary" id="gaps-apply">Fill these gaps</button>' : ""}<button class="btn" onclick="closeModal()">Close</button></div>`);
  const go = $("#gaps-apply");
  if (go) go.onclick = async () => {
    go.disabled = true; go.textContent = "Filling…";
    try { const r = await api("/api/gaps/bulk/apply?" + qs(f), { method: "POST" }); closeModal(); toast(`Filled ${r.fields} gaps in ${r.records} records`); loadRecords(); loadFacets(); }
    catch (e) { toast(e.message, true); go.disabled = false; go.textContent = "Fill these gaps"; }
  };
});

/* ---------------- Spreadsheet view (edit records like Excel, in the website) ---------------- */
const GRID_COLS = [
  ["record_date", "Date", "date"], ["category", "Category", "cat"], ["gps_address", "GPS address"],
  ["location", "Location"], ["district", "MMDA", "mmda"], ["city", "City"],
  ["region", "Region", "list:dl-regions"], ["property_type", "Property type", "list:dl-types"], ["client", "Client"],
  ["purpose", "Purpose", "list:dl-purposes"], ["basis_of_value", "Basis of value", "basis"], ["valuer", "Valuer"],
  ["property_name", "Property name / no."], ["owner", "Owner"], ["tenure", "Tenure", "list:dl-tenure"],
  ["address", "Address"], ["size_sqm", "Floor area m²", "num"], ["inspection_date", "Date of inspection", "date"],
  ["capital_value", "Market value", "num"], ["forced_sale_value", "Forced sale value", "num"],
  ["overall_marketability", "Overall marketability"],
  ["annual_rent", "Annual rent", "num"], ["yield_pct", "Yield %", "num"], ["method", "Method"],
  ["total_cost", "Total cost", "num"], ["cost_per_sqm", "Rate (cost / m²)", "num"], ["avg_price", "Avg price", "num"],
  ["avg_monthly_rent", "Avg rent / month", "num"], ["currency", "Currency"], ["tags", "Tags", "tags"], ["notes", "Notes"],
];
state.sheet = { on: false, items: [], dirty: {} };

function sheetCell(rec, [key, , kind]) {
  let v = rec[key];
  if (kind === "tags") v = (v || []).join(", ");
  v = v ?? "";
  const ro = can("junior") && !rec.locked ? "" : ` disabled${rec.locked ? ' title="Approved report: locked (re-run the extraction to change it)"' : ""}`;
  const common = `data-id="${rec.id}" data-key="${key}" data-kind="${kind || ""}"${ro}`;
  if (kind === "mmda") return `<select ${common}>${mmdaOptions(v, "—")}</select>`;
  if (kind === "basis") { const b = basisCanonical(v); return `<select ${common}><option value="">—</option>${(BASES.includes(b) || !b ? BASES : [b, ...BASES]).map((o) => `<option ${o === b ? "selected" : ""}>${esc(o)}</option>`).join("")}</select>`; }
  if (kind === "cat") return `<select ${common}>${["valuation", "cost", "housing"].map((c) => `<option ${c === v ? "selected" : ""}>${c}</option>`).join("")}</select>`;
  if (kind === "num") return `<input ${NUM_ATTR} value="${esc(withCommas(v))}" placeholder="—" ${common}>`;
  const type = kind === "date" ? "date" : "text";
  const list = kind && kind.startsWith("list:") ? ` list="${kind.slice(5)}"` : "";
  return `<input type="${type}"${list} value="${esc(v)}"${type === "date" ? "" : ' placeholder="—"'} ${common}>`;
}

function renderSheet() {
  const { items } = state.sheet;
  if (!items.length) { $("#records-table").innerHTML = '<p class="muted">No records match these filters.</p>'; return; }
  const head = `<tr><th class="sticky-col">Ref</th>${GRID_COLS.map(([, l]) => `<th>${esc(l)}</th>`).join("")}</tr>`;
  const body = items.map((r) => `<tr><td class="sticky-col muted">${r.id}</td>${GRID_COLS.map((c) => `<td>${sheetCell(r, c)}</td>`).join("")}</tr>`).join("");
  $("#records-table").innerHTML = `<div class="sheet-bar row between">
      <span class="muted small">Edit cells like a spreadsheet. Changed cells turn gold. ${can("junior") ? "" : "(Read-only for your role.)"}</span>
      <span class="row gap"><button class="btn" id="sheet-undo" disabled>Undo changes</button>
      <button class="btn primary" id="sheet-save" disabled>Save changes</button></span></div>
    <div class="sheet-wrap"><table class="sheet"><thead>${head}</thead><tbody>${body}</tbody></table></div>`;
  $$(".sheet [data-key]").forEach((el) => el.addEventListener(el.tagName === "SELECT" ? "change" : "input", () => {
    const id = el.dataset.id, key = el.dataset.key;
    const rec = items.find((x) => String(x.id) === id);
    let val = el.value.trim();
    if (el.dataset.kind === "num") val = toNum(val);
    else if (el.dataset.kind === "tags") val = val ? val.split(",").map((t) => t.trim()).filter(Boolean) : null;
    else if (val === "") val = null;
    const orig = rec[key];
    const same = JSON.stringify(orig ?? null) === JSON.stringify(val) || (el.dataset.kind === "tags" && (orig || []).join(", ") === el.value.trim());
    state.sheet.dirty[id] = state.sheet.dirty[id] || {};
    if (same) delete state.sheet.dirty[id][key]; else state.sheet.dirty[id][key] = val;
    if (!Object.keys(state.sheet.dirty[id]).length) delete state.sheet.dirty[id];
    el.classList.toggle("dirty", !same);
    updateSheetButtons();
  }));
  $("#sheet-save").onclick = saveSheet;
  $("#sheet-undo").onclick = () => { state.sheet.dirty = {}; renderSheet(); };
  updateSheetButtons();
}

function updateSheetButtons() {
  const n = Object.values(state.sheet.dirty).reduce((a, d) => a + Object.keys(d).length, 0);
  const save = $("#sheet-save"), undo = $("#sheet-undo");
  if (!save) return;
  save.disabled = undo.disabled = n === 0;
  save.textContent = n ? `Save ${n} change${n === 1 ? "" : "s"}` : "Save changes";
}

async function saveSheet() {
  const entries = Object.entries(state.sheet.dirty);
  const btn = $("#sheet-save"); btn.disabled = true; btn.textContent = "Saving…";
  const failed = [];
  for (const [id, body] of entries) {
    try { await api(`/api/records/${id}`, { method: "PATCH", body }); delete state.sheet.dirty[id]; }
    catch (e) { failed.push(`Ref ${id}: ${e.message}`); }
  }
  toast(failed.length ? `Saved with problems: ${failed.join("; ")}` : `Saved ${entries.length} record${entries.length === 1 ? "" : "s"}`, failed.length > 0);
  loadRecords(); loadFacets();
}

$("#sheet-toggle").addEventListener("click", () => {
  if (state.sheet.on && Object.keys(state.sheet.dirty).length && !confirm("Leave the spreadsheet without saving your changes?")) return;
  state.sheet.on = !state.sheet.on;
  state.sheet.dirty = {};
  $("#sheet-toggle").textContent = state.sheet.on ? "Table view" : "Spreadsheet view";
  loadRecords();
});

/* ---------------- Import an edited Excel file ---------------- */
$("#import-xlsx").addEventListener("click", () => $("#import-file").click());
$("#import-file").addEventListener("change", async (e) => {
  const file = e.target.files[0];
  e.target.value = "";
  if (!file) return;
  const fd = () => { const f = new FormData(); f.append("file", file); return f; };
  let plan;
  try { plan = await api("/api/imports/records/preview", { method: "POST", body: fd() }); }
  catch (err) { toast(err.message, true); return; }
  const show = (v) => (v === null || v === undefined || v === "" ? "(blank)" : Array.isArray(v) ? v.join(", ") : v);
  const label = (k) => (GRID_COLS.find((c) => c[0] === k) || [k, k.replace(/_/g, " ")])[1];
  const updates = plan.updates.map((u) => `<li><b>Ref ${u.id}</b> ${esc(u.label)} (row ${u.row}):<ul>${Object.entries(u.changes)
    .map(([k, [a, b]]) => `<li>${esc(label(k))}: <s>${esc(show(a))}</s> → <b>${esc(show(b))}</b></li>`).join("")}</ul></li>`).join("");
  const creates = plan.creates.map((c) => `<li>Row ${c.row}: new ${esc(c.values.category)} record ${esc(c.values.location || c.values.address || c.values.title || "")}</li>`).join("");
  const errors = plan.errors.map((x) => `<li>Row ${x.row}: ${esc(x.error)}</li>`).join("");
  const nothing = !plan.updates.length && !plan.creates.length;
  openModal(`<h3>Import "${esc(file.name)}"</h3>
    <p class="muted small">Sheet "${esc(plan.sheet)}" · ${plan.updates.length} to update · ${plan.creates.length} new · ${plan.unchanged} unchanged · ${plan.errors.length} with problems</p>
    ${updates ? `<h4>Changes</h4><ul class="import-list">${updates}</ul>` : ""}
    ${creates ? `<h4>New records</h4><ul class="import-list">${creates}</ul>` : ""}
    ${errors ? `<h4>Skipped rows</h4><ul class="import-list error">${errors}</ul>` : ""}
    ${nothing ? '<p>No changes found in this file.</p>' : ""}
    <div class="row gap">${nothing ? "" : '<button class="btn primary" id="import-apply">Apply changes</button>'}<button class="btn" onclick="closeModal()">Cancel</button></div>`);
  const applyBtn = $("#import-apply");
  if (applyBtn) applyBtn.onclick = async () => {
    applyBtn.disabled = true; applyBtn.textContent = "Applying…";
    try {
      const r = await api("/api/imports/records/apply", { method: "POST", body: fd() });
      closeModal();
      toast(`Excel imported: ${r.updated} updated, ${r.created} added${r.errors.length ? `, ${r.errors.length} rows skipped` : ""}`);
      loadRecords(); loadFacets();
    } catch (err) { toast(err.message, true); applyBtn.disabled = false; applyBtn.textContent = "Apply changes"; }
  };
});

/* ---------------- Intelligence ---------------- */
const METRIC_OPTIONS = [["capital_value", "Valuation opinion"], ["annual_rent", "Rental opinion (annual)"], ["rent_per_sqm", "Rental opinion / m²"],
  ["yield_pct", "Yield %"], ["cost_per_sqm", "Construction cost / m²"], ["avg_price", "Avg house price"], ["avg_monthly_rent", "Avg monthly rent"], ["affordability_ratio", "Affordability ratio"]];
$("#intel-filters").innerHTML = filterFormHTML(false);
$("#trend-metric").innerHTML = METRIC_OPTIONS.map(([v, l]) => `<option value="${v}">${l}</option>`).join("");
$("#intel-filters").addEventListener("submit", (e) => { e.preventDefault(); loadIntel(); });
$("#intel-filters").addEventListener("reset", () => setTimeout(loadIntel));
["#trend-metric", "#trend-interval"].forEach((s) => $(s).addEventListener("change", loadTrend));
$("#group-by").addEventListener("change", loadBreakdown);

const intelFilters = () => formData($("#intel-filters"));

/* Land value trends by MMDA */
const MMDA_MEASURES = {
  land: [["per_acre", "Average land value per acre"], ["per_sqm", "Average land value per m²"], ["total", "Average land value (per valuation)"]],
  cost: [["main", "Construction cost per m²: main building"], ["external", "Construction cost per m²: external works"]],
};
const MMDA_INTRO = {
  land: "Average land values over time in each Metropolitan, Municipal and District Assembly: from land valuations and the land values stated in other reports (e.g. the cost approach).",
  cost: "Average construction cost per m² over time in each MMDA: from construction cost reports and the rates adopted in valuation reports (main building = the floors; external works = tap area, compound, walls …).",
};
function setLandMeasures() {
  const topic = $("#land-topic").value;
  $("#land-metric").innerHTML = MMDA_MEASURES[topic].map(([v, l]) => `<option value="${v}">${l}</option>`).join("");
  $("#land-intro").textContent = MMDA_INTRO[topic];
}
function setupLandCard() {
  const main = $("#land-mmda");
  if (!main || main.options.length) return;
  setLandMeasures();
  $("#land-topic").addEventListener("change", () => { setLandMeasures(); loadLand().catch((e) => toast(e.message, true)); });
  main.innerHTML = mmdaOptions("", "All MMDAs");
  $("#land-cmp1").innerHTML = mmdaOptions("", "— none —");
  $("#land-cmp2").innerHTML = mmdaOptions("", "— none —");
  ["land-mmda", "land-cmp1", "land-cmp2", "land-metric", "land-interval"].forEach((id) =>
    $(`#${id}`).addEventListener("change", () => loadLand().catch((e) => toast(e.message, true))));
}
async function loadLand() {
  setupLandCard();
  const f = intelFilters(); delete f.district;
  const picks = [$("#land-mmda").value, $("#land-cmp1").value, $("#land-cmp2").value];
  const districts = picks[0] ? [...new Set(picks.filter(Boolean))] : ["", ...new Set(picks.slice(1).filter(Boolean))];
  const topic = $("#land-topic").value, metric = $("#land-metric").value, interval = $("#land-interval").value;
  const path = topic === "cost" ? "/api/analytics/cost-mmda?" : "/api/analytics/land?";
  const d = await api(path + qs({ ...f, districts: districts.join(","), metric, interval }));
  const cur = d.currency || (state.health && state.health.default_currency) || "GHS";
  const unit = { per_acre: " per acre", per_sqm: " per m²", total: "", main: " per m²", external: " per m²" }[metric];
  const what = topic === "cost" ? "record" : "valuation";
  const shortName = (n) => n.replace(/ (Metropolitan|Municipal|District)$/, "");
  $("#land-summary").innerHTML = d.series.map((s) => {
    const last = s.points[s.points.length - 1];
    return kpi(shortName(s.district), last ? `${cur} ${fmt(last.mean)}${unit}` : "—",
      last ? `${last.period} · ${s.valuations} ${what}${s.valuations === 1 ? "" : "s"}${s.overall_change_pct != null ? ` · ${s.overall_change_pct > 0 ? "▲" : "▼"} ${fmt(Math.abs(s.overall_change_pct), 1)}% since ${s.points[0].period}` : ""}` : topic === "cost" ? "no construction rates yet" : "no land values yet");
  }).join("");
  const periods = [...new Set(d.series.flatMap((s) => s.points.map((p) => p.period)))].sort();
  if (periods.length) {
    $("#land-chart").closest(".chart-box").hidden = false;
    lineChart("land-chart", periods, d.series.map((s) => {
      const m = Object.fromEntries(s.points.map((p) => [p.period, p.mean]));
      return { label: `${shortName(s.district)} (${cur}${unit})`, data: periods.map((p) => m[p] ?? null) };
    }));
  } else {
    $("#land-chart").closest(".chart-box").hidden = true;
  }
  const rows = [];
  d.series.forEach((s) => s.points.forEach((p) => rows.push([esc(shortName(s.district)), esc(p.period), { v: fmt(p.mean), num: true },
    { v: fmt(p.median), num: true }, { v: p.count, num: true },
    { v: p.change_pct == null ? "—" : `${p.change_pct > 0 ? "▲" : p.change_pct < 0 ? "▼" : ""} ${fmt(Math.abs(p.change_pct), 1)}%`, num: true }])));
  $("#land-table").innerHTML = rows.length
    ? table(["MMDA", "Period", { t: `Average (${cur}${unit})`, num: true }, { t: "Median", num: true }, { t: topic === "cost" ? "Records" : "Valuations", num: true }, { t: "Change", num: true }], rows)
    : topic === "cost"
      ? `<p class="muted">No ${metric === "external" ? "external works" : "construction"} rates ${picks[0] ? `for ${esc(picks[0])}` : "yet"} in the filtered data.</p>`
      : `<p class="muted">No land values ${picks[0] ? `for ${esc(picks[0])}` : "yet"}${metric !== "total" ? " with a land size" : ""} in the filtered data.</p>`;
  const notes = [];
  if (d.no_area) notes.push(`${d.no_area} land value${d.no_area === 1 ? "" : "s"} left out because the land size isn't recorded (choose “per valuation” to include them)`);
  if (d.undated) notes.push(`${d.undated} without a valuation date`);
  if (topic === "land") notes.push(`plots are counted as 100 × 70 ft (${fmt(d.plot_sqm)} m²) when the size is given in plots`);
  $("#land-note").textContent = notes.join(" · ");
}

async function loadIntel() {
  loadLand().catch((e) => toast(e.message, true));
  const f = intelFilters();
  const s = await api("/api/analytics/summary?" + qs(f));
  const cur = s.currency || "";
  showSuspects($("#intel-warning"), s.suspect_values, s.currency || "");
  renderBasisTable($("#intel-bases"), s.by_basis || [], s.currency || "");
  const chosen = f.basis_of_value;
  if (chosen && s.metrics.capital_value) s.metrics.capital_value.label = `Average ${chosen}`;
  const cards = [kpi("Records", fmt(s.record_count), s.scope)];
  Object.entries(s.metrics).forEach(([k, m]) => {
    const ratio = k === "yield_pct" || k === "affordability_ratio" || k === "supply_units";
    const v = ratio ? fmt(m.mean, 2) + (k === "yield_pct" ? "%" : "") : `${cur} ${fmt(m.mean)}`;
    cards.push(kpi(m.label, v, `median ${fmt(m.median, ratio ? 2 : 0)} · range ${fmt(m.min, ratio ? 2 : 0)}–${fmt(m.max, ratio ? 2 : 0)} · n=${m.count}`));
  });
  if (Object.keys(s.other_currencies_excluded || {}).length) cards.push(kpi("Excluded (other currency)", Object.entries(s.other_currencies_excluded).map(([c, n]) => `${n} ${c}`).join(", "), "filter by currency to analyse them"));
  $("#intel-metrics").innerHTML = cards.join("");
  await Promise.all([loadTrend(), loadCross(), loadBreakdown()]);
}
async function loadTrend() {
  const t = await api("/api/analytics/trends?" + qs({ ...intelFilters(), metric: $("#trend-metric").value, interval: $("#trend-interval").value }));
  lineChart("trend-chart", t.series.map((p) => p.period), [
    { label: `${t.label} — mean`, data: t.series.map((p) => p.mean) },
    { label: "median", data: t.series.map((p) => p.median), borderDash: [4, 4] },
  ]);
}
async function loadCross() {
  const f = intelFilters(); delete f.category;
  const c = await api("/api/analytics/cross?" + qs(f));
  lineChart("cross-chart", c.rows.map((r) => r.period), [
    { label: `Main building (${c.currency || ""} / m²)`, data: c.rows.map((r) => r.cost_per_sqm) },
    { label: `External works (${c.currency || ""} / m²)`, data: c.rows.map((r) => r.external_works_rate) },
  ], { type: "bar" });
}
async function loadBreakdown() {
  const b = await api("/api/analytics/breakdown?" + qs({ ...intelFilters(), group_by: $("#group-by").value }));
  const groups = $("#group-by").value.split(",");
  if (!b.rows.length) { $("#breakdown-table").innerHTML = '<p class="muted">No data.</p>'; return; }
  const cols = [["capital_value", "Valuation opinion"], ["annual_rent", "Rental opinion"], ["yield_pct", "Yield %"], ["cost_per_sqm", "Cost/m²"], ["avg_price", "Avg price"], ["avg_monthly_rent", "Avg rent/mo"]]
    .filter(([k]) => b.rows.some((r) => r[k] != null));
  $("#breakdown-table").innerHTML = `<p class="muted small">Averages in ${esc(b.currency || "—")}</p>` + table(
    [...groups.map((g) => g.replace("_", " ")), { t: "Records", num: true }, ...cols.map(([, l]) => ({ t: l, num: true }))],
    b.rows.map((r) => [...groups.map((g) => esc(r[g])), { v: r.count, num: true }, ...cols.map(([k]) => ({ v: fmt(r[k], k === "yield_pct" ? 2 : 0), num: true }))]));
}

$("#gen-commentary").addEventListener("click", async (e) => {
  const btn = e.target; btn.disabled = true; btn.textContent = "Generating…";
  try {
    const d = await api("/api/analytics/commentary", { method: "POST", body: { ...intelFilters(), tone: $("#comm-tone").value } });
    $("#commentary").value = d.commentary;
    $("#commentary").dataset.scope = d.scope;
    $("#comm-meta").textContent = `${d.method === "ai" ? "Drafted by AI" : "Template draft"} from ${d.record_count} records · ${d.scope}${d.warning ? " · " + d.warning : ""}. Review before use.`;
  } catch (err) { toast(err.message, true); }
  btn.disabled = false; btn.textContent = "Generate";
});
$("#intel-xlsx").addEventListener("click", async (e) => {
  const btn = e.target; btn.disabled = true; btn.textContent = "Preparing Excel…";
  try {
    await download("/api/exports/analysis", "pis-market-analysis.xlsx", { method: "POST", body: {
      ...intelFilters(), group_by: $("#group-by").value, commentary: $("#commentary").value || null } });
  } catch (err) { toast(err.message, true); }
  btn.disabled = false; btn.textContent = "Export analysis to Excel";
});
const commBody = (format) => ({ commentary: $("#commentary").value, scope: $("#commentary").dataset.scope, format });
$("#comm-docx").addEventListener("click", () => download("/api/analytics/commentary/export", "market-commentary.docx", { method: "POST", body: commBody("docx") }).catch((e) => toast(e.message, true)));
$("#comm-txt").addEventListener("click", () => download("/api/analytics/commentary/export", "market-commentary.txt", { method: "POST", body: commBody("txt") }).catch((e) => toast(e.message, true)));
$("#comm-copy").addEventListener("click", async () => { try { await navigator.clipboard.writeText($("#commentary").value); toast("Copied"); } catch (_) { toast("Copy failed — select the text manually", true); } });

/* ---------------- Comparables ---------------- */
let compResults = [];
$("#comp-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = formData(e.target);
  try {
    compResults = await api("/api/comparables?" + qs(f));
    if (!compResults.length) { $("#comp-results").innerHTML = '<p class="muted">No evidence found.</p>'; return; }
    $("#comp-results").innerHTML = table(["", { t: "Score", num: true }, "Date", "Address / location", "Type", { t: "Size m²", num: true }, { t: "Key figure", num: true }, { t: "Rate / m²", num: true }, "Why"],
      compResults.map((c, i) => [`<input type="checkbox" class="comp-pick" data-i="${i}" checked>`, { v: c.score, num: true }, esc(c.record.record_date || "—"),
        esc([c.record.address, c.record.location].filter(Boolean).join(" — ") || "—"), esc(c.record.property_type || "—"), { v: fmt(c.record.size_sqm), num: true },
        { v: keyFigure(c.record), num: true }, { v: rateFigure(c.record), num: true }, `<span class="muted small">${esc(c.reasons.join(", "))}</span>`]));
  } catch (err) { toast(err.message, true); }
});
function compExport(format) {
  const ids = $$(".comp-pick:checked").map((cb) => compResults[cb.dataset.i].record.id);
  if (!ids.length) { toast("Select at least one comparable", true); return; }
  const category = $("#comp-form [name=category]").value;
  download("/api/exports/evidence?" + qs({ category, ids: ids.join(","), format }), `comparables.${format}`).catch((e) => toast(e.message, true));
}
$("#comp-csv").addEventListener("click", () => compExport("csv"));
$("#comp-xlsx").addEventListener("click", () => compExport("xlsx"));
$("#comp-docx").addEventListener("click", () => compExport("docx"));

/* ---------------- Admin ---------------- */
function locationCell(u) {
  const last = u.last_latitude != null
    ? `${mapLink(u.last_latitude, u.last_longitude, u.last_location_accuracy)}<br><span class="muted small">${ago(u.last_location_at)}</span>` : "";
  const st = u.location_status;
  if (st && st !== "shared") {
    const why = st === "permission refused" ? "Location blocked on this device" : `Location not shared (${esc(st)})`;
    return `<span class="loc-warn">⚠ ${why}</span> <span class="muted small">${ago(u.location_status_at)}</span>${last ? `<br><span class="muted small">Earlier:</span> ${last}` : ""}`;
  }
  return last || '<span class="muted small">not shared yet</span>';
}

/* Online now / active x min ago / last seen ... */
function ago(iso) {
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 90) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return new Date(iso).toLocaleString(undefined, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
}
function presence(u) {
  if (u.online) return '<span class="presence"><span class="dot on"></span>Online now</span>';
  if (!u.last_seen_at) return '<span class="presence muted"><span class="dot"></span>Never signed in</span>';
  const out = u.signed_out_at && new Date(u.signed_out_at) >= new Date(u.last_seen_at);
  return `<span class="presence muted" title="${esc(new Date(out ? u.signed_out_at : u.last_seen_at).toLocaleString())}"><span class="dot"></span>${out ? `Signed out ${ago(u.signed_out_at)}` : `Last active ${ago(u.last_seen_at)}`}</span>`;
}

async function loadAdmin() {
  const [users, log] = await Promise.all([api("/api/users"), api("/api/activity?limit=300")]);
  const online = users.filter((u) => u.online);
  $("#online-now").innerHTML = online.length
    ? `<span class="dot on"></span><b>${online.length} online now:</b> ${online.map((u) => esc(u.full_name || u.email) + (state.user && u.id === state.user.id ? " (you)" : "")).join(", ")}`
    : '<span class="dot"></span>Nobody is online right now.';
  $("#users-table").innerHTML = table(["Name", "Email", "Role", "Status", "Location at last sign-in", "Account", ""], users.map((u) => [esc(u.full_name), esc(u.email),
    `<select class="role-sel" data-id="${u.id}">${Object.entries(ROLE_NAMES).map(([k, l]) => `<option value="${k}" ${k === u.role ? "selected" : ""}>${l}</option>`).join("")}</select>`,
    presence(u),
    locationCell(u),
    u.is_active ? statusPill("confirmed").replace("confirmed", "active") : '<span class="pill bad">disabled</span>',
    `<button class="btn small toggle-active" data-id="${u.id}" data-active="${u.is_active}">${u.is_active ? "Disable" : "Enable"}</button>`]));
  $$(".role-sel").forEach((s) => s.addEventListener("change", async () => {
    try { await api(`/api/users/${s.dataset.id}`, { method: "PATCH", body: { role: s.value } }); toast("Role updated"); } catch (e) { toast(e.message, true); loadAdmin(); }
  }));
  $$(".toggle-active").forEach((b) => b.addEventListener("click", async () => {
    try { await api(`/api/users/${b.dataset.id}`, { method: "PATCH", body: { is_active: b.dataset.active !== "true" } }); loadAdmin(); } catch (e) { toast(e.message, true); }
  }));
  $("#activity-table").innerHTML = table(["When", "User", "Action", "Target", "Detail", "IP", "Device location"], log.map((a) => [new Date(a.created_at).toLocaleString(), esc(a.user_email || "—"), esc(a.action), esc(a.target || ""), esc(a.detail || ""), ipLink(a.ip), mapLink(a.latitude, a.longitude, a.accuracy_m)]));
}
$("#user-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  try { await api("/api/users", { method: "POST", body: formData(e.target) }); e.target.reset(); toast("User created"); loadAdmin(); } catch (err) { toast(err.message, true); }
});

/* ---------------- Show / hide password ---------------- */
const EYE = '<svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-7 11-7 11 7 11 7-4 7-11 7S1 12 1 12z"/><circle cx="12" cy="12" r="3"/></svg>';
const EYE_OFF = '<svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17.94 17.94A10.07 10.07 0 0 1 12 19c-7 0-11-7-11-7a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 7 11 7a18.5 18.5 0 0 1-2.16 3.19M14.12 14.12a3 3 0 1 1-4.24-4.24"/><line x1="1" y1="1" x2="23" y2="23"/></svg>';

/* Wrap every password field in `root` with an eye button that shows/hides the text. */
function addPasswordToggles(root = document) {
  $$('input[type="password"]', root).forEach((input) => {
    if (input.dataset.toggle) return;
    input.dataset.toggle = "1";
    const wrap = document.createElement("span");
    wrap.className = "pw-wrap";
    input.parentNode.insertBefore(wrap, input);
    wrap.appendChild(input);
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "pw-toggle";
    btn.innerHTML = EYE;
    btn.setAttribute("aria-label", "Show password");
    btn.setAttribute("aria-pressed", "false");
    btn.addEventListener("click", () => {
      const show = input.type === "password";
      input.type = show ? "text" : "password";
      btn.innerHTML = show ? EYE_OFF : EYE;
      btn.setAttribute("aria-label", show ? "Hide password" : "Show password");
      btn.setAttribute("aria-pressed", String(show));
      input.focus();
    });
    wrap.appendChild(btn);
    // Never leave a password visible after the form is submitted or reset.
    input.form?.addEventListener("submit", () => { if (input.type === "text") btn.click(); });
    input.form?.addEventListener("reset", () => { if (input.type === "text") btn.click(); });
  });
}

/* ---------------- Modal ---------------- */
/* ---------------- Research Bank ---------------- */
let researchCats = null;
const highlight = (text, q) => {
  const safe = esc(text || "");
  const terms = (q || "").split(/\s+/).filter((t) => t.length > 1).map((t) => t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  return terms.length ? safe.replace(new RegExp(`(${terms.join("|")})`, "gi"), "<mark>$1</mark>") : safe;
};
const sizeText = (n) => (n > 1048576 ? `${(n / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1024))} KB`);
async function loadResearch() {
  if (!researchCats) {
    researchCats = await api("/api/research/categories");
    const opts = researchCats.categories.map((c) => `<option>${esc(c)}</option>`).join("");
    $("#research-search [name=category]").insertAdjacentHTML("beforeend", opts);
    $("#research-upload [name=category]").innerHTML = '<option value="">Choose for me</option>' + opts;
  }
  $("#research-upload-card").hidden = !can("junior");
  loadDiscussions().catch(() => {});
  const f = formData($("#research-search"));
  const box = $("#research-list");
  box.innerHTML = '<p class="muted">Loading…</p>';
  const res = await api("/api/research?" + qs(f));
  if (!res.items.length) {
    box.innerHTML = `<div class="card"><p class="muted">${f.q || f.category ? "No documents match this search." : "The Research Bank is empty. Add the first document above."}</p></div>`;
    return;
  }
  box.innerHTML = `<p class="muted small">${res.total} document${res.total === 1 ? "" : "s"}${f.q ? ` matching “${esc(f.q)}”` : ""}</p>` +
    res.items.map((d) => `<article class="card research-doc" data-id="${d.id}">
      <div class="row between"><h3>${highlight(d.title, f.q)}</h3><span class="pill">${esc(d.category)}</span></div>
      <p class="muted small">${[d.author, d.year].filter(Boolean).map(esc).join(" · ")}${d.author || d.year ? " · " : ""}${esc(d.file_type.toUpperCase())} · ${sizeText(d.file_size)}${d.words ? ` · ${fmt(d.words)} words` : ""} · added ${esc(new Date(d.created_at).toLocaleDateString())}${d.uploaded_by ? ` by ${esc(d.uploaded_by)}` : ""}</p>
      ${d.summary ? `<p>${highlight(d.summary, f.q)}</p>` : ""}
      ${d.snippet ? `<p class="snippet small">…${highlight(d.snippet, f.q)}</p>` : ""}
      ${(d.tags || []).length ? `<p>${d.tags.map((t) => `<button type="button" class="tag tag-btn" data-tag="${esc(t)}" title="Show documents tagged “${esc(t)}”">${esc(t)}</button>`).join(" ")}</p>` : ""}
      <div class="row gap">${d.has_text ? '<button class="btn small primary rs-discuss">💬 Discuss</button><button class="btn small rs-ask">Quick question</button><button class="btn small rs-read">Read</button>' : ""}
        <button class="btn small rs-dl">Download</button>
        ${can("junior") ? '<button class="btn small rs-edit">Edit details</button><button class="btn small rs-reread" title="Read the file again with the latest reader">↻ Re-read</button>' : ""}
        ${can("senior") ? '<button class="btn small danger rs-del">Delete</button>' : ""}</div>
    </article>`).join("");
  $$(".research-doc", box).forEach((card) => {
    const id = card.dataset.id, d = res.items.find((x) => String(x.id) === id);
    $(".rs-read", card)?.addEventListener("click", () => readResearch(id, f.q));
    $(".rs-ask", card)?.addEventListener("click", () => readResearch(id, f.q, true));
    $(".rs-discuss", card)?.addEventListener("click", () => discussPaper(Number(id)).catch((e) => toast(e.message, true)));
    $(".rs-dl", card).onclick = () => download(`/api/research/${id}/file`, d.filename).catch((e) => toast(e.message, true));
    $$(".tag-btn", card).forEach((b) => (b.onclick = () => {
      $("#research-search [name=q]").value = b.dataset.tag; loadResearch().catch((e) => toast(e.message, true));
    }));
    $(".rs-edit", card)?.addEventListener("click", () => editResearch(d));
    $(".rs-reread", card)?.addEventListener("click", async () => {
      try { await api(`/api/research/${id}/reread`, { method: "POST" }); toast("Read again"); loadResearch(); } catch (e) { toast(e.message, true); }
    });
    $(".rs-del", card)?.addEventListener("click", async () => {
      if (!confirm(`Delete “${d.title}” from the Research Bank?`)) return;
      try { await api(`/api/research/${id}`, { method: "DELETE" }); toast("Document deleted"); loadResearch(); } catch (e) { toast(e.message, true); }
    });
  });
}
/* ---- Discussions: texting with the AI about papers ---- */
let researchIndex = [];
function chatText(s) {   // safe, light formatting for chat messages: **bold**, bullets, line breaks
  let h = esc(s || "").replace(/\*\*(.+?)\*\*/g, "<b>$1</b>");
  const lines = h.split(/\n/);
  return lines.map((line, i) => (/^\s*[-•*]\s+/.test(line)
    ? `<span class="chat-li">• ${line.replace(/^\s*[-•*]\s+/, "")}</span>`
    : line + (i < lines.length - 1 ? "<br>" : ""))).join("");
}
const ago2 = (iso) => { try { return ago(iso); } catch (_) { return new Date(iso).toLocaleString(); } };
async function loadDiscussions() {
  const list = await api("/api/discussions");
  const box = $("#discussion-list");
  if (!box) return;
  box.innerHTML = list.length ? list.map((c) => `<button class="discussion-item" data-id="${c.id}">
      <b>${esc(c.title)}</b>
      <span class="muted small">${c.papers.map((p) => esc(p.title)).join(" · ")}</span>
      <span class="muted small">${c.message_count ? `${c.message_count} messages · ` : ""}${ago2(c.updated_at)}${c.last_message ? ` · “${esc(c.last_message.slice(0, 70))}${c.last_message.length > 70 ? "…" : ""}”` : ""}</span>
    </button>`).join("") : '<p class="muted small">No discussions yet. Tap <b>💬 Discuss</b> on a paper, or <b>+ New discussion</b> to pick several.</p>';
  $$(".discussion-item", box).forEach((b) => (b.onclick = () => openDiscussion(Number(b.dataset.id))));
}
/* Send a discussion message and read the reply as it streams (server-sent events). */
async function streamMessage(id, text, onText) {
  const res = await fetch(`/api/discussions/${id}/messages/stream`, {
    method: "POST", headers: { "Content-Type": "application/json", Authorization: `Bearer ${state.token}` },
    body: JSON.stringify({ message: text }),
  });
  if (res.status === 401) { logout(); throw new Error("Session expired - please sign in again"); }
  if (!res.ok || !res.body) {
    let msg = res.statusText;
    try { const j = await res.json(); msg = typeof j.detail === "string" ? j.detail : msg; } catch (_) {}
    throw new Error(msg || "Could not send the message");
  }
  const reader = res.body.getReader(), dec = new TextDecoder();
  let buf = "", soFar = "", done = null;
  for (;;) {
    const { value, done: end } = await reader.read();
    if (end) break;
    buf += dec.decode(value, { stream: true });
    let cut;
    while ((cut = buf.indexOf("\n\n")) >= 0) {
      const event = buf.slice(0, cut); buf = buf.slice(cut + 2);
      const line = event.split("\n").find((l) => l.startsWith("data: "));
      if (!line) continue;
      const msg = JSON.parse(line.slice(6));
      if (msg.type === "chunk") { soFar += msg.text; onText(soFar); }
      else if (msg.type === "done") done = msg;
    }
  }
  if (!done) throw new Error("The reply was interrupted; please send it again");
  return done;
}
async function discussPaper(paperId) {
  const mine = await api("/api/discussions");
  const existing = mine.find((c) => c.papers.length === 1 && c.papers[0].id === paperId);
  if (existing) return openDiscussion(existing.id);
  const chat = await api("/api/discussions", { method: "POST", body: { paper_ids: [paperId] } });
  openDiscussion(chat.id);
}
async function newDiscussion() {
  const res = await api("/api/research?limit=500");
  researchIndex = res.items;
  if (!res.items.length) { toast("Add a document to the Research Bank first", true); return; }
  openModal(`<div class="row between"><h3>New discussion</h3><button class="btn" onclick="closeModal()">Close</button></div>
    <p class="muted small">Pick up to 5 papers to discuss together.</p>
    <form id="nd-form"><div class="pick-list">${res.items.filter((d) => d.has_text).map((d) => `<label class="pick"><input type="checkbox" name="p" value="${d.id}"> <span><b>${esc(d.title)}</b> <span class="muted small">${esc(d.category)}</span></span></label>`).join("")}</div>
    <div class="row gap"><button class="btn primary">Start discussion</button></div></form>`);
  $("#nd-form").onsubmit = async (e) => {
    e.preventDefault();
    const ids = $$("#nd-form input:checked").map((x) => Number(x.value));
    if (!ids.length) { toast("Pick at least one paper", true); return; }
    if (ids.length > 5) { toast("Pick up to 5 papers", true); return; }
    try { const chat = await api("/api/discussions", { method: "POST", body: { paper_ids: ids } }); openDiscussion(chat.id); }
    catch (err) { toast(err.message, true); }
  };
}
async function openDiscussion(id) {
  let chat = await api(`/api/discussions/${id}`);
  if (!researchIndex.length) researchIndex = (await api("/api/research?limit=500")).items;
  openModal(`<div class="chat">
    <div class="chat-head"><div><h3 class="chat-title">${esc(chat.title)}</h3>
      <div class="chat-papers"></div></div>
      <div class="row gap"><button class="btn small" id="chat-del" title="Delete this discussion">🗑</button><button class="btn" onclick="closeModal()">Close</button></div></div>
    <div class="chat-log" aria-live="polite"></div>
    <form class="chat-send"><textarea name="m" rows="1" placeholder="Message…" aria-label="Message about the paper${chat.papers.length === 1 ? "" : "s"}" maxlength="4000"></textarea><button class="btn primary">Send</button></form>
  </div>`);
  const log = $(".chat-log"), input = $(".chat-send textarea");
  const papersBar = () => {
    const others = researchIndex.filter((d) => d.has_text && !chat.papers.some((p) => p.id === d.id));
    $(".chat-papers").innerHTML = chat.papers.map((p) => `<span class="tag paper-chip">📄 ${esc(p.title)}${chat.papers.length > 1 ? ` <button type="button" class="chip-x" data-id="${p.id}" title="Remove from discussion">✕</button>` : ""}</span>`).join(" ")
      + (chat.papers.length < 5 && others.length ? ` <select class="add-paper"><option value="">+ Add a paper…</option>${others.map((d) => `<option value="${d.id}">${esc(d.title)}</option>`).join("")}</select>` : "");
    const setPapers = async (ids) => { try { chat = { ...chat, ...(await api(`/api/discussions/${id}`, { method: "PATCH", body: { paper_ids: ids } })) }; papersBar(); } catch (e) { toast(e.message, true); } };
    $$(".chip-x").forEach((b) => (b.onclick = () => setPapers(chat.papers.map((p) => p.id).filter((x) => x !== Number(b.dataset.id)))));
    const add = $(".add-paper"); if (add) add.onchange = () => add.value && setPapers([...chat.papers.map((p) => p.id), Number(add.value)]);
  };
  const bubble = (m) => m.role === "user"
    ? `<div class="msg me"><div class="bubble">${chatText(m.content)}</div><span class="msg-at">${m.at ? new Date(m.at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : ""}</span></div>`
    : `<div class="msg ai"><div class="bubble">${m.typing || (m.streaming && !m.content) ? '<span class="typing"><i></i><i></i><i></i></span>' : chatText(m.content) + (m.streaming ? '<span class="cursor">▍</span>' : "")}
        ${(m.quotes || []).map((q) => `<blockquote class="chat-quote" data-paper="${q.paper_id}" data-q="${esc(q.quote.slice(0, 80))}">“${esc(q.quote)}”<span>— ${esc(q.paper)}</span></blockquote>`).join("")}
        ${m.warning ? `<p class="muted small">⚠ ${esc(m.warning)}</p>` : ""}</div>
        <span class="msg-at">${m.typing || m.streaming ? "PIS Research is typing…" : `PIS Research${m.method === "search" ? " (search only)" : ""}${m.at ? " · " + new Date(m.at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : ""}`}</span></div>`;
  const render = (extra = []) => {
    const msgs = [...(chat.messages || []), ...extra];
    log.innerHTML = msgs.length ? msgs.map(bubble).join("")
      : `<div class="chat-empty">👋 Ask anything about ${chat.papers.length === 1 ? "this paper" : "these papers"}, e.g. <i>“Summarise the key points”</i>, <i>“What does it say about construction cost?”</i>${chat.papers.length > 1 ? ", <i>“How do these papers differ?”</i>" : ""}</div>`;
    $$(".chat-quote", log).forEach((bq) => (bq.onclick = () => readResearch(bq.dataset.paper, bq.dataset.q.split(/\s+/).slice(0, 4).join(" "), false, () => openDiscussion(id))));
    log.scrollTop = log.scrollHeight;
  };
  papersBar(); render();
  const send = async () => {
    const text = input.value.trim();
    if (!text || input.disabled) return;
    input.value = ""; input.style.height = "";
    const mine = { role: "user", content: text, at: new Date().toISOString() };
    render([mine, { role: "assistant", typing: true }]);
    input.disabled = true;
    try {
      const res = await streamMessage(id, text, (soFar) => {
        // show the reply as it is written (the quotes list, after "QUOTES:", is shown once it is checked)
        const visible = soFar.split(/^\s*\**quotes:?\**\s*$/im)[0];
        render([mine, { role: "assistant", content: visible, streaming: true }]);
      });
      chat.messages = [...(chat.messages || []), res.message, res.reply];
      if (res.title) { chat.title = res.title; $(".chat-title").textContent = res.title; }
      render();
    } catch (e) { render(); input.value = text; toast(e.message, true); }
    finally { input.disabled = false; input.focus(); loadDiscussions().catch(() => {}); }
  };
  $(".chat-send").onsubmit = (e) => { e.preventDefault(); send(); };
  input.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } });
  input.addEventListener("input", () => { input.style.height = ""; input.style.height = Math.min(input.scrollHeight, 140) + "px"; });
  $("#chat-del").onclick = async () => {
    if (!confirm("Delete this discussion?")) return;
    try { await api(`/api/discussions/${id}`, { method: "DELETE" }); closeModal(); loadDiscussions(); } catch (e) { toast(e.message, true); }
  };
  input.focus();
}
$("#new-discussion").addEventListener("click", () => newDiscussion().catch((e) => toast(e.message, true)));

const researchChats = {};   // questions asked per document, kept while the page is open
/* PDF pages drawn with the bundled PDF.js, so the original looks the same on phones and computers. */
async function loadPdfJs() {
  if (window.pdfjsLib) return window.pdfjsLib;
  await new Promise((ok, bad) => {
    const s = document.createElement("script");
    s.src = `/static/vendor/pdfjs/pdf.min.js?v=${PAGE_VERSION || ""}`; s.onload = ok; s.onerror = bad;
    document.head.appendChild(s);
  });
  window.pdfjsLib.GlobalWorkerOptions.workerSrc = `/static/vendor/pdfjs/pdf.worker.min.js?v=${PAGE_VERSION || ""}`;
  return window.pdfjsLib;
}
async function renderPdf(box, buf) {
  const lib = await loadPdfJs();
  const pdf = await lib.getDocument({ data: buf }).promise;
  box.innerHTML = `<p class="muted small pdf-count">${pdf.numPages} page${pdf.numPages === 1 ? "" : "s"}</p>`;
  for (let n = 1; n <= pdf.numPages; n++) {
    if (!document.body.contains(box)) return;            // reader closed
    const page = await pdf.getPage(n);
    const width = Math.max(280, box.clientWidth - 8);
    const scale = width / page.getViewport({ scale: 1 }).width;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const vp = page.getViewport({ scale: scale * dpr });
    const canvas = document.createElement("canvas");
    canvas.width = vp.width; canvas.height = vp.height; canvas.className = "pdf-page";
    canvas.setAttribute("aria-label", `Page ${n}`);
    box.appendChild(canvas);
    await page.render({ canvasContext: canvas.getContext("2d"), viewport: vp }).promise;
  }
}
async function showOriginal(box, id, d) {
  try {
    const v = await api(`/api/research/${id}/view`);
    if (v.kind === "html") { box.innerHTML = `<div class="doc-html">${v.html}</div>`; return; }
    if (v.kind === "text") { box.innerHTML = `<pre class="doc-pre">${esc(d.text || "")}</pre>`; return; }
    const res = await api(`/api/research/${id}/file?inline=true`, { raw: true });
    if (v.kind === "image") { box.innerHTML = `<img class="doc-img" alt="${esc(d.title)}" src="${URL.createObjectURL(await res.blob())}">`; return; }
    box.innerHTML = '<p class="muted">Opening the PDF…</p>';
    await renderPdf(box, await res.arrayBuffer());
  } catch (e) {
    box.innerHTML = `<p class="muted">The original can't be shown here (${esc(e.message || "error")}). Use <b>Download original</b> or the <b>Text</b> tab.</p>`;
  }
}
async function readResearch(id, q, focusAsk = false, back = null) {
  const d = await api(`/api/research/${id}?${qs({ q })}`);
  const body = (d.text || "").split(/\n+/).map((line) => line.trim()).filter(Boolean)
    .map((line) => (/^##\s/.test(line) ? `<h4>${highlight(line.replace(/^##\s*/, ""), q)}</h4>` : `<p>${highlight(line, q)}</p>`)).join("");
  openModal(`<div class="row between"><h3>${esc(d.title)}</h3><div class="row gap">${back ? '<button class="btn small primary" id="rs-back">← Back to discussion</button>' : ""}<button class="btn small" id="rs-dl2">Download original</button><button class="btn" onclick="closeModal()">Close</button></div></div>
    <p class="muted small">${esc(d.category)}${d.author ? ` · ${esc(d.author)}` : ""}${d.year ? ` · ${esc(d.year)}` : ""} · ${esc(d.filename)}</p>
    <section class="ask-box">
      <form class="row gap rs-ask-form"><input name="question" maxlength="1000" placeholder="💬 Ask this document, e.g. What are the success metrics?" autocomplete="off"><button class="btn small primary">Ask</button></form>
      <div class="ask-log"></div>
    </section>
    <div class="view-tabs" role="tablist"><button type="button" class="tab-btn" data-v="orig">📄 Original</button><button type="button" class="tab-btn" data-v="text">Text (search)</button></div>
    <div class="view-pane" data-v="orig"><div class="orig-view"><p class="muted">Loading…</p></div></div>
    <div class="view-pane" data-v="text" hidden>
      <form class="row gap rs-find"><input name="q" value="${esc(q || "")}" placeholder="Find in this document"><button class="btn small">Find</button></form>
      <div class="reader">${body || '<p class="muted">No readable text in this file. Download it to view.</p>'}</div></div>`);
  const setView = (v) => {
    $$(".view-tabs .tab-btn").forEach((b) => b.classList.toggle("active", b.dataset.v === v));
    $$(".view-pane").forEach((p) => (p.hidden = p.dataset.v !== v));
  };
  $$(".view-tabs .tab-btn").forEach((b) => (b.onclick = () => setView(b.dataset.v)));
  setView(q ? "text" : "orig");             // the original form by default; the text view when searching
  showOriginal($(".orig-view"), id, d);
  $("#rs-dl2").onclick = () => download(`/api/research/${id}/file`, d.filename).catch((e) => toast(e.message, true));
  if (back) $("#rs-back").onclick = back;
  $(".rs-find").onsubmit = (e) => { e.preventDefault(); readResearch(id, new FormData(e.target).get("q"), false, back); };
  const log = $(".ask-log");
  const chat = (researchChats[id] = researchChats[id] || []);
  const showInDoc = (quote) => {
    setView("text");
    const norm = (x) => x.replace(/\s+/g, " ").trim().toLowerCase();
    const paras = $$(".reader p, .reader h4");
    // a quote may join several lines ("Heading: line; line"): find the first piece that is in the text
    const pieces = [quote, ...quote.split(/[:;]\s+/)].map((x) => norm(x).slice(0, 60)).filter((x) => x.length >= 6);
    let hit = null;
    for (const want of pieces) { hit = paras.find((p) => norm(p.textContent).includes(want)); if (hit) break; }
    if (hit) { $$(".reader .quoted").forEach((x) => x.classList.remove("quoted")); hit.classList.add("quoted"); hit.scrollIntoView({ block: "center", behavior: "smooth" }); }
  };
  const render = () => {
    log.innerHTML = chat.map((m, i) => `<div class="ask-q">🙋 ${esc(m.question)}</div>
      <div class="ask-a ${m.found === false ? "not-found" : ""}">${m.pending ? '<span class="muted">Reading the document…</span>' : `
        <p>${esc(m.answer).replace(/\n/g, "<br>")}</p>
        ${(m.quotes || []).map((x, j) => `<blockquote data-i="${i}" data-j="${j}" title="Show in the document">“${esc(x)}”</blockquote>`).join("")}
        <p class="muted small">${m.method === "ai" ? "Answered by the AI from this document only" : "Built-in search (no AI)"}${m.warning ? ` · ${esc(m.warning)}` : ""}</p>`}</div>`).join("");
    $$("blockquote", log).forEach((bq) => (bq.onclick = () => showInDoc(chat[bq.dataset.i].quotes[bq.dataset.j])));
  };
  render();
  $(".rs-ask-form").onsubmit = async (e) => {
    e.preventDefault();
    const input = $("[name=question]", e.target), question = input.value.trim();
    if (question.length < 3) return;
    const entry = { question, pending: true };
    chat.push(entry); render(); input.value = "";
    try { Object.assign(entry, await api(`/api/research/${id}/ask`, { method: "POST", body: { question } }), { pending: false }); }
    catch (err) { Object.assign(entry, { pending: false, found: false, answer: err.message, quotes: [], method: "error" }); }
    render();
    log.lastElementChild?.scrollIntoView({ block: "nearest" });
  };
  if (focusAsk) $(".rs-ask-form [name=question]").focus();
  $(".reader mark")?.scrollIntoView({ block: "center" });
}
function editResearch(d) {
  const opts = (researchCats?.categories || []).map((c) => `<option ${c === d.category ? "selected" : ""}>${esc(c)}</option>`).join("");
  openModal(`<div class="row between"><h3>Edit document details</h3><button class="btn" onclick="closeModal()">Close</button></div>
    <form id="rs-edit-form" class="filters">
      <label class="wide">Title<input name="title" value="${esc(d.title)}" required></label>
      <label>Category<select name="category">${opts}</select></label>
      <label>Author / source<input name="author" value="${esc(d.author || "")}"></label>
      <label>Year<input name="year" value="${esc(d.year || "")}"></label>
      <label class="wide">Tags (comma separated)<input name="tags" value="${esc((d.tags || []).join(", "))}"></label>
      <label class="wide">Summary<textarea name="summary" rows="4">${esc(d.summary || "")}</textarea></label>
      <div class="filter-actions"><button class="btn primary">Save</button></div></form>`);
  $("#rs-edit-form").onsubmit = async (e) => {
    e.preventDefault();
    const body = Object.fromEntries(new FormData(e.target).entries());
    try { await api(`/api/research/${d.id}`, { method: "PATCH", body }); closeModal(); toast("Saved"); loadResearch(); } catch (err) { toast(err.message, true); }
  };
}
$("#research-search").addEventListener("submit", (e) => { e.preventDefault(); loadResearch().catch((err) => toast(err.message, true)); });
$("#research-search [name=category]").addEventListener("change", () => loadResearch().catch((err) => toast(err.message, true)));
$("#research-upload").addEventListener("submit", async (e) => {
  e.preventDefault();
  const fd = new FormData(e.target);
  try {
    const d = await api("/api/research", { method: "POST", body: fd });
    toast(`Added “${d.title}”`); e.target.reset(); $("#research-upload-card").open = false; loadResearch();
  } catch (err) { toast(err.message, true); }
});

function openModal(html) { $("#modal-body").innerHTML = html; addPasswordToggles($("#modal-body")); $("#modal").showModal(); }
function closeModal() { $("#modal").close(); }
$("#modal").addEventListener("close", () => { $("#modal-body").innerHTML = ""; });
window.closeModal = closeModal;

addPasswordToggles();
boot();
