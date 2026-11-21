/* 地区 × 品类 预测校准计划表 —— 前端逻辑（无框架，原生 DOM） */
"use strict";

// ---------------------------------------------------------------------------
// state
// ---------------------------------------------------------------------------
const state = {
  regions: [], categories: [],
  cellF: [], cellW: [],
  regF: [], regW: [], regL: [],
  catF: [], catW: [], catL: [],
  grdF: null, grdW: null, grdL: null,
  result: null,          // last /api/calibrate response
  solvedKey: null,       // inputKey() at the moment of the last solve
  error: null,           // last calibration error (banner)
  notice: null,          // transient success message (banner)
  tab: "edit",
  publishedSnapshot: null,
};

function blankState(nr, nc) {
  const row = () => Array(nc).fill(null);
  state.regions = Array.from({ length: nr }, (_, i) => "地区" + (i + 1));
  state.categories = Array.from({ length: nc }, (_, j) => "品类" + (j + 1));
  state.cellF = Array.from({ length: nr }, row);
  state.cellW = Array.from({ length: nr }, row);
  state.regF = Array(nr).fill(null); state.regW = Array(nr).fill(null); state.regL = Array(nr).fill(null);
  state.catF = Array(nc).fill(null); state.catW = Array(nc).fill(null); state.catL = Array(nc).fill(null);
  state.grdF = null; state.grdW = null; state.grdL = null;
  state.result = null; state.solvedKey = null; state.error = null;
}

function stateFromPayload(input) {
  const nr = input.regions.length, nc = input.categories.length;
  blankState(nr, nc);
  state.regions = input.regions.slice();
  state.categories = input.categories.slice();
  const c = input.cells || {};
  for (let i = 0; i < nr; i++) for (let j = 0; j < nc; j++) {
    state.cellF[i][j] = (c.forecast && c.forecast[i] && c.forecast[i][j] !== undefined) ? c.forecast[i][j] : null;
    state.cellW[i][j] = (c.weight && c.weight[i] && c.weight[i][j] !== undefined) ? c.weight[i][j] : null;
  }
  const rt = input.regionTotals || {}, ct = input.categoryTotals || {}, gt = input.grandTotal || {};
  for (let i = 0; i < nr; i++) {
    state.regF[i] = (rt.forecast || [])[i] ?? null;
    state.regW[i] = (rt.weight || [])[i] ?? null;
    state.regL[i] = (rt.lock || [])[i] ?? null;
  }
  for (let j = 0; j < nc; j++) {
    state.catF[j] = (ct.forecast || [])[j] ?? null;
    state.catW[j] = (ct.weight || [])[j] ?? null;
    state.catL[j] = (ct.lock || [])[j] ?? null;
  }
  state.grdF = gt.forecast ?? null; state.grdW = gt.weight ?? null; state.grdL = gt.lock ?? null;
  state.result = null; state.solvedKey = null; state.error = null;
}

function payloadFromState() {
  return {
    regions: state.regions.slice(),
    categories: state.categories.slice(),
    cells: { forecast: state.cellF.map(r => r.slice()), weight: state.cellW.map(r => r.slice()) },
    regionTotals: { forecast: state.regF.slice(), weight: state.regW.slice(), lock: state.regL.slice() },
    categoryTotals: { forecast: state.catF.slice(), weight: state.catW.slice(), lock: state.catL.slice() },
    grandTotal: { forecast: state.grdF, weight: state.grdW, lock: state.grdL },
  };
}

const inputKey = () => JSON.stringify(payloadFromState());
const isFresh = () => state.result && state.solvedKey === inputKey();
const isPublishable = () => isFresh() && state.result.status === "optimal";

// ---------------------------------------------------------------------------
// helpers
// ---------------------------------------------------------------------------
const $ = sel => document.querySelector(sel);
const el = (tag, cls, text) => {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined && text !== null) e.textContent = text;
  return e;
};

function fmt(v, digits = 2) {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  const av = Math.abs(v);
  if (av !== 0 && (av >= 1e6 || av < 1e-4)) return v.toExponential(2);
  return Number(v.toFixed(digits)).toString();
}

function parseNum(str) {
  if (str === "" || str === null || str === undefined) return null;
  const v = Number(str);
  return Number.isFinite(v) ? v : undefined; // undefined = invalid
}

async function api(path, body) {
  const opt = body === undefined ? {} : {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  };
  const resp = await fetch(path, opt);
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) {
    const err = new Error(data.error || ("HTTP " + resp.status));
    err.data = data;
    throw err;
  }
  return data;
}

// ---------------------------------------------------------------------------
// banner
// ---------------------------------------------------------------------------
function showBanner(node) {
  const b = $("#banner");
  b.innerHTML = "";
  b.classList.remove("hidden");
  if (node) b.appendChild(node);
}
function hideBanner() { $("#banner").classList.add("hidden"); }

function notice(kind, text) {
  const d = el("div", "notice " + kind);
  d.appendChild(el("div", null, text));
  return d;
}

function showConflict(result) {
  const d = el("div", "notice bad");
  d.appendChild(el("div", null,
    "⚠ " + (result.message || "锁定条件冲突，计划不可行。") +
    " 下面列出参与冲突的锁定条件（去掉其中任意一条都可能恢复可行），撤销一条后点「校准」重算；没有可行方案不能发布。"));
  const ul = el("ul", "conflict-list");
  for (const c of result.conflicts || []) {
    const li = el("li");
    const levelName = { region: "地区锁定", category: "品类锁定", grand: "全盘锁定" }[c.level] || c.level;
    li.appendChild(el("span", null, `🔒 ${levelName}：${c.label} = ${fmt(c.value)}`));
    const btn = el("button", "small danger", "撤销此锁定并重算");
    btn.onclick = () => {
      if (c.level === "region") state.regL[c.index] = null;
      else if (c.level === "category") state.catL[c.index] = null;
      else state.grdL = null;
      renderEdit();
      calibrate();
    };
    li.appendChild(btn);
    ul.appendChild(li);
  }
  d.appendChild(ul);
  showBanner(d);
}

// ---------------------------------------------------------------------------
// edit grid
// ---------------------------------------------------------------------------
function numInput(value, opts) {
  const inp = document.createElement("input");
  inp.type = "number"; inp.step = "any";
  if (value !== null && value !== undefined) inp.value = value;
  if (opts && opts.cls) inp.className = opts.cls;
  if (opts && opts.ph) inp.placeholder = opts.ph;
  if (opts && opts.title) inp.title = opts.title;
  return inp;
}

function bindNum(inp, get, set) {
  inp.addEventListener("change", () => {
    const v = parseNum(inp.value.trim());
    if (v === undefined) {
      inp.classList.add("invalid");
      inp.title = "请输入数字或留空";
      return;
    }
    inp.classList.remove("invalid");
    set(v);
    markEdited();
  });
}

function tripleInputs(getF, setF, getW, setW, getL, setL) {
  const wrap = el("div", "cellinputs");
  const f = numInput(getF(), { ph: "预测", title: "预测值（留空 = 不提供，不参与目标函数）" });
  const w = numInput(getW(), { ph: "权重", cls: "w", title: "可信权重，必须为正数" });
  bindNum(f, getF, setF); bindNum(w, getW, setW);
  wrap.appendChild(f); wrap.appendChild(w);
  if (getL) {
    const l = numInput(getL(), { ph: "锁定", cls: "lock" + (getL() !== null ? " set" : ""), title: "锁定值：该校准总量必须等于此值（留空 = 不锁定）" });
    l.addEventListener("change", () => {
      const v = parseNum(l.value.trim());
      if (v === undefined) { l.classList.add("invalid"); return; }
      l.classList.remove("invalid");
      setL(v);
      l.classList.toggle("set", v !== null);
      markEdited();
    });
    wrap.appendChild(l);
  }
  return wrap;
}

function renderEdit() {
  const host = $("#tab-edit");
  host.innerHTML = "";
  const panel = el("div", "panel");
  panel.appendChild(el("h2", null, "草稿：预测、权重与锁定"));
  panel.appendChild(el("p", "hint",
    "每个单元格填「预测 / 权重」；右侧与底部分别填地区合计、品类合计的「预测 / 权重 / 锁定」；右下角为全盘总量。" +
    "预测留空表示不提供（不会当作 0，也不进入目标函数）；锁定留空表示不锁定。修改后请重新「校准」。"));

  const t = el("table", "grid");
  const thead = el("thead");
  const hr = el("tr");
  hr.appendChild(el("th", "rowhead", "地区 ＼ 品类"));
  state.categories.forEach((c, j) => {
    const th = el("th");
    const nc = el("div", "namecell");
    const name = document.createElement("input");
    name.value = c; name.title = "品类名称";
    name.addEventListener("change", () => { state.categories[j] = name.value.trim() || state.categories[j]; markEdited(); });
    const del = el("button", "del", "✕");
    del.title = "删除该品类";
    del.onclick = () => { removeCategory(j); };
    nc.appendChild(name); nc.appendChild(del);
    th.appendChild(nc);
    hr.appendChild(th);
  });
  hr.appendChild(el("th", "totalcol", "地区合计\n预测 / 权重 / 锁定"));
  thead.appendChild(hr);
  t.appendChild(thead);

  const tb = el("tbody");
  state.regions.forEach((r, i) => {
    const tr = el("tr");
    const th = el("th", "rowhead");
    const nc = el("div", "namecell");
    const name = document.createElement("input");
    name.value = r; name.title = "地区名称";
    name.addEventListener("change", () => { state.regions[i] = name.value.trim() || state.regions[i]; markEdited(); });
    const del = el("button", "del", "✕");
    del.title = "删除该地区";
    del.onclick = () => { removeRegion(i); };
    nc.appendChild(name); nc.appendChild(del);
    th.appendChild(nc);
    tr.appendChild(th);
    state.categories.forEach((_, j) => {
      const td = el("td");
      td.appendChild(tripleInputs(
        () => state.cellF[i][j], v => state.cellF[i][j] = v,
        () => state.cellW[i][j], v => state.cellW[i][j] = v));
      tr.appendChild(td);
    });
    const td = el("td", "totalcell");
    td.appendChild(tripleInputs(
      () => state.regF[i], v => state.regF[i] = v,
      () => state.regW[i], v => state.regW[i] = v,
      () => state.regL[i], v => state.regL[i] = v));
    tr.appendChild(td);
    tb.appendChild(tr);
  });

  // category totals row
  const tr = el("tr");
  tr.appendChild(el("th", "rowhead", "品类合计\n预测 / 权重 / 锁定"));
  state.categories.forEach((_, j) => {
    const td = el("td", "totalcell");
    td.appendChild(tripleInputs(
      () => state.catF[j], v => state.catF[j] = v,
      () => state.catW[j], v => state.catW[j] = v,
      () => state.catL[j], v => state.catL[j] = v));
    tr.appendChild(td);
  });
  const gd = el("td", "grandcell");
  gd.appendChild(el("div", null, "全盘总量"));
  gd.appendChild(tripleInputs(
    () => state.grdF, v => state.grdF = v,
    () => state.grdW, v => state.grdW = v,
    () => state.grdL, v => state.grdL = v));
  tr.appendChild(gd);
  tb.appendChild(tr);
  t.appendChild(tb);
  panel.appendChild(t);
  host.appendChild(panel);
}

function addRegion() {
  const nc = state.categories.length;
  state.regions.push("地区" + (state.regions.length + 1));
  state.cellF.push(Array(nc).fill(null));
  state.cellW.push(Array(nc).fill(null));
  state.regF.push(null); state.regW.push(null); state.regL.push(null);
  state.result = null;
  renderEdit(); markEdited();
}
function addCategory() {
  state.categories.push("品类" + (state.categories.length + 1));
  state.cellF.forEach(r => r.push(null));
  state.cellW.forEach(r => r.push(null));
  state.catF.push(null); state.catW.push(null); state.catL.push(null);
  state.result = null;
  renderEdit(); markEdited();
}
function removeRegion(i) {
  if (state.regions.length <= 1) return alert("至少保留一个地区");
  state.regions.splice(i, 1);
  state.cellF.splice(i, 1); state.cellW.splice(i, 1);
  state.regF.splice(i, 1); state.regW.splice(i, 1); state.regL.splice(i, 1);
  state.result = null;
  renderEdit(); markEdited();
}
function removeCategory(j) {
  if (state.categories.length <= 1) return alert("至少保留一个品类");
  state.categories.splice(j, 1);
  state.cellF.forEach(r => r.splice(j, 1));
  state.cellW.forEach(r => r.splice(j, 1));
  state.catF.splice(j, 1); state.catW.splice(j, 1); state.catL.splice(j, 1);
  state.result = null;
  renderEdit(); markEdited();
}

// ---------------------------------------------------------------------------
// compare view
// ---------------------------------------------------------------------------
function deltaHtml(cal, fc) {
  const d = el("div", "caldelta");
  if (fc === null || fc === undefined) {
    d.appendChild(el("span", "miss", "无预测"));
    return d;
  }
  const diff = cal - fc;
  const cls = Math.abs(diff) < 1e-9 ? "same" : (diff > 0 ? "up" : "down");
  const sign = diff > 0 ? "+" : "";
  d.appendChild(el("span", cls, `预测 ${fmt(fc)} → ${sign}${fmt(diff)}`));
  return d;
}

function zeroBadge(v, fc) {
  if (Math.abs(v) < 1e-9 && fc !== null && fc !== undefined && Math.abs(fc) > 1e-9) {
    return el("span", "zerobadge", "压到 0");
  }
  return null;
}

function renderCompare() {
  const host = $("#tab-compare");
  host.innerHTML = "";
  const r = state.result;
  const panel = el("div", "panel");
  if (!r || r.status !== "optimal") {
    panel.appendChild(el("p", "hint", "尚无可行的校准结果。请先在「编辑草稿」中点「校准」。"));
    host.appendChild(panel);
    return;
  }
  if (!isFresh()) {
    panel.appendChild(el("p", "hint", "⚠ 草稿在校准后又被修改过，以下结果已过期，请重新校准。"));
  }
  panel.appendChild(el("h2", null, "校准前后对比（校准值 / 与原始预测的差异）"));
  const t = el("table", "grid");
  const thead = el("thead");
  const hr = el("tr");
  hr.appendChild(el("th", "rowhead", "地区 ＼ 品类"));
  state.categories.forEach(c => hr.appendChild(el("th", null, c)));
  hr.appendChild(el("th", "totalcol", "地区合计"));
  thead.appendChild(hr);
  t.appendChild(thead);
  const tb = el("tbody");
  state.regions.forEach((_, i) => {
    const tr = el("tr");
    tr.appendChild(el("th", "rowhead", state.regions[i]));
    state.categories.forEach((_, j) => {
      const td = el("td");
      const v = r.cells[i][j];
      const val = el("div", "calval", fmt(v));
      td.appendChild(val);
      const zb = zeroBadge(v, state.cellF[i][j]);
      if (zb) val.appendChild(zb);
      td.appendChild(deltaHtml(v, state.cellF[i][j]));
      tr.appendChild(td);
    });
    const td = el("td", "totalcell");
    td.appendChild(el("div", "calval", fmt(r.regionTotals[i])));
    td.appendChild(deltaHtml(r.regionTotals[i], state.regF[i]));
    if (state.regL[i] !== null) td.appendChild(el("div", "lockbadge", "🔒 锁定 " + fmt(state.regL[i])));
    tr.appendChild(td);
    tb.appendChild(tr);
  });
  const tr = el("tr");
  tr.appendChild(el("th", "rowhead", "品类合计"));
  state.categories.forEach((_, j) => {
    const td = el("td", "totalcell");
    td.appendChild(el("div", "calval", fmt(r.categoryTotals[j])));
    td.appendChild(deltaHtml(r.categoryTotals[j], state.catF[j]));
    if (state.catL[j] !== null) td.appendChild(el("div", "lockbadge", "🔒 锁定 " + fmt(state.catL[j])));
    tr.appendChild(td);
  });
  const gd = el("td", "grandcell");
  gd.appendChild(el("div", null, "全盘总量"));
  gd.appendChild(el("div", "calval", fmt(r.grandTotal)));
  gd.appendChild(deltaHtml(r.grandTotal, state.grdF));
  if (state.grdL !== null) gd.appendChild(el("div", "lockbadge", "🔒 锁定 " + fmt(state.grdL)));
  tr.appendChild(gd);
  tb.appendChild(tr);
  t.appendChild(tb);
  panel.appendChild(t);
  host.appendChild(panel);
}

// ---------------------------------------------------------------------------
// objective view
// ---------------------------------------------------------------------------
function card(k, v) {
  const c = el("div", "card");
  c.appendChild(el("div", "k", k));
  c.appendChild(el("div", "v", v));
  return c;
}

function renderObjective() {
  const host = $("#tab-objective");
  host.innerHTML = "";
  const r = state.result;
  const panel = el("div", "panel");
  if (!r || r.status !== "optimal") {
    panel.appendChild(el("p", "hint", "尚无可行的校准结果。"));
    host.appendChild(panel);
    return;
  }
  panel.appendChild(el("h2", null, "目标函数核对：每一项加权平方偏差"));
  const cards = el("div", "cards");
  cards.appendChild(card("目标函数值 Σ w·偏差²", fmt(r.objective, 4)));
  cards.appendChild(card("参与项数", String(r.objectiveTerms.length)));
  cards.appendChild(card("最大锁定残差", fmt(r.checks.maxLockResidual, 8)));
  cards.appendChild(card("最小单元格值（非负性）", fmt(r.checks.minCellValue, 8)));
  cards.appendChild(card("并列最优 tie-break Σx²", fmt(r.checks.tieBreakSumSquares, 4)));
  const it1 = r.checks.stage1.iterations, it2 = r.checks.stage2.iterations;
  cards.appendChild(card("求解迭代（阶段一 / 阶段二）",
    (it1 !== undefined ? it1 : "—") + " / " + (it2 !== undefined ? it2 : (r.checks.stage2.skipped ? "跳过（解唯一）" : "—"))));
  panel.appendChild(cards);

  const levelName = { cell: "单元格", region: "地区总量", category: "品类总量", grand: "全盘总量" };
  const t = el("table", "data");
  t.appendChild((() => {
    const tr = el("tr");
    ["层级", "项目", "预测 f", "权重 w", "校准值", "偏差 (校准−f)", "w·偏差²"].forEach(h => {
      const th = el("th", null, h);
      if (h === "层级" || h === "项目") th.className = "l";
      tr.appendChild(th);
    });
    return tr;
  })());
  let sum = 0;
  for (const term of r.objectiveTerms) {
    sum += term.contribution;
    const tr = el("tr", "level-" + term.level);
    tr.appendChild(el("td", "l", levelName[term.level] || term.level));
    tr.appendChild(el("td", "l", term.label));
    tr.appendChild(el("td", null, fmt(term.forecast)));
    tr.appendChild(el("td", null, fmt(term.weight)));
    tr.appendChild(el("td", null, fmt(term.value)));
    tr.appendChild(el("td", null, fmt(term.deviation, 4)));
    tr.appendChild(el("td", null, fmt(term.contribution, 6)));
    t.appendChild(tr);
  }
  const tr = el("tr");
  tr.appendChild(el("td", "l", "合计"));
  tr.appendChild(el("td", "l", "（逐项重算求和，应与目标函数值一致）"));
  for (let k = 0; k < 4; k++) tr.appendChild(el("td", null, ""));
  tr.appendChild(el("td", null, fmt(sum, 6)));
  t.appendChild(tr);
  panel.appendChild(t);

  if (r.locks && r.locks.length) {
    panel.appendChild(el("h2", null, "锁定条件核验"));
    const lt = el("table", "data");
    const hr2 = el("tr");
    ["层级", "项目", "锁定值", "校准后实际值", "残差"].forEach(h => {
      const th = el("th", null, h);
      if (h === "层级" || h === "项目") th.className = "l";
      hr2.appendChild(th);
    });
    lt.appendChild(hr2);
    for (const l of r.locks) {
      const row = el("tr");
      row.appendChild(el("td", "l", levelName[l.level] || l.level));
      row.appendChild(el("td", "l", l.label));
      row.appendChild(el("td", null, fmt(l.value)));
      row.appendChild(el("td", null, fmt(l.actual)));
      row.appendChild(el("td", null, fmt(l.residual, 8)));
      lt.appendChild(row);
    }
    panel.appendChild(lt);
  }
  panel.appendChild(el("p", "hint",
    "校验口径：每个汇总值都由单元格重新求和得到；目标函数按定义逐项重算；" +
    "求解器返回前已检查 KKT 残差、锁定残差与非负性，未提供预测的项不出现在上表中（也不会被当作 0）。"));
  host.appendChild(panel);
}

// ---------------------------------------------------------------------------
// published
// ---------------------------------------------------------------------------
async function renderPublished() {
  const host = $("#tab-published");
  host.innerHTML = "";
  const panel = el("div", "panel");
  panel.appendChild(el("h2", null, "发布记录（不可变快照）"));
  panel.appendChild(el("p", "hint",
    "发布时服务器会用固定的输入重新求解并整体存档：预测、权重、锁定条件与结果一并冻结。" +
    "之后修改草稿不会影响已发布的计划。"));
  let items = [];
  try {
    items = (await api("/api/published")).items;
  } catch (e) {
    panel.appendChild(el("p", "hint", "载入发布记录失败：" + e.message));
  }
  if (!items.length) {
    panel.appendChild(el("p", "hint", "还没有发布记录。"));
  } else {
    const t = el("table", "data");
    const hr = el("tr");
    ["#", "名称", "发布时间 (UTC)", "全盘总量", "内容哈希 (SHA-256)", ""].forEach(h => {
      const th = el("th", null, h);
      if (h === "名称") th.className = "l";
      hr.appendChild(th);
    });
    t.appendChild(hr);
    for (const it of items) {
      const tr = el("tr");
      tr.appendChild(el("td", null, String(it.id)));
      tr.appendChild(el("td", "l", it.name));
      tr.appendChild(el("td", null, it.created_at));
      tr.appendChild(el("td", null, fmt(it.grandTotal)));
      tr.appendChild(el("td", null, "")).className = "hash";
      tr.lastChild.textContent = (it.sha256 || "").slice(0, 16) + "…";
      const td = el("td");
      const btn = el("button", "small", "查看快照");
      btn.onclick = () => viewPublished(it.id);
      td.appendChild(btn);
      tr.appendChild(td);
      t.appendChild(tr);
    }
    panel.appendChild(t);
  }
  const snap = el("div", "snapshot");
  snap.id = "snapshot";
  panel.appendChild(snap);
  host.appendChild(panel);
  if (state.publishedSnapshot) renderSnapshot(state.publishedSnapshot);
}

function renderSnapshot(rec) {
  const snap = $("#snapshot");
  snap.innerHTML = "";
  snap.appendChild(el("h2", null, `快照 #${rec.id}：${rec.name}`));
  const meta = el("p", "pubmeta",
    `发布时间 (UTC)：${rec.created_at}　内容哈希：`);
  const h = el("span", "hash", rec.sha256);
  meta.appendChild(h);
  snap.appendChild(meta);
  snap.appendChild(snapshotGrid(rec.input, rec.result));
  const dl = el("button", "small", "下载快照 JSON");
  dl.onclick = () => downloadJson(rec, `published-plan-${rec.id}.json`);
  snap.appendChild(dl);
}

function snapshotGrid(input, result) {
  const t = el("table", "grid");
  const regions = input.regions, categories = input.categories;
  const thead = el("thead");
  const hr = el("tr");
  hr.appendChild(el("th", "rowhead", "地区 ＼ 品类"));
  categories.forEach(c => hr.appendChild(el("th", null, c)));
  hr.appendChild(el("th", "totalcol", "地区合计"));
  thead.appendChild(hr);
  t.appendChild(thead);
  const tb = el("tbody");
  const cf = (input.cells || {}).forecast || [];
  regions.forEach((rname, i) => {
    const tr = el("tr");
    tr.appendChild(el("th", "rowhead", rname));
    categories.forEach((_, j) => {
      const td = el("td");
      td.appendChild(el("div", "calval", fmt(result.cells[i][j])));
      td.appendChild(deltaHtml(result.cells[i][j], cf[i] ? cf[i][j] : null));
      tr.appendChild(td);
    });
    const td = el("td", "totalcell");
    td.appendChild(el("div", "calval", fmt(result.regionTotals[i])));
    const rt = input.regionTotals || {};
    td.appendChild(deltaHtml(result.regionTotals[i], (rt.forecast || [])[i]));
    if ((rt.lock || [])[i] !== null && (rt.lock || [])[i] !== undefined) {
      td.appendChild(el("div", "lockbadge", "🔒 " + fmt(rt.lock[i])));
    }
    tr.appendChild(td);
    tb.appendChild(tr);
  });
  const tr = el("tr");
  tr.appendChild(el("th", "rowhead", "品类合计"));
  const ct = input.categoryTotals || {};
  categories.forEach((_, j) => {
    const td = el("td", "totalcell");
    td.appendChild(el("div", "calval", fmt(result.categoryTotals[j])));
    td.appendChild(deltaHtml(result.categoryTotals[j], (ct.forecast || [])[j]));
    if ((ct.lock || [])[j] !== null && (ct.lock || [])[j] !== undefined) {
      td.appendChild(el("div", "lockbadge", "🔒 " + fmt(ct.lock[j])));
    }
    tr.appendChild(td);
  });
  const gd = el("td", "grandcell");
  gd.appendChild(el("div", "calval", fmt(result.grandTotal)));
  const gt = input.grandTotal || {};
  gd.appendChild(deltaHtml(result.grandTotal, gt.forecast));
  if (gt.lock !== null && gt.lock !== undefined) gd.appendChild(el("div", "lockbadge", "🔒 " + fmt(gt.lock)));
  tr.appendChild(gd);
  tb.appendChild(tr);
  t.appendChild(tb);
  return t;
}

async function viewPublished(id) {
  try {
    const rec = await api("/api/published/" + id);
    state.publishedSnapshot = rec;
    renderSnapshot(rec);
  } catch (e) {
    alert("载入快照失败：" + e.message);
  }
}

// ---------------------------------------------------------------------------
// actions
// ---------------------------------------------------------------------------
function updateActionState() {
  const pub = $("#publishBtn");
  pub.disabled = !isPublishable();
  if (state.error) {
    showBanner(notice("bad", state.error));
  } else if (state.notice) {
    showBanner(notice("ok", state.notice));
  } else if (state.result && !isFresh() && state.result.status === "optimal") {
    showBanner(notice("warn", "草稿在校准后已被修改，结果可能已过期：请重新「校准」后再发布。"));
  } else if (state.result && state.result.status === "infeasible") {
    showConflict(state.result);
  } else if (isPublishable()) {
    showBanner(notice("ok",
      `✓ 校准完成：目标函数值 ${fmt(state.result.objective, 4)}，最大锁定残差 ${fmt(state.result.checks.maxLockResidual, 8)}，可以发布。`));
  } else {
    hideBanner();
  }
}

// any user edit to the draft clears transient messages and re-evaluates state
function markEdited() {
  state.notice = null;
  state.error = null;
  updateActionState();
}

async function calibrate() {
  const btn = $("#calibrateBtn");
  btn.disabled = true;
  state.error = null;
  state.notice = null;
  try {
    const result = await api("/api/calibrate", payloadFromState());
    state.result = result;
    state.solvedKey = inputKey();
    if (result.status === "optimal") {
      renderCompare(); renderObjective();
    } else if (result.status !== "infeasible") {
      state.error = result.message || "求解器错误";
    }
  } catch (e) {
    state.result = null; state.solvedKey = null;
    state.error = "校准失败：" + e.message;
  } finally {
    btn.disabled = false;
    updateActionState();
  }
}

async function publish() {
  const name = $("#publishName").value.trim();
  if (!name) { alert("请先填写发布名称"); return; }
  if (!isPublishable()) { alert("没有可行且未过期的校准结果，不能发布"); return; }
  const btn = $("#publishBtn");
  btn.disabled = true;
  try {
    const resp = await api("/api/publish", { name, input: payloadFromState() });
    state.notice = `✓ 已发布「${resp.record.name}」（#${resp.record.id}，哈希 ${resp.record.sha256.slice(0, 16)}…）。` +
      "该快照已冻结，之后修改草稿不会影响它。";
    state.publishedSnapshot = null;
    if (state.tab === "published") renderPublished();
  } catch (e) {
    if (e.data && e.data.calibration && e.data.calibration.status === "infeasible") {
      state.result = e.data.calibration;
      showConflict(state.result);
    } else {
      showBanner(notice("bad", "发布失败：" + e.message));
    }
  } finally {
    btn.disabled = false;
    updateActionState();
  }
}

// ---------------------------------------------------------------------------
// import / export
// ---------------------------------------------------------------------------
function downloadJson(obj, filename) {
  const blob = new Blob([JSON.stringify(obj, null, 2)], { type: "application/json" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  a.click();
  URL.revokeObjectURL(a.href);
}

function importJsonFile(file) {
  const reader = new FileReader();
  reader.onload = () => {
    try {
      const data = JSON.parse(reader.result);
      const input = data.input || data; // accept {name, input} or bare payload
      if (!Array.isArray(input.regions) || !Array.isArray(input.categories)) {
        throw new Error("缺少 regions / categories 数组");
      }
      stateFromPayload(input);
      renderEdit(); updateActionState();
      showBanner(notice("ok", "✓ 已导入 JSON 草稿" + (data.description ? "：" + data.description : "")));
    } catch (e) {
      showBanner(notice("bad", "导入失败：" + e.message));
    }
  };
  reader.readAsText(file);
}

function importCsvFile(file) {
  const reader = new FileReader();
  reader.onload = () => {
    try {
      const lines = reader.result.split(/\r?\n/).map(s => s.trim()).filter(Boolean);
      if (!lines.length) throw new Error("文件为空");
      const start = /地区|region/i.test(lines[0]) ? 1 : 0;
      const regions = [], categories = [], cells = [];
      for (let k = start; k < lines.length; k++) {
        const parts = lines[k].split(/[,，\t]/).map(s => s.trim());
        if (parts.length < 3) continue;
        const [r, c] = parts;
        const f = parseNum(parts[2]);
        const w = parts.length > 3 ? parseNum(parts[3]) : null;
        if (f === undefined || (parts.length > 3 && parts[3] !== "" && w === undefined)) {
          throw new Error("第 " + (k + 1) + " 行数值无法解析");
        }
        if (!regions.includes(r)) regions.push(r);
        if (!categories.includes(c)) categories.push(c);
        cells.push([r, c, f, w]);
      }
      if (!regions.length || !categories.length) throw new Error("没有有效数据行");
      blankState(regions.length, categories.length);
      state.regions = regions; state.categories = categories;
      for (const [r, c, f, w] of cells) {
        const i = regions.indexOf(r), j = categories.indexOf(c);
        state.cellF[i][j] = f; state.cellW[i][j] = w;
      }
      renderEdit(); updateActionState();
      showBanner(notice("ok", `✓ 已导入 CSV：${regions.length} 个地区 × ${categories.length} 个品类，${cells.length} 条单元格预测。总量预测与锁定请在表格中继续填写。`));
    } catch (e) {
      showBanner(notice("bad", "CSV 导入失败：" + e.message));
    }
  };
  reader.readAsText(file);
}

async function loadSample() {
  const name = $("#sampleSelect").value;
  if (!name) return;
  try {
    const resp = await fetch("/samples/" + name + ".json");
    const data = await resp.json();
    stateFromPayload(data.input);
    renderEdit();
    updateActionState();
    showBanner(notice("ok", "✓ 已载入「" + (data.name || name) + "」。" + (data.description || "")));
  } catch (e) {
    showBanner(notice("bad", "示例载入失败：" + e.message));
  }
}

// ---------------------------------------------------------------------------
// tabs & wiring
// ---------------------------------------------------------------------------
function switchTab(tab) {
  state.tab = tab;
  document.querySelectorAll("#tabs button").forEach(b => b.classList.toggle("active", b.dataset.tab === tab));
  for (const t of ["edit", "compare", "objective", "published"]) {
    $("#tab-" + t).classList.toggle("hidden", t !== tab);
  }
  if (tab === "compare") renderCompare();
  if (tab === "objective") renderObjective();
  if (tab === "published") { state.publishedSnapshot = null; renderPublished(); }
}

function main() {
  blankState(3, 3);
  renderEdit();
  document.querySelectorAll("#tabs button").forEach(b => b.addEventListener("click", () => switchTab(b.dataset.tab)));
  $("#calibrateBtn").addEventListener("click", calibrate);
  $("#publishBtn").addEventListener("click", publish);
  $("#addRegionBtn").addEventListener("click", addRegion);
  $("#addCategoryBtn").addEventListener("click", addCategory);
  $("#loadSampleBtn").addEventListener("click", loadSample);
  $("#exportBtn").addEventListener("click", () => downloadJson({ name: "草稿", input: payloadFromState() }, "planner-draft.json"));
  const fileInput = $("#fileInput");
  let importKind = "json";
  $("#importJsonBtn").addEventListener("click", () => { importKind = "json"; fileInput.accept = ".json"; fileInput.click(); });
  $("#importCsvBtn").addEventListener("click", () => { importKind = "csv"; fileInput.accept = ".csv,.txt"; fileInput.click(); });
  fileInput.addEventListener("change", () => {
    const f = fileInput.files[0];
    if (f) (importKind === "csv" ? importCsvFile : importJsonFile)(f);
    fileInput.value = "";
  });
  // greet with the mismatch sample so the page is not empty
  fetch("/samples/mismatch.json").then(r => r.json()).then(data => {
    stateFromPayload(data.input);
    renderEdit(); updateActionState();
  }).catch(() => {});
}

main();
