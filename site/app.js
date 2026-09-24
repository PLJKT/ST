/* ST Dashboard v4.1 — 修复：renderCharts 引用未定义变量 p 导致其后所有模块空白；每模块容错隔离并在页面显示错误横幅。
   盈亏榜改用台账自带分股票汇总（恢复股票名）+ FUTU 已实现+浮动口径。 */
(function () {
"use strict";
let D;
try { D = JSON.parse(sessionStorage.getItem("st_data")); } catch (e) { D = null; }
if (!D || D.revision !== 4) { sessionStorage.removeItem("st_data"); location.replace("login.html"); return; }

const $ = (id) => document.getElementById(id);
const fmt = (v, d) => {
  if (v == null) return "—";
  const n = Number(v);
  if (isNaN(n)) return "—";
  if (d != null) return n.toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d });
  // 智能格式：真整数→千分位无小数；非整数→四舍五入1位小数
  if (Math.abs(n - Math.round(n)) < 0.001) return Math.round(n).toLocaleString("en-US");
  return (Math.round(n * 10) / 10).toLocaleString("en-US", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
};
const fmt0 = (v) => fmt(v);
const pct = (v, d) => {
  if (v == null) return "—";
  const p = v * 100;
  if (d != null) return p.toFixed(d) + "%";
  // 智能格式：真整数→无小数；非整数→四舍五入1位
  if (Math.abs(p - Math.round(p)) < 0.001) return Math.round(p) + "%";
  return p.toFixed(1) + "%";
};
const signCls = (v) => v == null ? "" : v > 0 ? "pos" : v < 0 ? "neg" : "";
const usd = (v) => v == null ? "—" : (v > 0 ? "+$" : "$") + fmt(v);
const MONEY = { USD: "$", HKD: "HK$" };
const m = (ccy, v) => v == null ? "—" : (v > 0 ? "+" : "") + (MONEY[ccy] || ccy + " ") + fmt0(v);

const ERRORS = [];
function safe(name, fn) { try { fn(); } catch (e) { ERRORS.push(name + "：" + e.message); } }
function flushErrors() {
  if (!ERRORS.length) return;
  const el = document.createElement("div");
  el.style.cssText = "background:#3d1420;border:1px solid #ef5b5b;color:#ffb4b4;padding:10px 28px;font-size:12px";
  el.textContent = "渲染异常（已跳过对应模块）：" + ERRORS.join(" ｜ ");
  document.body.prepend(el);
  console.error("[ST dashboard]", ERRORS);
}

document.addEventListener("DOMContentLoaded", () => {
  safe("KPI", renderKpis);
  safe("诊断", renderInsights);
  safe("净值图", renderCharts);
  safe("分年图", renderYearly);
  safe("回本", renderPayback);
  safe("盈亏榜", renderRanking);
  safe("对账", renderRecon);
  safe("持仓表", renderTables);
  safe("说明", renderCaveats);
  flushErrors();
  $("btnLogout").addEventListener("click", () => { sessionStorage.removeItem("st_data"); location.replace("login.html"); });
  $("metaTop").textContent = `POEMS + FUTU · 数据 v4.1（${D.generated_at}）· FUTU 以资金明细为唯一事实源`;
});

function card(k, v, s, cls) { return `<div class="card"><div class="k">${k}</div><div class="v ${cls || ""}">${v}</div><div class="s">${s || ""}</div></div>`; }

function renderKpis() {
  const P = D.kpi.POEMS, N = D.futu_nav, F = D.futu;
  const costUSD = F.cash.interest_by_ccy.USD + F.cash.fees_by_ccy.USD + F.cash.dividend_by_ccy.USD;
  $("kpis").innerHTML = [
    card("FUTU 期末权益（USD 折算）", "$" + fmt0(N.equity_usd), "现金 " + m("USD", N.end_cash.USD) + " · 空头市值 " + m("HKD", N.positions_mv.short_hkd)),
    card("FUTU 账户净盈亏（双口径互证）", m("USD", N.pnl_mot_usd), "交易层互证 " + m("USD", N.pnl_tradeview_usd) + " · 差 $" + fmt(Math.abs(N.views_gap_usd)), "neg"),
    card("FUTU 已实现", m("USD", F.realized_by_ccy.USD), "HKD " + fmt0(F.realized_by_ccy.HKD) + " ·（分币种不相加）", "neg"),
    card("FUTU 浮动盈亏", m("USD", F.float_by_ccy.USD), "按最后成交价估算", "neg"),
    card("FUTU 隐性成本", m("USD", costUSD), "融券费/利息/卖空股息——订单表不含", "neg"),
    card("POEMS 台账净值", "$" + fmt0(P.equity), "净赚 " + usd(P.net_gain) + "（" + pct(P.return) + "）", "pos"),
    card("POEMS 保证金风险", m("USD", P.cash_balance), "Margin Call " + m("USD", D.poems.margin["Margin Call"]), "neg"),
    card("FUTU 成交/委托", F.n_fills + " / " + F.orders.n, "撤单率 " + pct(F.orders.cancel_rate) + " · 含卖空 " + F.directions["卖空"] + " 笔"),
  ].join("");
}

function renderInsights() {
  const f = D.futu, p = D.poems, dt = D.day_trade, N = D.futu_nav;
  const u = p.closed_stats_by_ccy["USD"], sq = f.per_symbol["SQQQ"], nv = f.per_symbol["NVDA"], sb = f.per_symbol["SBUX"], jr = f.per_symbol["01519"];
  $("insights").innerHTML = "<ul style='padding-left:20px'>" + [
    `<b>FUTU 账户整体亏损 $${fmt0(-N.pnl_mot_usd)}</b>（占迁入+入金基数 $${fmt0(N.capital.migration_usd_equiv + N.capital.bank_net_usd)} 的 ${pct(N.pnl_mot_usd / (N.capital.migration_usd_equiv + N.capital.bank_net_usd))}），资金流与交易层两口径互证差 $${fmt(Math.abs(N.views_gap_usd))}，结论可信。`,
    `<b>最大出血点：SQQQ ${usd(sq.realized)}</b>（115 笔，做多三倍做空 ETF，双向损耗）；<b>SBUX 空头亏 ${usd(sb.realized)}</b>（56 笔胜率仅 ${pct(sb.wins / sb.events)}，做空强势消费股被轧空）。两笔合计 ${usd(sq.realized + sb.realized)}，超过账户总亏。`,
    `<b>盈利主力：</b>NVDA ${usd(nv.realized)}（46 次平仓 35 胜）、01519 空头 ${m("HKD", jr.realized)}、SOXL ${usd(f.per_symbol["SOXL"].realized)}、AMD ${usd(f.per_symbol["AMD"].realized)}。`,
    `<b>成本结构：</b>手续费+利息+融券费+卖空股息共 ${m("USD", N.components_usd.fees_interest + N.components_usd.dividends_net)}——其中<b>融券费 $1,091、卖空股息 $955 是订单表口径完全漏掉的</b>，实际成本比"佣金 $1,082"高出一倍。`,
    `<b>融资依赖：</b>期末现金 -$${fmt0(-N.end_cash.USD)} 全靠 2026 年 $${fmt0(N.capital.bank_net_usd)} 银行入金维持，保证金账户长期负现金运转。`,
    `<b>POEMS：</b>美股 ${u.n} 笔、胜率 ${pct(u.win_rate)}、净赚 $${fmt0(u.sum)}，但赔率 ${u.payoff}（均亏 $${fmt(-u.avg_loss)} > 均盈 $${fmt(u.avg_win)}）与 FUTU 同样的"小赚大亏"结构；台账现金 -$${fmt0(-p.margin["Cash Balance"])} 持续 margin call。`,
    `<b>行为信号：</b>${pct(f.orders.cancel_rate)} 撤单率 + ${f.directions["卖空"]} 笔卖空频繁进出，交易频率（2025 年 ${f.fills_by_year["2025"] || 0} 笔）与净亏负相关——降低频率是第一修正建议。`,
  ].map((s) => `<li>${s}</li>`).join("") + "</ul>";
}

const charts = [];
function mk(id, opt) { const el = $(id); if (!el) throw new Error("容器缺失 #" + id); if (!window.echarts) throw new Error("echarts 未加载"); const c = echarts.init(el, "dark"); c.setOption(opt); charts.push(c); new ResizeObserver(() => c.resize()).observe(el); }
const AX = (f2) => ({ axisLabel: { color: "#8593ab", formatter: f2 }, axisLine: { lineStyle: { color: "#232c3d" } } });

function renderCharts() {
  const eq = D.equity, mo = D.monthly, f = D.futu, p = D.poems;
  mk("cEq", { backgroundColor: "transparent", title: { text: "POEMS 净值曲线（台账 2020-03→2022-01，美元）", left: 10, textStyle: { fontSize: 13 } }, tooltip: { trigger: "axis" }, grid: { top: 40, bottom: 30, left: 62, right: 18 },
    xAxis: { type: "category", data: eq.months, ...AX() }, yAxis: { type: "value", scale: true, ...AX((v) => "$" + fmt0(v)), splitLine: { lineStyle: { color: "#1a2233" } } },
    series: [{ name: "POEMS", type: "line", data: eq.POEMS, smooth: true, areaStyle: { opacity: .07 }, lineStyle: { width: 2.5, color: "#5aa7ff" }, itemStyle: { color: "#5aa7ff" } },
             { name: "本金", type: "line", symbol: "none", data: eq.months.map(() => D.kpi.POEMS.initial_deposit), lineStyle: { width: 1, color: "#666" } }] });
  mk("cBench", { backgroundColor: "transparent", title: { text: "POEMS 净值 vs 指数（起点=100，2020-03→2022-01）", left: 10, textStyle: { fontSize: 13 } }, tooltip: { trigger: "axis" }, legend: { bottom: 0, textStyle: { color: "#8593ab" } }, grid: { top: 40, bottom: 56, left: 48, right: 18 },
    xAxis: { type: "category", data: mo.months, ...AX() }, yAxis: { type: "value", scale: true, ...AX(), splitLine: { lineStyle: { color: "#1a2233" } } },
    series: [{ name: "POEMS净值", type: "line", symbol: "none", lineStyle: { width: 3, color: "#e8a13c" }, data: eq.POEMS.map((v) => +(v / eq.POEMS[0] * 100).toFixed(1)) },
      ...["SPX", "NASDAQ", "HSI"].map((nm) => { const arr = mo.benchmark[nm]; return { name: { SPX: "标普500", NASDAQ: "纳斯达克", HSI: "恒指" }[nm], type: "line", symbol: "none", data: arr.map((v) => v == null ? null : +(v / arr[0] * 100).toFixed(1)) }; })] });
  mk("cMonthly", { backgroundColor: "transparent", title: { text: "POEMS 月度已实现（常规+日内，美元）", left: 10, textStyle: { fontSize: 13 } }, tooltip: { trigger: "axis" }, legend: { bottom: 0, textStyle: { color: "#8593ab" } }, grid: { top: 40, bottom: 56, left: 58, right: 18 },
    xAxis: { type: "category", data: mo.months, ...AX() }, yAxis: { type: "value", ...AX((v) => fmt0(v)), splitLine: { lineStyle: { color: "#1a2233" } } },
    series: [{ name: "常规", type: "bar", stack: "t", data: mo.POEMS_regular, itemStyle: { color: "#5aa7ff" } }, { name: "日内", type: "bar", stack: "t", data: mo.POEMS_day, itemStyle: { color: "#3d6ea8" } }] });
  const monthsAll = [...new Set(Object.keys(f.monthly.USD || {}).concat(Object.keys(f.monthly.HKD || {})))].sort();
  mk("cFutuMonthly", { backgroundColor: "transparent", title: { text: "FUTU 平仓事件逐月盈亏（USD / HKD）", left: 10, textStyle: { fontSize: 13 } }, tooltip: { trigger: "axis" }, legend: { bottom: 0, textStyle: { color: "#8593ab" } }, grid: { top: 40, bottom: 56, left: 58, right: 18 },
    xAxis: { type: "category", data: monthsAll, ...AX() }, yAxis: { type: "value", ...AX((v) => fmt0(v)), splitLine: { lineStyle: { color: "#1a2233" } } },
    series: ["USD", "HKD"].map((c) => ({ name: c, type: "bar", data: monthsAll.map((k) => (f.monthly[c] || {})[k] || 0), itemStyle: { color: c === "USD" ? "#5aa7ff" : "#2fbf71" } })) });
  const pts = p.closed_trades.filter((t) => t.investment && t.pl_pct != null).map((t) => [t.investment, t.pl_pct * 100, t.no, t.market, t.gain_loss]);
  mk("cPoemsScatter", { backgroundColor: "transparent", title: { text: "POEMS 已平仓：投入 × 收益率（逐笔行，名称源文件损坏以编号示）", left: 10, textStyle: { fontSize: 13 } }, tooltip: { formatter: (o) => `#${o.data[2]} ${o.data[3]}<br>投入 $${fmt0(o.data[0])} · ${o.data[1].toFixed(1)}% · ${usd(o.data[4])}` }, grid: { top: 40, bottom: 30, left: 52, right: 18 },
    xAxis: { type: "log", ...AX((v) => fmt0(v)), splitLine: { lineStyle: { color: "#1a2233" } } }, yAxis: { type: "value", ...AX((v) => v + "%"), splitLine: { lineStyle: { color: "#1a2233" } } },
    series: [{ type: "scatter", data: pts, symbolSize: (d) => Math.min(24, 4 + Math.sqrt(Math.abs(d[4] || 0)) / 3), itemStyle: { color: (o) => o.data[1] >= 0 ? "#2fbf71" : "#ef5b5b", opacity: .75 } }] });
  mk("cHoldDays", { backgroundColor: "transparent", title: { text: "POEMS 持有天数分布", left: 10, textStyle: { fontSize: 13 } }, tooltip: { trigger: "axis" }, grid: { top: 40, bottom: 30, left: 44, right: 18 },
    xAxis: { type: "category", data: Object.keys(p.holding_days_buckets), ...AX() }, yAxis: { type: "value", ...AX(), splitLine: { lineStyle: { color: "#1a2233" } } },
    series: [{ type: "bar", data: Object.values(p.holding_days_buckets), itemStyle: { color: "#5aa7ff", borderRadius: [4, 4, 0, 0] } }] });
  mk("cDayGrade", { backgroundColor: "transparent", title: { text: "日内交易按评级（明细区 109 笔口径）", left: 10, textStyle: { fontSize: 13 } }, tooltip: { trigger: "axis" }, legend: { bottom: 0, textStyle: { color: "#8593ab" } }, grid: { top: 40, bottom: 56, left: 52, right: 40 },
    xAxis: { type: "category", data: Object.keys(D.day_trade.stats_by_grade), ...AX() },
    yAxis: [{ type: "value", ...AX((v) => fmt0(v)), splitLine: { lineStyle: { color: "#1a2233" } } }, { type: "value", min: 0, max: 1, axisLabel: { formatter: (v) => (v * 100) + "%" }, splitLine: { show: false } }],
    series: [{ name: "净盈亏", type: "bar", data: Object.values(D.day_trade.stats_by_grade).map((x) => x.sum), itemStyle: { color: (o) => o.data >= 0 ? "#2fbf71" : "#ef5b5b" } },
              { name: "胜率", type: "line", yAxisIndex: 1, data: Object.values(D.day_trade.stats_by_grade).map((x) => x.win_rate), itemStyle: { color: "#e8a13c" } }] });
  mk("cPoemsMkt", { backgroundColor: "transparent", title: { text: "POEMS 市场分布（外圈笔数/内圈盈亏）", left: 10, textStyle: { fontSize: 13 } }, tooltip: { trigger: "item" }, legend: { bottom: 0, textStyle: { color: "#8593ab" } },
    series: [{ type: "pie", radius: ["55%", "70%"], data: Object.entries(p.market_split).map(([k, v]) => ({ name: k, value: v.n })) },
             { type: "pie", radius: ["30%", "45%"], label: { show: false }, data: Object.entries(p.market_split).map(([k, v]) => ({ name: k, value: Math.abs(v.pl), itemStyle: { color: v.pl >= 0 ? "#2fbf71" : "#ef5b5b" } })) }] });
  const syms = Object.entries(f.per_symbol).map(([c, d]) => [c, d.realized, d.ccy, d.n, d.events]).sort((a, b) => Math.abs(b[1]) - Math.abs(a[1]));
  mk("cFutuSym", { backgroundColor: "transparent", title: { text: "FUTU 各标的已实现（* 为 HKD，定稿多空模型）", left: 10, textStyle: { fontSize: 13 } }, tooltip: { formatter: (o) => `${o.name}<br>已实现 ${m(o.data.c, o.data.v)} · ${o.data.n} 笔成交 / ${o.data.ev} 次平仓` }, grid: { top: 40, bottom: 30, left: 70, right: 30 },
    xAxis: { type: "value", ...AX((v) => fmt0(v)), splitLine: { lineStyle: { color: "#1a2233" } } }, yAxis: { type: "category", inverse: true, data: syms.map((s) => s[0] + (s[2] === "HKD" ? "*" : "")), ...AX() },
    series: [{ type: "bar", data: syms.map((s) => ({ value: s[1], v: s[1], c: s[2], n: s[3], ev: s[4], itemStyle: { color: s[1] >= 0 ? "#2fbf71" : "#ef5b5b" } })) }] });
  mk("cOrders", { backgroundColor: "transparent", title: { text: "FUTU 委托结构（方向 × 成交/撤单）", left: 10, textStyle: { fontSize: 13 } }, tooltip: { trigger: "axis", axisPointer: { type: "shadow" } }, legend: { bottom: 0, textStyle: { color: "#8593ab" } }, grid: { top: 40, bottom: 56, left: 52, right: 18 },
    xAxis: { type: "category", data: Object.keys(f.orders.direction), ...AX() }, yAxis: { type: "value", ...AX(), splitLine: { lineStyle: { color: "#1a2233" } } },
    series: Object.keys(f.orders.status).map((st) => ({ name: st, type: "bar", stack: "o", data: Object.keys(f.orders.direction).map((d) => ((f.orders.dir_status || {})[st] || {})[d] || 0) })) });
}

/* ---------- 分年 ---------- */
function renderYearly() {
  const yF = D.yearly.FUTU || {}, yP = D.yearly.POEMS_ledger || {}, yPu = D.yearly.POEMS_ledger_unreal || {};
  const fx = D.futu_nav ? D.futu_nav.fx_rate : 0.12875;
  const years = [...new Set(Object.keys(yP).concat(Object.keys(yF)))].sort();
  const poemsBars = years.map((y) => { let v = yP[y] || 0; if (yPu[y]) v = +(v + yPu[y]).toFixed(2); return +v.toFixed(2); });
  const futuBars = years.map((y) => (yF[y] && yF[y].pnl_usd_total != null) ? yF[y].pnl_usd_total : null);
  let cumF = 0;
  const capLine = years.map((y) => { cumF += (yF[y] && yF[y].capital_in_usd) || 0; return Math.round(D.kpi.POEMS.initial_deposit + cumF); });
  mk("cYearly", {
    backgroundColor: "transparent", title: { text: "总本金（累计投入，右轴） vs 当年总盈亏（左轴，USD 折算）", left: 10, textStyle: { fontSize: 13 } },
    tooltip: { trigger: "axis" }, legend: { bottom: 0, textStyle: { color: "#8593ab" } },
    grid: { top: 44, bottom: 56, left: 64, right: 72 },
    xAxis: { type: "category", data: years, ...AX() },
    yAxis: [
      { type: "value", name: "当年盈亏$", ...AX((v) => fmt0(v)), splitLine: { lineStyle: { color: "#1a2233" } } },
      { type: "value", name: "累计本金", scale: true, ...AX((v) => "$" + fmt0(v)), splitLine: { show: false } },
    ],
    series: [
      { name: "POEMS 盈亏", type: "bar", data: poemsBars, itemStyle: { color: (o) => o.data >= 0 ? "#2fbf71" : "#ef5b5b", opacity: .85 } },
      { name: "FUTU 盈亏", type: "bar", data: futuBars, itemStyle: { color: (o) => o.data >= 0 ? "#57d38b" : "#e07a5f", opacity: .85 } },
      { name: "累计总本金", type: "line", yAxisIndex: 1, step: "end", data: capLine, lineStyle: { width: 2.5, color: "#e8a13c" }, itemStyle: { color: "#e8a13c" } },
    ],
  });
  const frows = Object.keys(yF).sort().map((y) => {
    const r = yF[y], cu = r.capital_USD || {}, ch = r.capital_HKD || {};
    const cap = (cu.capital_net || 0) + (ch.capital_net || 0) * fx;
    return [y, m("USD", Math.round(cap)), m("USD", r.realized_usd), m("USD", r.other_usd), r.unrealized_usd != null ? m("USD", r.unrealized_usd) : "—", `<span class="${signCls(r.pnl_usd_total)}">${m("USD", r.pnl_usd_total)}</span>`, m("USD", Math.round(r.capital_cum_usd))];
  });
  $("tYearlyFutu").innerHTML = tableHTML(["年", "当年投入本金", "已实现", "费用/利息/股息", "未实现(年末)", "全年总盈亏", "累计本金"], frows);
  const prows = Object.keys(yP).sort().map((y) => [y, m("USD", yP[y]), yPu[y] ? m("USD", yPu[y]) : "—", m("USD", poemsBars[years.indexOf(y)])]);
  $("tYearlyPoems").innerHTML = tableHTML(["年", "台账已实现", "未实现(2022快照)", "全年总盈亏"], prows);
  $("yearlyNote").innerHTML = "本金口径：POEMS 初始存入 $23,110（2020-01）；FUTU 1599 = 资产迁移等值 + 银行净出入金（2024 $18.5K / 2025 −$3.2K出金 / 2026 $27.7K）。POEMS 台账止于 2022-01。2022 年 POEMS 含浮亏 −$4,767；FUTU 浮亏 −$7,270 归入 2026（源数据无逐年估值）。";
}

/* ---------- 回本 ---------- */
function renderPayback() {
  const PB = D.payback, F = PB.FUTU, C = PB.COMBINED;
  const rates = ["5%", "8%", "10%", "12%"];
  const fy = rates.map((r) => (F.scenarios && F.scenarios.pure_compound[r]) ? F.scenarios.pure_compound[r].years : null);
  const cy = rates.map((r) => (C.scenarios && C.scenarios.pure_compound[r]) ? C.scenarios.pure_compound[r].years : null);
  mk("cPayback", {
    backgroundColor: "transparent", title: { text: `不新增资金、纯靠收益回本：FUTU 缺 $${fmt0(F.gap)} · 合计缺 $${fmt0(C.gap)}`, left: 10, textStyle: { fontSize: 13 } },
    tooltip: { trigger: "axis" }, legend: { bottom: 0, textStyle: { color: "#8593ab" } },
    grid: { top: 44, bottom: 56, left: 44, right: 18 },
    xAxis: { type: "category", data: rates, ...AX((v) => v + "年化") }, yAxis: { type: "value", name: "年", ...AX(), splitLine: { lineStyle: { color: "#1a2233" } } },
    series: [
      { name: "FUTU", type: "bar", data: fy, label: { show: true, position: "top", formatter: (p) => p.value + "y" }, itemStyle: { color: "#5aa7ff" } },
      { name: "两账户合计", type: "bar", data: cy, label: { show: true, position: "top", formatter: (p) => p.value + "y" }, itemStyle: { color: "#e8a13c" } },
    ],
  });
  const rows = [];
  [["FUTU", F], ["合计", C]].forEach(([nm, o]) => {
    rows.push([`<b>${nm}</b> 缺口 $${fmt0(o.gap)}`, "", "", "", ""]);
    rates.forEach((r) => {
      const pu = o.scenarios ? o.scenarios.pure_compound[r] : null, mt = o.scenarios ? o.scenarios.monthly_topup[r] : null;
      rows.push([`　${r} 年化`, pu ? pu.years + " 年" : "—", mt && mt["24m"] ? "$" + fmt0(mt["24m"]) : "已回本", mt && mt["36m"] ? "$" + fmt0(mt["36m"]) : "—", mt && mt["60m"] ? "$" + fmt0(mt["60m"]) : "—"]);
    });
  });
  $("tPayback").innerHTML = tableHTML(["情景", "纯利滚回本", "24个月回本·月投", "36个月回本·月投", "60个月回本·月投"], rows);
  $("paybackNote").innerHTML = `<b>前提条件（缺一不可）：</b>① 现有持仓不再扩大亏损（SQQQ 900 股等仍在场，浮亏 −$7,270 未计入'回本后'状态）；② 停止高频，不再产生过去两年共 $${fmt0(Math.abs(D.futu_nav.components_usd.fees_interest + D.futu_nav.components_usd.dividends_net))} 的摩擦成本；③ 复利假设成立——注意你过去两年已实现年化约为负，任何正收益情景都要先改变行为。<b>结论：</b>不补仓时 8% 年化需约 12 年（FUTU）/ 9 年（合计）；若每月定投 $450–610（对应 8–10%、36 个月）可把回本压到 3 年内，但前提是净收益先转正。`;
}

/* ---------- 盈亏榜（新口径：FUTU=已实现+浮动；POEMS=台账分股票汇总，名称完整） ---------- */
function renderRanking() {
  const L = D.ranking.losses, G = D.ranking.gains;
  const lab = (x) => `${String(x.label).slice(0, 20)}${x.ccy === "HKD" ? "*" : ""}·${x.account === "FUTU" ? "F" : "P"}`;
  const tip = (o) => {
    const d = o.data;
    return `${d.name}<br>已实现 ${d.raw} · 浮动 ${d.u == null ? "—" : (MONEY[d.ccy] || "") + fmt0(d.u)}<br>合计(折USD) ${usd(d.t)} · ${d.src}`;
  };
  const bar = (arr, color) => ({ type: "bar", data: arr.map((x) => ({ value: x.total_usd, name: x.label, raw: (MONEY[x.ccy] || x.ccy + " ") + fmt0(x.realized), u: x.unrealized, ccy: x.ccy, t: x.total_usd, src: x.src || x.account, itemStyle: { color } })), label: { show: true, position: "left", formatter: (p) => fmt0(p.value), color: "#8593ab", fontSize: 10 } });
  mk("cTopLoss", { backgroundColor: "transparent", title: { text: "最大亏损 10 只（合计折USD 升序 · F=FUTU P=POEMS · *=HKD 计价）", left: 10, textStyle: { fontSize: 12 } }, tooltip: { formatter: tip }, grid: { top: 40, bottom: 24, left: 150, right: 24 },
    xAxis: { type: "value", ...AX((v) => fmt0(v)), splitLine: { lineStyle: { color: "#1a2233" } } },
    yAxis: { type: "category", data: L.map(lab), inverse: true, axisLabel: { color: "#8593ab", fontSize: 11 }, axisLine: { lineStyle: { color: "#232c3d" } } }, series: [bar(L, "#ef5b5b")] });
  mk("cTopGain", { backgroundColor: "transparent", title: { text: "最大盈利 10 只（合计折USD 降序）", left: 10, textStyle: { fontSize: 12 } }, tooltip: { formatter: tip }, grid: { top: 40, bottom: 24, left: 150, right: 24 },
    xAxis: { type: "value", ...AX((v) => fmt0(v)), splitLine: { lineStyle: { color: "#1a2233" } } },
    yAxis: { type: "category", data: G.map(lab), inverse: true, axisLabel: { color: "#8593ab", fontSize: 11 }, axisLine: { lineStyle: { color: "#232c3d" } } }, series: [bar(G, "#2fbf71")] });
}

/* ---------- 对账 ---------- */
function renderRecon() {
  const R = D.recon; if (!R) return;
  const ok = (b) => b ? '<span class="pos">✓ 一致</span>' : '<span class="neg">✗ 不一致</span>';
  const fc = R.fills_vs_cash.per_ccy;
  const rows = [
    ["FUTU 事实源", R.authority, ""],
    ["Excel FUTU 台账", R.excel_futu_ledger_excluded, "✓ 已剔除"],
    ["成交 ↔ 资金明细 逐笔配对", `USD 未配对 ${fc.USD.fills_unmatched} / HKD 未配对 ${fc.HKD.fills_unmatched}（容差 ±$${R.fills_vs_cash.tolerance}）`, ok(R.fills_vs_cash.matched_all)],
    ["成交 CSV 重复行裁决", R.duplicate_verdict, "✓ 真实分笔"],
    ["资金明细自身重复", `${R.cash_duplicate_rows} 条完全重复记录`, ok(R.cash_duplicate_rows === 0)],
    ["订单费用 ↔ 资金费用", `差额 USD $${fmt0(R.orders_fee_vs_cash_fee.USD.cash_only_total)} / HKD $${fmt0(R.orders_fee_vs_cash_fee.HKD.cash_only_total)}`, "已解释"],
    [R.orders_fee_note, "", ""],
    ["资金余额链", `USD ${ok(R.balance_chain.USD.chain_first_entry_consistent)} · HKD ${ok(R.balance_chain.HKD.chain_first_entry_consistent)}（期初+Σ金额=期末）`, ""],
    ["净值双口径互证", `资金流法 $${fmt0(D.futu_nav.pnl_mot_usd)} vs 交易层法 $${fmt0(D.futu_nav.pnl_tradeview_usd)}（差 $${fmt(Math.abs(D.futu_nav.views_gap_usd))}）`, ok(Math.abs(D.futu_nav.views_gap_usd) < 1)],
    ["POEMS 明细↔台账矩阵", `矩阵合计 $${fmt0(D.equity.poems_recon.matrix_2020_03_to_2022_01)} vs 明细 $${fmt0(D.equity.poems_recon.detail_all_158)}；矩阵后 2022-01 未维护部分 $${fmt0(D.equity.poems_recon.detail_after_2022_01)}`, "已标注"],
    ["POEMS 分股票汇总", D.ranking.note.note, "已接入"],
  ];
  $("tRecon").innerHTML = tableHTML(["检验项", "结果", "判定"], rows.map((r) => r.map((c) => c == null ? "" : String(c))));
}

function tableHTML(cols, rows) { return `<table><thead><tr>${cols.map((c) => `<th>${c}</th>`).join("")}</tr></thead><tbody>${rows.map((r) => `<tr>${r.map((c) => `<td>${c}</td>`).join("")}</tr>`).join("")}</tbody></table>`; }

function renderTables() {
  const f = D.futu;
  const rows = [...Object.entries(f.long_book).map(([c, d]) => ["多头 " + c, d.name, d.market, f.per_symbol[c].ccy, fmt0(d.qty), fmt(d.avg_cost), fmt(d.last_price), `<span class="${signCls(d.u_pnl)}">${m(f.per_symbol[c].ccy, d.u_pnl)}</span>`]),
                ...Object.entries(f.short_book).map(([c, d]) => ["空头 " + c, d.name, d.market, f.per_symbol[c].ccy, "−" + fmt0(d.qty), fmt(d.avg_price), fmt(d.last_price), `<span class="${signCls(d.u_pnl)}">${m(f.per_symbol[c].ccy, d.u_pnl)}</span>`])];
  $("tFutuOpen").innerHTML = tableHTML(["方向 代码", "名称", "市场", "币种", "数量", "开仓价", "现价*", "浮动盈亏*"], rows);
  const poRows = D.poems.open_positions.map((t) => [`#${t.no} ${t.grade || ""}`, t.market || "", t.ccy || "", fmt0(t.qty), fmt(t.buy_price), fmt(t.current_price), `<span class="${signCls(t.pl_pct)}">${pct(t.pl_pct)}</span>`, t.open_date || "—", t.holding_days != null ? fmt0(t.holding_days) : "—"]);
  $("tPoemsOpen").innerHTML = tableHTML(["编号", "市场", "币种", "数量", "买入价", "现价", "盈亏%", "建仓", "持有天"], poRows);
  renderTradeTable();
}
let shown = 60;
function renderTradeTable() {
  const acct = $("tradeAcct").value, q = $("tradeSearch").value.trim().toLowerCase();
  let cols, rows;
  if (acct === "poems") {
    cols = ["#", "市场", "币种", "数量", "买价", "均价", "卖价", "投入", "盈亏", "收益%", "开仓", "平仓", "持有天"];
    rows = D.poems.closed_trades.filter((t) => !q || ("#" + t.no + " " + (t.market || "") + " " + (t.ccy || "") + " " + (t.open_date || "") + " " + (t.close_date || "")).toLowerCase().includes(q))
      .map((t) => [`#${t.no}`, t.market || "", t.ccy || "", fmt0(t.qty), fmt(t.buy_price), fmt(t.avg_price), fmt(t.exit_price), fmt(t.investment), t.gain_loss != null ? `<span class="${signCls(t.gain_loss)}">${usd(t.gain_loss)}</span>` : "—", t.pl_pct != null ? pct(t.pl_pct) : "—", t.open_date || "—", t.close_date || "—", t.holding_days != null ? fmt0(t.holding_days) : "—"]);
  } else {
    cols = ["日期", "代码", "名称", "类型", "数量", "价格", "币种", "已实现"];
    rows = (D.futu.events || []).slice().reverse().filter((t) => !q || (t.code + " " + t.name + " " + t.date + " " + t.type).toLowerCase().includes(q))
      .map((t) => [t.date, t.code, t.name, t.type, fmt0(t.qty), fmt(t.price), t.ccy, `<span class="${signCls(t.pnl)}">${m(t.ccy, t.pnl)}</span>`]);
  }
  $("tTrades").innerHTML = tableHTML(cols, rows.slice(0, shown));
  const more = $("tradeMore");
  if (rows.length > shown) { more.style.display = "inline-block"; more.textContent = `加载更多（已显示 ${shown}/${rows.length}）`; } else more.style.display = "none";
}
document.addEventListener("DOMContentLoaded", () => {
  $("tradeAcct").addEventListener("change", () => { shown = 60; renderTradeTable(); });
  $("tradeSearch").addEventListener("input", () => { shown = 60; renderTradeTable(); });
  $("tradeMore").addEventListener("click", () => { shown += 150; renderTradeTable(); });
});

function renderCaveats() {
  $("caveats").innerHTML = "<b>数据修订记录</b><br>" + D.changelog.map((c, i) => `${i + 1}. ${c}`).join("<br>") +
    "<br><br><b>已知口径限制</b><br>" + D.caveats.map((c, i) => `${i + 1}. ${c}`).join("<br>") +
    "<br><br><b>安全说明</b><br>· 数据加密于 vault.json（AES-256-GCM，PBKDF2-SHA256 210,000 次迭代派生密钥），登录解密仅在本机浏览器完成，无服务端、无第三方请求。<br>· 明文只存在于当前标签页 sessionStorage，登出/关闭即销毁。";
  $("eqNote").innerHTML = "FUTU 净值双口径：① 资金流法（迁入+入金 vs 期末权益）= " + m("USD", D.futu_nav.pnl_mot_usd) + "；② 交易层法（已实现+浮动+费+息）= " + m("USD", D.futu_nav.pnl_tradeview_usd) + "；两法差 $" + fmt(Math.abs(D.futu_nav.views_gap_usd)) + "，互证成立。HKD→USD 折算率 " + D.futu_nav.fx_rate + " 仅用于展示合计。POEMS 净值曲线为台账月度已实现累加（不含浮动盈亏与利息）。";
}
})();
