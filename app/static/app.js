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
async function api(path, opts = {}) {
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
const formData = (form) => Object.fromEntries([...new FormData(form).entries()].filter(([, v]) => v !== ""));

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
  try {
    const d = await api("/api/auth/login", { method: "POST", body: formData(e.target) });
    saveSession(d.access_token, d.user);
    e.target.reset();
    startApp();
  } catch (err) { $("#login-error").textContent = err.message; }
});
$("#logout-btn").addEventListener("click", logout);
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
  const bar = document.createElement("div");
  bar.id = "update-bar";
  bar.className = "update-bar";
  bar.innerHTML = 'PIS has been updated. <button class="btn small primary" type="button">Reload now</button>';
  bar.querySelector("button").onclick = () => location.replace(`${location.pathname}?fresh=${Date.now()}${location.hash}`);
  document.body.appendChild(bar);
}
document.addEventListener("visibilitychange", async () => {
  if (document.visibilityState !== "visible") return;
  try { checkForUpdate(await (await fetch("/api/health", { cache: "no-store" })).json()); } catch (_) { /* offline */ }
});

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
    $("#upload-hint").textContent = `PDF, Word (.docx), text or scanned images — single or bulk · up to ${state.health.max_upload_mb} MB per file`;
  }
  loadFacets();
  route();
}

/* ---------------- Routing ---------------- */
const loaders = { dashboard: loadDashboard, ingest: loadReports, records: loadRecords, intel: loadIntel, comps: () => {}, admin: loadAdmin };
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
    <label>Purpose<input name="purpose"></label>
    <label>From<input type="date" name="date_from"></label>
    <label>To<input type="date" name="date_to"></label>
    ${withSearch ? '<label>Min value/cost<input type="number" step="any" name="min_amount"></label><label>Max value/cost<input type="number" step="any" name="max_amount"></label><label>Tag<input name="tag"></label>' : ""}
    <div class="filter-actions"><button class="btn primary" type="submit">Apply</button><button class="btn" type="reset">Clear</button></div>`;
}

/* ---------------- Dashboard ---------------- */
async function loadDashboard() {
  const [s, reports, t] = await Promise.all([
    api("/api/analytics/summary"),
    api("/api/reports?limit=8"),
    api("/api/analytics/trends?metric=value_per_sqm&interval=quarter"),
  ]);
  const cur = s.currency || (state.health && state.health.default_currency) || "";
  const m = s.metrics;
  const pending = reports.filter((r) => r.status === "extracted").length;
  $("#kpis").innerHTML = [
    kpi("Records in database", fmt(s.record_count), Object.entries(s.by_category).map(([k, v]) => `${v} ${k}`).join(" · ") || "No data yet"),
    kpi("Average value opinion", m.capital_value ? `${cur} ${fmt(m.capital_value.mean)}` : "—", m.capital_value ? `market value · ${m.capital_value.count} valuation${m.capital_value.count === 1 ? "" : "s"}` : "No valuations yet"),
    kpi("Average value opinion / m²", m.value_per_sqm ? `${cur} ${fmt(m.value_per_sqm.mean)}` : "—", m.value_per_sqm ? `${m.value_per_sqm.count} with floor area` : "needs floor area"),
    ...(m.annual_rent ? [kpi("Average rental opinion", `${cur} ${fmt(m.annual_rent.mean)}`, `per year · ${m.annual_rent.count} valuation${m.annual_rent.count === 1 ? "" : "s"}`)] : []),
    kpi("Avg construction cost / m²", m.cost_per_sqm ? `${cur} ${fmt(m.cost_per_sqm.mean)}` : "—", m.cost_per_sqm ? `${m.cost_per_sqm.count} records` : ""),
    kpi("Avg yield", m.yield_pct ? `${fmt(m.yield_pct.mean, 2)}%` : "—", m.yield_pct ? `${m.yield_pct.count} records` : ""),
    kpi("Reports awaiting review", fmt(pending), "in the latest uploads"),
  ].join("");
  const locs = Object.entries(s.top_locations);
  $("#dash-locations").innerHTML = locs.length ? table(["Location", "Records"], locs.map(([k, v]) => [esc(k), { v: fmt(v), num: true }])) : '<p class="muted">No records yet.</p>';
  $("#dash-reports").innerHTML = reports.length ? table(["Report", "Type", "Status"], reports.map((r) => [esc(r.title || r.filename), esc(r.report_type || "—"), statusPill(r.status)])) : '<p class="muted">No reports uploaded yet.</p>';
  lineChart("dash-chart", t.series.map((p) => p.period), [{ label: `Value opinion / m² (${t.currency || cur})`, data: t.series.map((p) => p.mean) }]);
  showSuspects($("#dash-warning"), s.suspect_values, cur);
}
/* Valuations whose figure looks mis-read (e.g. 2,300 for 2,300,000): listed, kept out of averages. */
function showSuspects(box, list, cur) {
  if (!box) return;
  if (!list || !list.length) { box.hidden = true; box.innerHTML = ""; return; }
  box.hidden = false;
  box.innerHTML = `<b>⚠ ${list.length} valuation${list.length === 1 ? "" : "s"} with a value that looks too low</b> — left out of the averages until corrected:
    <ul>${list.map((x) => `<li>Ref ${x.id} ${esc(x.label)}: value opinion ${esc(cur)} ${fmt(x.capital_value)}${x.value_per_sqm != null ? ` (${esc(cur)} ${fmt(x.value_per_sqm, 2)} per m²)` : ""}
      <button class="btn small fix-suspect" data-id="${x.id}">Open &amp; correct</button></li>`).join("")}</ul>`;
  $$(".fix-suspect", box).forEach((b) => (b.onclick = async () => {
    try { const rec = await api(`/api/records/${b.dataset.id}`); state.lastTab = "valuation"; openRecord(rec); } catch (e) { toast(e.message, true); }
  }));
}
function checkValueOpinion(el) {
  const v = Number($("[name=capital_value]", el)?.value || 0), size = Number($("[name=size_sqm]", el)?.value || 0);
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
const PALETTE = ["#0f4c5c", "#c9a227", "#5b8e7d", "#a4508b"];
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
const RATING = ["Excellent", "Very good", "Good", "Fair", "Poor"];
const BASES = ["Market Value", "Market Rent", "Forced Sale Value", "Fair Value", "Investment Value",
  "Reinstatement Cost", "Depreciated Replacement Cost", "Mortgage Lending Value"];
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
  repair: ["Excellent", "Good", "Fair", "Poor", "Dilapidated"],
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
    [["currency", "Currency"], ["annual_rent", "Market rent (annual)", "num"], ["yield_pct", "Yield %", "num"],
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
    ["currency", "Currency"], ["total_cost", "Total cost", "num"], ["cost_per_sqm", "Cost / m²", "num"],
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

function fieldHTML([key, label, kind], rec) {
  const v = rec[key] ?? "";
  if (kind === "text") return `<label class="wide">${esc(label)}<textarea name="${key}" rows="2">${esc(v)}</textarea></label>`;
  if (kind === "mmda") {
    return `<label>${esc(label)} <span class="muted small">(${MMDAS.length})</span><select name="${key}" class="mmda-select">${mmdaOptions(v)}</select></label>`;
  }
  if (SELECTS[kind]) {
    const val = kind === "basis" ? basisCanonical(v) : v;
    const opts = SELECTS[kind].includes(val) || !val ? SELECTS[kind] : [val, ...SELECTS[kind]];
    return `<label>${esc(label)}<select name="${key}"><option value=""></option>${opts.map((o) => `<option ${o === val ? "selected" : ""}>${esc(o)}</option>`).join("")}</select></label>`;
  }
  if (LISTS[kind] || HINTS[kind]) {
    const list = LISTS[kind] ? ` list="${LISTS[kind]}"` : "";
    const hint = HINTS[kind] ? ` placeholder="${HINTS[kind]}"` : "";
    const extra = kind === "gps" ? ' autocapitalize="characters" spellcheck="false"' : "";
    return `<label>${esc(label)}<input type="text" name="${key}" value="${esc(v)}"${list}${hint}${extra}></label>`;
  }
  const type = kind === "num" || kind === "int" ? "number" : kind === "date" ? "date" : "text";
  const step = kind === "num" ? ' step="any"' : "";
  return `<label class="${kind === "wide" ? "wide" : ""}">${esc(label)}<input type="${type}"${step} name="${key}" value="${esc(v)}" data-kind="${kind || ""}"></label>`;
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
    if (key === "valuation") {
      html += comparablesEditorHTML(comps);
    }
    if (key === "cost") {
      html += `<p class="sub-head">Elemental cost breakdown</p><div class="form-grid">${BREAKDOWN_KEYS.map((k) => `<label>${k.replace("_", " ")}<input type="number" step="any" name="bd_${k}" value="${esc(bd[k] ?? "")}"></label>`).join("")}</div>`;
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

/* Comparables: an editable table (one row per comparable). */
const COMP_FIELDS = [["address", "Address / location"], ["property_type", "Type"], ["date", "Date"],
  ["price", "Price", "num"], ["rent", "Rent", "num"], ["size_sqm", "Size m²", "num"], ["price_per_sqm", "Price / m²", "num"],
  ["notes", "Remarks"]];
let compRowSeq = 0;
function compRowHTML(c = {}) {
  const i = compRowSeq++;
  return `<tr data-row="${i}">${COMP_FIELDS.map(([k, , kind]) => `<td><input name="cmp_${i}_${k}" value="${esc(c[k] ?? "")}"${kind === "num" ? ' type="number" step="any" data-kind="num"' : ""} aria-label="${k}"></td>`).join("")}
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
  markAutofilled(el, rec.autofilled);
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
    const cm = name.match(/^cmp_(\d+)_(\w+)$/);
    if (cm) {
      if (v === "") return;
      comps[cm[1]] = comps[cm[1]] || {};
      comps[cm[1]][cm[2]] = inp.dataset.kind === "num" ? Number(v) : v;
      return;
    }
    if (name.startsWith("bd_")) {
      if (v !== "") { rec.cost_breakdown = rec.cost_breakdown || {}; rec.cost_breakdown[name.slice(3)] = Number(v); }
      return;
    }
    if (name === "tags") { rec.tags = v ? v.split(",").map((t) => t.trim()).filter(Boolean) : null; return; }
    if (v === "") { rec[name] = null; return; }
    if (inp.dataset.kind === "num") v = Number(v);
    if (inp.dataset.kind === "int") v = parseInt(v, 10);
    rec[name] = v;
  });
  if (hasCompEditor) {
    const list = Object.keys(comps).sort((a, b) => a - b).map((k) => comps[k]);
    list.forEach((c) => { if (c.price && c.size_sqm && !c.price_per_sqm) c.price_per_sqm = Math.round((c.price / c.size_sqm) * 100) / 100; });
    rec.comparables = list.length ? list : null;
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
         Fields marked <span class="changed-chip">changed</span> differ from the saved record; the saved value is shown under each one.
         Nothing changes until you tap <b>Update saved records</b>.</div>`
      : approved ? `<p class="muted small">Approved ${r.confirmed_at ? esc(new Date(r.confirmed_at).toLocaleString()) : ""}. Showing the saved record${saved.length === 1 ? "" : "s"}.
         Tap <b>Re-run extraction</b> to read the report again (e.g. with Gemini) and update it.</p>` : "";
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

/* Mark every field whose new value differs from the saved record, showing the saved value. */
function markChanges(panel, saved) {
  const norm = (v) => {
    if (v === null || v === undefined) return "";
    if (Array.isArray(v)) return v.map((x) => String(x).trim().toLowerCase()).filter(Boolean).sort().join(",");
    const n = Number(v);
    if (v !== "" && !Number.isNaN(n) && /^-?[\d.,\s]+$/.test(String(v))) return String(Math.round(n * 100) / 100);
    return String(v).trim().toLowerCase();
  };
  let changed = 0;
  $$(".review-record", panel).forEach((el) => {
    const old = saved[Number(el.dataset.idx)];
    if (!old) { el.insertAdjacentHTML("afterbegin", '<p class="was-hint">New record: not in the saved data</p>'); changed++; return; }
    $$("input[name], select[name], textarea[name]", el).forEach((inp) => {
      const name = inp.name;
      if (name.startsWith("bd_") || name.startsWith("cmp_")) return;
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
  if (!changed) panel.querySelector(".notice")?.insertAdjacentHTML("beforeend", "<br><b>No differences from the saved record.</b>");
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
  const editable = can("junior");
  openModal(`<div class="row between"><h3>Record #${rec && rec.id ? rec.id : "new"}</h3><button class="btn" onclick="closeModal()">Close</button></div>
    ${rec && rec.report_id ? `<p class="muted small">Source report #${rec.report_id}</p>` : ""}
    <form id="rec-form">${recordEditorHTML(rec || { category: "valuation", currency: (state.health && state.health.default_currency) || "GHS" }, 0)}</form>
    <div class="row gap">${editable ? '<button class="btn primary" id="rec-save">Save</button>' : ""}${rec && rec.id && can("senior") ? '<button class="btn danger" id="rec-del">Delete</button>' : ""}</div>`);
  const el = $("#rec-form .review-record");
  $(".rm-record", el).remove();
  initRecordEditor(el, rec || {});
  if (editable) {
    $(".row.gap", el).insertAdjacentHTML("afterbegin", '<button type="button" class="btn small fill-gaps">✨ Fill gaps</button>');
    $(".fill-gaps", el).onclick = (ev) => fillGaps(el, rec || {}, rec && rec.report_id, ev.target);
  }
  if (!editable) $$("#rec-form input, #rec-form select, #rec-form textarea").forEach((x) => (x.disabled = true));
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
const GAP_SKIP = /^(cmp_|bd_)/;
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
  if (field === "comparables") {
    const tbody = $(".comp-table tbody", el);
    if (!tbody || !Array.isArray(value)) return false;
    value.forEach((c) => tbody.insertAdjacentHTML("beforeend", compRowHTML(c)));
    initCompRows(el);
    return true;
  }
  if (field === "cost_breakdown" && value && typeof value === "object") {
    let any = false;
    Object.entries(value).forEach(([k, v]) => { const c = $(`[name="bd_${k}"]`, el); if (c && isEmpty(c)) { c.value = v; any = true; } });
    return any;
  }
  const c = $(`[name="${field}"]`, el);
  if (!c) return false;
  let v = Array.isArray(value) ? value.join(", ") : value ?? "";
  if (c.tagName === "SELECT") {
    if (field === "basis_of_value") v = basisCanonical(v);
    if (![...c.options].some((o) => o.value === String(v))) c.insertAdjacentHTML("beforeend", `<option value="${esc(v)}">${esc(v)}</option>`);
  }
  c.value = v;
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
  ["total_cost", "Total cost", "num"], ["cost_per_sqm", "Cost / m²", "num"], ["avg_price", "Avg price", "num"],
  ["avg_monthly_rent", "Avg rent / month", "num"], ["currency", "Currency"], ["tags", "Tags", "tags"], ["notes", "Notes"],
];
state.sheet = { on: false, items: [], dirty: {} };

function sheetCell(rec, [key, , kind]) {
  let v = rec[key];
  if (kind === "tags") v = (v || []).join(", ");
  v = v ?? "";
  const ro = can("junior") ? "" : " disabled";
  const common = `data-id="${rec.id}" data-key="${key}" data-kind="${kind || ""}"${ro}`;
  if (kind === "mmda") return `<select ${common}>${mmdaOptions(v, "")}</select>`;
  if (kind === "basis") { const b = basisCanonical(v); return `<select ${common}><option value=""></option>${(BASES.includes(b) || !b ? BASES : [b, ...BASES]).map((o) => `<option ${o === b ? "selected" : ""}>${esc(o)}</option>`).join("")}</select>`; }
  if (kind === "cat") return `<select ${common}>${["valuation", "cost", "housing"].map((c) => `<option ${c === v ? "selected" : ""}>${c}</option>`).join("")}</select>`;
  const type = kind === "num" ? "number" : kind === "date" ? "date" : "text";
  const list = kind && kind.startsWith("list:") ? ` list="${kind.slice(5)}"` : "";
  return `<input type="${type}"${type === "number" ? ' step="any"' : ""}${list} value="${esc(v)}" ${common}>`;
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
    if (el.dataset.kind === "num") val = val === "" ? null : Number(val);
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
const METRIC_OPTIONS = [["value_per_sqm", "Value opinion / m²"], ["capital_value", "Value opinion"], ["annual_rent", "Rental opinion (annual)"], ["rent_per_sqm", "Rental opinion / m²"],
  ["yield_pct", "Yield %"], ["cost_per_sqm", "Construction cost / m²"], ["avg_price", "Avg house price"], ["avg_monthly_rent", "Avg monthly rent"], ["affordability_ratio", "Affordability ratio"]];
$("#intel-filters").innerHTML = filterFormHTML(false);
$("#trend-metric").innerHTML = METRIC_OPTIONS.map(([v, l]) => `<option value="${v}">${l}</option>`).join("");
$("#intel-filters").addEventListener("submit", (e) => { e.preventDefault(); loadIntel(); });
$("#intel-filters").addEventListener("reset", () => setTimeout(loadIntel));
["#trend-metric", "#trend-interval"].forEach((s) => $(s).addEventListener("change", loadTrend));
$("#group-by").addEventListener("change", loadBreakdown);

const intelFilters = () => formData($("#intel-filters"));

async function loadIntel() {
  const f = intelFilters();
  const s = await api("/api/analytics/summary?" + qs(f));
  const cur = s.currency || "";
  showSuspects($("#intel-warning"), s.suspect_values, s.currency || "");
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
    { label: `Value opinion / m² (${c.currency || ""})`, data: c.rows.map((r) => r.value_per_sqm) },
    { label: "Construction cost / m²", data: c.rows.map((r) => r.cost_per_sqm) },
  ], { type: "bar" });
}
async function loadBreakdown() {
  const b = await api("/api/analytics/breakdown?" + qs({ ...intelFilters(), group_by: $("#group-by").value }));
  const groups = $("#group-by").value.split(",");
  if (!b.rows.length) { $("#breakdown-table").innerHTML = '<p class="muted">No data.</p>'; return; }
  const cols = [["value_per_sqm", "Value opinion/m²"], ["capital_value", "Value opinion"], ["annual_rent", "Rental opinion"], ["yield_pct", "Yield %"], ["cost_per_sqm", "Cost/m²"], ["avg_price", "Avg price"], ["avg_monthly_rent", "Avg rent/mo"]]
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
async function loadAdmin() {
  const [users, log] = await Promise.all([api("/api/users"), api("/api/activity?limit=300")]);
  $("#users-table").innerHTML = table(["Name", "Email", "Role", "Active", ""], users.map((u) => [esc(u.full_name), esc(u.email),
    `<select class="role-sel" data-id="${u.id}">${Object.entries(ROLE_NAMES).map(([k, l]) => `<option value="${k}" ${k === u.role ? "selected" : ""}>${l}</option>`).join("")}</select>`,
    u.is_active ? statusPill("confirmed").replace("confirmed", "active") : '<span class="pill bad">disabled</span>',
    `<button class="btn small toggle-active" data-id="${u.id}" data-active="${u.is_active}">${u.is_active ? "Disable" : "Enable"}</button>`]));
  $$(".role-sel").forEach((s) => s.addEventListener("change", async () => {
    try { await api(`/api/users/${s.dataset.id}`, { method: "PATCH", body: { role: s.value } }); toast("Role updated"); } catch (e) { toast(e.message, true); loadAdmin(); }
  }));
  $$(".toggle-active").forEach((b) => b.addEventListener("click", async () => {
    try { await api(`/api/users/${b.dataset.id}`, { method: "PATCH", body: { is_active: b.dataset.active !== "true" } }); loadAdmin(); } catch (e) { toast(e.message, true); }
  }));
  $("#activity-table").innerHTML = table(["When", "User", "Action", "Target", "Detail", "IP"], log.map((a) => [new Date(a.created_at).toLocaleString(), esc(a.user_email || "—"), esc(a.action), esc(a.target || ""), esc(a.detail || ""), esc(a.ip || "")]));
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
function openModal(html) { $("#modal-body").innerHTML = html; addPasswordToggles($("#modal-body")); $("#modal").showModal(); }
function closeModal() { $("#modal").close(); }
$("#modal").addEventListener("close", () => { $("#modal-body").innerHTML = ""; });
window.closeModal = closeModal;

addPasswordToggles();
boot();
