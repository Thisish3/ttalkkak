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

async function pingServer(url, token, allowCleartext) {
  return tauri.invoke("ping_server", { url, token, allowCleartext });
}

const NAV = [
  ["live", "실시간", '<path d="M3 12h4l3-8 4 16 3-8h4"/>'],
  ["overview", "개요", '<rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/>'],
  ["cost", "원가", '<circle cx="12" cy="12" r="9"/><path d="M12 7v10M9.5 9.5h4a1.8 1.8 0 010 3.6h-3a1.8 1.8 0 000 3.4H15"/>'],
  ["tenants", "테넌트", '<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20a6.5 6.5 0 0113 0M16 4.6a3.5 3.5 0 010 6.8M18 14.5a6.5 6.5 0 013.5 5.5"/>'],
  ["usage", "사용량", '<path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/>'],
  ["runs", "서빙 실행", '<path d="M6 4l14 8-14 8z"/>'],
];

const onShow = {};
let panelsReady = false;

function showPanel() {
  const nav = document.getElementById("nav");
  const id = NAV.some((n) => n[0] === location.hash.slice(1)) ? location.hash.slice(1) : "live";
  document.querySelectorAll(".panel").forEach((p) => (p.hidden = p.id !== "panel-" + id));
  nav.querySelectorAll("a").forEach((a) =>
    a.dataset.panel === id ? a.setAttribute("aria-current", "page") : a.removeAttribute("aria-current")
  );
  if (panelsReady && onShow[id]) onShow[id]();
}

function renderShell() {
  const nav = document.getElementById("nav");
  nav.innerHTML = NAV.map(
    ([id, name, icon]) =>
      `<a href="#${id}" data-panel="${id}"><svg viewBox="0 0 24 24" aria-hidden="true">${icon}</svg><span>${name}</span></a>`
  ).join("");
  window.addEventListener("hashchange", showPanel);
  showPanel();
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
  initPanels();
  try {
    const res = await api("/api/ping");
    if (!res.ok) {
      setConn(res.status === 401 ? "bad" : "warn", `서버 응답 ${res.status}`);
      return;
    }
    renderChip(await res.json());
    setConn("ok", "연결됨");
  } catch (e) {
    setConn("bad", "연결 끊김");
  }
}

// ---- common components ----
const $ = (id) => document.getElementById(id);
const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmt = (n) => (typeof n === "number" ? n.toLocaleString("en-US") : "-");
const BADGE_ICON = { ok: "●", warn: "▲", bad: "✕", mut: "–" };
const badge = (kind, text) => `<span class="badge ${kind}">${BADGE_ICON[kind]} ${esc(text)}</span>`;
const errorBox = (msg) => `<div class="errbox inline" role="alert">✕ ${esc(msg)}</div>`;
const emptyState = (msg) => `<div class="empty-state">${esc(msg)}</div>`;
const skeleton = (n = 3) => '<span class="skeleton"></span>'.repeat(n);
const skeletonRows = (cols, n = 3) =>
  `<tr><td colspan="${cols}">${skeleton(n)}</td></tr>`;
const rowMessage = (cols, html) => `<tr><td colspan="${cols}">${html}</td></tr>`;

function toast(message, kind = "ok") {
  let box = $("toasts");
  if (!box) {
    box = document.createElement("div");
    box.id = "toasts";
    box.className = "toasts";
    box.setAttribute("role", "status");
    document.body.append(box);
  }
  const el = document.createElement("div");
  el.className = "toast " + kind;
  el.textContent = `${BADGE_ICON[kind] || ""} ${message}`;
  box.append(el);
  setTimeout(() => el.remove(), 5000);
}

function confirmDialog({ title, message, confirmText = "확인", danger = false }) {
  return new Promise((resolve) => {
    const back = document.createElement("div");
    back.className = "modal";
    const card = document.createElement("div");
    card.className = "card";
    card.setAttribute("role", "dialog");
    card.setAttribute("aria-modal", "true");
    const h = document.createElement("h2");
    h.textContent = title;
    const p = document.createElement("p");
    p.textContent = message;
    const actions = document.createElement("div");
    actions.className = "actions";
    const cancel = document.createElement("button");
    cancel.className = "btn secondary";
    cancel.textContent = "취소";
    const ok = document.createElement("button");
    ok.className = "btn " + (danger ? "danger" : "primary");
    ok.textContent = confirmText;
    actions.append(cancel, ok);
    card.append(h, p, actions);
    back.append(card);
    const done = (v) => {
      document.removeEventListener("keydown", onKey);
      back.remove();
      resolve(v);
    };
    const onKey = (e) => e.key === "Escape" && done(false);
    document.addEventListener("keydown", onKey);
    cancel.onclick = () => done(false);
    ok.onclick = () => done(true);
    document.body.append(back);
    cancel.focus();
  });
}

async function call(path, opts) {
  const res = await api(path, opts);
  const body = await res.json().catch(() => null);
  if (!res.ok) throw new Error(body && body.error ? body.error : String(res.status));
  return body;
}

const CANCELLED = Symbol("cancelled");

async function mutate(path, body, ask) {
  const yes = await confirmDialog(ask);
  if (!yes) return CANCELLED;
  return call(path, { method: "POST", body: JSON.stringify(body || {}) });
}

// ---- panels ----
const STAGES = ["probe", "plan", "launch", "verify", "pick"];
const STAGE_KO = { probe: "조사", plan: "계획", launch: "실행", verify: "검증", pick: "선택" };
let current = null, poller = null, hasGpu = false;

function setMsg(id, kind, text) {
  const cls = { err: "errbox inline", ok: "okbox", info: "infobox" }[kind];
  const el = $(id);
  el.className = cls;
  el.textContent = text ? (kind === "err" ? "✕ " : kind === "ok" ? "● " : "") + text : "";
}

function canRun() {
  const m = $("model").value.trim();
  const ok = !!m && (hasGpu || m.startsWith("nim:"));
  $("run").disabled = !ok;
  $("run").title = !m ? "모델을 고르세요" : ok ? "" : "GPU가 없어 로컬 모델은 실행할 수 없습니다 (nim: 모델은 가능)";
  if ($("run-msg").dataset.kind !== "run") {
    setMsg("run-msg", "info", m && !ok ? "GPU를 찾지 못해 로컬 모델은 실행할 수 없습니다. nim: 모델은 실행할 수 있습니다." : "");
  }
}

async function loadGpu() {
  try {
    const g = await call("/api/gpu");
    hasGpu = !!g;
    $("gpu").innerHTML = g
      ? badge("ok", `GPU · ${g.name} · ${(g.memory_mib / 1024).toFixed(0)} GiB`)
      : badge("warn", "GPU 없음 · NIM 모델만 실행 가능");
  } catch (e) {
    $("gpu").innerHTML = badge("bad", "GPU 확인 실패: " + e.message);
  }
  canRun();
}

async function loadSkills() {
  $("skills").innerHTML = skeleton();
  try {
    const rows = await call("/api/skills");
    $("skills").innerHTML = rows.map((s) => {
      const req = Object.entries(s.requires || {}).filter(([, v]) => v).map(([k]) => `<span class="tag">${esc(k)}</span>`).join("");
      const ctx = s.min_context ? `<span class="tag">context ≥ ${fmt(s.min_context)}</span>` : "";
      return `<label class="tile"><input type="checkbox" value="${esc(s.name)}"> <b>${esc(s.name)}</b><p>${esc(s.description)}</p><div>${req}${ctx}</div></label>`;
    }).join("") || emptyState("skill이 없습니다");
  } catch (e) {
    $("skills").innerHTML = errorBox(e.message);
  }
}

async function searchModels(e) {
  e.preventDefault();
  $("models-err").innerHTML = "";
  const tbody = $("models").querySelector("tbody");
  tbody.innerHTML = skeletonRows(4);
  try {
    const rows = await call("/api/models?q=" + encodeURIComponent($("q").value));
    tbody.innerHTML = rows.map((m) => {
      const fit = m.fits === null
        ? badge("mut", hasGpu ? "알 수 없음" : "GPU 없음")
        : m.fits ? badge("ok", "들어감") : badge("bad", "안 들어감");
      const w = m.weight_gib === null ? "-" : m.weight_gib.toFixed(1) + " GiB";
      return `<tr data-id="${esc(m.id)}"><td>${esc(m.id)}</td><td class="num">${fmt(m.downloads)}</td><td class="num">${w}</td><td>${fit}</td></tr>`;
    }).join("") || rowMessage(4, emptyState("결과 없음"));
    tbody.querySelectorAll("tr[data-id]").forEach((tr) => (tr.onclick = () => {
      tbody.querySelectorAll("tr").forEach((x) => x.classList.remove("sel"));
      tr.classList.add("sel");
      $("model").value = tr.dataset.id;
      canRun();
    }));
  } catch (err) {
    tbody.innerHTML = "";
    $("models-err").innerHTML = errorBox(err.message);
  }
}

async function startRun() {
  const model = $("model").value.trim();
  const skills = [...document.querySelectorAll("#skills input:checked")].map((x) => x.value);
  try {
    const r = await mutate("/api/runs", { model, skills }, {
      title: "서빙 구성 실행",
      message: `모델 ${model}로 후보 측정을 시작합니다. skill ${skills.length}개를 검사하고, 후보마다 엔진을 띄워 GPU를 점유합니다. 한 번에 하나의 run만 실행합니다.`,
      confirmText: "실행",
    });
    if (r === CANCELLED) return;
    $("run-msg").dataset.kind = "run";
    setMsg("run-msg", "info", "실행 중: " + r.run_id);
    watch(r.run_id);
  } catch (err) {
    setMsg("run-msg", "err", err.message);
  }
}

function renderSteps(st) {
  const next = STAGES.find((s) => !st.done.includes(s));
  $("steps").innerHTML = STAGES.map((s) => {
    const done = st.done.includes(s), cur = st.running && s === next;
    return `<span class="${done ? "ok" : cur ? "cur" : ""}">${done ? "●" : cur ? "▲" : "–"} ${STAGE_KO[s]}</span>`;
  }).join("");
}

function watch(run_id) {
  current = run_id;
  clearInterval(poller);
  poller = setInterval(async () => {
    try {
      const st = await call("/api/runs/" + run_id);
      renderSteps(st);
      if (!st.running) {
        clearInterval(poller);
        if (st.error) setMsg("run-msg", "err", "오류: " + st.error.message);
        else setMsg("run-msg", "ok", "완료: " + (st.pick && st.pick.chosen ? "선택 " + st.pick.chosen : "선택된 후보 없음"));
        showRun(st);
        loadRuns();
      }
    } catch (err) {
      clearInterval(poller);
      setMsg("run-msg", "err", err.message);
    }
  }, 2000);
}

function showRun(st) {
  current = st.run_id;
  $("report").hidden = !st.report_md;
  $("report").textContent = st.report_md || "";
  $("export").hidden = !(st.pick && st.pick.chosen && st.pick.chosen !== "nim");
  document.querySelectorAll("#runs button").forEach((b) => b.classList.toggle("cur", b.dataset.run === current));
}

async function loadRuns() {
  try {
    const rows = await call("/api/runs");
    $("runs").innerHTML = rows.slice(0, 10).map((r) => {
      const s = r.error ? badge("bad", "오류") : r.running ? badge("warn", "실행 중")
        : r.chosen ? badge("ok", "선택 " + r.chosen) : badge("mut", "선택 없음");
      return `<button class="btn" data-run="${esc(r.run_id)}"><span>${esc(r.run_id)}</span>${s}</button>`;
    }).join("") || emptyState("아직 실행한 run이 없습니다");
    $("runs").querySelectorAll("button").forEach((b) => (b.onclick = async () => {
      try {
        showRun(await call("/api/runs/" + b.dataset.run));
      } catch (err) {
        toast(err.message, "bad");
      }
    }));
  } catch (e) {
    $("runs").innerHTML = errorBox(e.message);
  }
}

async function exportRun() {
  const target = $("target").value;
  try {
    const r = await mutate(`/api/runs/${current}/export`, { target, image: $("image").value }, {
      title: "내보내기",
      message: `run ${current}의 설정을 ${target} 형식으로 내보냅니다. 서버의 run 폴더 안 export-${target} 폴더에 파일을 씁니다.`,
      confirmText: "내보내기",
    });
    if (r === CANCELLED) return;
    setMsg("export-msg", "ok", "저장: " + r.dir + " (" + r.files.join(", ") + ")");
  } catch (err) {
    setMsg("export-msg", "err", err.message);
  }
}

async function loadOverview() {
  $("ov-msg").innerHTML = "";
  $("ov-rows").innerHTML = skeletonRows(4);
  try {
    const s = await call("/api/ops/status");
    $("ov-rows").innerHTML = s.pods
      .map((p) => `<tr><td>${esc(p.name)}</td><td class="num">${esc(p.ready)}</td><td class="num">${fmt(p.restarts)}</td><td>${esc(p.phase)}</td></tr>`)
      .join("");
    if (s.error) $("ov-msg").innerHTML = errorBox(s.error);
    else if (!s.pods.length) $("ov-rows").innerHTML = rowMessage(4, emptyState("파드가 없습니다: ttalkkak ops cluster up"));
  } catch (e) {
    $("ov-rows").innerHTML = "";
    $("ov-msg").innerHTML = errorBox(e.message);
  }
}

async function loadRunChoices() {
  const runs = await call("/api/runs").catch(() => []);
  const opts = runs.map((r) => `<option value="${esc(r.run_id || r.id)}">${esc(r.run_id || r.id)}</option>`).join("");
  $("cost-run").innerHTML = opts;
  $("u-run").innerHTML = `<option value="">가격 없이</option>` + opts;
}

async function loadCost() {
  $("cost-out").innerHTML = skeleton();
  try {
    const c = await call("/api/ops/cost?run=" + encodeURIComponent($("cost-run").value));
    const p = c.price_krw_per_mtok, k = c.cost_krw_per_mtok, op = c.operating_point;
    $("cost-out").innerHTML = `<div class="kv" style="margin-top:12px">
      <div>출력 가격<b>${fmt(Math.round(p.output))}원</b>원가 ${fmt(Math.round(k.output))}원 /100만</div>
      <div>입력 가격<b>${fmt(Math.round(p.input))}원</b>원가 ${fmt(Math.round(k.input))}원 /100만</div>
      <div>캐시 입력<b>${fmt(Math.round(p.cached_input))}원</b>원가 ${fmt(Math.round(k.cached_input))}원 /100만</div>
      <div>운영점<b>${op ? "동시 " + op.streams : "없음"}</b>${op ? fmt(Math.round(op.output_tok_s)) + " 토큰/초" : "SLO를 지킬 수 없음"}</div>
      <div>필요 GPU<b>${c.gpus_needed ?? "-"}</b>목표 처리량 기준</div></div>` +
      (c.warnings.length ? `<div class="infobox">▲ 경고: ${c.warnings.map(esc).join(" · ")}</div>` : "");
  } catch (e) {
    $("cost-out").innerHTML = errorBox(e.message);
  }
}

const tenantError = (e) => setMsg("t-msg", "err", e.message);

async function loadTenants() {
  $("t-rows").innerHTML = skeletonRows(6);
  try {
    const ts = await call("/api/ops/tenants");
    $("t-rows").innerHTML = ts.map((t) => `<tr><td>${esc(t.id)}</td><td>${esc(t.name)}</td>
      <td class="num"><input class="num" data-limit="${esc(t.id)}" value="${t.token_limit_per_min ?? ""}" placeholder="없음" style="width:90px" aria-label="분당 토큰"></td>
      <td><select data-cache="${esc(t.id)}" aria-label="캐시"><option ${t.cache_mode === "isolated" ? "selected" : ""}>isolated</option><option ${t.cache_mode === "shared" ? "selected" : ""}>shared</option></select></td>
      <td>${t.keys.map((k) => `<code>${esc(k.prefix)}…</code> <button class="btn danger small" data-revoke="${esc(k.prefix)}">폐기</button>`).join("<br>") || "-"}</td>
      <td><button class="btn secondary" data-issue="${esc(t.id)}">키 발급</button></td></tr>`).join("")
      || rowMessage(6, emptyState("테넌트가 없습니다"));
  } catch (e) {
    $("t-rows").innerHTML = rowMessage(6, errorBox(e.message));
  }
}

async function addTenant() {
  const id = $("t-id").value.trim(), name = $("t-name").value.trim();
  try {
    const r = await mutate("/api/ops/tenants", { id, name }, {
      title: "테넌트 추가",
      message: `테넌트 ${id}(${name})를 추가합니다. 키는 따로 발급해야 합니다.`,
      confirmText: "추가",
    });
    if (r === CANCELLED) return;
    setMsg("t-msg", "info", "");
    loadTenants();
  } catch (e) {
    tenantError(e);
  }
}

function showKey(key) {
  const back = document.createElement("div");
  back.className = "modal";
  back.innerHTML = `<div class="card" role="dialog" aria-modal="true"><h2>새 API 키</h2><p>창을 닫으면 다시 볼 수 없습니다. 지금 저장하세요.</p>
    <div class="secret"></div><div class="actions" style="margin-top:12px"><button class="btn secondary">복사</button><button class="btn primary">닫기</button></div></div>`;
  const text = back.querySelector(".secret");
  text.textContent = key;
  const [copy, close] = back.querySelectorAll("button");
  copy.onclick = () => navigator.clipboard?.writeText(text.textContent).then(() => toast("복사했습니다"), () => toast("복사하지 못했습니다", "bad"));
  close.onclick = () => {
    text.textContent = "";
    back.remove();
    loadTenants();
  };
  document.body.append(back);
  close.focus();
}

async function tenantClick(e) {
  const t = e.target.closest("button");
  if (!t) return;
  try {
    if (t.dataset.issue) {
      const r = await mutate(`/api/ops/tenants/${t.dataset.issue}/keys`, {}, {
        title: "API 키 발급",
        message: `테넌트 ${t.dataset.issue}에 새 API 키 1개를 발급합니다. 키 원문은 이번 한 번만 보입니다.`,
        confirmText: "발급",
      });
      if (r !== CANCELLED) showKey(r.key);
    }
    if (t.dataset.revoke) {
      const r = await mutate(`/api/ops/keys/${encodeURIComponent(t.dataset.revoke)}/revoke`, {}, {
        title: "API 키 폐기",
        message: `키 ${t.dataset.revoke}…를 폐기합니다. 이 키를 쓰는 클라이언트는 즉시 접근할 수 없게 되고 되돌릴 수 없습니다.`,
        confirmText: "폐기",
        danger: true,
      });
      if (r !== CANCELLED) loadTenants();
    }
  } catch (err) {
    tenantError(err);
  }
}

async function tenantChange(e) {
  const t = e.target;
  try {
    let r;
    if (t.dataset.limit) {
      const v = t.value.trim();
      r = await mutate(`/api/ops/tenants/${t.dataset.limit}/limit`, { per_min: v || null }, {
        title: "분당 토큰 한도 변경",
        message: v ? `테넌트 ${t.dataset.limit}의 분당 토큰 한도를 ${v}로 바꿉니다.` : `테넌트 ${t.dataset.limit}의 분당 토큰 한도를 없앱니다.`,
        confirmText: "변경",
      });
    } else if (t.dataset.cache) {
      r = await mutate(`/api/ops/tenants/${t.dataset.cache}/cache`, { mode: t.value }, {
        title: "캐시 모드 변경",
        message: `테넌트 ${t.dataset.cache}의 캐시 모드를 ${t.value}로 바꿉니다.`,
        confirmText: "변경",
      });
    } else return;
    if (r !== CANCELLED) toast("변경했습니다");
  } catch (err) {
    tenantError(err);
  }
  loadTenants();
}

const usageQuery = () =>
  `from=${$("u-from").value}&to=${$("u-to").value}` + ($("u-run").value ? `&run=${encodeURIComponent($("u-run").value)}` : "");

async function loadUsage() {
  $("u-rows").innerHTML = skeletonRows(7);
  try {
    const rows = await call("/api/ops/usage?" + usageQuery());
    $("u-rows").innerHTML = rows.map((u) => `<tr><td>${esc(u.tenant)}</td><td>${esc(u.model)}</td><td class="num">${fmt(u.requests)}</td><td class="num">${fmt(u.input_tokens)}</td><td class="num">${fmt(u.cached_input_tokens)}</td><td class="num">${fmt(u.output_tokens)}</td><td class="num">${u.amount_krw === undefined ? "-" : fmt(u.amount_krw)}</td></tr>`).join("")
      || rowMessage(7, emptyState("기간 안 사용량이 없습니다"));
  } catch (e) {
    $("u-rows").innerHTML = rowMessage(7, errorBox(e.message));
  }
}

async function downloadCsv() {
  try {
    const res = await api("/api/ops/invoice.csv?" + usageQuery());
    if (!res.ok) throw new Error(String(res.status));
    const url = URL.createObjectURL(await res.blob());
    const a = document.createElement("a");
    a.href = url;
    a.download = "invoice.csv";
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  } catch (e) {
    toast("CSV를 받지 못했습니다: " + e.message, "bad");
  }
}

function initPanels() {
  const today = new Date(), iso = (d) => d.toISOString().slice(0, 10);
  $("u-from").value = iso(new Date(Date.UTC(today.getUTCFullYear(), today.getUTCMonth(), 1)));
  $("u-to").value = iso(new Date(today.getTime() + 86400000));
  $("search").onsubmit = searchModels;
  $("model").oninput = canRun;
  $("run").onclick = startRun;
  $("do-export").onclick = exportRun;
  $("cost-load").onclick = loadCost;
  $("t-add").onclick = addTenant;
  $("t-rows").addEventListener("click", tenantClick);
  $("t-rows").addEventListener("change", tenantChange);
  $("u-load").onclick = loadUsage;
  $("u-csv").onclick = downloadCsv;
  onShow.overview = loadOverview;
  onShow.cost = loadRunChoices;
  onShow.tenants = loadTenants;
  onShow.usage = loadRunChoices;
  onShow.runs = () => {
    loadGpu();
    loadSkills();
    loadRuns();
  };
  panelsReady = true;
  showPanel();
}

if (typeof document !== "undefined" && document.getElementById("nav")) {
  startShell();
}
