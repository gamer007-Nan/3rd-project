"use strict";
const isAdmin = document.body.dataset.role === "admin";
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];

// Build DOM safely (text only, never innerHTML) so product names can't inject markup.
function el(tag, attrs = {}, ...kids) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") n.className = v;
    else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
    else n.setAttribute(k, v);
  }
  kids.flat().forEach(c => n.append(c instanceof Node ? c : document.createTextNode(c ?? "")));
  return n;
}
const tag = (cls, text) => el("span", {class: "tag " + cls}, text);
const money = n => "₦" + Number(n).toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2});

async function api(method, url, body) {
  const r = await fetch(url, {method, credentials: "same-origin",
    headers: {"Accept": "application/json", "Content-Type": "application/json"},
    body: body ? JSON.stringify(body) : undefined});
  if (r.status === 401) { location.href = "/login"; throw new Error("Signed out"); }
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || "Request failed");
  return data;
}

function table(target, headers, rows) {
  target.replaceChildren(
    el("thead", {}, el("tr", {}, headers.map(h => el("th", {}, h)))),
    el("tbody", {}, rows.length ? rows : el("tr", {}, el("td", {colspan: headers.length}, "Nothing to show yet."))));
}

// ---------- tabs ----------
let current = "overview";
function show(view) {
  current = view;
  $$("#tabs button").forEach(b => b.classList.toggle("active", b.dataset.view === view));
  $$(".view").forEach(v => v.hidden = v.id !== "view-" + view);
  refresh();
}
$("#tabs").addEventListener("click", e => { if (e.target.dataset.view) show(e.target.dataset.view); });
$("#logout").addEventListener("click", async () => { await api("POST", "/logout"); location.href = "/login"; });

// ---------- data ----------
let products = [];
async function loadProducts() {
  products = await api("GET", "/products");
  $$(".product-select").forEach(s => {
    const keep = s.value;
    s.replaceChildren(...products.map(p => el("option", {value: p.id}, `${p.name} (${p.sku}) – stock ${p.stock_level}`)));
    if (keep) s.value = keep;
  });
}

function alertRows(alerts, withActions) {
  return alerts.map(a => el("tr", {},
    el("td", {}, tag(a.alert_type, a.alert_type === "theft" ? "Possible theft" : "Low stock")),
    el("td", {}, a.product_name), el("td", {}, a.camera_event_time),
    el("td", {}, tag(a.status, a.status.replace("_", " "))),
    withActions ? el("td", {}, a.status === "unresolved" && isAdmin ? [
      el("button", {class: "btn btn-small", onclick: () => review(a.id, "resolved")}, "Resolve"), " ",
      el("button", {class: "btn btn-small btn-ghost", onclick: () => review(a.id, "false_alarm")}, "False alarm")] : "") : ""));
}
async function review(id, status) { await api("POST", `/alerts/${id}/resolve`, {status}); refresh(); }

async function renderOverview() {
  const s = await api("GET", "/summary");
  $("#stats").replaceChildren(
    ...[["Products", s.products, ""], ["Low stock", s.low_stock, s.low_stock ? "warn" : ""],
        ["Open theft alerts", s.open_theft_alerts, s.open_theft_alerts ? "warn" : ""],
        ["Units sold today", s.sales_today, ""]]
      .map(([l, v, c]) => el("div", {class: "stat " + c}, el("b", {}, v), l)));
  const alerts = await api("GET", "/alerts?status=unresolved&limit=5");
  table($("#overview-alerts"), ["Type", "Product", "Time", "Status", ""], alertRows(alerts, false));
}

async function renderProducts() {
  await loadProducts();
  const q = $("#search").value.toLowerCase();
  const rows = products.filter(p => `${p.name} ${p.sku} ${p.category}`.toLowerCase().includes(q)).map(p => el("tr", {},
    el("td", {}, p.name), el("td", {}, p.sku), el("td", {}, p.category),
    el("td", {class: "num"}, money(p.price)), el("td", {class: "num"}, p.stock_level),
    el("td", {class: "num"}, p.reorder_point),
    el("td", {}, p.low_stock ? tag("low", "Reorder") : tag("ok", "OK")),
    isAdmin ? el("td", {}, [el("button", {class: "btn btn-small", onclick: () => openDialog(p)}, "Edit"), " ",
      el("button", {class: "btn btn-small btn-danger", onclick: () => removeProduct(p)}, "Delete")]) : ""));
  table($("#products-table"), ["Name", "SKU", "Category", "Price", "Stock", "Reorder at", "Status", isAdmin ? "Actions" : ""], rows);
}
$("#search").addEventListener("input", renderProducts);

async function removeProduct(p) {
  if (!confirm(`Delete ${p.name}?`)) return;
  try { await api("DELETE", `/products/${p.id}`); renderProducts(); } catch (e) { alert(e.message); }
}

const dialog = $("#product-dialog"), pform = $("#product-form");
let editing = null;
function openDialog(p) {
  editing = p || null;
  $("#dialog-title").textContent = p ? "Edit product" : "Add product";
  pform.reset();
  if (p) for (const k of ["name", "sku", "category", "price", "stock_level", "reorder_point"]) pform.elements[k].value = p[k];
  $("#dialog-msg").textContent = "";
  dialog.showModal();
}
if ($("#add-product")) $("#add-product").addEventListener("click", () => openDialog());
$("#dialog-cancel").addEventListener("click", () => dialog.close());
pform.addEventListener("submit", async e => {
  e.preventDefault();
  const f = Object.fromEntries(new FormData(pform));
  const body = {name: f.name, sku: f.sku, category: f.category, price: f.price,
                stock_level: parseInt(f.stock_level, 10), reorder_point: parseInt(f.reorder_point, 10)};
  try {
    await (editing ? api("PUT", `/products/${editing.id}`, body) : api("POST", "/products", body));
    dialog.close(); renderProducts();
  } catch (err) { $("#dialog-msg").textContent = err.message; }
});

async function renderSales() {
  await loadProducts();
  const rows = (await api("GET", "/transactions?limit=20")).map(t => el("tr", {},
    el("td", {}, t.timestamp), el("td", {}, t.product_name), el("td", {class: "num"}, t.quantity),
    el("td", {}, t.source === "qr_scan" ? "QR scan-out" : "Manual POS"), el("td", {}, t.till_id || "–")));
  table($("#sales-table"), ["Time", "Product", "Qty", "Captured by", "Till"], rows);
}
$("#sale-form").addEventListener("submit", async e => {
  e.preventDefault();
  const f = Object.fromEntries(new FormData(e.target)), msg = $("#sale-msg");
  try {
    const r = await api("POST", "/transactions", {product_id: +f.product_id, quantity: parseInt(f.quantity, 10),
                                                  source: f.source, till_id: f.till_id || null});
    msg.className = "ok-text";
    msg.textContent = `Sale recorded. Stock is now ${r.stock_level}.` + (r.resolved_alerts.length ? " A pending alert was matched to this sale." : "") + (r.oversold ? " Warning: more sold than was in stock." : "");
    renderSales();
  } catch (err) { msg.className = "err-text"; msg.textContent = err.message; }
});

async function renderAlerts() {
  const status = $("#alert-filter").value;
  const alerts = await api("GET", "/alerts" + (status ? `?status=${status}` : ""));
  table($("#alerts-table"), ["Type", "Product", "Time", "Status", isAdmin ? "Review" : ""], alertRows(alerts, true));
}
$("#alert-filter").addEventListener("change", renderAlerts);

$("#camera-form").addEventListener("submit", async e => {
  e.preventDefault();
  const msg = $("#camera-msg");
  try {
    const r = await api("POST", "/camera/events", {product_id: +new FormData(e.target).get("product_id")});
    msg.className = r.outcome === "legitimate" ? "ok-text" : "err-text";
    msg.textContent = r.outcome === "legitimate"
      ? `Matched sale #${r.transaction_id}: closed as a legitimate sale.`
      : `No matching sale within the window: alert #${r.alert_id} raised.`;
  } catch (err) { msg.className = "err-text"; msg.textContent = err.message; }
});

if ($("#report-form")) $("#report-form").addEventListener("submit", e => {
  e.preventDefault();
  const f = new FormData(e.target), q = new URLSearchParams();
  if (f.get("from")) q.set("from", f.get("from"));
  if (f.get("to")) q.set("to", f.get("to"));
  location.href = "/reports/audit-log?" + q;
});

// ---------- refresh + polling (report section 4.4.4) ----------
async function pollBadge() {
  try {
    const open = (await api("GET", "/alerts?status=unresolved&type=theft&limit=500")).length;
    const b = $("#alert-badge");
    b.textContent = open; b.hidden = open === 0;
  } catch (_) {}
}
function refresh() {
  ({overview: renderOverview, products: renderProducts, sales: renderSales,
    alerts: renderAlerts, camera: loadProducts, reports: () => {}})[current]?.();
  pollBadge();
}
setInterval(() => { if (current === "alerts" || current === "overview") refresh(); else pollBadge(); }, 5000);
refresh();
