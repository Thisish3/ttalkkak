// Shared by the desktop app (Tauri) and the browser console (127.0.0.1).
const tauri = window.__TAURI__ && window.__TAURI__.core;
const inApp = Boolean(tauri);

async function api(path, opts = {}) {
  const method = (opts.method || "GET").toUpperCase();
  if (inApp) {
    const body = opts.body === undefined ? null : String(opts.body);
    const res = await tauri.invoke("api_request", { path, method, body });
    return new Response(res.body, { status: res.status });
  }
  const headers = { ...(opts.headers || {}) };
  if (opts.body !== undefined && !headers["Content-Type"]) {
    headers["Content-Type"] = "application/json";
  }
  return fetch(path, { ...opts, method, headers });
}

async function loadConnection() {
  return inApp ? tauri.invoke("load_connection") : null;
}

async function pingServer(url, token) {
  const info = await tauri.invoke("ping_server", { url, token });
  return info;
}

const NAV = [
  ["live", "실시간", '<path d="M3 12h4l3-8 4 16 3-8h4"/>'],
  ["overview", "개요", '<rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/>'],
  ["cost", "원가", '<circle cx="12" cy="12" r="9"/><path d="M12 7v10M9.5 9.5h4a1.8 1.8 0 010 3.6h-3a1.8 1.8 0 000 3.4H15"/>'],
  ["tenants", "테넌트", '<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20a6.5 6.5 0 0113 0M16 4.6a3.5 3.5 0 010 6.8M18 14.5a6.5 6.5 0 013.5 5.5"/>'],
  ["usage", "사용량", '<path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/>'],
  ["runs", "서빙 실행", '<path d="M6 4l14 8-14 8z"/>'],
];

function renderShell() {
  const nav = document.getElementById("nav");
  nav.innerHTML = NAV.map(
    ([id, name, icon]) =>
      `<a href="#${id}" data-panel="${id}"><svg viewBox="0 0 24 24" aria-hidden="true">${icon}</svg><span>${name}</span></a>`
  ).join("");
  const show = () => {
    const id = NAV.some((n) => n[0] === location.hash.slice(1)) ? location.hash.slice(1) : "live";
    document.querySelectorAll(".panel").forEach((p) => (p.hidden = p.id !== "panel-" + id));
    nav.querySelectorAll("a").forEach((a) =>
      a.dataset.panel === id ? a.setAttribute("aria-current", "page") : a.removeAttribute("aria-current")
    );
  };
  window.addEventListener("hashchange", show);
  show();
}

function setConn(state, text) {
  const el = document.getElementById("conn");
  const icon = { ok: "●", warn: "▲", bad: "✕" }[state];
  el.dataset.state = state;
  el.textContent = `${icon} ${text}`;
}

function renderChip(info) {
  const chip = document.getElementById("chip");
  const n = info && info.max_replicas;
  if (!n) {
    chip.hidden = true;
    return;
  }
  chip.hidden = false;
  chip.innerHTML = "";
  const slots = document.createElement("span");
  slots.className = "slots";
  slots.innerHTML = "<i></i>".repeat(Math.min(n, 12));
  chip.append(slots, document.createTextNode(`GPU 한 장 · 최대 복제본 ${n}개`));
}

async function startShell() {
  renderShell();
  const conn = await loadConnection();
  if (inApp && !conn) {
    location.replace("connect.html");
    return;
  }
  document.getElementById("server").textContent = conn ? conn.url : location.host;
  try {
    const res = await api("/api/ping");
    if (res.status === 401) throw new Error("401");
    const info = await res.json();
    renderChip(info);
    setConn("ok", "연결됨");
  } catch (e) {
    setConn("bad", "연결 끊김");
  }
}

if (typeof document !== "undefined" && document.getElementById("nav")) {
  startShell();
}
