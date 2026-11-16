// Runs the live-screen render functions of desktop/web/app.js against a DOM stub
// with fixture snapshots. Prints "OK" on success.
const fs = require("fs");
const vm = require("vm");
const path = require("path");

const src = fs.readFileSync(path.join(__dirname, "../../desktop/web/app.js"), "utf8");
const EVIL = '<img src=x onerror=alert(1)>';
const els = {};
const el = (id) =>
  (els[id] ||= {
    id, innerHTML: "", textContent: "", value: "", dataset: {}, hidden: false,
    className: "", classList: { toggle() {}, add() {}, remove() {} },
    querySelector: () => el(id + ">q"), querySelectorAll: () => [],
    addEventListener() {}, append() {}, remove() {}, setAttribute() {}, focus() {},
  });
const timers = [];
const ctx = {
  window: { addEventListener() {} },
  location: { hash: "#live", host: "x" },
  navigator: {},
  console,
  setTimeout: (fn, ms) => (timers.push({ fn, ms }), timers.length),
  clearTimeout() {},
  document: {
    hidden: false,
    getElementById: (id) => (id === "nav" ? null : el(id)),
    querySelectorAll: () => [],
    createElement: () => el("created"),
    body: el("body"), addEventListener() {}, removeEventListener() {},
  },
  Response,
};
vm.createContext(ctx);
vm.runInContext(src, ctx);
const run = (code) => vm.runInContext(code, ctx);
const put = (name, v) => { ctx[name] = v; };

const fail = (m) => { console.error("FAIL: " + m); process.exit(1); };
const has = (id, text) => { if (!els[id].innerHTML.includes(text)) fail(`${id} lacks "${text}": ${els[id].innerHTML.slice(0, 300)}`); };
const hasNot = (id, text) => { if (els[id].innerHTML.includes(text)) fail(`${id} must not contain "${text}"`); };
const reset = () => { for (const k of Object.keys(els)) delete els[k]; };

const series = Array.from({ length: 20 }, (_, i) => [1700000000 + i * 30, 20 + ((i * 7) % 70)]);
const base = () => ({
  headline: { concurrent: 4, ttft_p99_ms: 412.4, itl_p99_ms: 21, output_tok_s: 830, power_w: 300, krw_per_hour: 63 },
  capacity: {
    state: "ok", pct: 42.8, now_tpm: 9200, max_tpm: 21500,
    source: { loadtest_id: "20261114-031000", created_at: "2026-11-14T03:10:00+00:00", replicas: 3, model: "m" },
    period: { max: 83, avg: 38, over_edge: 2 }, series,
    missing_loadtest: [], missing_text: null,
    signals: { rejected_429_1h: 14, waiting: 1, idle_replicas: 0, ready_replicas: 3, max_replicas: 3 },
  },
  tenants: [{ tenant: "acme", tokens_last_min: 5800, limit_tpm: 6000, pct: 96.7, throttled_1h: 14 },
            { tenant: "free", tokens_last_min: 10, limit_tpm: null, pct: null, throttled_1h: 0 }],
  replicas: [{ pod: "vllm-abc", status: "Running", running: 6, waiting: 0, kv: 41, hit_rate: 0.62, gpu_util: 71 }],
  recommendations: [{ kind: "tenant_limit", tenant: "acme", text: "acme 고객이 한도를 다 쓰고 있습니다" }],
  autoscale: { enabled: true, decisions: [
    { t: "2026-11-16T01:00:00+00:00", action: "ready", from_replicas: 2, to_replicas: 3, reason: "복제본 준비", ready_after_s: 87 },
    { t: "2026-11-16T00:58:00+00:00", action: "up", from_replicas: 2, to_replicas: 3, reason: "사용률 90 % 넘음" },
    { t: "2026-11-16T00:50:00+00:00", action: "hold", from_replicas: null, to_replicas: null, reason: "Prometheus 오류" },
  ] },
  errors: {},
});
const render = (s) => { put("SNAP", s); run("renderLive(SNAP)"); };

(async () => {
  // normal
  render(base());
  has("live-head", "412"); has("live-head", "830");
  has("live-gauge", "43 %"); has("live-gauge", "9,200"); has("live-gauge", "21,500");
  has("live-period", "83"); has("live-period", "<svg");
  has("live-signals", "14");
  has("live-tenants", "acme"); has("live-tenants", "✕"); has("live-tenants", "한도 없음");
  has("live-replicas", "vllm-abc"); has("live-replicas", "끄기"); has("live-replicas", "62 %");
  has("live-recs", "acme");
  has("live-decisions", "87"); has("live-decisions", "준비 완료"); has("live-decisions", "2 → 3");
  has("live-decisions", "늘림");
  hasNot("live-gauge", "부하 시험을 먼저");

  // null replicas on a hold row render a dash, never "null"
  hasNot("live-decisions", "null"); hasNot("live-decisions", "undefined");

  // needs_loadtest
  const nl = base();
  Object.assign(nl.capacity, { state: "needs_loadtest", pct: null, max_tpm: null, source: null, series: [],
    period: { max: null, avg: null, over_edge: null },
    missing_loadtest: [2], missing_text: "용량 기록 없음: 복제본 2개 부하 시험 필요" });
  render(nl);
  has("live-gauge", "부하 시험을 먼저 돌리세요");
  has("live-gauge", "용량 기록 없음: 복제본 2개 부하 시험 필요");
  has("live-gauge", 'data-act="goto-loadtest"');
  hasNot("live-gauge", "<circle");

  // per-card errors: only the affected card shows the error box
  const er = base();
  er.errors = { replicas: "파드 상태 " + EVIL, capacity: "용량 " + EVIL, tenants: "고객 " + EVIL };
  render(er);
  has("live-replicas-err", "errbox"); has("live-replicas-err", "&lt;img");
  has("live-tenants-err", "errbox");
  has("live-gauge", "errbox"); has("live-period", "errbox");
  hasNot("live-gauge", "<circle");
  if (els["live-auto-err"].innerHTML.includes("errbox")) fail("autoscale card got an error box");
  if (els["live-head-err"].innerHTML.includes("errbox")) fail("headline card got an error box");

  // hostile server strings are escaped everywhere
  const ev = base();
  ev.tenants[0].tenant = EVIL;
  ev.replicas[0].pod = EVIL; ev.replicas[0].status = EVIL;
  ev.recommendations = [{ kind: "tenant_limit", tenant: EVIL, text: EVIL }, { kind: EVIL, text: EVIL }];
  ev.autoscale.decisions[1].reason = EVIL; ev.autoscale.decisions[1].t = EVIL; ev.autoscale.decisions[1].action = EVIL;
  ev.capacity.missing_text = EVIL; ev.capacity.source.model = EVIL; ev.capacity.state = "needs_loadtest";
  render(ev);
  for (const id of ["live-head", "live-gauge", "live-period", "live-signals", "live-tenants", "live-replicas", "live-recs", "live-auto", "live-decisions"]) {
    if (els[id].innerHTML.includes("<img")) fail(`${id} not escaped`);
  }

  // null enabled
  const un = base(); un.autoscale.enabled = null;
  render(un);
  has("live-auto", "disabled");

  // load tests
  const runs = [
    { id: EVIL, created_at: "2026-11-14T03:10:00+00:00", replicas: 3, model: "m", autoscale_on: false, capacity_tpm: 21500, error: null, running: false,
      stages: [{ users: 1, ttft_p99_ms: 120 }, { users: 5, ttft_p99_ms: 300 }, { users: 20, ttft_p99_ms: 1400 }] },
    { id: "20261115-010000", created_at: "2026-11-15T01:00:00+00:00", replicas: 2, model: "m", autoscale_on: true, capacity_tpm: null, error: "guidellm " + EVIL, running: false,
      stages: [{ users: 1, ttft_p99_ms: 150 }, { users: 5, ttft_p99_ms: 500 }] },
    { id: "20261116-020000", created_at: "2026-11-16T02:00:00+00:00", replicas: 2, model: "m", autoscale_on: false, capacity_tpm: null, error: null, running: true,
      stages: [{ users: 1, ttft_p99_ms: 100 }] },
  ];
  put("RUNS", runs);
  run("renderLoadtests(RUNS)");
  has("lt-chart", "<polyline"); has("lt-chart", "첫 글자 p99");
  has("lt-progress", "진행 중"); has("lt-progress", "1명");
  has("lt-list", "21,500"); has("lt-list", "&lt;img");
  for (const id of ["lt-chart", "lt-progress", "lt-list"]) if (els[id].innerHTML.includes("<img")) fail(`${id} not escaped`);
  put("NONE", []);
  run("renderLoadtests(NONE)");
  has("lt-list", "아직 부하 시험 기록이 없습니다");

  // polling: runs while visible, schedules nothing while hidden, shows 연결 끊김 on failure
  run("live.on = true");
  timers.length = 0;
  ctx.document.hidden = true;
  run("liveSchedule()");
  if (timers.length) fail("scheduled a refresh while the window is hidden");
  ctx.document.hidden = false;
  run("liveSchedule()");
  if (timers.length !== 1 || timers[0].ms !== 2000) fail("expected one 2000 ms timer");
  reset();
  render(base());
  const kept = els["live-head"].innerHTML;
  run("liveConn(false)");
  if (!els["conn"].textContent.includes("연결 끊김")) fail("status bar lacks 연결 끊김");
  if (els["live-head"].innerHTML !== kept) fail("last values were not kept");
  run("liveConn(true)");
  if (!els["conn"].textContent.includes("연결됨")) fail("status bar not restored");
  console.log("OK");
})().catch((e) => { console.error(e); process.exit(1); });
