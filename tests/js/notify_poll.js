// Background notification poll, permission handling and the toggle in app.js,
// run with the Tauri globals present (app mode) or absent (browser mode).
const fs = require("fs");
const vm = require("vm");
const path = require("path");

const read = (f) => fs.readFileSync(path.join(__dirname, "../../desktop/web", f), "utf8");
const fail = (m) => { console.error("FAIL: " + m); process.exit(1); };

function build({ app }) {
  const els = {};
  const el = (id) =>
    (els[id] ||= {
      id, innerHTML: "", textContent: "", value: "", dataset: {}, hidden: false, checked: false,
      disabled: false, className: "", classList: { toggle() {}, add() {}, remove() {} },
      querySelector: () => el(id + ">q"), querySelectorAll: () => [],
      addEventListener() {}, append() {}, remove() {}, setAttribute() {}, focus() {},
    });
  els['notif-row'] = { ...el('notif-row'), hidden: true };
  const store = {};
  const timers = [];
  const notes = { sent: [], perm: "default", requests: 0 };
  const ctx = {
    window: { addEventListener() {} },
    location: { hash: "#overview", host: "x" },
    navigator: {},
    console: { error() {}, warn() {}, log: console.log },
    localStorage: { getItem: (k) => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = String(v); } },
    setTimeout: () => 0, clearTimeout() {},
    setInterval: (fn, ms) => (timers.push({ fn, ms }), timers.length),
    document: { hidden: false, getElementById: (id) => (id === "nav" ? null : el(id)), querySelectorAll: () => [],
      createElement: () => el("created"), body: el("body"), addEventListener() {}, removeEventListener() {} },
    Response, AbortController, fetch: () => { throw new Error("no fetch"); },
  };
  if (app) {
    ctx.window.__TAURI__ = {
      core: { invoke: async () => { throw new Error("no invoke"); } },
      notification: {
        isPermissionGranted: async () => notes.perm === "granted",
        requestPermission: async () => { notes.requests++; return notes.perm === "denied" ? "denied" : (notes.perm = "granted"); },
        sendNotification: (n) => notes.sent.push(n),
      },
    };
  }
  vm.createContext(ctx);
  vm.runInContext(read("notify.js") + "\n" + read("app.js"), ctx);
  return { ctx, els, el, store, timers, notes, run: (c) => vm.runInContext(c, ctx) };
}

const snap = (decisions, n429 = 0) => ({ autoscale: { decisions }, capacity: { signals: { rejected_429_1h: n429 } } });
const up = (t) => ({ t, action: "up", from_replicas: 2, to_replicas: 3, reason: "사용률 90 % 넘음" });
const tick = () => new Promise((r) => setImmediate(r));

(async () => {
  // browser mode: no toggle, no poll
  let b = build({ app: false });
  b.run("initNotifications()");
  if (!b.el("notif-row").hidden) fail("toggle visible in browser mode");
  if (b.timers.length) fail("background poll started in browser mode");

  // app mode
  let a = build({ app: true });
  a.run("initNotifications()");
  if (a.el("notif-row").hidden) fail("toggle hidden in app mode");
  if (!a.el("notif").checked) fail("default is on");
  if (a.timers.length !== 1 || a.timers[0].ms !== 15000) fail("expected one 15 s background poll");

  // hidden window + other tab: a new up decision notifies once; liveTick and the poll share state
  let live = snap([]);
  a.ctx.API = async (p) => {
    if (p !== "/api/live") throw new Error("unexpected " + p);
    return new Response(JSON.stringify(live), { status: 200 });
  };
  a.run("api = API");
  a.ctx.document.hidden = true;
  a.run("live.on = false");
  await a.run("notifyPoll()");
  live = snap([up("2026-11-16T01:00:00+00:00")]);
  await a.run("notifyPoll()"); await tick();
  if (a.notes.sent.length !== 1) fail("background poll did not notify: " + a.notes.sent.length);
  a.ctx.document.hidden = false;
  a.run("live.on = true; live.ltAt = Infinity");
  await a.run("liveTick()"); await a.run("notifyPoll()"); await tick();
  if (a.notes.sent.length !== 1) fail("same event notified twice: " + a.notes.sent.length);

  // the poll skips while the live screen is polling visibly, and while notifications are off
  let calls = 0;
  a.ctx.API = async () => { calls++; return new Response(JSON.stringify(live), { status: 200 }); };
  a.run("api = API");
  await a.run("notifyPoll()");
  if (calls) fail("poll ran while the live screen is polling");
  a.run("live.on = false");
  a.el("notif").checked = false;
  await a.run("notifyPoll()");
  if (calls) fail("poll ran while notifications are off");

  // a failing poll never throws
  a.el("notif").checked = true;
  a.ctx.API = async () => { throw new Error("down"); };
  a.run("api = API");
  await a.run("notifyPoll()");

  // M1: denied permission is asked once, then the toggle says 권한 없음
  let d = build({ app: true });
  d.notes.perm = "denied";
  d.run("initNotifications()");
  await d.run("sendOsNotification('a', 'b')");
  await d.run("sendOsNotification('a', 'b')");
  if (d.notes.requests !== 1) fail("permission asked " + d.notes.requests + " times");
  if (!d.el("notif-text").textContent.includes("권한 없음")) fail("toggle lacks 권한 없음");
  if (d.notes.sent.length) fail("sent without permission");

  // a granted permission sends the notification
  let g = build({ app: true });
  g.notes.perm = "granted";
  g.run("initNotifications()");
  await g.run("sendOsNotification('제목', '내용')");
  if (g.notes.sent.length !== 1 || g.notes.sent[0].title !== "제목") fail("granted send");

  // toggle persists
  g.el("notif").checked = false;
  g.el("notif").onchange();
  if (g.store["ttalkkak.notifications"] !== "off") fail("toggle not persisted");
  console.log("OK");
})().catch((e) => { console.error(e); process.exit(1); });
