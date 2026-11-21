// Frontend smoke test: run static/app.js under a minimal DOM stub in node,
// driving real user flows against a live server.
// Usage: node tests/frontend_smoke.mjs <port>
import { readFileSync } from "fs";

const PORT = process.argv[2] || "8000";
const BASE = `http://127.0.0.1:${PORT}`;

// ---------------------------------------------------------------------------
// minimal DOM stub
// ---------------------------------------------------------------------------
class ClassList {
  constructor(owner) { this.owner = owner; this.set = new Set(); }
  add(...cs) { cs.forEach(c => this.set.add(c)); this.owner._syncClass(); }
  remove(...cs) { cs.forEach(c => this.set.delete(c)); this.owner._syncClass(); }
  toggle(c, force) {
    const want = force === undefined ? !this.set.has(c) : force;
    want ? this.set.add(c) : this.set.delete(c);
    this.owner._syncClass();
    return want;
  }
  contains(c) { return this.set.has(c); }
}

class El {
  constructor(tag) {
    this.tagName = tag.toUpperCase();
    this.children = [];
    this.parentNode = null;
    this.attributes = {};
    this.listeners = {};
    this._cls = new ClassList(this);
    this._className = "";
    this._textContent = "";
    this._innerHTML = "";
    this.value = "";
    this.disabled = false;
    this.dataset = {};
    this.style = {};
  }
  get classList() { return this._cls; }
  set className(v) { this._className = v; this._cls.set = new Set(String(v).split(/\s+/).filter(Boolean)); }
  get className() { return this._className; }
  _syncClass() { this._className = [...this._cls.set].join(" "); }
  set textContent(v) { this._textContent = String(v); this.children = []; }
  get textContent() {
    if (this.children.length) return this.children.map(c => c.textContent).join("");
    return this._textContent;
  }
  set innerHTML(v) { this._innerHTML = v; if (v === "") this.children = []; }
  get innerHTML() { return this._innerHTML; }
  set id(v) { this._id = v; registerById(v, this); }
  get id() { return this._id; }
  appendChild(c) { c.parentNode = this; this.children.push(c); return c; }
  addEventListener(ev, fn) { (this.listeners[ev] = this.listeners[ev] || []).push(fn); }
  set onclick(fn) { this.listeners.click = fn ? [fn] : []; }
  get onclick() { return (this.listeners.click || [])[0] || null; }
  click() { (this.listeners.click || []).forEach(f => f({})); }
  querySelector() { return null; }
  get lastChild() { return this.children[this.children.length - 1]; }
  get firstChild() { return this.children[0]; }
  findAll(pred, out = []) {
    for (const c of this.children) {
      if (pred(c)) out.push(c);
      c.findAll(pred, out);
    }
    return out;
  }
}

const byId = new Map();
function registerById(id, el) { byId.set(id, el); }

function makeStaticDom() {
  const ids = ["sampleSelect", "loadSampleBtn", "importJsonBtn", "importCsvBtn",
    "exportBtn", "fileInput", "addRegionBtn", "addCategoryBtn", "calibrateBtn",
    "publishName", "publishBtn", "banner",
    "tab-edit", "tab-compare", "tab-objective", "tab-published"];
  const els = {};
  for (const id of ids) {
    const e = new El("div");
    e._id = id; byId.set(id, e);
    els[id] = e;
  }
  // tabs with 4 buttons
  const tabs = new El("nav");
  tabs._id = "tabs"; byId.set("tabs", tabs);
  for (const t of ["edit", "compare", "objective", "published"]) {
    const b = new El("button");
    b.dataset.tab = t;
    tabs.appendChild(b);
  }
  els.tabs = tabs;
  els.sampleSelect.value = "";
  return els;
}

const documentStub = {
  createElement: tag => new El(tag),
  querySelector: sel => byId.get(sel.slice(1)) || null,
  querySelectorAll: sel => {
    if (sel === "#tabs button") return byId.get("tabs").children;
    return [];
  },
};

const staticEls = makeStaticDom();
globalThis.document = documentStub;
globalThis.alert = msg => { globalThis.__alerts.push(msg); };
globalThis.__alerts = [];
// FileReader stub: fake files carry their content directly
globalThis.FileReader = class {
  readAsText(file) { this.result = file.content; setTimeout(() => this.onload(), 0); }
};
// fetch: rewrite relative URLs to the test server
const realFetch = globalThis.fetch;
globalThis.fetch = (url, opt) => realFetch(url.startsWith("/") ? BASE + url : url, opt);

// ---------------------------------------------------------------------------
// load app.js and run flows
// ---------------------------------------------------------------------------
const src = readFileSync("/workspace/static/app.js", "utf8");
eval(src);

const sleep = ms => new Promise(r => setTimeout(r, ms));
const $ = sel => documentStub.querySelector(sel);
let failures = 0;
function check(name, cond, extra) {
  if (cond) console.log("  ok:", name);
  else { failures++; console.log("  FAIL:", name, extra === undefined ? "" : extra); }
}
// app.js runs main() at eval time; all assertions below go through the DOM
// stub (button disabled state, banner text), so no direct access to its
// lexical `state` is needed.

async function main() {
  await sleep(300); // main() auto-loads mismatch sample

  console.log("flow 1: sample auto-load + calibrate + publish");
  check("grid rendered", $("#tab-edit").children.length === 1);
  check("publish disabled before calibrate", $("#publishBtn").disabled === true);

  $("#calibrateBtn").click();
  await sleep(300);
  check("publish enabled after feasible calibrate", $("#publishBtn").disabled === false);
  check("banner ok shown", !$("#banner").classList.contains("hidden"));

  // edit a cell -> result becomes stale -> publish disabled
  const grid = $("#tab-edit");
  const inputs = grid.findAll(e => e.tagName === "INPUT" && e.type === "number");
  check("grid has numeric inputs", inputs.length > 20, inputs.length);
  const cellInput = inputs[0];
  cellInput.value = "777";
  (cellInput.listeners.change || []).forEach(f => f({}));
  check("publish disabled after edit (stale)", $("#publishBtn").disabled === true);

  $("#calibrateBtn").click();
  await sleep(300);
  check("publish re-enabled after re-calibrate", $("#publishBtn").disabled === false);

  $("#publishName").value = "前端冒烟计划";
  $("#publishBtn").click();
  await sleep(300);
  const bannerText = $("#banner").textContent;
  check("publish success banner", bannerText.includes("已发布"), bannerText);

  console.log("flow 2: conflict sample -> IIS banner -> remove one lock -> feasible");
  staticEls.sampleSelect.value = "lock_conflict";
  $("#loadSampleBtn").click();
  await sleep(300);
  $("#calibrateBtn").click();
  await sleep(300);
  check("publish disabled when infeasible", $("#publishBtn").disabled === true);
  const conflictBtns = $("#banner").findAll(e => e.tagName === "BUTTON");
  check("4 conflicting locks listed", conflictBtns.length === 4, conflictBtns.length);
  conflictBtns[0].click(); // 撤销此锁定并重算
  await sleep(300);
  check("feasible after removing one lock", $("#publishBtn").disabled === false);

  console.log("flow 3: published records listed");
  documentStub.querySelectorAll("#tabs button").find(b => b.dataset.tab === "published").click();
  await sleep(300);
  const pubText = $("#tab-published").textContent;
  check("published plan visible", pubText.includes("前端冒烟计划"), pubText.slice(0, 120));
  const viewBtns = $("#tab-published").findAll(e => e.tagName === "BUTTON");
  check("snapshot button exists", viewBtns.length >= 1);
  viewBtns[0].click();
  await sleep(300);
  check("snapshot rendered", $("#tab-published").textContent.includes("快照 #"));

  console.log("flow 4: CSV import builds a fresh draft");
  $("#importCsvBtn").click();
  const csv = "地区,品类,预测,权重\n华北,饮料,100,1\n华北,零食,120,1\n华东,饮料,90,2\n华东,零食,110,1\n";
  staticEls.fileInput.files = [{ content: csv }];
  (staticEls.fileInput.listeners.change || []).forEach(f => f({}));
  await sleep(300);
  // 2x2 cells x2 inputs + 2 regions x3 + 2 categories x3 + 3 grand = 23
  const csvInputs = $("#tab-edit").findAll(e => e.tagName === "INPUT" && e.type === "number");
  check("CSV grid has 23 numeric inputs", csvInputs.length === 23, csvInputs.length);
  $("#calibrateBtn").click();
  await sleep(300);
  check("CSV draft calibrates", $("#publishBtn").disabled === false);

  console.log(failures === 0 ? "ALL FRONTEND SMOKE CHECKS PASSED" : `${failures} FAILURES`);
  process.exit(failures === 0 ? 0 : 1);
}

main().catch(e => { console.error("harness error:", e); process.exit(2); });
