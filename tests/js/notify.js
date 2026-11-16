// Decision -> notification mapping and 429 spike debounce of desktop/web/notify.js.
// Prints "OK" on success.
const fs = require("fs");
const vm = require("vm");
const path = require("path");

const src = fs.readFileSync(path.join(__dirname, "../../desktop/web/notify.js"), "utf8");
const ctx = { console: { error() {} } };
vm.createContext(ctx);
vm.runInContext(src, ctx);
const fail = (m) => { console.error("FAIL: " + m); process.exit(1); };
const eq = (a, b, m) => { if (JSON.stringify(a) !== JSON.stringify(b)) fail(`${m}: ${JSON.stringify(a)} != ${JSON.stringify(b)}`); };

let clock = 0, on = true;
const sent = [];
const make = () => {
  sent.length = 0; clock = 0; on = true;
  ctx.SEND = (title, body) => sent.push([title, body]);
  ctx.NOW = () => clock;
  ctx.ON = () => on;
  return vm.runInContext("createNotifier({ send: SEND, now: NOW, enabled: ON })", ctx);
};
const dec = (t, action, from, to, reason = "사유") => ({ t, action, from_replicas: from, to_replicas: to, reason });
const snap = (decisions, rejected, tenantRejected) => ({
  autoscale: { decisions },
  capacity: { signals: { rejected_429_1h: rejected } },
});

// the decision history present at first sight is never announced
let n = make();
n.observe(snap([dec("t1", "up", 2, 3)], 0));
eq(sent.length, 0, "history at start");

// a new up / down decision notifies once, with the replica change and reason
n.observe(snap([dec("t2", "up", 3, 4, "사용률 90 % 넘음"), dec("t1", "up", 2, 3)], 0));
eq(sent.length, 1, "new up");
if (!sent[0][0].includes("늘") || !sent[0][1].includes("3 → 4") || !sent[0][1].includes("사용률 90 % 넘음")) fail("up text: " + sent[0]);
n.observe(snap([dec("t2", "up", 3, 4, "사용률 90 % 넘음"), dec("t1", "up", 2, 3)], 0));
eq(sent.length, 1, "same decision polled again");
n.observe(snap([dec("t3", "down", 4, 3, "여유"), dec("t2", "up", 3, 4)], 0));
eq(sent.length, 2, "new down");
if (!sent[1][0].includes("줄")) fail("down title: " + sent[1]);

// hold / ready are not notified
n.observe(snap([dec("t5", "ready", 3, 3), dec("t4", "hold", null, null), dec("t3", "down", 4, 3)], 0));
eq(sent.length, 2, "hold/ready silent");

// null replicas on an up row do not print null
n = make();
n.observe(snap([], 0));
n.observe(snap([dec("t9", "up", null, null)], 0));
if (sent[0][1].includes("null") || sent[0][1].includes("undefined")) fail("null in text: " + sent[0]);

// switch off: nothing is sent, and what happened meanwhile is not replayed when switched on
n = make();
n.observe(snap([], 0));
on = false;
n.observe(snap([dec("a", "up", 1, 2)], 0));
on = true;
n.observe(snap([dec("a", "up", 1, 2)], 0));
eq(sent.length, 0, "off then on must not replay");

// 429 spike: rise of the 1 h count by >= 10 within 60 s
n = make();
for (let i = 0; i < 4; i++) { clock = i * 2000; n.observe(snap([], 100)); }
eq(sent.length, 0, "flat count");
clock = 10000; n.observe(snap([], 105));
eq(sent.length, 0, "rise of 5 is below the threshold");
clock = 12000; n.observe(snap([], 112));
eq(sent.length, 1, "rise of 12 in 12 s");
if (!sent[0][0].includes("429") || !sent[0][1].includes("12")) fail("spike text: " + sent[0]);
// debounce: staying high or rising further stays silent
clock = 14000; n.observe(snap([], 130));
clock = 16000; n.observe(snap([], 160));
eq(sent.length, 1, "one event, one notification");
// quiet for a while, then a second spike after the cooldown notifies again
for (let t = 20000; t <= 100000; t += 2000) { clock = t; n.observe(snap([], 160)); }
clock = 110000; n.observe(snap([], 175));
eq(sent.length, 1, "second spike inside the cooldown is merged");
for (let t = 112000; t <= 400000; t += 2000) { clock = t; n.observe(snap([], 175)); }
clock = 410000; n.observe(snap([], 190));
eq(sent.length, 2, "spike after rearm + cooldown");

// the 1 h window sliding (count falls) never counts as a spike
n = make();
clock = 0; n.observe(snap([], 200));
clock = 2000; n.observe(snap([], 50));
clock = 4000; n.observe(snap([], 55));
eq(sent.length, 0, "falling count");

// a missing count (null / no capacity) is ignored, not read as 0
n = make();
clock = 0; n.observe(snap([], 40));
clock = 2000; n.observe({ autoscale: { decisions: [] }, capacity: null });
clock = 4000; n.observe(snap([], 41));
eq(sent.length, 0, "null count");
n.observe(null);
n.observe({});

// a send that throws never breaks the poll loop
n = make();
ctx.SEND = () => { throw new Error("denied"); };
n = vm.runInContext("createNotifier({ send: SEND, now: NOW, enabled: ON })", ctx);
n.observe(snap([], 0));
n.observe(snap([dec("z", "up", 1, 2)], 0));

// settings persisted through a storage object; default on
const store = {};
ctx.STORE = { getItem: (k) => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = String(v); } };
eq(vm.runInContext("notifyEnabled(STORE)", ctx), true, "default on");
vm.runInContext("setNotifyEnabled(STORE, false)", ctx);
eq(vm.runInContext("notifyEnabled(STORE)", ctx), false, "persisted off");
vm.runInContext("setNotifyEnabled(STORE, true)", ctx);
eq(vm.runInContext("notifyEnabled(STORE)", ctx), true, "persisted on");
ctx.BROKEN = { getItem() { throw new Error("x"); }, setItem() { throw new Error("x"); } };
eq(vm.runInContext("notifyEnabled(BROKEN)", ctx), true, "broken storage -> on");
vm.runInContext("setNotifyEnabled(BROKEN, false)", ctx);

console.log("OK");
