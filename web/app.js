/* ST v5 前端：组合 + 选股 */
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];

const state = {
  view: "portfolio",
  account: "all",
  base: "USD",
  accounts: [],
  fx: null,
  posRows: [],
  sortKey: null,
  sortDir: -1,
  lastScreenQuery: "",
  symbol: null,
  chartNav: null,
  chartMkt: null,
  chartSym: null,
  warmTimer: null,
};

async function api(path, opts = {}) {
  const r = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error((data.detail && (data.detail.message || data.detail.error)) || data.message || r.status);
  return data;
}

function banner(msg) {
  const el = $("#banner");
  if (!msg) { el.classList.add("hidden"); el.textContent = ""; return; }
  el.textContent = msg;
  el.classList.remove("hidden");
}
function setStatus(t) { $("#status").textContent = t; }

const nf = new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 });
const nf0 = new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 });

function decimals(ccy) {
  if (ccy === "IDR" || ccy === "JPY" || ccy === "KRW") return 0;
  return 2;
}
function money(v, ccy = "USD") {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  const d = decimals(ccy);
  return (ccy && ccy !== "USD" ? ccy + " " : "$") + (d ? nf.format(v) : nf0.format(v));
}
function num(v, d = 2) {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  return new Intl.NumberFormat("en-US", { maximumFractionDigits: d }).format(v);
}
function pct(v, d = 2) {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  return (v * 100).toFixed(d) + "%";
}
function signCls(v) { return v == null ? "" : v > 0 ? "up" : v < 0 ? "down" : ""; }
function signed(v, ccy = "USD") {
  if (v === null || v === undefined) return "—";
  const s = money(Math.abs(v), ccy);
  return (v > 0 ? "+" : v < 0 ? "−" : "") + s;
}
function chip(text, cls = "") { return `<span class="chip ${cls}">${text}</span>`; }

/* 折算：USD 是记账本位，只在前端换算展示，避免改后端口径 */
function rate() {
  if (state.base === "USD" || !state.fx) return 1;
  return state.fx[state.base] || 1;
}
function toBase(usd) {
  if (usd === null || usd === undefined) return null;
  return usd / rate();
}
function moneyB(usd) { return money(toBase(usd), state.base); }
function signedB(usd) { return signed(toBase(usd), state.base); }
function ccSym() { return state.base === "USD" ? "$" : state.base === "HKD" ? "HK$" : state.base === "SGD" ? "S$" : "Rp"; }
function shortTime(s) {
  if (!s) return "—";
  const m = String(s).replace("T", " ").match(/(\d{4})-(\d{2})-(\d{2})[ ]?(\d{2}:\d{2})?/);
  if (!m) return String(s);
  return `${m[2]}-${m[3]} ${m[4] || ""}`.trim();
}

/* ============ 导航 ============ */
function goView(name) {
  const b = $$("#tabs button").find(x => x.dataset.view === name);
  if (b) b.click();
}

$$("#tabs button").forEach(b => b.addEventListener("click", () => {
  $$("#tabs button").forEach(x => x.classList.toggle("active", x === b));
  $$(".view").forEach(v => v.classList.toggle("active", v.id === "view-" + b.dataset.view));
  state.view = b.dataset.view;
  if (state.view === "analysis") loadInsights();
  if (state.view === "risk") loadRisk();
}));

$("#accountSel").addEventListener("change", e => { state.account = e.target.value; loadPortfolio(); });
$("#baseSel").addEventListener("change", e => { state.base = e.target.value; loadPortfolio(); });
$("#refreshBtn").addEventListener("click", () => loadPortfolio(true));
$$("#posTable th.sortable").forEach(th => th.addEventListener("click", () => {
  const k = th.dataset.key;
  state.sortDir = state.sortKey === k ? -state.sortDir : -1;
  state.sortKey = k;
  $$("#posTable th").forEach(x => x.classList.remove("sort-asc", "sort-desc"));
  th.classList.add(state.sortDir === 1 ? "sort-asc" : "sort-desc");
  renderPositions(state.posRows);
}));

/* ============ 组合 ============ */
async function loadPortfolio(refresh = false) {
  setStatus(refresh ? "刷新行情…" : "载入…");
  banner("");
  if (refresh) {
    // 强刷：跳过服务端 5 分钟缓存，重新向 Yahoo 拉一遍持仓报价；静态站会返回 501，忽略即可
    try { await api("/api/prices/refresh", { method: "POST" }); } catch (e) { /* 忽略 */ }
  }
  try {
    const [ov, pos, recon, syms, nav, legacy] = await Promise.all([
      api(`/api/overview?base=${state.base}&account=${state.account}`),
      api(`/api/positions?account=${state.account}`),
      api("/api/recon"),
      api("/api/symbols?limit=200"),
      api(`/api/nav?account=${state.account}`),
      api(`/api/positions/legacy?account=${state.account}`),
    ]);
    state.accounts = ov.accounts;
    state.fx = ov.fx;
    state.posRows = pos;
    renderAccounts(ov);
    renderKpis(ov);
    renderReconStrip(ov);
    renderLedgerStrip(ov);
    renderPositions(pos);
    renderLegacy(legacy, ov);
    renderRecon(recon);
    renderSymbols(syms);
    renderNav(nav);
    renderMarkets(ov.markets);
    syncAccountSelect(ov.accounts);
    setStatus("已更新 · " + (ov.as_of || "").slice(11));
    $("#footMeta").textContent = `台账生成 ${ov.st_reference?.as_of || "—"} · 折算 ${ov.fx_note ? "实时汇率" : ""}`;
  } catch (e) {
    banner("载入失败：" + e.message);
    setStatus("失败");
  }
}

function syncAccountSelect(accounts) {
  const sel = $("#accountSel");
  if (sel.dataset.ready === "1") return;
  accounts.forEach(a => {
    const o = document.createElement("option");
    o.value = a.id; o.textContent = a.name;
    sel.appendChild(o);
  });
  sel.dataset.ready = "1";
  const sim = $("#simAccount");
  sim.innerHTML = accounts.filter(a => a.status === "active")
    .map(a => `<option value="${a.id}">${a.name}</option>`).join("");
}

function renderAccounts(ov) {
  const box = $("#acctCards");
  const rc = ov.recon || {};
  const corr = rc.equity_est != null && Math.abs(rc.adjust_usd || 0) > 100 ? rc : null;
  box.innerHTML = ov.accounts.map(a => {
    const status = a.status === "active" ? chip("active", "on") : a.status === "closed" ? chip("已关闭", "off") : chip(a.status);
    const mks = a.markets.map(m => {
      const mm = ov.markets.find(x => x.code === m) || { name: m, tradable: 1 };
      return chip(mm.name + (mm.tradable ? "" : " · 参考"), mm.tradable ? "" : "ref");
    }).join(" ");
    const adjHere = corr && a.id === "futu_main" ? corr : null;
    const equityShown = adjHere ? adjHere.equity_est : a.equity_usd;
    const eq = a.status === "closed"
      ? `<div class="equity">0 <small>已关闭 ${a.closed_at || ""} · 权益按 ST 口径归零</small></div>`
      : a.data_gap
        ? `<div class="equity">0 <small>无当前持仓数据</small></div>`
        : `<div class="equity">${moneyB(equityShown)} <small>${state.base} 折算${adjHere ? ` · 已按券商流水校正（台账 ${money(adjHere.equity_ledger)}）` : ""}</small></div>`;
    const rows = [];
    if (a.status !== "closed") {
      rows.push(`<div><span>持仓市值（实时）</span><b>${moneyB(a.positions_usd)}</b></div>`);
      rows.push(`<div><span>现金（券商期末）</span><b class="${signCls(a.cash_usd)}">${moneyB(a.cash_usd)}</b></div>`);
      rows.push(`<div><span>浮动盈亏</span><b class="${signCls(a.floating_usd)}">${signedB(a.floating_usd)}</b></div>`);
      if (adjHere) {
        rows.push(`<div><span>9-24 后成交未同步</span><b class="down">${signedB(adjHere.adjust_usd)}</b></div>`);
      }
    }
    rows.push(`<div><span>已实现${a.realized_detail_usd != null && Math.abs(a.realized_detail_usd - a.realized_usd) > 1 ? "（对账口径）" : ""}</span>
        <b class="${signCls(a.realized_usd)}">${signedB(a.realized_usd)}</b></div>`);
    if (a.realized_detail_usd != null && Math.abs(a.realized_detail_usd - a.realized_usd) > 1) {
      rows.push(`<div><span>逐笔明细合计（旧台账）</span><b class="${signCls(a.realized_detail_usd)}">${signedB(a.realized_detail_usd)}</b></div>`);
      rows.push(`<div><span>差额原因</span><b class="muted">资金矩阵 vs 逐笔（ST 已解释）</b></div>`);
    }
    if (a.legacy_positions_count) {
      rows.push(`<div><span>台账留档 ${a.legacy_positions_count} 行</span><b>${moneyB(a.legacy_positions_usd)}</b></div>`);
    }
    rows.push(`<div><span>记账币 · 当前持仓</span><b>${a.base_ccy} · ${a.positions_count}</b></div>`);
    const feedTxt = a.feed
      ? `<div class="feed"><span class="feed-dot ${a.feed_status === "live" ? "live" : "arch"}"></span>${a.feed}</div>`
      : "";
    return `
      <div class="card ${a.status === 'closed' ? 'closed' : ''}">
        <div class="card-top">
          <div>
            <div class="name">${a.name}</div>
            <div class="entity">${a.entity || a.broker}</div>
          </div>
          <div>${status}</div>
        </div>
        ${eq}
        <div class="card-rows">${rows.join("")}</div>
        <div>${mks}</div>
        ${feedTxt}
        <div class="card-note">${a.note || ""}${a.status === "closed" ? " 台账持仓只留档，不计入合并权益。" : ""}</div>
      </div>`;
  }).join("");
}

function renderKpis(ov) {
  const scopeTxt = ov.scope && ov.scope !== "all" ? "本账户" : "三账户合并";
  const rc = ov.recon || {};
  const useEst = rc.equity_est != null && Math.abs(rc.adjust_usd || 0) > 100;
  const equity = useEst ? rc.equity_est : ov.total_equity_usd;
  const lev = equity ? Math.abs(ov.total_positions_usd) / equity : null;
  const items = [
    ["权益", moneyB(equity), useEst ? `台账 ${money(rc.equity_ledger)} · 校正 ${signed(rc.adjust_usd)}` : `${scopeTxt} · ${state.base}`, useEst ? "ref" : ""],
    ["持仓市值", moneyB(ov.total_positions_usd), useEst ? "数量仍为 9-24 快照 · 未校正" : "空头为负", ""],
    ["现金", moneyB(ov.total_cash_usd), "券商 11-06 期末余额", ov.total_cash_usd < 0 ? "down" : ""],
    ["浮动盈亏", signedB(ov.total_floating_usd), "现价 vs FIFO 成本", signCls(ov.total_floating_usd)],
    ["净盈亏", signedB(ov.total_net_usd), "已实现 + 浮动", signCls(ov.total_net_usd)],
    ["杠杆", lev == null ? "—" : lev.toFixed(2) + "x", useEst ? "持仓 / 校正权益" : "持仓 / 权益", lev && lev > 1.5 ? "ref" : ""],
  ];
  $("#kpis").innerHTML = items.map(([k, v, s, cls]) => `
    <div class="kpi"><div class="k">${k}</div><div class="v ${cls}">${v}</div><div class="s">${s}</div></div>`).join("");
}

function renderReconStrip(ov) {
  const rc = ov.recon || {};
  const el = $("#reconStrip");
  if (rc.equity_est == null) { el.innerHTML = ""; return; }
  const stale = Object.entries(rc.stale || {})
    .map(([s, v]) => `${s} ${signed(v)}`).join(" · ") || "无";
  el.innerHTML = `
    <span class="ledger-tag">券商核对</span>
    <span>现金 ${money(rc.cash_broker_usd)} <span class="muted">（${(rc.cash_broker_asof || "").slice(0, 10)} 流水期末）</span></span>
    <span>·</span>
    <span>${(rc.positions_snapshot || "").slice(0, 10)} 后成交 ${signed(rc.adjust_usd)} <span class="muted">（${stale}）</span></span>
    <span>·</span>
    <span class="${rc.adjust_usd < -1 ? "down" : ""}">校正权益 ${money(rc.equity_est)}</span>
    <span class="muted">持仓数量未按流水重建，此处仅按成交金额估算</span>`;
}

function renderLedgerStrip(ov) {
  const st = ov.st_reference || {};
  const el = $("#ledgerStrip");
  if (!st.equity_usd) { el.innerHTML = ""; return; }
  const rc = ov.recon || {};
  const live = rc.equity_est != null && Math.abs(rc.adjust_usd || 0) > 100 ? rc.equity_est : ov.total_equity_usd;
  const d = st.delta_usd == null ? null : live - st.equity_usd;
  const deltaHtml = d == null
    ? `<span class="muted">台账是三账户合计，本账户视图不比较</span>`
    : `<span class="${signCls(d)}">${d > 0 ? "+" : "−"}${moneyB(Math.abs(d))}</span>
       <span class="muted">· 行情变动 + 9-24 后成交，不是数据错误</span>`;
  el.innerHTML = `
    <span class="ledger-tag">台账定稿</span>
    <span>${(st.as_of || "").slice(0, 10)} · 权益 ${money(st.equity_usd)} · 已实现 ${signed(st.pnl_tradeview_usd)}</span>
    <span class="ledger-delta">实时差额 ${deltaHtml}</span>
    <button class="btn-mini" id="ledgerJump">看台账 →</button>`;
  $("#ledgerJump").addEventListener("click", () => {
    document.getElementById("legacyTable").scrollIntoView({ behavior: "smooth", block: "start" });
  });
}

function renderLegacy(rows, ov) {
  $("#legacyHint").textContent = `${rows.length} 行台账（无证券代码）· 合计 ${money(ov.legacy?.value_usd)} · 不计入权益`;
  $("#legacyTable tbody").innerHTML = rows.map((r, i) => {
    const abnormal = (r.qty != null && Math.abs(r.qty) < 1) || r.avg_cost == null || r.price == null;
    return `<tr>
      <td>${r.account_name}</td><td>${r.id.split(":").pop()}</td><td>${r.market}</td>
      <td class="num">${num(r.qty, 4)}</td>
      <td class="num">${r.avg_cost == null ? "—" : num(r.avg_cost, 4)}</td>
      <td class="num">${r.price == null ? "—" : num(r.price, decimals(r.ccy))}</td>
      <td class="num">${r.value_native == null ? "—" : money(r.value_native, r.ccy)}</td>
      <td class="num ${signCls(r.pnl_native)}">${r.pnl_native == null ? "—" : signed(r.pnl_native, r.ccy)}</td>
      <td>${abnormal ? '<span class="chip off">台账异常行</span>' : '<span class="chip">历史</span>'}</td>
    </tr>`;
  }).join("") || `<tr><td colspan="9" class="muted">当前筛选没有台账行</td></tr>`;
}

function renderPositions(rows) {
  const tbody = $("#posTable tbody");
  if (!rows.length) {
    tbody.innerHTML = `<tr><td colspan="13" class="muted">当前筛选下没有持仓</td></tr>`;
    $("#posHint").textContent = state.account === "all" ? "无持仓" : "本账户在当前行情数据下无持仓";
    return;
  }
  let list = rows;
  if (state.sortKey) {
    const k = state.sortKey, d = state.sortDir;
    list = [...rows].sort((a, b) => {
      const va = a[k], vb = b[k];
      if (va == null && vb == null) return 0;
      if (va == null) return 1;
      if (vb == null) return -1;
      return (va - vb) * d;
    });
  }
  const groups = {};
  list.forEach(r => {
    if (!r.symbol) return;
    const k = r.market + "|" + r.symbol;
    (groups[k] = groups[k] || []).push(r);
  });

  let html = "";
  list.forEach(r => {
    const priceTag = r.price_source === "manual"
      ? `<span class="tag manual">手动价</span>` : r.price_source === "live"
        ? `<span class="tag live">◐ 5m</span>` : r.price_source === "close"
          ? `<span class="tag stale">收盘价</span>` : r.price_source === "st"
            ? `<span class="tag stale">台账价</span>` : `<span class="tag">无价</span>`;
    html += `<tr>
      <td>${r.account_name || r.account}</td>
      <td class="sym">${r.symbol || "—"}</td>
      <td>${r.name || (r.source === "manual" ? "手工录入" : '<span class="muted">台账未记名</span>')}</td>
      <td>${r.market}</td>
      <td>${r.side === "SHORT" ? '<span class="down">空</span>' : "多"}</td>
      <td class="num">${num(r.qty, 0)}</td>
      <td class="num">${num(r.avg_cost, 4)}</td>
      <td class="num" title="${r.price_asof || ""}">${num(r.price, decimals(r.ccy))} ${priceTag}</td>
      <td class="num">${money(r.value_native, r.ccy)}<span class="unit">${r.ccy}</span>${state.base !== "USD" && r.value_usd != null ? `<span class="unit">≈ ${moneyB(r.value_usd)}</span>` : ""}</td>
      <td class="num ${signCls(r.pnl_native)}">${signed(r.pnl_native, r.ccy)}</td>
      <td class="num">${pct(r.weight, 1)}</td>
      <td class="advice-cell">${(r.advice || []).map(a => `<span class="chip ${a.level} tag-mini">${a.text}</span>`).join("") || '<span class="muted">—</span>'}</td>
      <td><div class="row-actions">
        <button class="btn-mini ghost" data-price="${r.id}">价</button>
        <button class="btn-mini ghost" data-edit="${r.id}">改</button>
        <button class="btn-mini ghost" data-delpos="${r.id}" data-sym="${r.symbol || ""}">删</button>
      </div></td>
    </tr>`;
    const k = r.market + "|" + r.symbol;
    const peers = groups[k];
    if (peers && peers.length > 1 && r.merged && peers[peers.length - 1] === r) {
      const qty = peers.reduce((a, x) => a + (x.qty || 0), 0);
      const val = peers.reduce((a, x) => a + (x.value_usd || 0), 0);
      const pnl = peers.reduce((a, x) => a + (x.pnl_usd || 0), 0);
      const w = peers.reduce((a, x) => a + (x.weight || 0), 0);
      html += `<tr class="merged-row">
        <td>合并</td><td class="sym">${r.symbol}</td>
        <td>${r.name || ""}<span class="tag">${peers.length} 个账户</span></td>
        <td>${r.market}</td><td>—</td>
        <td class="num">${num(qty, 0)}</td><td class="num">—</td><td class="num">—</td>
        <td class="num">${moneyB(val)}</td>
        <td class="num ${signCls(pnl)}">${signedB(pnl)}</td>
        <td class="num">${pct(w, 1)}</td><td></td><td></td></tr>`;
    }
  });
  tbody.innerHTML = html;
  const live = rows.filter(r => r.price_source === "live").length;
  const manual = rows.filter(r => r.price_source === "manual").length;
  const closeN = rows.filter(r => r.price_source === "close").length;
  const parts = [`${rows.length} 行`];
  if (live) parts.push(`${live} 实时`);
  if (manual) parts.push(`${manual} 手动`);
  if (closeN) parts.push(`${closeN} 收盘价`);
  const other = rows.length - live - manual - closeN;
  if (other) parts.push(`${other} 台账价`);
  $("#posHint").textContent = parts.join(" · ") + " · 空头市值记为负";
}

function renderRecon(checks) {
  const cls = { pass: "on", warn: "ref", info: "" };
  $("#reconTable tbody").innerHTML = checks.map(c => `
    <tr>
      <td>${c.name}</td><td>${c.scope}</td>
      <td>${chip(c.status, cls[c.status] || "")}</td>
      <td class="muted">${c.value || ""}</td>
    </tr>`).join("");
}

function renderSymbols(rows) {
  $("#symTable tbody").innerHTML = rows.map(r => `
    <tr>
      <td class="sym">${r.symbol}</td><td>${r.name || "—"}</td>
      <td class="num">${r.n || 0}</td>
      <td class="num ${signCls(r.realized_usd)}">${signed(r.realized_usd)}</td>
      <td class="num">${num(r.open_long, 0)}</td>
      <td class="num">${num(r.open_short, 0)}</td>
      <td class="num">${r.events ? Math.round((r.wins / r.events) * 100) + "%" : "—"}</td>
    </tr>`).join("");
}

function renderNav(points) {
  if (!state.chartNav) state.chartNav = echarts.init($("#navChart"));
  const poems = points.filter(p => p.account_id === "poems_sg").sort((a, b) => a.as_of.localeCompare(b.as_of));
  const futu = points.filter(p => p.account_id === "futu_main");
  const x = [...new Set(points.map(p => p.as_of))].sort();
  const opt = {
    backgroundColor: "transparent",
    animation: false,
    tooltip: { trigger: "axis" },
    legend: { data: ["POEMS 月度权益", "FUTU 期末权益"], textStyle: { color: "#8b9bb4" }, top: 0 },
    grid: { left: 54, right: 18, top: 34, bottom: 28 },
    xAxis: { type: "category", data: x, axisLine: { lineStyle: { color: "#1f2a3a" } },
      axisLabel: { color: "#5f6f88", fontSize: 10,
        interval: (i) => i === 0 || i === x.length - 1 || String(x[i]).slice(0, 4) !== String(x[i - 1]).slice(0, 4) } },
    yAxis: { type: "value", scale: true, axisLabel: { color: "#5f6f88", fontSize: 10 }, splitLine: { lineStyle: { color: "#17202e" } } },
    series: [
      { name: "POEMS 月度权益", type: "line", smooth: true, symbol: "circle", symbolSize: 4,
        data: x.map(k => { const r = poems.find(p => p.as_of === k); return r ? toBase(r.equity) : null; }),
        lineStyle: { color: "#4f8cff", width: 2 }, itemStyle: { color: "#4f8cff" } },
      { name: "FUTU 期末权益", type: "scatter", symbolSize: 9,
        data: x.map(k => { const r = futu.find(p => p.as_of === k); return r ? toBase(r.equity) : null; }),
        itemStyle: { color: "#2ee6a8" } },
    ],
  };
  state.chartNav.setOption(opt, true);
  $("#navChart").dataset.dbg = JSON.stringify({ points: points.length, poems: poems.length,
    x: x.length, d0: opt.series[0].data.slice(0, 3), d1: opt.series[1].data.slice(0, 3) });
}

function renderMarkets(markets) {
  if (!state.chartMkt) state.chartMkt = echarts.init($("#mktChart"));
  const opt = {
    backgroundColor: "transparent",
    animation: false,
    tooltip: { trigger: "axis", formatter: p => `${p[0].name}<br/>净市值 ${ccSym()}${nf.format(p[0].value)}` },
    grid: { left: 74, right: 24, top: 12, bottom: 22 },
    xAxis: { type: "value", axisLabel: { color: "#5f6f88", fontSize: 10 }, splitLine: { lineStyle: { color: "#17202e" } } },
    yAxis: { type: "category", data: markets.map(m => m.name), axisLabel: { color: "#8b9bb4", fontSize: 11 }, axisLine: { lineStyle: { color: "#1f2a3a" } } },
    series: [{
      type: "bar", barWidth: 20,
      data: markets.map(m => ({ value: toBase(m.value_usd), itemStyle: { color: m.value_usd < 0 ? "#ff6b6b" : (m.tradable ? "#4f8cff" : "#5f6f88") } })),
      label: { show: true, position: "inside", color: "#0b0f17", fontSize: 10, fontWeight: 600,
        formatter: p => (p.value < 0 ? "−" : "") + ccSym() + nf0.format(Math.abs(p.value || 0)) },
    }],
  };
  state.chartMkt.setOption(opt, true);
  $("#mktChart").dataset.dbg = JSON.stringify({ n: markets.length, d: opt.series[0].data });
}

/* ============ 选股 ============ */
$("#searchBtn").addEventListener("click", doSearch);
$("#q").addEventListener("keydown", e => { if (e.key === "Enter") doSearch(); });

async function doSearch() {
  const q = $("#q").value.trim();
  if (!q) return;
  setStatus("搜索…");
  try {
    const rows = await api("/api/search?q=" + encodeURIComponent(q) + "&limit=12");
    renderSearch(rows);
    setStatus(`命中 ${rows.length} 条`);
  } catch (e) { banner("搜索失败：" + e.message); }
}

function renderSearch(rows) {
  const box = $("#searchResults");
  if (!rows.length) { box.innerHTML = `<div class="muted" style="padding:8px">没有找到可交易的标的</div>`; return; }
  box.innerHTML = rows.map(r => {
    const mkTxt = [r.market_name, r.exchDisp].filter(Boolean).join(" · ");
    return `
    <div class="res-item">
      <span class="sym">${r.symbol}</span>
      <span class="nm">${r.shortname || ""}</span>
      <span class="mk">${mkTxt}</span>
      ${r.tradable ? "" : '<span class="chip ref">参考</span>'}
      <button class="btn-mini" data-sym="${r.symbol}" data-mk="${r.market}">查看</button>
    </div>`;
  }).join("");
  $$(".res-item button", box).forEach(b => b.addEventListener("click", () => showSymbol(b.dataset.sym, b.dataset.mk)));
}

async function showSymbol(symbol, marketCode) {
  state.symbol = { symbol, market: marketCode };
  $("#symbolPanel").style.display = "";
  $("#symTitle").textContent = symbol;
  $("#symAsOf").textContent = "载入中…";
  setStatus("载入单票…");
  try {
    const info = await api(`/api/info?symbol=${encodeURIComponent(symbol)}&market_code=${marketCode}`);
    const raw = info.raw || {};
    $("#symAsOf").textContent = raw.fetched_at || "";
    const cards = [
      ["价格", num(raw.currentPrice, decimals(raw.currency)) + " " + (raw.currency || ""), ""],
      ["市值", raw.marketCap ? (raw.marketCap > 1e12 ? (raw.marketCap / 1e12).toFixed(2) + "T" : (raw.marketCap / 1e9).toFixed(1) + "B") : "—", ""],
      ["PE", num(info.pe, 2), ""],
      ["PB", num(info.pb, 2), ""],
      ["股息率", info.div_yield == null ? "—" : num(info.div_yield, 2) + "%", ""],
      ["ROE", info.roe == null ? "—" : num(info.roe, 2) + "%", ""],
    ];
    $("#symKpis").innerHTML = cards.map(([k, v]) => `<div class="kpi"><div class="k">${k}</div><div class="v">${v}</div></div>`).join("");
    // 只列出能交易该市场的活跃账户，避免选了必报错
    const eligible = state.accounts.filter(a => a.status === "active" && (a.markets || []).includes(marketCode));
    $("#simAccount").innerHTML = eligible.map(a => `<option value="${a.id}">${a.name}</option>`).join("");
    const simBtn = $("#simBtn");
    simBtn.disabled = !eligible.length;
    $("#simOut").textContent = eligible.length ? "" : "没有活跃账户能交易该市场";
    const hist = await api(`/api/history?symbol=${encodeURIComponent(symbol)}&market_code=${marketCode}&period=1y&interval=1d`);
    renderSymbolChart(hist.bars || []);
    setStatus("就绪");
  } catch (e) { banner("单票载入失败：" + e.message); }
}

function renderSymbolChart(bars) {
  if (!state.chartSym) state.chartSym = echarts.init($("#symChart"));
  state.chartSym.setOption({
    backgroundColor: "transparent",
    animation: false,
    tooltip: { trigger: "axis" },
    grid: { left: 56, right: 16, top: 16, bottom: 24 },
    xAxis: { type: "category", data: bars.map(b => b.t), axisLabel: { color: "#5f6f88", fontSize: 10 }, axisLine: { lineStyle: { color: "#1f2a3a" } } },
    yAxis: { scale: true, axisLabel: { color: "#5f6f88", fontSize: 10 }, splitLine: { lineStyle: { color: "#17202e" } } },
    series: [{
      type: "line", data: bars.map(b => b.c), showSymbol: false, smooth: false,
      lineStyle: { color: "#4f8cff", width: 1.6 },
      areaStyle: { color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
        { offset: 0, color: "rgba(79,140,255,.28)" }, { offset: 1, color: "rgba(79,140,255,0)" }]) },
    }],
  }, true);
}

$("#watchBtn").addEventListener("click", async () => {
  if (!state.symbol) return;
  try {
    await api("/api/watchlist", {
      method: "POST",
      body: JSON.stringify({ symbol: state.symbol.symbol.replace(/\.(HK|JK|SS|SZ)/, ""), market: state.symbol.market }),
    });
    await loadWatch();
    setStatus("已加入观察池");
  } catch (e) { banner("加入失败：" + e.message); }
});

$("#simBtn").addEventListener("click", async () => {
  if (!state.symbol) return;
  const account = $("#simAccount").value;
  const qty = parseFloat($("#simQty").value || "0");
  try {
    const r = await api("/api/simulate", {
      method: "POST",
      body: JSON.stringify({ account, symbol: state.symbol.symbol, qty }),
    });
    $("#simOut").innerHTML = `
      <div>${r.symbol} × ${num(r.qty, 0)} @ ${num(r.price, decimals(r.ccy))} ${r.ccy}</div>
      <div>新增市值 <b>$${nf.format(r.add_usd)}</b> · 占合并组合 ${r.weight_before == null ? "—" : (r.weight_before * 100).toFixed(2) + "%"}</div>
      <div>该市场持仓将变为 $${nf.format(r.market_value_after_usd)}</div>
      <div class="muted">${r.cash_note}</div>`;
  } catch (e) { $("#simOut").innerHTML = `<span class="down">${e.message}</span>`; }
});

/* ---------- 筛选 ---------- */
$("#screenForm").addEventListener("submit", async e => {
  e.preventDefault();
  await runScreen();
});

function buildScreenParams() {
  const f = new FormData($("#screenForm"));
  const markets = $$('input[name=market]:checked').map(x => x.value).join(",");
  const params = new URLSearchParams({ markets_q: markets || "US,HK,ID", allow_missing: $("#allowMissing").checked ? "true" : "false" });
  [["pe_max", "pe_max"], ["pb_max", "pb_max"], ["div_min", "div_min"],
   ["roe_min", "roe_min"], ["mcap_min", "mcap_min"], ["price_max", "price_max"]].forEach(([n, p]) => {
    const v = f.get(n);
    if (v !== null && v !== "") params.set(p, v);
  });
  return params;
}

async function runScreen() {
  const params = buildScreenParams();
  state.lastScreenQuery = params.toString();
  setStatus("筛选中…");
  try {
    const r = await api("/api/screen?" + state.lastScreenQuery);
    renderScreen(r);
    setStatus(r.rules && r.rules.length ? `命中 ${r.matched} / 可评估 ${r.evaluated}` : `未设条件 · 列出 ${r.evaluated} 只`);
  } catch (e) { banner("筛选失败：" + e.message); }
}

$("#exportBtn").addEventListener("click", () => {
  const q = state.lastScreenQuery || buildScreenParams().toString();
  const a = document.createElement("a");
  a.href = "/api/screen/export.csv?" + q;
  a.download = "st-screen.csv";
  document.body.appendChild(a);
  a.click();
  setTimeout(() => a.remove(), 0);
});

function fmtMcap(v, ccy) {
  if (v == null) return "—";
  if (v >= 1e12) return (v / 1e12).toFixed(2) + "T";
  if (v >= 1e9) return (v / 1e9).toFixed(1) + "B";
  return (v / 1e6).toFixed(1) + "M";
}

function renderScreen(r) {
  const cov = r.coverage || { fields: {} };
  const rulesCount = (r.rules || []).length;
  $("#coverageHint").textContent = rulesCount
    ? `股票池 ${r.coverage?.total || 0} 只 · 命中 ${r.matched} / 可评估 ${r.evaluated}`
    : `股票池 ${r.coverage?.total || 0} 只 · 未设条件，按市场列出`;
  const covHtml = Object.entries(cov.fields || {}).map(([k, v]) =>
    `<span>${({ pe: "PE", pb: "PB", div_yield: "股息率", roe: "ROE", mcap: "市值", price: "价格" })[k]} 覆盖 ${v.pct}%</span>`).join("");
  const warm = r.warm || {};
  const warmNote = warm.running ? `预热中 ${warm.done}/${warm.total}` : (warm.total ? `缓存 ${warm.done}/${warm.total}` : "尚未预热");
  $("#screenOut").innerHTML = `
    <div class="coverage">${covHtml}<span>${warmNote}</span></div>
    ${r.rows.length ? `<div class="table-wrap"><table class="grid"><thead>
      <tr><th>代码</th><th>名称</th><th>市场</th><th class="num">价格</th><th class="num">PE</th>
      <th class="num">PB</th><th class="num">股息率</th><th class="num">ROE</th><th class="num">市值</th>${rulesCount ? '<th class="num">命中</th>' : ""}</tr>
    </thead><tbody>${r.rows.map(x => `<tr>
      <td class="sym">${x.symbol}</td><td>${x.name || "—"}</td>
      <td>${x.market_name}${x.tradable ? "" : ' <span class="chip ref">参考</span>'}</td>
      <td class="num">${num(x.price, decimals(x.ccy))}</td>
      <td class="num">${x.pe == null ? (x.pe_note ? `<span title="${x.pe_note}">—</span>` : "—") : num(x.pe, 2)}</td>
      <td class="num">${x.pb == null ? (x.pb_note ? `<span title="${x.pb_note}">—</span>` : "—") : num(x.pb, 2)}</td>
      <td class="num">${x.div_yield == null ? "—" : num(x.div_yield, 2) + "%"}</td>
      <td class="num">${x.roe == null ? "—" : num(x.roe, 2) + "%"}</td>
      <td class="num">${fmtMcap(x.mcap)}${x.mcap != null && x.ccy ? ' <span class="unit">' + x.ccy + "</span>" : ""}</td>${rulesCount ? `<td class="num">${x.hits}</td>` : ""}</tr>`).join("")}</tbody></table></div>`
    : `<div class="muted" style="padding:8px">没有满足条件的标的。若刚启动，先点「预热数据」把基本面拉一遍。</div>`}
    <div class="hint" style="margin-top:8px">${r.note || ""}</div>`;
}

$("#warmBtn").addEventListener("click", async () => {
  try {
    const s = await api("/api/screen/warm", { method: "POST" });
    $("#warmStatus").textContent = `预热中 ${s.done}/${s.total}`;
    if (state.warmTimer) clearInterval(state.warmTimer);
    state.warmTimer = setInterval(async () => {
      const st = await api("/api/screen/warm/status");
      $("#warmStatus").textContent = st.running ? `预热中 ${st.done}/${st.total}` : `完成 ${st.done}/${st.total}（失败 ${st.errors}）`;
      if (!st.running) { clearInterval(state.warmTimer); state.warmTimer = null; runScreen(); }
    }, 2000);
  } catch (e) { banner("预热失败：" + e.message); }
});

/* ---------- 观察池 ---------- */
async function loadWatch() {
  try {
    const rows = await api("/api/watchlist");
    $("#watchList").innerHTML = rows.length ? rows.map(r => `
      <div class="res-item">
        <span class="sym">${r.symbol}</span>
        <span class="nm">${r.note || ""}</span>
        <span class="mk">${r.created_at || ""}</span>
        <button class="btn-mini" data-del="${r.symbol}">移除</button>
      </div>`).join("") : `<div class="muted" style="padding:6px">观察池为空。搜到感兴趣的标的，点「加入观察池」。</div>`;
    $$("[data-del]").forEach(b => b.addEventListener("click", async () => {
      await api("/api/watchlist/" + encodeURIComponent(b.dataset.del), { method: "DELETE" });
      loadWatch();
    }));
  } catch (e) { /* 静默 */ }
}

window.addEventListener("resize", () => {
  [state.chartNav, state.chartMkt, state.chartSym, state.chartPnl].forEach(c => c && c.resize());
});

/* ============ 持仓增 / 改 / 删 ============ */
let editingId = null;
let lastCheck = null;
let confirmArmed = false;
let checkTimer = null;

function resetCheckUI() {
  lastCheck = null;
  confirmArmed = false;
  $("#fCheck").innerHTML = "";
  $("#posModalSave").textContent = "保存";
}

function scheduleCheck() {
  clearTimeout(checkTimer);
  checkTimer = setTimeout(runCheck, 450);
}

async function runCheck() {
  const symbol = $("#fSymbol").value.trim().toUpperCase();
  const qty = parseFloat($("#fQty").value || "0");
  const cost = parseFloat($("#fCost").value || "0");
  if (!symbol || !(qty > 0) || !(cost > 0)) { $("#fCheck").innerHTML = ""; lastCheck = null; return; }
  const body = {
    account: editingId ? (state.posRows.find(r => r.id === editingId) || {}).account : $("#fAccount").value,
    symbol, side: $("#fSide").value, qty, avg_cost: cost,
  };
  if (editingId) body.exclude_id = editingId;
  try {
    const res = await api("/api/positions/check", { method: "POST", body: JSON.stringify(body) });
    lastCheck = res;
    confirmArmed = false;
    $("#posModalSave").textContent = res.requires_confirm ? "确认超限保存" : "保存";
    const lv = { pass: "ok", warn: "warn", danger: "danger" };
    $("#fCheck").innerHTML = `
      <div class="check-head">纪律预检 · 名义 ${res.notional_usd.toLocaleString()} · 账本权益将变为 ${res.equity_after.toLocaleString()} · 总敞口 ${res.leverage_after.toFixed(2)}x</div>
      ${(res.checks || []).map(c => `<div class="check-row ${c.level}">
        <span class="chip ${lv[c.level] || "info"}">${c.level === "pass" ? "通过" : c.level === "warn" ? "超限" : "失效"}</span>
        <span>${c.text}</span></div>`).join("")}`;
  } catch (e) {
    $("#fCheck").innerHTML = `<div class="check-row danger"><span class="chip danger">失效</span><span>${e.message}</span></div>`;
    lastCheck = null;
  }
}

function openPosModal(row) {
  editingId = row ? row.id : null;
  resetCheckUI();
  $("#posModalTitle").textContent = row ? `修改持仓 · ${row.symbol}` : "新增持仓";
  $("#fErr").textContent = "";
  const active = state.accounts.filter(a => a.status === "active");
  $("#fAccount").innerHTML = active.map(a => `<option value="${a.id}">${a.name}（${a.markets.join("/")}）</option>`).join("");
  if (row) {
    $("#fAccount").value = row.account;
    $("#fAccount").disabled = true;
    $("#fSymbol").value = row.symbol || "";
    $("#fName").value = row.name || "";
    $("#fSide").value = row.side || "LONG";
    $("#fQty").value = row.qty ?? "";
    $("#fCost").value = row.avg_cost ?? "";
    $("#fOpen").value = (row.price_asof || "").slice(0, 10) || "";
    $("#fNote").value = row.note || "";
    $("#fTip").textContent = `当前现价 ${money(row.price, row.ccy)} · 市值 ${money(row.value_native, row.ccy)} · 权重 ${pct(row.weight, 1)}`;
  } else {
    $("#fAccount").disabled = false;
    $("#fSymbol").value = "";
    $("#fName").value = "";
    $("#fSide").value = "LONG";
    $("#fQty").value = "";
    $("#fCost").value = "";
    $("#fOpen").value = new Date().toISOString().slice(0, 10);
    $("#fNote").value = "";
    $("#fTip").textContent = "新增会立即计入组合权益与权重（现金不变，相当于建仓金额由保证金/自有资金承担）。";
  }
  $("#posModal").classList.remove("hidden");
  if (!row) setTimeout(() => $("#fSymbol").focus(), 30);
  else setTimeout(scheduleCheck, 60);
}

function closePosModal() { $("#posModal").classList.add("hidden"); editingId = null; resetCheckUI(); }

["fSymbol", "fQty", "fCost", "fSide", "fAccount"].forEach(id => {
  const el = $("#" + id);
  el.addEventListener("input", scheduleCheck);
  el.addEventListener("change", scheduleCheck);
});

$("#addPosBtn").addEventListener("click", () => openPosModal(null));
$("#posModalClose").addEventListener("click", closePosModal);
$("#posModalCancel").addEventListener("click", closePosModal);
$("#posModal").addEventListener("click", e => { if (e.target.id === "posModal") closePosModal(); });
$("#posModalSave").addEventListener("click", async () => {
  if (lastCheck && lastCheck.requires_confirm && !confirmArmed) {
    confirmArmed = true;
    $("#posModalSave").textContent = "仍要保存（已超纪律）";
    $("#fErr").textContent = "预检有「失效」项，再点一次才会写入。";
    return;
  }
  const payload = {
    account: $("#fAccount").value,
    symbol: $("#fSymbol").value.trim().toUpperCase(),
    name: $("#fName").value.trim(),
    side: $("#fSide").value,
    qty: parseFloat($("#fQty").value || "0"),
    avg_cost: parseFloat($("#fCost").value || "0"),
    opened_at: $("#fOpen").value || undefined,
    note: $("#fNote").value,
  };
  $("#fErr").textContent = "";
  const wasEdit = !!editingId;
  try {
    if (wasEdit) {
      delete payload.account;
      await api("/api/positions/" + encodeURIComponent(editingId), { method: "PUT", body: JSON.stringify(payload) });
    } else {
      await api("/api/positions", { method: "POST", body: JSON.stringify(payload) });
    }
    closePosModal();
    await loadPortfolio();
    setStatus(wasEdit ? "已修改持仓" : "已新增持仓");
  } catch (e) { $("#fErr").textContent = e.message; }
});

$("#posTable tbody").addEventListener("click", async e => {
  const priceBtn = e.target.closest("[data-price]");
  const editBtn = e.target.closest("[data-edit]");
  const delBtn = e.target.closest("[data-delpos]");
  if (priceBtn) {
    const row = state.posRows.find(r => r.id === priceBtn.dataset.price);
    if (row) openPriceModal(row);
    return;
  }
  if (editBtn) {
    const row = state.posRows.find(r => r.id === editBtn.dataset.edit);
    if (row) openPosModal(row);
    return;
  }
  if (delBtn) {
    const sym = delBtn.dataset.sym || "该持仓";
    if (!confirm(`删除 ${sym} 这一行？删除后立即从权益与权重里剔除（原始行不保留，可重新新增）。`)) return;
    try {
      await api("/api/positions/" + encodeURIComponent(delBtn.dataset.delpos), { method: "DELETE" });
      await loadPortfolio();
      setStatus("已删除持仓");
    } catch (err) { banner("删除失败：" + err.message); }
  }
});

/* ============ 手动定价 ============ */
let pricingRow = null;

function openPriceModal(row) {
  pricingRow = row;
  $("#pSymbol").textContent = `手动定价 · ${row.symbol} ${row.name || ""}`;
  $("#pPrice").value = row.price ?? "";
  $("#pNote").value = "";
  $("#pErr").textContent = "";
  $("#pClear").style.display = row.price_source === "manual" ? "" : "none";
  $("#pTip").textContent = `当前来源：${{ manual: "手动", live: "实时", close: "收盘价", st: "台账价", none: "无价" }[row.price_source] || row.price_source}（${row.price_asof || "—"}）· 币种 ${row.ccy}。保存后该标的的市值、盈亏、权重、风控全部按新价格重算，直到清除。`;
  $("#priceModal").classList.remove("hidden");
  setTimeout(() => $("#pPrice").focus(), 30);
}

function closePriceModal() {
  $("#priceModal").classList.add("hidden");
  pricingRow = null;
}

$("#pClose").addEventListener("click", closePriceModal);
$("#pCancel").addEventListener("click", closePriceModal);
$("#priceModal").addEventListener("click", e => { if (e.target.id === "priceModal") closePriceModal(); });

$("#pSave").addEventListener("click", async () => {
  if (!pricingRow) return;
  const price = parseFloat($("#pPrice").value || "NaN");
  if (!(price > 0)) { $("#pErr").textContent = "请输入大于 0 的价格"; return; }
  const symLabel = pricingRow.symbol;
  try {
    await api("/api/prices/manual", {
      method: "POST",
      body: JSON.stringify({
        symbol: pricingRow.symbol, market: pricingRow.market,
        price, note: $("#pNote").value.trim(),
      }),
    });
    closePriceModal();
    await loadPortfolio();
    setStatus(`已手动定价 ${symLabel}`);
  } catch (e) { $("#pErr").textContent = "保存失败：" + e.message; }
});

$("#pClear").addEventListener("click", async () => {
  if (!pricingRow) return;
  try {
    await api(`/api/prices/manual?symbol=${encodeURIComponent(pricingRow.symbol)}&market_code=${encodeURIComponent(pricingRow.market || "")}`,
      { method: "DELETE" });
    closePriceModal();
    await loadPortfolio();
    setStatus("已清除手动价，恢复自动取价");
  } catch (e) { $("#pErr").textContent = "清除失败：" + e.message; }
});

$("#pPrice").addEventListener("keydown", e => { if (e.key === "Enter") $("#pSave").click(); });

/* ============ 分析页 ============ */
const LEVEL_TXT = { danger: "高", warn: "中", info: "提示" };

/* ================= 交易关键指标 ================= */
let mxFlowOffset = 0;

const nf2 = new Intl.NumberFormat("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
function money2(v) {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  return (v < 0 ? "−" : "") + "$" + nf2.format(Math.abs(v));
}
function signed2(v) {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  return (v > 0 ? "+" : v < 0 ? "−" : "") + "$" + nf2.format(Math.abs(v));
}

function mxCcyTag(a) {
  return a.id === "futu_main" ? "USD · 券商流水" : (a.id === "poems_sg" ? "USD 结算 · SG 活跃（2 期单据）" : "IDR · 印尼活跃");
}

function renderMetrics(m) {
  const ov = m.overall || {};
  const accs = m.accounts || [];
  const recon = m.recon || {};
  const futu = accs.find(a => a.id === "futu_main") || {};
  const sg = accs.find(a => a.id === "poems_sg") || {};

  const warnCnt = (m.recon_rows || []).filter(r => r.level !== "ok").length;
  $("#mxHint").textContent =
    `${ov.start || "—"} → ${ov.end || "—"} · ${ov.years || 0} 年 · 对账 ${warnCnt} 项待解释`;

  const pi = accs.find(x => x.id === "poems_id") || {};
  const hero = [
    ["总交易期间", `${ov.years} 年`, `${ov.start || "—"} → ${ov.end || "—"} · ${nf0.format(ov.days || 0)} 天`, ""],
    ["平仓笔数", nf0.format(ov.closed_trades || 0), `券商成交 ${nf0.format(ov.broker_trades || 0)} 笔 · 含 POEMS ID ${nf0.format(pi.closed_trades || 0)}`, ""],
    ["已实现盈亏", signed(ov.realized_usd), `富途 ${signed(futu.realized_usd)} · SG ${signed(sg.realized_usd)} · ID ${signed(pi.realized_usd)}`, signCls(ov.realized_usd)],
    ["净外部投入", money2(ov.net_external_usd), `存入 ${money2(futu.deposit_usd)} · 取出 ${money2(futu.withdraw_usd)} · 迁移 ${money2(futu.migration_usd)}`, ""],
    ["平均持仓", ov.avg_hold_days == null ? "—" : num(ov.avg_hold_days, 1) + " 天", `台账可算部分：POEMS SG ${nf0.format(sg.closed_trades || 0)} + ID ${nf0.format(pi.closed_trades || 0)} 笔`, ""],
    ["单笔平均回报", pct(ov.avg_month_return, 2) + " / 月", `年化 ${pct(ov.annual_return, 1)}（SG 单笔口径）· ID 资金口径 ${pct(pi.avg_month_return, 2)}/月`, signCls(ov.avg_month_return)],
  ];
  $("#mxHero").innerHTML = hero.map(([k, v, s, cls]) => `
    <div class="kpi"><div class="k">${k}</div><div class="v ${cls}">${v}</div><div class="s">${s}</div></div>`).join("");

  $("#mxAccounts").innerHTML = accs.map(a => {
    const rows = [
      ["交易期间", `${a.start || "—"} → ${a.end || "—"}`, `${nf0.format(a.days || 0)} 天`],
      ["笔数", a.closed_trades ? `${nf0.format(a.closed_trades)} 笔平仓` : `${nf0.format(a.trade_events || 0)} 笔平仓`, `胜率 ${a.win_rate == null ? "—" : pct(a.win_rate, 1)}`],
      ["已实现", a.realized_usd ? signed(a.realized_usd) : "—", a.realized_detail_usd && a.realized_detail_usd !== a.realized_usd ? `明细合计 ${signed(a.realized_detail_usd)}` : (a.closed_trades || a.trade_events ? "FIFO 平仓" : "无平仓记录")],
      ["出入金", a.net_external_usd == null ? "台账未记" : `净 ${signed(a.net_external_usd)}`, a.deposit_usd == null ? "无流水" : `入 ${money2(a.deposit_usd)} / 出 ${money2(a.withdraw_usd)}`],
      ["持仓时长", a.avg_hold_days == null ? "缺开仓日" : `${num(a.avg_hold_days, 1)} 天`, a.avg_hold_days == null ? "事件只记平仓日" : "平均值"],
      ["回报率", a.avg_month_return == null ? (a.capital_weighted_annual == null ? "—" : pct(a.capital_weighted_annual, 1) + " / 年") : pct(a.avg_month_return, 2) + " / 月",
        a.annual_return == null ? "资金加权口径" : pct(a.annual_return, 1) + " / 年" + (a.return_basis ? `（${a.return_basis}）` : "")],
    ];
    const zero = !(a.closed_trades || a.trade_events || a.net_external_usd);
    return `<div class="mx-acc ${zero ? "muted" : ""}">
      <div class="mx-top"><span class="mx-name">${a.name}</span><span class="mx-period">${mxCcyTag(a)}</span></div>
      <div class="mx-big ${signCls(a.realized_usd)}">${(a.closed_trades || a.trade_events) ? signed(a.realized_usd) : "—"}<small>已实现</small></div>
      <div class="mx-rows">${rows.map(([k, v, s]) => `<div><span>${k}</span><b>${v}<span class="muted"> · ${s}</span></b></div>`).join("")}</div>
      <div class="mx-tags">${(a.tags || []).map(t => chip(`${t.k} ${t.v}`, "tag-mini " + (t.k.includes("缺") ? "warn" : "info"))).join("")}</div>
    </div>`;
  }).join("");

  $("#mxRecon").querySelector("tbody").innerHTML = (m.recon_rows || []).map(r => `
    <tr><td>${r.item} ${chip({ ok: "通过", warn: "待解释", danger: "差异大" }[r.level] || r.level, r.level === "ok" ? "ok" : r.level)}</td>
      <td>${r.broker}</td><td>${r.ledger}</td>
      <td class="num ${r.level === "danger" ? "down" : ""}">${r.diff}</td>
      <td>${r.note}</td></tr>`).join("");

  const st = m.statement || {};
  const buckets = [
    ["外部存入", st.deposit?.usd, "银行转存 5 笔", "in"],
    ["外部取出", st.withdraw?.usd, "银行转取 2 笔", "out"],
    ["资产迁移/换汇", st.migration?.usd, "USD/HKD 互转净额", st.migration?.usd >= 0 ? "in" : "out"],
    ["交易净额", st.trading_net?.usd, `成交 ${nf0.format(st.trade_count || 0)} 笔（买卖+费用）`, st.trading_net?.usd >= 0 ? "in" : "out"],
    ["股息与税", st.income?.usd, "现金分红 - 预扣税", st.income?.usd >= 0 ? "in" : "out"],
    ["利息/融券", st.cost?.usd, "融资利息 + 融券费", "out"],
    ["基金申赎", st.fund_flow?.usd, "货币基金", st.fund_flow?.usd >= 0 ? "in" : "out"],
    ["期末现金", recon.cash_broker_usd, `${(recon.cash_broker_asof || "").slice(0, 10)} 余额`, "in"],
  ];
  const maxAbs = Math.max(...buckets.map(b => Math.abs(b[1] || 0)), 1);
  $("#mxBuckets").innerHTML = buckets.map(([k, v, s, dir]) => `
    <div class="mx-bucket"><div class="bk">${k}</div>
      <div class="bv ${v == null ? "" : (v >= 0 ? "up" : "down")}">${signed2(v)}</div>
      <div class="bs">${s}</div>
      <div class="mx-bar"><i class="${dir === "out" ? "neg" : ""}" style="width:${Math.min(100, Math.abs(v || 0) / maxAbs * 100)}%"></i></div>
    </div>`).join("");

  $("#mxStTag").textContent = `${st.rows ? nf0.format(st.rows) : 0} 行 · ${(st.period_start || "").slice(0, 10)} 起`;
  loadFlows(true);
}

async function loadFlows(reset) {
  if (reset) mxFlowOffset = 0;
  try {
    const r = await api(`/api/statement/rows?limit=50&offset=${mxFlowOffset}`);
    const tb = $("#mxFlows").querySelector("tbody");
    const html = r.rows.map(x => `<tr>
      <td>${(x.occurred_at || "").slice(5, 16)}</td>
      <td>${x.trade_type}${x.symbol && x.symbol !== "--" ? `<span class="fx">${x.symbol}</span>` : ""}</td>
      <td class="num ${x.amount >= 0 ? "up" : "down"}">${x.ccy === "USD" ? signed2(x.amount) : signed(x.amount, x.ccy)}</td>
      <td class="num">${nf2.format(x.balance)}</td><td>${x.ccy}</td></tr>`).join("");
    tb.innerHTML = reset ? html : tb.innerHTML + html;
    mxFlowOffset += r.rows.length;
    $("#mxFlowHint").textContent = `倒序 · 已载 ${mxFlowOffset} / ${nf0.format(r.total)} 条`;
    $("#mxFlowMore").style.display = mxFlowOffset >= r.total ? "none" : "";
  } catch (e) { $("#mxFlowHint").textContent = "流水载入失败： " + e.message; }
}

async function loadMetrics(force) {
  $("#mxHint").textContent = "重算中…";
  try {
    const m = await api("/api/metrics", force ? { cache: "no-store" } : {});
    renderMetrics(m);
  } catch (e) {
    $("#mxHint").textContent = "指标载入失败：" + e.message;
    banner("指标载入失败：" + e.message);
  }
}

async function loadPoems(force) {
  try {
    const p = await api("/api/poems", force ? { cache: "no-store" } : {});
    renderPoems(p);
  } catch (e) {
    $("#pmTag").textContent = "台账载入失败：" + e.message;
  }
}

function renderPoems(p) {
  if (!p || p.error) { $("#pmTag").textContent = "POEMS 台账缺失"; return; }
  const v = p.valuation || {}, fl = p.flows || {}, mf = p.mf || {};
  const trs = p.trades || [], lots = p.closed_lots || [];
  const realized = lots.reduce((s, c) => s + (c.pnl || 0), 0);
  const wins = lots.filter(c => (c.pnl || 0) > 0).length;
  const winRate = lots.length ? wins / lots.length : null;
  $("#pmTag").textContent = `${p.meta.poems_id} · ${p.meta.ccy} · ${lots.length} 个平仓批次`;

  const hero = [
    ["交易期间", `${(p.period || "").includes("→") ? p.period.split("→")[1].trim() : "—"} 为止`, p.period || "", ""],
    ["成交笔数", nf0.format(trs.length), `买入 ${trs.filter(t => t.side === "B").length} · 卖出 ${trs.filter(t => t.side === "S").length}（单侧）`, ""],
    ["已实现盈亏", nf0.format(realized), `胜率 ${winRate == null ? "—" : pct(winRate, 1)} · ${lots.length} 个批次`, signCls(realized)],
    ["账户现金", nf0.format(v.cash_idr || 0), "9-30 对账 + 10–11 月成交净额", ""],
    ["货币基金", nf0.format(mf.units || 0) + " 份", `NAV ${num(mf.nav, 2)} · ${(mf.period_end || "").slice(0, 7)}`, ""],
    ["账户总资产", nf0.format(v.total_idr || 0), `股票 ${nf0.format(v.stocks_idr || 0)} + 基金 ${nf0.format(v.mf_idr || 0)}（IDR）`, ""],
  ];
  $("#pmHero").innerHTML = hero.map(([k, val, sub, cls]) => `
    <div class="kpi"><div class="k">${k}</div><div class="v ${cls}">${val}</div><div class="s">${sub}</div></div>`).join("");

  const pos = p.positions || [];
  $("#pmPosHint").textContent = `${pos.length} 行 · 现价随行情刷新`;
  $("#pmPos tbody").innerHTML = pos.map(r => `
    <tr><td>${r.symbol.replace(".JK", "")} <span class="muted">${(r.name || "").slice(0, 14)}</span></td>
      <td class="num">${nf0.format(r.qty)}</td>
      <td class="num">${num(r.avg_cost, 2)}</td>
      <td class="num">${num(r.price, 2)}</td>
      <td class="num">${nf0.format(r.value_native || 0)}</td>
      <td class="num ${(r.pnl_native || 0) >= 0 ? "up" : "down"}">${nf0.format(r.pnl_native || 0)}</td>
      <td>${chip(r.price_source === "live" ? "实时" : r.price_source === "manual" ? "人工" : r.price_source === "close" ? "收盘" : "台账", "tag-mini info")}</td></tr>`).join("");

  $("#pmTr tbody").innerHTML = trs.slice(-10).reverse().map(t => `
    <tr><td>${t.date}</td>
      <td>${chip(t.side === "B" ? "买入" : "卖出", "tag-mini " + (t.side === "B" ? "info" : "ok"))}</td>
      <td>${t.code}</td><td class="num">${nf0.format(t.qty)}</td>
      <td class="num">${num(t.price, 2)}</td>
      <td class="num ${t.net >= 0 ? "up" : "down"}">${nf0.format(t.net)}</td></tr>`).join("");

  $("#pmMonth tbody").innerHTML = (p.monthly || []).filter(m => m.cash != null || (m.holdings || []).length || m.tc_trades).slice(-9).map(m => {
    const held = (m.holdings || []).length;
    return `<tr><td>${(m.period_end || "").slice(0, 7)}</td>
      <td class="num">${m.cash == null ? "—" : nf0.format(m.cash)}</td>
      <td class="num">${held}</td>
      <td class="num">${nf0.format(m.tc_trades || 0)}</td>
      <td class="num">${m.deposit ? nf0.format(m.deposit) : "—"}</td>
      <td class="num">${m.withdraw ? nf0.format(m.withdraw) : "—"}</td>
      <td class="num">${m.dividend ? nf0.format(m.dividend) : "—"}</td></tr>`;
  }).join("");

  $("#pmChecks").innerHTML = (p.checks || []).map(c =>
    `<span class="chip ${c.status === "ok" ? "ok" : c.status === "warn" ? "warn" : "danger"}">${c.item}：${c.note}</span>`).join("");

  const st = $("#pmBtn");
  if (st) st.dataset.ready = "1";
}

async function loadPoemsSg(force) {
  try {
    const p = await api("/api/poems/sg", force ? { cache: "no-store" } : {});
    renderPoemsSg(p);
  } catch (e) {
    $("#psTag").textContent = "SG 月结单载入失败：" + e.message;
  }
}

function renderPoemsSg(p) {
  if (!p || p.error) { $("#psTag").textContent = "POEMS SG 台账缺失"; return; }
  const cur = p.current || {}, v = p.valuation || {}, dv = p.dividends || {};
  const mf = cur.mmf || {}, cash = cur.cash || {};
  $("#psTag").textContent = `${p.meta.acct_no} · ${p.meta.ac_type} · ${p.meta.docs || 0} 份单据`;

  const eq = cur.equities || [];
  const hero = [
    ["账户权益", money(v.total_usd || 0), `${nf0.format(v.total_sgd || 0)} SGD · ${cur.as_of || ""}`, ""],
    ["持仓市值", money(v.stocks_usd || 0), eq.length ? `${nf0.format(eq[0].qty)} 股 ${eq[0].symbol}（实时价）` : "无股票持仓", ""],
    ["现金", money(v.cash_usd || 0), `${cash.ccy || "USD"} · 月结单期末`, ""],
    ["货币基金", nf0.format(mf.units || 0) + " 份", `NAV ${num(mf.nav, 4)} · ${money(mf.value_usd || 0)}`, ""],
    ["2025 股息", money((dv.totals || {}).net_usd || 0), `税前 ${money((dv.totals || {}).gross_usd || 0)} · 预扣 30%`, ""],
    ["单据覆盖", `${p.meta.docs || 0} 份`, p.meta.gap || "", "warn"],
  ];
  $("#psHero").innerHTML = hero.map(([k, val, sub, cls]) => `
    <div class="kpi"><div class="k">${k}</div><div class="v ${cls}">${val}</div><div class="s">${sub}</div></div>`).join("");

  const pos = p.positions || [];
  $("#psPosHint").textContent = `${pos.length} 行 · ${cur.as_of || ""} 月结单`;
  $("#psPos tbody").innerHTML = pos.map(r => `
    <tr><td>${r.symbol.replace(".SG", "")} <span class="muted">${(r.name || "").slice(0, 18)}</span></td>
      <td class="num">${num(r.qty, 4)}</td>
      <td class="num">${num(r.avg_cost, 4)}</td>
      <td class="num">${num(r.price, 4)}</td>
      <td class="num">${nf0.format(r.value_usd || 0)}</td>
      <td class="num ${(r.pnl_usd || 0) >= 0 ? "up" : "down"}">${nf0.format(r.pnl_usd || 0)}</td>
      <td>${chip(r.price_source === "live" ? "实时" : r.price_source === "manual" ? "人工" : r.price_source === "close" ? "收盘" : "台账", "tag-mini info")}</td></tr>`).join("");

  const snaps = p.snapshots || [];
  if (snaps.length >= 2) {
    const a = snaps[0], b = snaps[snaps.length - 1];
    const ea = a.equities[0], eb = b.equities[0];
    const ta = a.cash.amount + a.mmf.value_usd + ea.mv_usd;
    const tb = b.cash.amount + b.mmf.value_usd + eb.mv_usd;
    const line = (label, x, y, fmt) => {
      const d = (y || 0) - (x || 0);
      return `<tr><td>${label}</td><td class="num">${fmt(x)}</td><td class="num">${fmt(y)}</td>
        <td class="num ${d >= 0 ? "up" : "down"}">${d >= 0 ? "+" : ""}${fmt(d)}</td></tr>`;
    };
    $("#psSnap tbody").innerHTML = [
      line("现金 USD", a.cash.amount, b.cash.amount, x => num(x, 2)),
      line("基金 USD", a.mmf.value_usd, b.mmf.value_usd, x => num(x, 2)),
      line("SQQQ 股数", ea.qty, eb.qty, x => nf0.format(x)),
      line("SQQQ 市值 USD", ea.mv_usd, eb.mv_usd, x => nf0.format(x)),
      line("合计 USD", ta, tb, x => num(x, 2)),
      line("合计 SGD（对账单口径）", a.total.mv_sgd, b.total.mv_sgd, x => nf0.format(x)),
      line("未实现盈亏 SGD", a.total.unreal_sgd, b.total.unreal_sgd, x => nf0.format(x)),
    ].join("");
  }

  const rows = dv.rows || [];
  $("#psDivHint").textContent = dv.totals
    ? `${rows.length} 笔 · 净入账 ${money(dv.totals.credited_usd)}（${nf0.format(dv.totals.credited_sgd)} SGD）`
    : "—";
  $("#psDiv tbody").innerHTML = rows.map(r => `
    <tr><td>${r.pay_date}</td><td>SQQQ</td>
      <td class="num">${nf0.format(r.shares)}</td>
      <td class="num">${num(r.rate, 6)}</td>
      <td class="num">${num(r.gross, 2)}</td>
      <td class="num down">−${num(r.tax, 2)}</td>
      <td class="num up">${num(r.credited_usd, 2)}</td></tr>`).join("");

  $("#psChecks").innerHTML = (p.checks || []).map(c =>
    `<span class="chip ${c.status === "ok" ? "ok" : c.status === "warn" ? "warn" : "danger"}">${c.k}：${c.detail}</span>`).join("");

  const btn = $("#psBtn");
  if (btn) btn.dataset.ready = "1";
}

async function loadInsights() {
  setStatus("分析中…");
  try {
    const [ins, hyg, disc] = await Promise.all([
      api("/api/insights"), api("/api/hygiene"), api("/api/discipline")]);
    loadMetrics();
    loadPoems();
    loadPoemsSg();
    renderHygiene(hyg);
    renderTimeline(ins.timeline);
    renderProfile(ins.profile);
    renderDiscipline(disc);
    renderActions(ins.actions);
    renderRisks(ins.risks);
    setStatus("分析完成 · " + (ins.as_of || "").slice(11));
  } catch (e) { banner("分析失败：" + e.message); }
}

function renderDiscipline(d) {
  const s = d.summary || {};
  $("#ruleHint").textContent =
    `通过 ${s.pass || 0} · 超限 ${s.warn || 0} · 失效 ${s.danger || 0}`;
  const txt = { pass: "通过", warn: "超限", danger: "失效", info: "提示" };
  $("#ruleList").innerHTML = (d.rules || []).map(r => `
    <div class="rule-row ${r.status}">
      <span class="chip ${r.status === "pass" ? "ok" : r.status}">${txt[r.status] || r.status}</span>
      <div class="r-body">
        <div class="r-title">${r.label}<span class="r-limit">上限 ${r.limit}</span></div>
        <div class="r-actual">${r.actual}</div>
        <div class="r-detail">${r.detail}</div>
      </div>
    </div>`).join("");
}

$("#ruleBtn").addEventListener("click", async () => {
  try {
    setStatus("纪律检查…");
    renderDiscipline(await api("/api/discipline"));
    setStatus("检查完成 · " + new Date().toTimeString().slice(0, 8));
  } catch (e) { banner("纪律检查失败：" + e.message); }
});

let hygRows = [];
let hygExpanded = false;

function renderHygiene(h) {
  const s = h.summary || {};
  const names = { closed_trades: "已平仓台账", trade_events: "成交事件", positions: "持仓" };
  $("#hygieneSummary").innerHTML = Object.entries(s).map(([t, v]) => {
    const total = (v.ok || 0) + (v.flag || 0) + (v.reject || 0);
    return `<div>${names[t] || t}：共 ${total} 行<b class="up">有效 ${v.ok || 0}</b>
      <b class="down">剔除 ${v.reject || 0}</b><b class="muted">可疑 ${v.flag || 0}</b></div>`;
  }).join("");
  $("#hygieneHint").textContent = `${h.flagged_count} 行被标记`;
  hygRows = (h.flagged || []).slice().sort((a, b) =>
    (a.quality === "reject" ? 0 : 1) - (b.quality === "reject" ? 0 : 1));
  hygExpanded = false;
  $("#hygToggle").textContent = `显示全部 ${hygRows.length} 行`;
  paintHygiene();
}

function paintHygiene() {
  const names = { closed_trades: "已平仓台账", trade_events: "成交事件", positions: "持仓" };
  const rows = hygExpanded ? hygRows : hygRows.slice(0, 10);
  $("#hygieneTable tbody").innerHTML = rows.length ? rows.map(r => `
    <tr>
      <td>${names[r.table] || r.table}</td>
      <td class="sym">${r.id}</td>
      <td>${r.quality === "reject" ? '<span class="chip off">剔除</span>' : '<span class="chip ref">可疑</span>'}</td>
      <td class="muted" style="white-space:normal">${r.note || ""}</td>
      <td>${r.account || ""}</td>
    </tr>`).join("") : `<tr><td colspan="5" class="muted">没有异常行</td></tr>`;
}

$("#hygToggle").addEventListener("click", () => {
  hygExpanded = !hygExpanded;
  $("#hygToggle").textContent = hygExpanded ? "只看前 10 行" : `显示全部 ${hygRows.length} 行`;
  paintHygiene();
});

function renderTimeline(tl) {
  const cards = (tl.phases || []).map(p => {
    const acc = (p.accounts || []).join(" + ");
    const top = (p.top_symbols || []).map(t => `${t.symbol} ${t.n} 笔`).join("、");
    return `<div class="phase">
      <div class="ph-top"><span class="ph-date">${p.start} → ${p.end} · ${p.months} 个月</span>
        <span class="chip">${acc}</span></div>
      <div class="ph-style">${p.style}</div>
      <div class="ph-rows">
        <div><span>交易笔数</span><b>${p.n}</b></div>
        <div><span>已实现盈亏</span><b class="${signCls(p.pnl_usd)}">${signed(p.pnl_usd)}</b></div>
        <div><span>胜率 / 赔率</span><b>${p.win_rate == null ? "—" : (p.win_rate * 100).toFixed(0) + "%"} / ${p.payoff == null ? "—" : p.payoff.toFixed(2)}</b></div>
        <div><span>平均持有</span><b>${p.avg_hold == null ? "无记录" : p.avg_hold + " 天"}</b></div>
        <div><span>杠杆 ETF 占比</span><b>${(p.levered_share * 100).toFixed(0)}%</b></div>
        <div><span>主要标的</span><b style="text-align:right">${top || "无代码台账"}</b></div>
      </div>
    </div>`;
  }).join("");
  $("#timelineCards").innerHTML = cards || `<div class="muted">没有可用的日期数据</div>`;

  if (!state.chartPnl) state.chartPnl = echarts.init($("#pnlChart"));
  const pts = tl.points || [];
  state.chartPnl.setOption({
    backgroundColor: "transparent",
    animation: false,
    tooltip: { trigger: "axis", formatter: p => {
      const d = pts[p[0].dataIndex];
      return `${d.month}<br/>已实现 ${signed(d.pnl_usd)}<br/>${d.n} 笔（赢 ${d.win} / 亏 ${d.loss}）`;
    } },
    grid: { left: 62, right: 20, top: 16, bottom: 40 },
    xAxis: { type: "category", data: pts.map(p => p.month), axisLabel: { color: "#5f6f88", fontSize: 10,
      interval: (i) => i % 6 === 0 || i === pts.length - 1 }, axisLine: { lineStyle: { color: "#1f2a3a" } } },
    yAxis: { type: "value", axisLabel: { color: "#5f6f88", fontSize: 10, formatter: v => (v / 1000).toFixed(0) + "k" },
      splitLine: { lineStyle: { color: "#17202e" } } },
    series: [{
      type: "bar", barWidth: "70%",
      data: pts.map(p => ({ value: toBase(p.pnl_usd), itemStyle: { color: p.pnl_usd < 0 ? "#ff6b6b" : "#2ee6a8" } })),
    }],
  }, true);
}

function renderProfile(p) {
  const items = [
    ["交易笔数", p.n_trades, `跨 ${p.active_months} 个有成交的月份`],
    ["月均笔数", p.per_month, "换手频率"],
    ["胜率", p.win_rate == null ? "—" : (p.win_rate * 100).toFixed(0) + "%", "按笔统计"],
    ["赔率", p.payoff == null ? "—" : p.payoff.toFixed(2), "平均盈利 / 平均亏损"],
    ["平均持有", p.avg_hold_days == null ? "无记录" : p.avg_hold_days + " 天", "仅 POEMS 台账有"],
    ["做多占比", p.long_share == null ? "—" : (p.long_share * 100).toFixed(0) + "%", "按平仓方向"],
    ["杠杆 ETF 笔数占比", (p.levered_share * 100).toFixed(0) + "%", `合计 ${signed(p.levered_pnl_usd)}`],
    ["最大单票集中", p.concentration == null ? "—" : (p.concentration * 100).toFixed(0) + "%", "笔数口径"],
  ];
  $("#profileCards").innerHTML = items.map(([k, v, s]) => `
    <div class="kpi"><div class="k">${k}</div><div class="v">${v}</div><div class="s">${s}</div></div>`).join("");
  $("#profileNotes").innerHTML = (p.conclusion || []).map(t => `<li>${t}</li>`).join("") ||
    `<li class="muted">数据不足</li>`;
  $("#profileTable tbody").innerHTML = (p.top_symbols || []).concat(p.top_loss_symbols || [])
    .filter((v, i, a) => a.findIndex(x => x.symbol === v.symbol) === i)
    .map(s => `<tr><td class="sym">${s.symbol}</td><td class="num">${s.n}</td>
      <td class="num ${signCls(s.pnl_usd)}">${signed(s.pnl_usd)}</td>
      <td class="num">${(s.win_rate * 100).toFixed(0)}%</td>
      <td>${s.levered ? '<span class="chip ref">杠杆 ETF</span>' : "普通标的"}</td></tr>`).join("");
}

function renderActions(list) {
  $("#actionList").innerHTML = (list || []).map((a, i) => `
    <div class="action-item">
      <span class="step-no">${i + 1}</span>
      <div class="r-body">
        <div class="r-title">${a.step}</div>
        <div class="r-detail">${a.text}</div>
      </div>
    </div>`).join("") || `<div class="muted">当前没有待办动作</div>`;
}

function renderRisks(list) {
  $("#riskList").innerHTML = (list || []).map(r => `
    <div class="risk-item ${r.level}">
      <span class="chip ${r.level}">${LEVEL_TXT[r.level] || r.level}</span>
      <div class="r-body">
        <div class="r-title">${r.title}</div>
        <div class="r-detail">${r.detail}</div>
      </div>
    </div>`).join("") || `<div class="muted">没有识别到风险项</div>`;
}

$("#mxBtn").addEventListener("click", async () => {
  await loadMetrics(true);
  await loadPortfolio(true);
  setStatus("指标已重算，现金已按券商流水核对");
});
$("#mxFlowMore").addEventListener("click", () => loadFlows(false));

$("#pmBtn").addEventListener("click", async () => {
  try {
    setStatus("重导 POEMS 单据…");
    const r = await api("/api/poems/import", { method: "POST" });
    await Promise.all([loadPoems(true), loadMetrics(true), loadPortfolio()]);
    setStatus(`POEMS 已重导：${r.trades} 成交 / ${r.closed} 平仓批次 / ${r.positions} 持仓`);
  } catch (e) { banner("POEMS 重导失败：" + e.message); }
});
if (window.ST_STATIC) {
  const b = $("#pmBtn");
  b.disabled = true;
  b.textContent = "只读快照";
  b.title = "静态快照不支持写库，重导请在本机 8300 服务上执行";
}

$("#psBtn").addEventListener("click", async () => {
  try {
    setStatus("重导 POEMS SG 月结单…");
    const r = await api("/api/poems/sg/import", { method: "POST" });
    await Promise.all([loadPoemsSg(true), loadPoems(true), loadMetrics(true), loadPortfolio()]);
    setStatus(`POEMS SG 已重导：${r.positions} 持仓 / 现金 ${r.cash} / ${r.snapshots} 期`);
  } catch (e) { banner("POEMS SG 重导失败：" + e.message); }
});
if (window.ST_STATIC) {
  const b = $("#psBtn");
  if (b) { b.disabled = true; b.textContent = "只读快照"; b.title = "静态快照不支持写库，重导请在本机 8300 服务上执行"; }
}

$("#hygieneBtn").addEventListener("click", async () => {
  try {
    setStatus("重新体检…");
    await api("/api/hygiene/apply", { method: "POST" });
    await loadInsights();
    await loadPortfolio();
    setStatus("体检已应用");
  } catch (e) { banner("体检失败：" + e.message); }
});

document.addEventListener("DOMContentLoaded", () => {
  loadPortfolio();
  loadWatch();
  api("/api/meta").then(m => { $("#footMeta").textContent = `台账 ${m.generated_at} · rev ${m.revision}`; }).catch(() => {});
  const h = (location.hash || "").replace("#", "");
  if (["portfolio", "discover", "analysis", "risk"].includes(h)) goView(h);
});

/* ============ 风控页：盘面 / 逐仓 / 对冲 ============ */
let riskLoaded = false;
let riskBusy = false;

async function loadRisk(force) {
  if (riskBusy) return;
  if (riskLoaded && !force) return;
  riskBusy = true;
  setStatus("计算盘面与风控…（首次约 5–10 秒，之后缓存 30 分钟）");
  $("#riskAsOf").textContent = "计算中…";
  try {
    const r = await api("/api/risk");
    renderRisk(r);
    riskLoaded = true;
    setStatus("风控已更新 · " + (r.as_of || "").slice(11, 16));
  } catch (e) {
    banner("风控计算失败：" + e.message);
    $("#riskAsOf").textContent = "失败";
  } finally { riskBusy = false; }
}

function renderRisk(r) {
  const s = r.summary || {};
  $("#riskAsOf").textContent = `权益 ${money(s.equity)} · ${(r.as_of || "").slice(0, 16)}`;
  $("#riskKpis").innerHTML = [
    ["持仓数", s.positions ?? "—", ""],
    ["平均盈亏比", s.avg_rr ?? "—", "止损止盈结构"],
    ["平均首达概率", s.avg_prob != null ? pct(s.avg_prob, 1) : "—", "先碰止盈"],
    ["逐仓期望合计", signed(s.expectancy_usd), "按历史胜率"],
  ].map(([k, v, h]) => `<div class="kpi"><div class="k">${k}</div><div class="v">${v}</div><div class="s">${h}</div></div>`).join("");

  /* 盘面 */
  $("#mktBoard").innerHTML = (r.board.markets || []).map(m => `
    <div class="mkt-card">
      <div class="mkt-head"><strong>${m.label}</strong>
        <span class="hint">${m.positions} 个持仓 · 敞口 ${moneyB(m.exposure_usd)} · 浮盈 ${signed(m.pnl_usd)}</span></div>
      ${m.indices.map(x => x.error ? `<div class="mkt-row muted">${x.name} · ${x.error}</div>` : `
        <div class="mkt-row">
          <span class="mk-name">${x.name}</span>
          <b class="${signCls(x.chg_pct)}">${num(x.price, 2)}</b>
          <span class="${signCls(x.chg_pct)}">${x.chg_pct > 0 ? "+" : ""}${num(x.chg_pct, 2)}%</span>
          <span class="muted small">vs200 ${x.vs200 > 0 ? "+" : ""}${num(x.vs200, 1)}%</span>
          <span class="muted small">距高 ${num(x.off_high, 1)}%</span>
          <span class="chip ${x.atr_pct >= 4 ? "warn" : "info"}">ATR ${num(x.atr_pct, 1)}%</span>
        </div>`).join("")}
    </div>`).join("");
  $("#mktNotes").innerHTML = (r.board.notes || []).map(n => `<li>${n}</li>`).join("");

  /* 逐仓 */
  const lv = r.levels || [];
  $("#lvlHint").textContent = `${lv.length} 个持仓 · 止盈止损按日线结构`;
  $("#lvlTable tbody").innerHTML = lv.map(x => {
    if (x.note && !x.stop) {
      return `<tr><td class="sym">${x.symbol}</td><td>${x.side === "SHORT" ? "空" : "多"}</td>
        <td colspan="11" class="muted">${x.note}</td></tr>`;
    }
    return `<tr>
      <td class="sym">${x.symbol}<div class="muted small">${x.name || ""}</div></td>
      <td>${x.side === "SHORT" ? '<span class="chip off">空</span>' : '<span class="chip on">多</span>'}</td>
      <td class="num">${num(x.qty, 0)}</td>
      <td class="num">${money(x.price, x.ccy)}</td>
      <td class="num ${x.atr_pct >= 0.04 ? "down" : ""}">${pct(x.atr_pct, 1)}</td>
      <td class="num down">${money(x.stop, x.ccy)}<div class="muted small">${pct(-x.stop_dist, 1)}</div></td>
      <td class="num up">${money(x.target, x.ccy)}<div class="muted small">+${pct(x.target_dist, 1)}</div></td>
      <td class="num"><b class="${x.rr >= 1.5 ? "up" : x.rr >= 1 ? "" : "down"}">${num(x.rr)}</b></td>
      <td class="num">${x.prob != null ? pct(x.prob, 1) : "—"}
        <div class="muted small">${x.hist_win != null ? "胜率 " + pct(x.hist_win, 0) + " · " + x.hist_n + " 笔"
          : "整体 " + pct(x.win_used, 0) + " · " + x.hist_n + " 笔"}</div></td>
      <td class="num ${signCls(x.exp_usd)}">${signed(x.exp_usd)}</td>
      <td>${x.note ? `<span class="chip ${x.exp_usd < 0 || x.rr < 1 ? "danger" : "warn"}">${x.note}</span>` : '<span class="muted">—</span>'}</td>
    </tr>`;
  }).join("") || `<tr><td colspan="13" class="muted">没有持仓</td></tr>`;

  /* 对冲 */
  const h = r.hedge || {};
  $("#hedgeCards").innerHTML = [
    ["净 beta 敞口", signed(h.net_beta_exposure), `≈ 权益 ${h.net_vs_equity}x`],
    ["权益", money(h.equity), "USD"],
    ["组合浮亏回本", pct(r.breakeven?.portfolio_need_pct || 0, 1), "按敞口同比例"],
    ["浮亏持仓", (r.breakeven?.losers || []).length + " 个", "见下表"],
  ].map(([k, v, e]) => `<div class="kpi"><div class="k">${k}</div><div class="v">${v}</div><div class="s">${e}</div></div>`).join("");
  const w = $("#hedgeWarn");
  if (h.warning) { w.textContent = h.warning; w.classList.remove("hidden"); }
  else { w.classList.add("hidden"); }

  $("#hedgeTable tbody").innerHTML = (h.by_market || []).map(p => `
    <tr>
      <td>${p.market}</td>
      <td>${p.bench || "—"}</td>
      <td class="num ${signCls(p.net)}">${signed(p.net)}</td>
      <td>${p.hedge || "—"}</td>
      <td class="num">${p.hedge_shares ? `${p.side} ${Math.abs(p.hedge_shares)}` : "—"}</td>
      <td class="num">${p.hedge_notional ? money(p.hedge_notional) : "—"}</td>
      <td>${p.feasible === false ? '<span class="chip danger">不可执行</span>' : (p.hedge ? '<span class="chip on">可执行</span>' : '<span class="chip warn">只能减仓</span>')}
        ${p.note ? `<div class="muted small">${p.note}</div>` : ""}</td>
    </tr>`).join("") || `<tr><td colspan="7" class="muted">没有可计算 beta 的持仓</td></tr>`;

  $("#scenTable tbody").innerHTML = (h.scenarios || []).map(x => `
    <tr>
      <td>基准 ${x.shock > 0 ? "+" : ""}${pct(x.shock, 0)}</td>
      <td class="num ${signCls(x.unhedged)}">${signed(x.unhedged)}</td>
      <td class="num ${signCls(x.unhedged_pct)}">${x.unhedged_pct != null ? pct(x.unhedged_pct, 1) : "—"}</td>
      <td class="num">${signed(x.hedged)}</td>
      <td class="num muted">${signed(x.diff)}</td>
    </tr>`).join("");

  const be = r.breakeven || {};
  $("#beTable tbody").innerHTML = (be.losers || []).map(x => `
    <tr>
      <td class="sym">${x.symbol}<div class="muted small">${x.name || ""}</div></td>
      <td>${x.side === "SHORT" ? "空" : "多"}</td>
      <td class="num">${num(x.price, 3)}</td>
      <td class="num">${num(x.breakeven, 3)}</td>
      <td class="num ${x.pnl_usd < 0 ? "down" : "up"}">${x.need_pct > 0 ? `${x.need_dir} ${pct(x.need_pct, 1)}` : "已回本"}</td>
      <td class="num down">${signed(x.pnl_usd)}</td>
    </tr>`).join("") || `<tr><td colspan="6" class="muted">当前没有浮亏持仓</td></tr>`;
  $("#beNote").textContent = `组合浮亏 ${money(be.portfolio_unrealized)}，按敞口同比例回升需要 +${pct(be.portfolio_need_pct, 2)}。回本价按成本价（不含费用）算。`;
}

$("#riskRefresh")?.addEventListener("click", () => loadRisk(true));
