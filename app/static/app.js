/* Pinnacle Intelligence System - single-page front end (no build step). */
"use strict";

const ROLE_LEVEL = { viewer: 0, junior: 1, senior: 2, admin: 3 };
const ROLE_NAMES = { viewer: "Viewer", junior: "Junior Surveyor", senior: "Senior Surveyor", admin: "Admin" };
const state = { token: null, user: null, health: null, recordsPage: 1, charts: {}, facets: null };

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmt = (v, digits = 0) => (v === null || v === undefined || v === "" ? "—" : Number(v).toLocaleString(undefined, { maximumFractionDigits: digits, minimumFractionDigits: 0 }));
const can = (role) => state.user && ROLE_LEVEL[state.user.role] >= ROLE_LEVEL[role];

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

async function boot() {
  let token = null;
  try { token = sessionStorage.getItem("pis_token"); } catch (_) {}
  state.health = await api("/api/health").catch(() => null);
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
  pill.textContent = ai ? "AI: Claude connected" : "AI: offline (rule-based)";
  pill.className = "pill " + (ai ? "ok" : "warn");
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
  fill("#dl-types", state.facets.property_types);
}

function filterFormHTML(withSearch = true) {
  return `
    ${withSearch ? '<label>Search<input name="q" placeholder="address, client, notes…"></label>' : ""}
    <label>Category<select name="category"><option value="">All</option><option value="valuation">Valuation</option><option value="cost">Construction cost</option><option value="housing">Housing</option></select></label>
    <label>Location<input name="location" list="dl-locations"></label>
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
    kpi("Avg capital value / m²", m.value_per_sqm ? `${cur} ${fmt(m.value_per_sqm.mean)}` : "—", m.value_per_sqm ? `${m.value_per_sqm.count} records` : ""),
    kpi("Avg construction cost / m²", m.cost_per_sqm ? `${cur} ${fmt(m.cost_per_sqm.mean)}` : "—", m.cost_per_sqm ? `${m.cost_per_sqm.count} records` : ""),
    kpi("Avg yield", m.yield_pct ? `${fmt(m.yield_pct.mean, 2)}%` : "—", m.yield_pct ? `${m.yield_pct.count} records` : ""),
    kpi("Reports awaiting review", fmt(pending), "in the latest uploads"),
  ].join("");
  const locs = Object.entries(s.top_locations);
  $("#dash-locations").innerHTML = locs.length ? table(["Location", "Records"], locs.map(([k, v]) => [esc(k), { v: fmt(v), num: true }])) : '<p class="muted">No records yet.</p>';
  $("#dash-reports").innerHTML = reports.length ? table(["Report", "Type", "Status"], reports.map((r) => [esc(r.title || r.filename), esc(r.report_type || "—"), statusPill(r.status)])) : '<p class="muted">No reports uploaded yet.</p>';
  lineChart("dash-chart", t.series.map((p) => p.period), [{ label: `Value / m² (${t.currency || cur})`, data: t.series.map((p) => p.mean) }]);
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
const COMMON_FIELDS = [
  ["title", "Title / project"], ["address", "Address", "wide"], ["location", "Location / area"], ["city", "City"], ["region", "Region"],
  ["property_type", "Property type"], ["purpose", "Purpose"], ["client", "Client"], ["record_date", "Date", "date"],
  ["currency", "Currency"], ["size_sqm", "Size / GFA (m²)", "num"],
];
const CATEGORY_FIELDS = {
  valuation: [["valuation_basis", "Basis (capital/rental/both)"], ["method", "Method"], ["capital_value", "Capital value", "num"], ["annual_rent", "Annual rent", "num"],
    ["yield_pct", "Yield %", "num"], ["value_per_sqm", "Value / m²", "num"], ["rent_per_sqm", "Rent / m² p.a.", "num"], ["assumptions", "Assumptions", "text"]],
  cost: [["total_cost", "Total cost", "num"], ["cost_per_sqm", "Cost / m²", "num"], ["specification", "Specification"]],
  housing: [["avg_price", "Average price", "num"], ["avg_monthly_rent", "Average monthly rent", "num"], ["supply_units", "Supply (units)", "int"],
    ["demand_level", "Demand level"], ["affordability_ratio", "Affordability ratio", "num"], ["stock_notes", "Housing stock notes", "text"]],
};
const BREAKDOWN_KEYS = ["preliminaries", "substructure", "superstructure", "finishes", "services", "external_works", "contingency", "other"];

function fieldHTML([key, label, kind], rec) {
  const v = rec[key] ?? "";
  if (kind === "text") return `<label class="wide">${esc(label)}<textarea name="${key}" rows="2">${esc(v)}</textarea></label>`;
  const type = kind === "num" || kind === "int" ? "number" : kind === "date" ? "date" : "text";
  const step = kind === "num" ? ' step="any"' : "";
  return `<label class="${kind === "wide" ? "wide" : ""}">${esc(label)}<input type="${type}"${step} name="${key}" value="${esc(v)}" data-kind="${kind || ""}"></label>`;
}

function recordEditorHTML(rec, idx) {
  const cat = rec.category || "valuation";
  const comps = rec.comparables || [];
  const bd = rec.cost_breakdown || {};
  return `<div class="review-record" data-idx="${idx}">
    <div class="row between"><strong>Record ${idx + 1}</strong>
      <div class="row gap"><label>Category<select name="category">${["valuation", "cost", "housing"].map((c) => `<option ${c === cat ? "selected" : ""}>${c}</option>`).join("")}</select></label>
      <button type="button" class="btn small danger rm-record">Remove</button></div></div>
    <div class="form-grid">${COMMON_FIELDS.map((f) => fieldHTML(f, rec)).join("")}</div>
    <fieldset><legend>${esc(cat)} details</legend><div class="form-grid">${CATEGORY_FIELDS[cat].map((f) => fieldHTML(f, rec)).join("")}</div></fieldset>
    ${cat === "cost" ? `<fieldset><legend>Elemental cost breakdown</legend><div class="form-grid">${BREAKDOWN_KEYS.map((k) => `<label>${k.replace("_", " ")}<input type="number" step="any" name="bd_${k}" value="${esc(bd[k] ?? "")}"></label>`).join("")}</div></fieldset>` : ""}
    ${cat === "valuation" ? `<fieldset><legend>Comparables (${comps.length})</legend>${comps.length ? table(["Address", "Date", "Price", "Rent", "Size m²", "Price/m²", "Notes"], comps.map((c) => [esc(c.address), esc(c.date), { v: fmt(c.price), num: true }, { v: fmt(c.rent), num: true }, { v: fmt(c.size_sqm), num: true }, { v: fmt(c.price_per_sqm), num: true }, esc(c.notes)])) : '<p class="muted small">None extracted.</p>'}</fieldset>` : ""}
    <div class="form-grid"><label class="wide">Tags (comma separated)<input name="tags" value="${esc((rec.tags || []).join(", "))}"></label>
    <label class="wide">Notes<textarea name="notes" rows="2">${esc(rec.notes || "")}</textarea></label></div>
  </div>`;
}

function readRecordEditor(el, original) {
  const rec = { comparables: original.comparables || null };
  $$("input, select, textarea", el).forEach((inp) => {
    const name = inp.name;
    if (!name) return;
    let v = inp.value.trim();
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
  if (!rec.currency) rec.currency = (state.health && state.health.default_currency) || "GHS";
  return rec;
}

async function openReview(id) {
  const r = await api(`/api/reports/${id}`);
  const panel = $("#review-panel");
  panel.classList.remove("hidden");
  const draft = r.draft || { records: [] };
  let records = (draft.records || []).map((x) => ({ ...x }));
  const editable = r.status !== "confirmed" && can("junior");

  const render = () => {
    panel.innerHTML = `
      <div class="row between"><h3>Review: ${esc(r.filename)}</h3><div class="row gap">
        <button class="btn" id="dl-file">Open original</button>
        ${editable ? '<button class="btn" id="reprocess">Re-run extraction</button><button class="btn danger" id="reject">Reject</button>' : ""}
        ${can("senior") ? '<button class="btn danger" id="del-report">Delete</button>' : ""}
        <button class="btn" id="close-review">Close</button></div></div>
      <p class="muted small">Status: ${statusPill(r.status)} · Extraction: ${esc(r.extraction_method || "—")} ${r.status === "confirmed" ? "· Saved records are edited under Data &amp; Search." : ""}</p>
      ${draft.summary ? `<p>${esc(draft.summary)}</p>` : ""}
      ${draft.warning ? `<div class="notice">${esc(draft.warning)}</div>` : ""}
      ${r.error ? `<div class="notice">${esc(r.error)}</div>` : ""}
      <form id="review-form">${records.map(recordEditorHTML).join("")}</form>
      ${editable ? `<div class="row gap"><button class="btn" id="add-rec">+ Add record</button><button class="btn primary" id="confirm">Confirm &amp; save to database</button></div>` : ""}`;
    $("#close-review").onclick = () => panel.classList.add("hidden");
    $("#dl-file").onclick = () => download(`/api/reports/${id}/file`, r.filename).catch((e) => toast(e.message, true));
    if (!editable) $$("input, select, textarea, .rm-record", $("#review-form")).forEach((x) => (x.disabled = true));
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
    if (!editable) return;
    $("#add-rec").onclick = () => { records = collect(); records.push({ category: r.report_type || "valuation" }); render(); };
    $("#confirm").onclick = async () => {
      try {
        const payload = collect();
        await api(`/api/reports/${id}/confirm`, { method: "POST", body: { records: payload } });
        toast(`Saved ${payload.length} record(s) to the database`);
        panel.classList.add("hidden");
        loadReports(); loadFacets();
      } catch (e) { toast(e.message, true); }
    };
    $("#reject").onclick = async () => { await api(`/api/reports/${id}/reject`, { method: "POST" }); panel.classList.add("hidden"); loadReports(); };
    $("#reprocess").onclick = async (ev) => {
      ev.target.disabled = true; ev.target.textContent = "Extracting…";
      try { await api(`/api/reports/${id}/reprocess`, { method: "POST" }); openReview(id); loadReports(); } catch (e) { toast(e.message, true); ev.target.disabled = false; }
    };
  };
  const collect = () => $$(".review-record", panel).map((el) => readRecordEditor(el, records[Number(el.dataset.idx)] || {}));
  render();
  panel.scrollIntoView({ behavior: "smooth" });
}

/* ---------------- Records ---------------- */
$("#record-filters").innerHTML = filterFormHTML(true);
$("#record-filters").addEventListener("submit", (e) => { e.preventDefault(); state.recordsPage = 1; loadRecords(); });
$("#record-filters").addEventListener("reset", () => setTimeout(() => { state.recordsPage = 1; loadRecords(); }));

async function loadRecords() {
  const f = formData($("#record-filters"));
  const d = await api("/api/records?" + qs({ ...f, page: state.recordsPage, page_size: 25 }));
  if (!d.items.length) { $("#records-table").innerHTML = '<p class="muted">No records match these filters.</p>'; $("#records-pager").innerHTML = ""; return; }
  $("#records-table").innerHTML = table(["Date", "Category", "Location", "Type", "Address / title", { t: "Size m²", num: true }, { t: "Key figure", num: true }, { t: "Rate / m²", num: true }, "Tags"],
    d.items.map((r) => [esc(r.record_date || "—"), esc(r.category), esc([r.location, r.city].filter(Boolean).join(", ") || "—"), esc(r.property_type || "—"),
      esc(r.address || r.title || "—"), { v: fmt(r.size_sqm), num: true }, { v: keyFigure(r), num: true }, { v: rateFigure(r), num: true }, (r.tags || []).map((t) => `<span class="tag">${esc(t)}</span>`).join("")]),
    { clickable: true });
  $$("#records-table tbody tr").forEach((tr) => tr.addEventListener("click", () => openRecord(d.items[tr.dataset.i])));
  const pages = Math.max(1, Math.ceil(d.total / d.page_size));
  $("#records-pager").innerHTML = `<span class="muted small">${d.total} records</span>
    <button class="btn small" ${d.page <= 1 ? "disabled" : ""} id="pg-prev">‹ Prev</button><span>${d.page} / ${pages}</span>
    <button class="btn small" ${d.page >= pages ? "disabled" : ""} id="pg-next">Next ›</button>`;
  $("#pg-prev").onclick = () => { state.recordsPage--; loadRecords(); };
  $("#pg-next").onclick = () => { state.recordsPage++; loadRecords(); };
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
  if (!f.category) { toast("Choose a category (valuation, cost or housing) in the filters first", true); return; }
  download("/api/exports/evidence?" + qs({ ...f, format }), `evidence.${format}`).catch((e) => toast(e.message, true));
}
$("#export-csv").addEventListener("click", () => evidenceExport("csv"));
$("#export-docx").addEventListener("click", () => evidenceExport("docx"));

/* ---------------- Intelligence ---------------- */
const METRIC_OPTIONS = [["value_per_sqm", "Capital value / m²"], ["capital_value", "Capital value"], ["annual_rent", "Annual rent"], ["rent_per_sqm", "Rent / m²"],
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
    { label: `Capital value / m² (${c.currency || ""})`, data: c.rows.map((r) => r.value_per_sqm) },
    { label: "Construction cost / m²", data: c.rows.map((r) => r.cost_per_sqm) },
  ], { type: "bar" });
}
async function loadBreakdown() {
  const b = await api("/api/analytics/breakdown?" + qs({ ...intelFilters(), group_by: $("#group-by").value }));
  const groups = $("#group-by").value.split(",");
  if (!b.rows.length) { $("#breakdown-table").innerHTML = '<p class="muted">No data.</p>'; return; }
  const cols = [["value_per_sqm", "Value/m²"], ["capital_value", "Capital value"], ["annual_rent", "Annual rent"], ["yield_pct", "Yield %"], ["cost_per_sqm", "Cost/m²"], ["avg_price", "Avg price"], ["avg_monthly_rent", "Avg rent/mo"]]
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
    $("#comm-meta").textContent = `${d.method === "ai" ? "Drafted by Claude" : "Template draft"} from ${d.record_count} records · ${d.scope}${d.warning ? " · " + d.warning : ""}. Review before use.`;
  } catch (err) { toast(err.message, true); }
  btn.disabled = false; btn.textContent = "Generate";
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

/* ---------------- Modal ---------------- */
function openModal(html) { $("#modal-body").innerHTML = html; $("#modal").showModal(); }
function closeModal() { $("#modal").close(); }
$("#modal").addEventListener("close", () => { $("#modal-body").innerHTML = ""; });
window.closeModal = closeModal;

boot();
