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
  if (id !== "live" && typeof liveStop === "function") liveStop();
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
const skeleton = (n = 3) =>
  `<span aria-busy="true" aria-label="불러오는 중">${'<span class="skeleton"></span>'.repeat(n)}</span>`;
const skeletonRows = (cols, n = 3) =>
  `<tr><td colspan="${cols}">${skeleton(n)}</td></tr>`;
const rowMessage = (cols, html) => `<tr><td colspan="${cols}">${html}</td></tr>`;

function ensureToastBox() {
  let box = $("toasts");
  if (!box) {
    box = document.createElement("div");
    box.id = "toasts";
    box.className = "toasts";
    box.setAttribute("role", "status");
    document.body.append(box);
  }
  return box;
}

function toast(message, kind = "ok") {
  const box = ensureToastBox();
  const el = document.createElement("div");
  el.className = "toast " + kind;
  el.textContent = `${BADGE_ICON[kind] || ""} ${message}`;
  box.append(el);
  setTimeout(() => el.remove(), 5000);
}

function openModal(card, titleEl, initial, onClose, { persistent = false } = {}) {
  const back = document.createElement("div");
  back.className = "modal";
  titleEl.id = "modal-title";
  card.setAttribute("role", "dialog");
  card.setAttribute("aria-modal", "true");
  card.setAttribute("aria-labelledby", "modal-title");
  back.append(card);
  const opener = document.activeElement;
  const focusable = () => [...card.querySelectorAll("button, input, select, textarea, [href]")].filter((e) => !e.disabled);
  const close = () => {
    document.removeEventListener("keydown", onKey);
    back.remove();
    if (opener && opener.focus) opener.focus();
    onClose();
  };
  const onKey = (e) => {
    if (e.key === "Escape") {
      e.preventDefault();
      if (!persistent) close();
    } else if (e.key === "Tab") {
      const f = focusable();
      if (!f.length) return;
      const first = f[0], last = f[f.length - 1];
      if (!card.contains(document.activeElement)) {
        e.preventDefault();
        first.focus();
      } else if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    }
  };
  document.addEventListener("keydown", onKey);
  document.body.append(back);
  initial.focus();
  return close;
}

function confirmDialog({ title, message, confirmText = "확인", danger = false }) {
  return new Promise((resolve) => {
    const card = document.createElement("div");
    card.className = "card";
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
    let answer = false;
    const close = openModal(card, h, cancel, () => resolve(answer));
    cancel.onclick = close;
    ok.onclick = () => {
      answer = true;
      close();
    };
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
  const checked = new Set([...document.querySelectorAll("#skills input:checked")].map((x) => x.value));
  if (!checked.size) $("skills").innerHTML = skeleton();
  try {
    const rows = await call("/api/skills");
    $("skills").innerHTML = rows.map((s) => {
      const req = Object.entries(s.requires || {}).filter(([, v]) => v).map(([k]) => `<span class="tag">${esc(k)}</span>`).join("");
      const ctx = s.min_context ? `<span class="tag">context ≥ ${fmt(s.min_context)}</span>` : "";
      return `<label class="tile"><input type="checkbox" value="${esc(s.name)}"${checked.has(s.name) ? " checked" : ""}> <b>${esc(s.name)}</b><p>${esc(s.description)}</p><div>${req}${ctx}</div></label>`;
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
  const keep = [$("cost-run").value, $("u-run").value];
  $("cost-run").innerHTML = opts;
  $("u-run").innerHTML = `<option value="">가격 없이</option>` + opts;
  if (keep[0]) $("cost-run").value = keep[0];
  if (keep[1]) $("u-run").value = keep[1];
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
      <div>운영점<b>${op ? "동시 " + esc(op.streams) : "없음"}</b>${op ? fmt(Math.round(op.output_tok_s)) + " 토큰/초" : "SLO를 지킬 수 없음"}</div>
      <div>필요 GPU<b>${esc(c.gpus_needed ?? "-")}</b>목표 처리량 기준</div></div>` +
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
      <td class="num"><input class="num" data-limit="${esc(t.id)}" value="${esc(t.token_limit_per_min ?? "")}" placeholder="없음" style="width:90px" aria-label="분당 토큰"></td>
      <td><select data-cache="${esc(t.id)}" aria-label="캐시"><option ${t.cache_mode === "isolated" ? "selected" : ""}>isolated</option><option ${t.cache_mode === "shared" ? "selected" : ""}>shared</option></select></td>
      <td>${t.keys.map((k) => `<code>${esc(k.prefix)}…</code> <button class="btn danger small" data-revoke="${esc(k.prefix)}" data-tenant="${esc(t.id)}">폐기</button>`).join("<br>") || "-"}</td>
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
  const card = document.createElement("div");
  card.className = "card";
  card.innerHTML = `<h2>새 API 키</h2><p>▲ 이 키는 다시 볼 수 없습니다. 복사해서 안전한 곳에 저장한 뒤 닫으세요.</p>
    <div class="secret"></div><div class="actions" style="margin-top:12px"><button class="btn secondary">복사</button><button class="btn primary">복사했습니다, 닫기</button></div>`;
  const text = card.querySelector(".secret");
  text.textContent = key;
  const [copy, done] = card.querySelectorAll("button");
  copy.onclick = () => navigator.clipboard?.writeText(text.textContent).then(() => toast("복사했습니다"), () => toast("복사하지 못했습니다", "bad"));
  const close = openModal(card, card.querySelector("h2"), done, () => {
    text.textContent = "";
    loadTenants();
  }, { persistent: true });
  done.onclick = close;
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
        message: `테넌트 ${t.dataset.tenant}의 키 ${t.dataset.revoke}…를 폐기합니다. 이 키를 쓰는 클라이언트는 즉시 접근할 수 없게 되고 되돌릴 수 없습니다.`,
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
    if (inApp) {
      const path = await tauri.invoke("save_csv", { name: "invoice", content: await res.text() });
      toast(`CSV를 저장했습니다: ${path}`);
      return;
    }
    const url = URL.createObjectURL(await res.blob());
    const a = document.createElement("a");
    a.href = url;
    a.download = "invoice.csv";
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    toast("CSV를 내려받았습니다");
  } catch (e) {
    toast("CSV를 저장하지 못했습니다: " + (e && e.message ? e.message : e), "bad");
  }
}

// ---- live screen ----
const LIVE_MS = 2000;
const live = { on: false, timer: null, busy: false, lost: false, last: null, max: 0, enabled: null };

const num = (n, d = 0) =>
  typeof n === "number" && isFinite(n) ? n.toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d }) : "-";
const pctText = (n) => (typeof n === "number" ? `${num(n)} %` : "-");
const LEVEL_ICON = { ok: "●", warn: "▲", bad: "✕", mut: "–" };
const level = (p, warn, bad) => (p == null ? "mut" : p >= bad ? "bad" : p >= warn ? "warn" : "ok");
const timeText = (t) => {
  const d = new Date(t);
  return esc(isNaN(d) ? t : d.toLocaleTimeString("ko-KR", { hour12: false }));
};
const dateTimeText = (t) => {
  const d = new Date(t);
  return esc(isNaN(d) ? t : d.toLocaleString("ko-KR", { hour12: false }));
};
const errsFor = (errors, keys) => keys.filter((k) => errors[k]).map((k) => errorBox(errors[k])).join("");

function barCell(p, kind) {
  const w = Math.max(0, Math.min(100, p));
  return `<div class="barcell"><div class="bar ${kind}" role="img" aria-label="${esc(num(p))} %"><i style="width:${w}%"></i></div><span class="num">${LEVEL_ICON[kind]} ${num(p)} %</span></div>`;
}

function gaugeSvg(p) {
  const r = 58, c = 2 * Math.PI * r, kind = level(p, 70, 90), w = Math.max(0, Math.min(100, p)) / 100;
  return `<svg class="gauge" viewBox="0 0 150 150" role="img" aria-label="용량 사용률 ${num(p)} %">
    <circle class="track" cx="75" cy="75" r="${r}"/>
    <circle class="fill ${kind}" cx="75" cy="75" r="${r}" stroke-dasharray="${(c * w).toFixed(1)} ${c.toFixed(1)}" transform="rotate(-90 75 75)"/>
    <text x="75" y="80" text-anchor="middle" font-size="26" font-weight="700">${num(p)} %</text>
    <text x="75" y="100" text-anchor="middle" font-size="11">${LEVEL_ICON[kind]} ${{ ok: "여유", warn: "주의", bad: "부족", mut: "-" }[kind]}</text></svg>`;
}

function hourChart(series) {
  const W = 300, H = 80, top = Math.max(100, ...series.map((p) => p[1]));
  const t0 = series[0][0], t1 = series[series.length - 1][0], span = Math.max(1, t1 - t0);
  const X = (t) => 4 + ((t - t0) / span) * (W - 8), Y = (v) => H - 14 - (v / top) * (H - 22);
  const pts = series.map(([t, v]) => `${X(t).toFixed(1)},${Y(v).toFixed(1)}`).join(" ");
  return `<svg class="chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="최근 1시간 용량 사용률">
    <line class="axis" x1="4" x2="${W - 4}" y1="${Y(0)}" y2="${Y(0)}"/>
    <line class="edge" x1="4" x2="${W - 4}" y1="${Y(90)}" y2="${Y(90)}"/>
    <text class="edge-t" x="${W - 4}" y="${Y(90) - 3}" text-anchor="end">▲ 90 %</text>
    <polyline class="ln" points="${pts}"/>
    <text x="4" y="${H - 2}">1시간 전</text><text x="${W - 4}" y="${H - 2}" text-anchor="end">지금</text></svg>`;
}

function liveHeadline(s) {
  const h = s.headline || {};
  const tile = (label, v, unit, d = 0) => `<div>${label}<b>${num(v, d)}${v == null ? "" : " " + unit}</b></div>`;
  return tile("동시 요청", h.concurrent, "개") + tile("첫 글자 p99", h.ttft_p99_ms, "ms") +
    tile("토큰 간격 p99", h.itl_p99_ms, "ms", 1) + tile("출력 속도", h.output_tok_s, "토큰/초") +
    tile("전력", h.power_w, "W") + tile("시간당 전기료", h.krw_per_hour, "원");
}

function liveGauge(c, errors) {
  if (errors.capacity || errors.capacity_record) return errsFor(errors, ["capacity", "capacity_record"]);
  const missing = c.missing_text ? `<div class="cap">▲ ${esc(c.missing_text)}</div>` : "";
  if (c.state !== "ok") {
    return `<div class="lab">용량 사용률</div>
      <p><b>▲ 부하 시험을 먼저 돌리세요</b></p>
      <div class="cap">${esc(c.missing_text || "지금 복제본 수와 모델에 맞는 용량 기록이 없습니다")}</div>
      <p><button class="btn primary" data-act="goto-loadtest">부하 시험으로 이동</button></p>`;
  }
  const src = c.source || {};
  return `<div class="row">${gaugeSvg(c.pct)}<div><div class="lab">용량 사용률</div>
    <div class="cap">지금 <b>${num(c.now_tpm)}</b> 토큰/분<br>÷ 최대 <b>${num(c.max_tpm)}</b> 토큰/분<br>
    최대 = 부하 시험(${esc(src.loadtest_id)}, 복제본 ${esc(src.replicas)}개)<br>100 %에 가까우면 거절(429)이 늘어납니다</div>${missing}</div></div>`;
}

function livePeriod(c, errors) {
  if (errors.capacity || errors.capacity_record) return errsFor(errors, ["capacity", "capacity_record"]);
  const p = c.period || {}, series = c.series || [];
  const chart = series.length >= 2 ? hourChart(series) : `<div class="cap">그릴 사용률 기록이 없습니다</div>`;
  return `<div class="lab">최근 1시간</div>${chart}
    <div class="k"><div>최대<b>${pctText(p.max)}</b></div><div>평균<b>${pctText(p.avg)}</b></div><div>90 % 넘은 횟수<b>${num(p.over_edge)}</b></div></div>`;
}

function liveSignals(c, errors) {
  if (errors.signals) return errsFor(errors, ["signals"]);
  const g = c.signals || {};
  const idle = g.idle_replicas == null ? "-" : `${num(g.idle_replicas)} / ${num(g.ready_replicas)}`;
  return `<div class="lab">용량 부족 신호</div>
    <div class="k"><div>한도로 거절(429)<b>${num(g.rejected_429_1h)}</b></div><div>대기 중 요청<b>${num(g.waiting)}</b></div><div>놀고 있는 복제본<b>${idle}</b></div></div>
    <div class="cap" style="margin-top:8px">놀고 있는 복제본 = 5분 넘게 처리한 요청이 없는 복제본</div>`;
}

function liveTenants(s) {
  return (s.tenants || []).map((t) => {
    const cell = t.pct == null ? `<span class="mut">한도 없음</span>` : barCell(t.pct, level(t.pct, 70, 95));
    const limit = t.limit_tpm == null ? "-" : num(t.limit_tpm);
    return `<tr><td>${esc(t.tenant)}</td><td class="num">${num(t.tokens_last_min)} / ${limit}</td><td>${cell}</td><td class="num">${num(t.throttled_1h)}</td></tr>`;
  }).join("") || rowMessage(4, emptyState("고객이 없습니다"));
}

const PHASE = { Running: ["ok", "실행 중"], Pending: ["warn", "대기"] };

function liveReplicas(s) {
  return (s.replicas || []).map((r) => {
    const [kind, text] = PHASE[r.status] || (r.status === "Failed" ? ["bad", "실패"] : ["mut", r.status]);
    const kv = r.kv == null ? "-" : barCell(r.kv, level(r.kv, 75, 90));
    const hit = r.hit_rate == null ? "-" : `${num(r.hit_rate * 100)} %`;
    const gpu = r.gpu_util == null ? "-" : `${num(r.gpu_util)} %`;
    return `<tr><td>${esc(r.pod)}</td><td>${badge(kind, text)}</td><td class="num">${num(r.running)} / ${num(r.waiting)}</td><td>${kv}</td><td class="num">${hit}</td><td class="num">${gpu}</td>
      <td><button class="btn danger small" data-act="kill" data-pod="${esc(r.pod)}">끄기</button></td></tr>`;
  }).join("") || rowMessage(7, emptyState("복제본이 없습니다"));
}

function liveRecs(s) {
  return (s.recommendations || []).map((r) => {
    const btn = r.kind === "tenant_limit"
      ? `<button class="btn secondary small" data-act="open-tenants">테넌트 화면에서 한도 보기</button>` : "";
    return `<div class="row">${badge("warn", r.text)}${btn}</div>`;
  }).join("") || `<div class="mut">${badge("ok", "지금 권장할 조치가 없습니다")}</div>`;
}

const DECISION = { up: ["warn", "늘림"], down: ["mut", "줄임"], hold: ["mut", "유지"], ready: ["ok", "준비 완료"] };

function liveDecisions(s) {
  return ((s.autoscale || {}).decisions || []).map((d) => {
    const [kind, text] = DECISION[d.action] || ["mut", d.action];
    const span = d.from_replicas == null && d.to_replicas == null ? "-" : `${d.from_replicas ?? "?"} → ${d.to_replicas ?? "?"}`;
    const took = d.action === "ready" && d.ready_after_s != null ? ` (${num(d.ready_after_s)}초 걸림)` : "";
    return `<tr><td>${timeText(d.t)}</td><td>${badge(kind, text)}</td><td class="num">${esc(span)}</td><td>${esc(d.reason)}${esc(took)}</td></tr>`;
  }).join("") || rowMessage(4, emptyState("아직 결정 기록이 없습니다"));
}

function liveAuto(s) {
  const en = (s.autoscale || {}).enabled, max = ((s.capacity || {}).signals || {}).max_replicas;
  const sw = en == null
    ? `<button class="btn switch" role="switch" aria-checked="false" disabled>자동 증감 – 설정을 읽지 못했습니다</button>`
    : `<button class="btn switch" role="switch" data-act="toggle-auto" aria-checked="${en}">${en ? "● 자동 증감 켜짐" : "– 자동 증감 꺼짐"}</button>`;
  const opts = Array.from({ length: max || 0 }, (_, i) => `<option value="${i + 1}">${i + 1}개</option>`).join("");
  const manual = max ? `<span class="mut">수동 조정</span><select id="live-count" aria-label="복제본 수">${opts}</select><button class="btn secondary" data-act="scale">적용</button>` : "";
  return sw + manual;
}

function renderLive(s) {
  const errors = s.errors || {}, c = s.capacity || {};
  live.max = (c.signals || {}).max_replicas || 0;
  live.enabled = (s.autoscale || {}).enabled;
  $("live-head").innerHTML = liveHeadline(s);
  $("live-head-err").innerHTML = errsFor(errors, ["headline", "power_w"]);
  $("live-gauge").innerHTML = liveGauge(c, errors);
  $("live-period").innerHTML = livePeriod(c, errors);
  $("live-signals").innerHTML = liveSignals(c, errors);
  $("live-tenants").innerHTML = liveTenants(s);
  $("live-tenants-err").innerHTML = errsFor(errors, ["tenants"]);
  $("live-replicas").innerHTML = liveReplicas(s);
  $("live-replicas-err").innerHTML = errsFor(errors, ["replicas"]);
  $("live-recs").innerHTML = liveRecs(s);
  $("live-auto").innerHTML = liveAuto(s);
  $("live-auto-err").innerHTML = errsFor(errors, ["autoscale"]);
  $("live-decisions").innerHTML = liveDecisions(s);
}

const LT_COLORS = ["var(--c1)", "var(--c2)", "var(--c3)", "var(--c4)"];
const LT_DASH = ["", "6 3", "2 3", "8 3 2 3"];

function loadtestChart(runs) {
  const shown = runs.filter((r) => (r.stages || []).length).slice(0, 4);
  if (!shown.length) return "";
  const W = 360, H = 170, L = 38, B = 22, T = 8, R = 8;
  const users = Math.max(...shown.flatMap((r) => r.stages.map((x) => x.users)));
  const top = Math.max(1000, ...shown.flatMap((r) => r.stages.map((x) => x.ttft_p99_ms)));
  const X = (u) => L + (u / users) * (W - L - R), Y = (v) => H - B - (v / top) * (H - B - T);
  const lines = shown.map((r, i) => {
    const pts = r.stages.map((x) => `${X(x.users).toFixed(1)},${Y(x.ttft_p99_ms).toFixed(1)}`).join(" ");
    return `<polyline class="ln" style="stroke:${LT_COLORS[i]}" stroke-dasharray="${LT_DASH[i]}" points="${pts}"/>`;
  }).join("");
  const legend = shown.map((r, i) =>
    `<span><svg viewBox="0 0 28 8" aria-hidden="true"><path d="M0 4h28" stroke="${LT_COLORS[i]}" stroke-dasharray="${LT_DASH[i]}"/></svg>${esc(r.id)} · 복제본 ${esc(r.replicas)}개${r.autoscale_on ? " · 자동 증감 켬" : ""}</span>`).join("");
  return `<svg class="chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="동시 사용자 대 첫 글자 p99">
    <line class="axis" x1="${L}" x2="${W - R}" y1="${Y(0)}" y2="${Y(0)}"/><line class="axis" x1="${L}" x2="${L}" y1="${T}" y2="${Y(0)}"/>
    <line class="edge" x1="${L}" x2="${W - R}" y1="${Y(1000)}" y2="${Y(1000)}"/><text class="edge-t" x="${W - R}" y="${Y(1000) - 3}" text-anchor="end">▲ 1초</text>
    <text x="${L - 4}" y="${Y(0)}" text-anchor="end">0</text><text x="${L - 4}" y="${T + 8}" text-anchor="end">${num(top)} ms</text>
    <text x="${X(users)}" y="${H - 6}" text-anchor="end">동시 사용자 ${num(users)}명</text>${lines}</svg>
    <div class="legend">${legend}</div><div class="mut">동시 사용자 대 첫 글자 p99 (ms)</div>`;
}

function renderLoadtests(runs) {
  const running = runs.find((r) => r.running);
  $("lt-progress").innerHTML = running
    ? `<div class="infobox">▲ 진행 중: ${esc(running.id)} · 복제본 ${esc(running.replicas)}개 · 끝낸 단계 ${(running.stages || []).map((x) => `${esc(x.users)}명`).join(", ") || "없음"}</div>`
    : "";
  $("lt-start").disabled = Boolean(running);
  $("lt-chart").innerHTML = loadtestChart(runs);
  $("lt-list").innerHTML = runs.slice(0, 5).map((r) => {
    const st = r.error ? badge("bad", "오류: " + r.error) : r.running ? badge("warn", "진행 중")
      : r.capacity_tpm == null ? badge("mut", "기준 만족 단계 없음") : badge("ok", "완료");
    const cap = r.capacity_tpm == null ? "-" : `${num(r.capacity_tpm)} 토큰/분`;
    return `<div class="row" style="margin-top:8px"><b>${esc(r.id)}</b><span class="mut">${dateTimeText(r.created_at)}</span><span>복제본 ${esc(r.replicas)}개 · 자동 증감 ${r.autoscale_on ? "켬" : "끔"}</span><span>${cap}</span>${st}</div>`;
  }).join("") || emptyState("아직 부하 시험 기록이 없습니다");
}

function liveConn(ok) {
  live.lost = !ok;
  setConn(ok ? "ok" : "bad", ok ? "연결됨" : "연결 끊김");
  const stale = $("live-stale");
  stale.hidden = ok;
  stale.textContent = ok ? "" : "✕ 연결 끊김 · 마지막 값을 보여 줍니다";
}

async function liveTick() {
  if (live.busy) return;
  live.busy = true;
  try {
    const snap = await call("/api/live");
    live.last = snap;
    $("live-msg").innerHTML = "";
    renderLive(snap);
    liveConn(true);
  } catch (e) {
    liveConn(false);
    if (!live.last) $("live-msg").innerHTML = errorBox(e.message);
  }
  try {
    renderLoadtests(await call("/api/loadtests"));
  } catch (e) {
    $("lt-list").innerHTML = errorBox(e.message);
  }
  live.busy = false;
}

function liveSchedule() {
  clearTimeout(live.timer);
  live.timer = null;
  if (!live.on || document.hidden) return;
  live.timer = setTimeout(async () => {
    await liveTick();
    liveSchedule();
  }, LIVE_MS);
}

async function liveStart() {
  live.on = true;
  await liveTick();
  liveSchedule();
}

function liveStop() {
  live.on = false;
  clearTimeout(live.timer);
  live.timer = null;
}

async function liveAct(e) {
  const t = e.target.closest("[data-act]");
  if (!t) return;
  const act = t.dataset.act;
  try {
    let r = CANCELLED;
    if (act === "goto-loadtest") {
      $("lt-card").scrollIntoView({ block: "start" });
      $("lt-users").focus();
      return;
    }
    if (act === "open-tenants") {
      location.hash = "#tenants";
      return;
    }
    if (act === "kill") {
      r = await mutate(`/api/replicas/${encodeURIComponent(t.dataset.pod)}/delete`, {}, {
        title: "복제본 끄기",
        message: `복제본 ${t.dataset.pod} 1개를 삭제합니다. 배포가 새 복제본을 다시 만들지만 준비될 때까지 이 복제본의 용량(약 1/${live.max || "?"})만큼 줄고, 처리 중인 요청은 끊깁니다.`,
        confirmText: "끄기",
        danger: true,
      });
    } else if (act === "toggle-auto") {
      const on = !live.enabled;
      r = await mutate("/api/autoscale", { enabled: on }, {
        title: on ? "자동 증감 켜기" : "자동 증감 끄기",
        message: on
          ? `켜면 사용률에 따라 복제본 수를 1~${live.max}개 사이에서 자동으로 늘리고 줄입니다.`
          : "끄면 지금 복제본 수를 그대로 유지하고, 늘리거나 줄이는 일은 수동으로만 합니다.",
        confirmText: on ? "켜기" : "끄기",
      });
    } else if (act === "scale") {
      const n = Number($("live-count").value);
      r = await mutate("/api/replicas", { count: n }, {
        title: "복제본 수 조정",
        message: `vLLM 복제본을 ${n}개로 맞춥니다 (허용 범위 1~${live.max}개). GPU를 ${n}장분 점유하고, 새 복제본은 모델을 불러올 때까지 요청을 받지 못합니다.`,
        confirmText: "적용",
      });
    }
    if (r !== CANCELLED) {
      toast("요청했습니다");
      liveTick();
    }
  } catch (err) {
    toast(err.message, "bad");
  }
}

async function startLoadtest() {
  const users = Number($("lt-users").value), on = $("lt-auto").checked;
  try {
    const r = await mutate("/api/loadtest", { max_users: users, autoscale_on: on }, {
      title: "부하 시험 시작",
      message: `동시 사용자를 1명부터 최대 ${users}명까지 단계별로 늘리며 단계마다 15초씩 실제 요청을 보냅니다. 지금 떠 있는 복제본 전체가 GPU를 쓰고 일반 요청은 느려질 수 있습니다. ` +
        (on ? "자동 증감을 켠 채로 시험하므로 결과는 용량 기록에 저장되지 않습니다." : "끝나면 현재 복제본 수의 용량 기록으로 저장됩니다."),
      confirmText: "시작",
    });
    if (r === CANCELLED) return;
    setMsg("lt-msg", "info", "시작했습니다: " + r.id);
    liveTick();
  } catch (err) {
    setMsg("lt-msg", "err", err.message);
  }
}

function initPanels() {
  ensureToastBox();
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
  $("panel-live").addEventListener("click", liveAct);
  $("lt-start").onclick = startLoadtest;
  document.addEventListener("visibilitychange", () => {
    if (!live.on) return;
    if (document.hidden) liveSchedule();
    else liveStart();
  });
  onShow.live = liveStart;
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
