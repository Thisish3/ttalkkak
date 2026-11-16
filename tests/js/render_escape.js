// Runs desktop/web/app.js against a minimal DOM stub and checks that hostile
// server strings never reach innerHTML unescaped. Prints "OK" on success.
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
const routes = {
  "/api/ops/tenants": [{ id: EVIL, name: EVIL, token_limit_per_min: EVIL, cache_mode: "isolated", keys: [{ prefix: EVIL }] }],
  "/api/ops/cost": {
    price_krw_per_mtok: { output: 1, input: 1, cached_input: 1 },
    cost_krw_per_mtok: { output: 1, input: 1, cached_input: 1 },
    operating_point: { streams: EVIL, output_tok_s: 1 }, gpus_needed: EVIL, warnings: [EVIL],
  },
  "/api/ops/status": { pods: [{ name: EVIL, ready: EVIL, restarts: 1, phase: EVIL }] },
  "/api/ops/usage": [{ tenant: EVIL, model: EVIL, requests: 1, input_tokens: 1, cached_input_tokens: 1, output_tokens: 1 }],
};
const ctx = {
  window: { addEventListener() {} },
  location: { hash: "", host: "x" },
  navigator: {},
  console,
  document: {
    getElementById: (id) => (id === "nav" ? null : el(id)),
    querySelectorAll: () => [],
    createElement: () => el("created"),
    body: el("body"), addEventListener() {}, removeEventListener() {},
  },
  Response,
  fetch: async (p) => {
    const key = String(p).split("?")[0];
    if (!(key in routes)) return new Response("null", { status: 500 });
    return new Response(JSON.stringify(routes[key]), { status: 200 });
  },
};
vm.createContext(ctx);
vm.runInContext(src, ctx);
const run = (code) => vm.runInContext(code, ctx);

(async () => {
  const fail = (m) => { console.error("FAIL: " + m); process.exit(1); };
  if (run(`esc(${JSON.stringify(EVIL)})`).includes("<")) fail("esc leaves <");
  await run("loadTenants()");
  await run("loadCost()");
  await run("loadOverview()");
  await run("loadUsage()");
  for (const id of ["t-rows", "cost-out", "ov-rows", "u-rows"]) {
    const html = els[id].innerHTML;
    if (!html || html.includes("<img")) fail(`${id} not escaped: ${html.slice(0, 120)}`);
  }
  console.log("OK");
})().catch((e) => { console.error(e); process.exit(1); });
