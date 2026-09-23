/* ST Dashboard — 渲染逻辑。数据来自登录会话(sessionStorage.st_data)，页面刷新后重新登录才可见。 */
(function () {
"use strict";
let D;
try { D = JSON.parse(sessionStorage.getItem("st_data")); } catch (e) { D = null; }
if (!D) { location.replace("login.html"); return; }

const $ = (id) => document.getElementById(id);
const fmt = (v, d = 2) => v == null ? "—" : Number(v).toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d });
const fmt0 = (v) => v == null ? "—" : Math.round(Number(v)).toLocaleString("en-US");
const pct = (v, d = 1) => v == null ? "—" : (v * 100).toFixed(d) + "%";
const signCls = (v) => v == null ? "" : v > 0 ? "pos" : v < 0 ? "neg" : "";
const usd = (v) => v == null ? "—" : (v > 0 ? "+$" : "$") + fmt(v);

document.addEventListener("DOMContentLoaded", () => {
  renderKpis(); renderInsights(); renderCharts(); renderTables(); renderCaveats();
  $("btnLogout").addEventListener("click", () => {
    sessionStorage.removeItem("st_data");
    location.replace("login.html");
  });
  $("metaTop").textContent = `POEMS + FUTU · 数据生成于 ${D.generated_at} · 会话内解密，关闭标签页即失效`;
});

/* ---------- KPI ---------- */
function card(k, v, s, cls) {
  return `<div class="card"><div class="k">${k}</div><div class="v ${cls || ""}">${v}</div><div class="s">${s || ""}</div></div>`;
}
function renderKpis() {
  const { POEMS, FUTU, TOTAL } = D.kpi;
  $("kpis").innerHTML = [
    card("总投入本金", "$" + fmt0(TOTAL.initial_deposit), "POEMS $" + fmt0(POEMS.initial_deposit) + " + FUTU $" + fmt0(FUTU.initial_deposit)),
    card("账户总权益", "$" + fmt0(TOTAL.equity), "现金余额 $" + fmt0(TOTAL.cash_balance), TOTAL.net_gain >= 0 ? "pos" : "neg"),
    card("净盈亏（台账口径）", usd(TOTAL.net_gain), "总收益率 " + pct(TOTAL.return), signCls(TOTAL.net_gain)),
    card("已实现盈亏", usd(TOTAL.realized), "POEMS " + usd(POEMS.realized) + " · FUTU(台账) " + usd(FUTU.realized), signCls(TOTAL.realized)),
    card("未实现盈亏", usd(TOTAL.unrealized), "POEMS 持仓浮亏", signCls(TOTAL.unrealized)),
    card("累计手续费", "$" + fmt0(TOTAL.fees_paid), "≈ 净亏的 " + fmt(Math.abs(TOTAL.fees_paid / TOTAL.net_gain), 1) + " 倍", "neu"),
    card("最大回撤（总）", pct(D.equity.mdd.TOTAL), "POEMS " + pct(D.equity.mdd.POEMS) + " · FUTU " + pct(D.equity.mdd.FUTU), "neg"),
    card("FUTU 成交笔数", D.futu.n_fills + " 笔", (D.futu.first_fill || "").slice(0, 10) + " → " + (D.futu.last_fill || "").slice(0, 10)),
  ].join("");
}

/* ---------- 自动诊断 ---------- */
function renderInsights() {
  const f = D.futu, p = D.poems, dt = D.day_trade;
  const usdS = p.closed_stats_by_ccy["USD"];
  const futuNet = (f.realized_fifo_gross - (f.orders.fees_by_ccy.USD || 0)).toFixed(0);
  const sqqq = f.per_symbol["SQQQ"], nvda = f.per_symbol["NVDA"], gld = f.per_symbol["GLD"], mu = f.per_symbol["MU"];
  const topLoss = Object.entries(f.per_symbol).filter(([, d]) => d.ccy === "USD").sort((a, b) => a[1].realized - b[1].realized)[0];
  const items = [
    `<b>整体：</b>投入 $${fmt0(D.kpi.TOTAL.initial_deposit)}，台账净亏 $${fmt0(-D.kpi.TOTAL.net_gain)}（${pct(D.kpi.TOTAL.return)}）。同期若以同样节奏持有大盘基准，差距主要来自择时与杠杆成本，见「基准对比」图。`,
    `<b>费用侵蚀：</b>两账户累计手续费 $${fmt0(D.kpi.TOTAL.fees_paid)}，是总已实现毛利（$${fmt0(D.kpi.TOTAL.realized)}）的 ${fmt(D.kpi.TOTAL.fees_paid / D.kpi.TOTAL.realized, 1)} 倍——高频小单的佣金+平台费显著拖累净收益。FUTU 仅 2024-08 以来手续费即 $${fmt0((f.orders.fees_by_ccy.USD || 0) + (f.orders.fees_by_ccy.HKD || 0))}（USD/HKD）。`,
    `<b>盈亏比失衡（两账户共性）：</b>POEMS 美股胜率 ${pct(usdS.win_rate, 0)} 但平均盈利 $${fmt(usdS.avg_win)} 对平均亏损 $${fmt(usdS.avg_loss)}（赔率 ${usdS.payoff}）；FUTU 卖出批次胜率 ${pct(f.sell_event_stats.win_rate, 0)}、赔率 ${f.sell_event_stats.payoff}。「高胜率+小赚大亏」组合意味着单笔尾部风险未受控——最大单亏 ${usd(f.sell_event_stats.max_loss)}（${topLoss[0]}）。`,
    sqqq ? `<b>FUTU 最大出血点是 SQQQ：</b>115 笔成交、FIFO 累计亏 ${usd(sqqq.realized)}；同期 NVDA ${usd(nvda.realized)}、GLD ${usd(gld.realized)} 为主要盈利来源，MU ${usd(mu.realized)}。做空杠杆 ETF 的波动损耗（volatility decay）在长持下是结构性的负期望。` : "",
    `<b>POEMS 杠杆风险：</b>现金余额 -$${fmt0(-p.margin["Cash Balance"] || 0)}（保证金透支），台账记录的 Margin Call 缺口 -$${fmt0(-(p.margin["Margin Call"] || 0))}、强平压力 -$${fmt0(-(p.margin["Force Selling"] || 0))}；账户权益 $${fmt0(D.kpi.POEMS.equity)} 几乎全靠持仓支撑，浮亏 ${usd(D.kpi.POEMS.unrealized)}。`,
    `<b>Day Trade 记录（${dt.overall.n} 笔）：</b>净 ${usd(dt.overall.sum)}，胜率 ${pct(dt.overall.win_rate, 0)}，但平均亏损 $${fmt(dt.overall.avg_loss)} 远大于平均盈利 $${fmt(dt.overall.avg_win)}（赔率 ${dt.overall.payoff}）——2022-01 单月 -1,349 是最大回撤来源。C 级标的样本少但赔率 ${dt.stats_by_grade["C"] ? dt.stats_by_grade["C"].payoff : "—"} 最优。`,
    `<b>挂单习惯：</b>FUTU 共 ${f.orders.n} 张订单，撤单率 ${pct(f.orders.cancel_rate, 0)}（已撤单 ${f.orders.status["已撤单"]} 张），限价单频繁改价反映盘口追单行为，交收费与平台费按单累计。`,
    `<b>当前敞口：</b>FUTU 未平仓 ${Object.keys(f.open_positions).length} 个标的，其中 SQQQ ${fmt0(f.open_positions["SQQQ"]?.qty)} 股 @均本 $${f.open_positions["SQQQ"]?.avg_cost}（最新成交 $35.35，浮亏约 -19%）；GLD 89 股 @ $${f.open_positions["GLD"]?.avg_cost} 是近期对冲动作。`,
  ].filter(Boolean);
  $("insights").innerHTML = "<ul style='padding-left:20px'>" + items.map((s) => `<li>${s}</li>`).join("") + "</ul>";
}

/* ---------- 图表 ---------- */
const charts = [];
function mk(id, opt) {
  const el = $(id); if (!el || !window.echarts) return;
  const c = echarts.init(el, "dark"); c.setOption(opt); charts.push(c);
  new ResizeObserver(() => c.resize()).observe(el);
}
const baseTip = { trigger: "axis", axisPointer: { type: "cross" } };
const AX = (fmtFn) => ({ axisLabel: { color: "#8593ab", formatter: fmtFn }, axisLine: { lineStyle: { color: "#232c3d" } } });

function renderCharts() {
  const eq = D.equity, mo = D.monthly, f = D.futu, p = D.poems;
  // 1. 净值曲线
  mk("cEq", {
    backgroundColor: "transparent", title: { text: "净值曲线（台账月度已实现，美元）", left: 10, textStyle: { fontSize: 13 } },
    tooltip: baseTip, legend: { bottom: 0, textStyle: { color: "#8593ab" } },
    grid: { top: 40, bottom: 56, left: 62, right: 18 },
    xAxis: { type: "category", data: eq.months, ...AX() },
    yAxis: { type: "value", scale: true, ...AX((v) => "$" + fmt0(v)), splitLine: { lineStyle: { color: "#1a2233" } } },
    series: [
      { name: "POEMS", type: "line", data: eq.POEMS, smooth: true, lineStyle: { width: 2 }, areaStyle: { opacity: .07 } },
      { name: "FUTU", type: "line", data: eq.FUTU, smooth: true, lineStyle: { width: 2 } },
      { name: "合计", type: "line", data: eq.TOTAL, smooth: true, lineStyle: { width: 2.5, type: "dashed" }, itemStyle: { color: "#e8a13c" } },
      { name: "总本金", type: "line", data: eq.months.map(() => D.kpi.TOTAL.initial_deposit), symbol: "none", lineStyle: { width: 1, color: "#666" } },
    ],
  });
  // 2. 基准对比（归一化）
  const bIdx = (arr, base) => arr.map((v) => v == null ? null : +(v / base * 100).toFixed(2));
  const normMonths = mo.months.length;
  const eqN = (arr) => arr.map((v, i) => +(v / arr[0] * 100).toFixed(2));
  mk("cBench", {
    backgroundColor: "transparent", title: { text: "净值 vs 大盘指数（起点=100，2020-03 → 2022-01）", left: 10, textStyle: { fontSize: 13 } },
    tooltip: baseTip, legend: { bottom: 0, textStyle: { color: "#8593ab" } },
    grid: { top: 40, bottom: 56, left: 48, right: 18 },
    xAxis: { type: "category", data: mo.months.slice(0, normMonths), ...AX() },
    yAxis: { type: "value", scale: true, ...AX(), splitLine: { lineStyle: { color: "#1a2233" } } },
    series: [
      { name: "合计净值", type: "line", data: eqN(eq.TOTAL.slice(0, normMonths)), lineStyle: { width: 3, color: "#e8a13c" }, itemStyle: { color: "#e8a13c" }, symbol: "none" },
      { name: "标普500", type: "line", data: bIdx(mo.benchmark.SPX, mo.benchmark.SPX[0]), symbol: "none", lineStyle: { color: "#5aa7ff" } },
      { name: "纳斯达克", type: "line", data: bIdx(mo.benchmark.NASDAQ, mo.benchmark.NASDAQ[0]), symbol: "none", lineStyle: { color: "#2fbf71" } },
      { name: "恒生指数", type: "line", data: bIdx(mo.benchmark.HSI, mo.benchmark.HSI[0]), symbol: "none", lineStyle: { color: "#ef5b5b" } },
    ],
  });
  // 3. 月度盈亏堆叠
  const addA = (a, b) => a.map((v, i) => +(((v || 0) + (b[i] || 0))).toFixed(2));
  mk("cMonthly", {
    backgroundColor: "transparent", title: { text: "月度已实现盈亏（美元）", left: 10, textStyle: { fontSize: 13 } },
    tooltip: baseTip, legend: { bottom: 0, textStyle: { color: "#8593ab" } },
    grid: { top: 40, bottom: 56, left: 58, right: 18 },
    xAxis: { type: "category", data: mo.months, ...AX() },
    yAxis: { type: "value", ...AX((v) => fmt0(v)), splitLine: { lineStyle: { color: "#1a2233" } } },
    series: [
      { name: "POEMS 常规", type: "bar", stack: "t", data: mo.POEMS_regular, itemStyle: { color: "#5aa7ff" } },
      { name: "POEMS 日内", type: "bar", stack: "t", data: mo.POEMS_day, itemStyle: { color: "#3d6ea8" } },
      { name: "FUTU", type: "bar", stack: "t", data: addA(mo.FUTU_regular, mo.FUTU_day), itemStyle: { color: "#e8a13c" } },
    ],
  });
  // 4. FUTU CSV 逐月 FIFO（2024-08 起）
  const fm = Object.keys(f.monthly_realized).flatMap((ccy) => Object.keys(f.monthly_realized[ccy]).map((k) => [k, f.monthly_realized[ccy][k], ccy]));
  fm.sort((a, b) => a[0] < b[0] ? -1 : 1);
  mk("cFutuMonthly", {
    backgroundColor: "transparent", title: { text: "FUTU CSV 卖出批次逐月 FIFO 毛利（USD/HKD 分列）", left: 10, textStyle: { fontSize: 13 } },
    tooltip: { trigger: "axis" }, legend: { bottom: 0, textStyle: { color: "#8593ab" } },
    grid: { top: 40, bottom: 56, left: 58, right: 18 },
    xAxis: { type: "category", data: [...new Set(fm.map((x) => x[0]))], ...AX() },
    yAxis: { type: "value", ...AX((v) => fmt0(v)), splitLine: { lineStyle: { color: "#1a2233" } } },
    series: ["USD", "HKD"].map((ccy) => ({
      name: ccy, type: "bar",
      data: [...new Set(fm.map((x) => x[0]))].map((m) => { const hit = fm.find((x) => x[0] === m && x[2] === ccy); return hit ? hit[1] : 0; }),
      itemStyle: { color: ccy === "USD" ? "#5aa7ff" : "#2fbf71" },
    })),
  });
  // 5. POEMS 投入-收益率散点
  const pts = p.closed_trades.filter((t) => t.investment && t.pl_pct != null).map((t) => [t.investment, t.pl_pct * 100, t.no, t.market, t.gain_loss]);
  mk("cPoemsScatter", {
    backgroundColor: "transparent", title: { text: "POEMS 已平仓：投入金额 × 收益率（点大小=|盈亏|）", left: 10, textStyle: { fontSize: 13 } },
    tooltip: { formatter: (o) => `#${o.data[2]} ${o.data[3]}<br>投入 $${fmt0(o.data[0])} · 收益率 ${o.data[1].toFixed(1)}%<br>盈亏 ${usd(o.data[4])}` },
    grid: { top: 40, bottom: 30, left: 52, right: 18 },
    xAxis: { type: "log", name: "投入$", ...AX((v) => fmt0(v)), splitLine: { lineStyle: { color: "#1a2233" } } },
    yAxis: { type: "value", name: "收益率%", ...AX((v) => v + "%"), splitLine: { lineStyle: { color: "#1a2233" } } },
    series: [{ type: "scatter", data: pts, symbolSize: (d) => Math.min(26, 4 + Math.sqrt(Math.abs(d[4] || 0)) / 3), itemStyle: { color: (o) => o.data[1] >= 0 ? "#2fbf71" : "#ef5b5b", opacity: .75 } }],
  });
  // 6. 持有天数分布
  mk("cHoldDays", {
    backgroundColor: "transparent", title: { text: "POEMS 持有天数分布", left: 10, textStyle: { fontSize: 13 } },
    tooltip: { trigger: "axis" }, grid: { top: 40, bottom: 30, left: 44, right: 18 },
    xAxis: { type: "category", data: Object.keys(p.holding_days_buckets), ...AX() },
    yAxis: { type: "value", ...AX(), splitLine: { lineStyle: { color: "#1a2233" } } },
    series: [{ type: "bar", data: Object.values(p.holding_days_buckets), itemStyle: { color: "#5aa7ff", borderRadius: [4, 4, 0, 0] } }],
  });
  // 7. Day Trade 按评级
  const grades = Object.keys(p ? D.day_trade.stats_by_grade : {}).sort();
  mk("cDayGrade", {
    backgroundColor: "transparent", title: { text: "日内交易（Day Trade 台账）按标的评级", left: 10, textStyle: { fontSize: 13 } },
    tooltip: { trigger: "axis" }, legend: { bottom: 0, textStyle: { color: "#8593ab" } },
    grid: { top: 40, bottom: 56, left: 52, right: 40 },
    xAxis: { type: "category", data: grades, ...AX() },
    yAxis: [
      { type: "value", name: "净盈亏$", ...AX((v) => fmt0(v)), splitLine: { lineStyle: { color: "#1a2233" } } },
      { type: "value", name: "胜率", min: 0, max: 1, axisLabel: { formatter: (v) => (v * 100) + "%" }, splitLine: { show: false } },
    ],
    series: [
      { name: "净盈亏", type: "bar", data: grades.map((g) => D.day_trade.stats_by_grade[g].sum), itemStyle: { color: (o) => o.data >= 0 ? "#2fbf71" : "#ef5b5b" } },
      { name: "胜率", type: "line", yAxisIndex: 1, data: grades.map((g) => D.day_trade.stats_by_grade[g].win_rate), itemStyle: { color: "#e8a13c" } },
    ],
  });
  // 8. POEMS 市场分布
  const ms = p.market_split;
  mk("cPoemsMkt", {
    backgroundColor: "transparent", title: { text: "POEMS 已平仓：市场分布（外圈笔数，内圈盈亏）", left: 10, textStyle: { fontSize: 13 } },
    tooltip: { trigger: "item" }, legend: { bottom: 0, textStyle: { color: "#8593ab" } },
    series: [
      { type: "pie", radius: ["55%", "70%"], label: { formatter: "{b} {c}" }, data: Object.entries(ms).map(([k, v]) => ({ name: k, value: v.n })) },
      { type: "pie", radius: ["30%", "45%"], label: { show: false }, data: Object.entries(ms).map(([k, v]) => ({ name: k, value: Math.abs(v.pl), itemStyle: { color: v.pl >= 0 ? "#2fbf71" : "#ef5b5b" } })) },
    ],
  });
  // 9. FUTU 标的 FIFO 排名
  const syms = Object.entries(f.per_symbol).map(([c, d]) => [c, d.realized, d.ccy, d.n_fills]).sort((a, b) => b[1] - a[1]);
  mk("cFutuSym", {
    backgroundColor: "transparent", title: { text: "FUTU 各标的 FIFO 毛利排名（USD；* 为 HKD）", left: 10, textStyle: { fontSize: 13 } },
    tooltip: { formatter: (o) => `${o.name}<br>FIFO ${usd(o.data.v)} · ${o.data.n} 笔成交` },
    grid: { top: 40, bottom: 30, left: 70, right: 30 },
    xAxis: { type: "value", ...AX((v) => fmt0(v)), splitLine: { lineStyle: { color: "#1a2233" } } },
    yAxis: { type: "category", data: syms.map((s) => s[0] + (s[2] === "HKD" ? "*" : "")), inverse: true, ...AX() },
    series: [{ type: "bar", data: syms.map((s) => ({ value: s[1], v: s[1], n: s[3], itemStyle: { color: s[1] >= 0 ? "#2fbf71" : "#ef5b5b" } })) }],
  });
  // 10. 订单行为
  mk("cOrders", {
    backgroundColor: "transparent", title: { text: "FUTU 订单状态分布（824 张委托单）", left: 10, textStyle: { fontSize: 13 } },
    tooltip: { trigger: "item" }, legend: { bottom: 0, textStyle: { color: "#8593ab" } },
    series: [
      { type: "pie", radius: ["40%", "65%"], itemStyle: { borderRadius: 6 }, label: { formatter: "{b}\n{d}%" }, data: Object.entries(f.orders.status).map(([k, v]) => ({ name: k, value: v })) },
    ],
  });
}

/* ---------- 持仓表 ---------- */
function tableHTML(cols, rows) {
  return `<table><thead><tr>${cols.map((c) => `<th>${c}</th>`).join("")}</tr></thead><tbody>${rows.map((r) => `<tr>${r.map((c) => `<td>${c}</td>`).join("")}</tr>`).join("")}</tbody></table>`;
}
function renderTables() {
  const f = D.futu;
  const futuRows = Object.entries(f.open_positions).map(([code, d]) => {
    const ps = f.per_symbol[code] || {};
    const cost = d.qty * d.avg_cost, mv = d.qty * (d.last_price ?? d.avg_cost);
    const upnl = d.last_price != null ? mv - cost : null;
    const upct = d.last_price != null && cost ? upnl / cost : null;
    return [code + (ps.name ? " · " + ps.name : ""), ps.market || "", ps.ccy || "", fmt0(d.qty),
      fmt(d.avg_cost, 3), d.last_price != null ? fmt(d.last_price, 3) : "—", fmt(cost, 0),
      upct != null ? `<span class="${signCls(upct)}">${usd(upnl)} (${pct(upct)})</span>` : "—"];
  });
  $("tFutuOpen").innerHTML = tableHTML(["代码", "市场", "币种", "数量", "均本", "现价*", "成本额", "浮动盈亏*"], futuRows);
  const usdUpnl = Object.entries(f.open_positions).reduce((s, [c, d]) => {
    if (f.per_symbol[c]?.ccy !== "USD" || d.last_price == null) return s;
    return s + (d.last_price - d.avg_cost) * d.qty;
  }, 0);
  $("futuUpnl").textContent = usd(usdUpnl);
  const poRows = p_open().map((t) => [`#${t.no} ${t.grade || ""}`, t.market || "", t.ccy || "", fmt0(t.qty), fmt(t.buy_price, 2), fmt(t.current_price, 2), t.pl_pct != null ? `<span class="${signCls(t.pl_pct)}">${pct(t.pl_pct)}</span>` : "—", t.open_date || "—", t.holding_days != null ? fmt0(t.holding_days) : "—"]);
  $("tPoemsOpen").innerHTML = tableHTML(["编号", "市场", "币种", "数量", "买入价", "现价", "盈亏%", "建仓", "持有天"], poRows);
  function p_open() { return D.poems.open_positions; }
  renderTradeTable();
}

/* ---------- 交易明细（搜索+懒加载） ---------- */
let shown = 60;
function tradeRowsPoems(q) {
  return D.poems.closed_trades.filter((t) => !q || ("#" + t.no + " " + (t.market || "") + " " + (t.ccy || "") + " " + (t.open_date || "") + " " + (t.close_date || "")).toLowerCase().includes(q))
    .map((t) => [`#${t.no}`, t.market || "", t.ccy || "", fmt0(t.qty), fmt(t.buy_price, 3), fmt(t.avg_price, 3), fmt(t.exit_price, 3), fmt(t.investment, 0), t.gain_loss != null ? `<span class="${signCls(t.gain_loss)}">${usd(t.gain_loss)}</span>` : "—", t.pl_pct != null ? pct(t.pl_pct) : "—", t.open_date || "—", t.close_date || "—", t.holding_days != null ? fmt0(t.holding_days) : "—"]);
}
function tradeRowsFutu(q) {
  const evs = (D.futu.sell_events || []).slice().reverse();
  return evs.filter((t) => !q || (t.code + " " + t.name + " " + t.date).toLowerCase().includes(q))
    .map((t) => [t.date, t.code, t.name, t.ccy, fmt0(t.qty), fmt(t.price, 3), t.pnl != null ? `<span class="${signCls(t.pnl)}">${usd(t.pnl)}</span>` : "—"]);
}
function renderTradeTable() {
  const acct = $("tradeAcct").value, q = $("tradeSearch").value.trim().toLowerCase();
  const cols = acct === "poems"
    ? ["#", "市场", "币种", "数量", "买价", "均价", "卖价", "投入", "盈亏", "收益%", "开仓", "平仓", "持有天"]
    : ["日期", "代码", "名称", "币种", "数量", "卖出价", "FIFO毛利"];
  const rows = (acct === "poems" ? tradeRowsPoems(q) : tradeRowsFutu(q));
  $("tTrades").innerHTML = tableHTML(cols, rows.slice(0, shown));
  const more = $("tradeMore");
  if (rows.length > shown) { more.style.display = "inline-block"; more.textContent = `加载更多（已显示 ${shown}/${rows.length}）`; }
  else more.style.display = "none";
  $("tradeSearch")._rows = rows;
}
$("tradeAcct").addEventListener("change", () => { shown = 60; renderTradeTable(); });
$("tradeSearch").addEventListener("input", () => { shown = 60; renderTradeTable(); });
$("tradeMore").addEventListener("click", () => { shown += 120; renderTradeTable(); });

/* ---------- 口径说明 ---------- */
function renderCaveats() {
  $("caveats").innerHTML = "<b>已知口径限制（诚实披露）</b><br>" + D.caveats.map((c, i) => `${i + 1}. ${c}`).join("<br>") +
    `<br><br><b>安全说明</b><br>· 交易数据加密于 vault.json（AES-256-GCM，PBKDF2-SHA256 210,000 次迭代派生密钥）。<br>· 登录时仅在你本机浏览器内派生密钥并解密，明文只存在于当前标签页的 sessionStorage，关闭标签页/登出即销毁。<br>· 未向任何服务端或第三方发送密码与数据；GitHub 私有仓库中 vault.json 对未授权者是不可读的密文。<br>· 若启用 GitHub Pages（私有仓库免费版不支持 Pages，可用公开仓库+仅上传 vault），数据仍受密码保护，但请知悉静态托管的口令门槛强度低于真实服务端认证。`;
  $("eqNote").innerHTML = `净值曲线由月度已实现盈亏累加本金得到（台账口径，未含未实现浮亏与保证金利息）；POEMS 净值末期 ${usd(D.kpi.POEMS.net_gain)}，与台账 equity ${fmt0(D.kpi.POEMS.equity)} 口径一致。`;
}
})();
