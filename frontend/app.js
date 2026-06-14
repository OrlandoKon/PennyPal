"use strict";

const CATEGORIES = ["餐饮", "交通", "购物", "服饰", "数码", "娱乐", "居住", "医疗", "学习", "人情", "其他", "收入"];
const ICONS = {
  餐饮: "🍜", 交通: "🚕", 购物: "🛍️", 服饰: "👕", 数码: "💻", 娱乐: "🎮",
  居住: "🏠", 医疗: "💊", 学习: "📚", 人情: "🎁", 其他: "📦", 收入: "💴",
};
// 每个分类固定颜色：饼图按分类名取色，不随占比排名变化
const CATCOLOR = {
  餐饮: "#ef4444", 交通: "#f59e0b", 购物: "#3b82f6", 服饰: "#8b5cf6", 数码: "#06b6d4",
  娱乐: "#ec4899", 居住: "#10b981", 医疗: "#f97316", 学习: "#14b8a6", 人情: "#eab308",
  其他: "#64748b", 收入: "#22c55e",
};
const EXPENSE_CATS = CATEGORIES.filter((c) => c !== "收入");
let budgetsCache = {};
const ACCOUNT_COLOR = { 微信: "#07c160", 支付宝: "#1677ff", 现金: "#f59e0b", 银行卡: "#64748b" };
const ACCOUNT_PALETTE = ["#6366f1", "#ec4899", "#14b8a6", "#f97316", "#8b5cf6", "#22c55e"];

const $ = (id) => document.getElementById(id);
const history = []; // 仅保存 {role:'user'|'assistant', content:string} 文本
const curMonth = () => new Date().toISOString().slice(0, 7);
const fmt = (n) => Number(n || 0).toFixed(2);

// ---------- 仪表盘：三块同屏，手动刷新按钮 ----------
function refreshDashboard() { loadLedger(); loadAnalysis(); }
const _refresh = $("refresh");
if (_refresh) _refresh.addEventListener("click", refreshDashboard);

// ---------- 记账（聊天）----------
function addBubble(role, text) {
  const wrap = document.createElement("div");
  wrap.className = role === "user" ? "flex justify-end" : "flex justify-start";
  const b = document.createElement("div");
  b.className =
    (role === "user" ? "bg-blue-600 text-white" : "bg-slate-100 text-slate-800") +
    " rounded-2xl px-3 py-2 text-sm max-w-[80%] whitespace-pre-wrap break-words";
  b.textContent = text;
  wrap.appendChild(b);
  $("chat").appendChild(wrap);
  $("chat").scrollTop = $("chat").scrollHeight;
}

function addCard(rec) {
  const div = document.createElement("div");
  div.className = "flex justify-start";
  const income = rec.type === "income";
  const pending = rec.status === "pending"
    ? ' · <span class="text-amber-600">待确认</span>' : "";
  div.innerHTML =
    '<div class="bg-white border rounded-2xl px-3 py-2 text-sm shadow-sm max-w-[80%]">' +
      '<div class="flex items-center gap-2">' +
        '<span class="text-lg">' + (ICONS[rec.category] || "📦") + "</span>" +
        '<span class="font-medium">' + rec.category + "</span>" +
        '<span class="ml-auto font-semibold ' + (income ? "text-emerald-600" : "") + '">' +
          (income ? "+" : "-") + "¥" + fmt(rec.amount) + "</span>" +
      "</div>" +
      '<div class="text-xs text-slate-500 mt-1">' +
        rec.account + " · " + (rec.occurred_at || "").slice(0, 10) + pending +
      "</div>" +
    "</div>";
  $("chat").appendChild(div);
  $("chat").scrollTop = $("chat").scrollHeight;
}

async function sendMessage(text, image) {
  if (text) addBubble("user", text);
  if (image) addBubble("user", "📷 [截图]");
  $("msg").value = "";

  const typing = document.createElement("div");
  typing.className = "text-xs text-slate-400 px-1";
  typing.textContent = "正在记账…";
  $("chat").appendChild(typing);
  $("chat").scrollTop = $("chat").scrollHeight;

  try {
    const resp = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: text || null, image: image || null, history }),
    });
    const data = await resp.json();
    typing.remove();
    const added = data.added_records || [];
    added.forEach(addCard);
    if (data.reply) addBubble("assistant", data.reply);
    if (added.length || data.dirty) {
      // 写库成功（新增/改/删）：本条线程结束，清空历史，避免历史里的“已记”诱导模型只回文字、不调用工具
      history.length = 0;
      refreshDashboard();   // 右侧账本/图表自动刷新
    } else {
      // 没入账（多为信息不全的追问）：保留本轮，便于用户补充后继续
      if (text) history.push({ role: "user", content: text });
      if (data.reply) history.push({ role: "assistant", content: data.reply });
    }
  } catch (e) {
    typing.remove();
    addBubble("assistant", "网络错误，请重试。");
  }
}

$("send").addEventListener("click", () => {
  const t = $("msg").value.trim();
  if (t) sendMessage(t);
});
$("msg").addEventListener("keydown", (e) => {
  if (e.key === "Enter") {
    const t = $("msg").value.trim();
    if (t) sendMessage(t);
  }
});

// ---------- 图片上传 ----------
$("pick").addEventListener("click", () => $("file").click());
$("file").addEventListener("change", (e) => {
  const f = e.target.files[0];
  if (!f) return;
  const reader = new FileReader();
  reader.onload = () => {
    const dataUrl = String(reader.result);
    const comma = dataUrl.indexOf(",");
    const b64 = dataUrl.slice(comma + 1);
    const media = dataUrl.slice(5, comma).split(";")[0] || f.type || "image/png";
    sendMessage(null, { data: b64, media_type: media });
  };
  reader.readAsDataURL(f);
  e.target.value = "";
});

// ---------- 语音（Web Speech API；需安全上下文：HTTPS 或 http://localhost）----------
const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
const micBtn = $("mic");
if (!SR) {
  micBtn.disabled = true;
  micBtn.title = "此浏览器不支持语音识别（建议用 Chrome / Edge）";
  micBtn.classList.add("opacity-50");
} else if (!window.isSecureContext) {
  // http 非 localhost：浏览器会拒绝麦克风，start() 必失败 → 直接提示，避免"点了没反应"
  micBtn.title = "语音需要 HTTPS 或在 http://localhost 打开";
  micBtn.classList.add("opacity-60");
  micBtn.addEventListener("click", () => {
    addBubble("assistant", "🎤 语音识别需要安全环境：请用 HTTPS 访问，或在本机用 http://localhost:9527 打开本页。当前是普通 HTTP 公网地址，浏览器会拒绝麦克风——直接打字也能记账。");
  });
} else {
  let recog = null;
  let listening = false;
  micBtn.addEventListener("click", () => {
    if (listening) { if (recog) recog.stop(); return; }
    recog = new SR();
    recog.lang = "zh-CN";
    recog.interimResults = false;
    recog.onstart = () => { listening = true; micBtn.textContent = "● 录音中…"; };
    recog.onresult = (ev) => { $("msg").value = ev.results[0][0].transcript; };
    recog.onerror = (e) => {
      listening = false;
      micBtn.textContent = "🎤 语音";
      const tips = {
        "not-allowed": "麦克风被拒绝：请允许麦克风权限（且需 HTTPS/localhost）。",
        "service-not-allowed": "浏览器拒绝了语音服务：通常因为不是 HTTPS/localhost。",
        "no-speech": "没听到声音，再说一次。",
        "audio-capture": "没检测到麦克风设备。",
        "network": "语音服务网络错误。",
      };
      addBubble("assistant", tips[e.error] || ("语音出错：" + e.error));
    };
    recog.onend = () => { listening = false; micBtn.textContent = "🎤 语音"; };
    try {
      recog.start();
    } catch (err) {
      addBubble("assistant", "语音启动失败：" + err.message);
    }
  });
}

// ---------- 账本 ----------
$("ledger-month").addEventListener("change", loadLedger);
async function loadLedger() {
  if (!$("ledger-month").value) $("ledger-month").value = curMonth();
  const month = $("ledger-month").value;
  try {
    const data = await (await fetch("/api/records?month=" + month)).json();
    renderLedger(data.records || []);
  } catch (e) {
    $("ledger-list").innerHTML =
      '<div class="text-center text-red-400 text-sm py-10">加载失败</div>';
  }
}
function renderLedger(records) {
  const box = $("ledger-list");
  box.innerHTML = "";
  if (!records.length) {
    box.innerHTML = '<div class="text-center text-slate-400 text-sm py-10">本月还没有记录</div>';
    return;
  }
  records.forEach((rec) => {
    const income = rec.type === "income";
    const row = document.createElement("div");
    row.className = "flex items-center gap-3 border rounded-lg px-3 py-2";
    row.innerHTML =
      '<span class="text-xl">' + (ICONS[rec.category] || "📦") + "</span>" +
      '<div class="min-w-0">' +
        '<div class="text-sm font-medium">' + rec.category +
          (rec.status === "pending" ? ' <span class="text-amber-600 text-xs">待确认</span>' : "") +
        "</div>" +
        '<div class="text-xs text-slate-500 truncate">' +
          rec.account + " · " + (rec.occurred_at || "").slice(0, 10) +
          (rec.note ? " · " + rec.note : "") +
        "</div>" +
      "</div>" +
      '<div class="ml-auto font-semibold ' + (income ? "text-emerald-600" : "") + '">' +
        (income ? "+" : "-") + "¥" + fmt(rec.amount) + "</div>" +
      '<button class="edit text-slate-400">✏️</button>' +
      '<button class="del text-slate-400">🗑️</button>';
    row.querySelector(".edit").addEventListener("click", () => openEdit(rec));
    row.querySelector(".del").addEventListener("click", async () => {
      if (!confirm("删除这笔记录？")) return;
      await fetch("/api/records/" + rec.id, { method: "DELETE" });
      refreshDashboard();
    });
    box.appendChild(row);
  });
}

// ---------- 编辑弹窗 ----------
let editing = null;
CATEGORIES.forEach((c) => {
  const o = document.createElement("option");
  o.value = c;
  o.textContent = c;
  $("e-category").appendChild(o);
});
function openEdit(rec) {
  editing = rec;
  $("e-amount").value = rec.amount;
  $("e-category").value = rec.category;
  $("e-account").value = rec.account;
  $("e-date").value = (rec.occurred_at || "").slice(0, 10);
  $("e-status").value = rec.status || "confirmed";
  $("modal").classList.remove("hidden");
  $("modal").classList.add("flex");
}
function closeEdit() {
  $("modal").classList.add("hidden");
  $("modal").classList.remove("flex");
  editing = null;
}
$("e-cancel").addEventListener("click", closeEdit);
$("e-save").addEventListener("click", async () => {
  if (!editing) return;
  const body = {
    amount: parseFloat($("e-amount").value),
    category: $("e-category").value,
    account: $("e-account").value,
    occurred_at: $("e-date").value,
    status: $("e-status").value,
  };
  await fetch("/api/records/" + editing.id, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  closeEdit();
  refreshDashboard();
});

// ---------- 分析 ----------
let pieChart = null;
let barChart = null;
$("analysis-month").addEventListener("change", loadAnalysis);
async function loadAnalysis() {
  if (!$("analysis-month").value) $("analysis-month").value = curMonth();
  const month = $("analysis-month").value;
  const d = await (await fetch("/api/summary?month=" + month)).json();

  $("a-total").textContent = "¥" + Number(d.total || 0).toFixed(0);
  $("a-income").textContent = "¥" + Number(d.income_total || 0).toFixed(0);

  const ch = $("a-change");
  if (d.change_ratio == null) {
    ch.textContent = "环比上月 —";
    ch.className = "text-xs text-slate-400";
  } else {
    const up = d.change_ratio > 0;
    ch.textContent = "环比上月 " + (up ? "↑" : "↓") + Math.abs(d.change_ratio * 100).toFixed(0) + "%";
    ch.className = "text-xs " + (up ? "text-red-500" : "text-emerald-600");
  }
  $("a-insight").textContent = d.insight || "";
  budgetsCache = d.budgets || {};
  renderBudgetInputs();
  renderBudgetBars(d);
  renderPrefs(d.prefs || {});
  drawPie(d.by_category || []);
  drawAccountPie(d.by_account || []);
  drawBar(d.trend || []);
  renderHeatmap(month, d.daily || {});
  renderTopSpend(d.top_records || []);
}
function drawPie(byCat) {
  if (pieChart) pieChart.destroy();
  if (!byCat.length) { pieChart = null; return; }
  pieChart = new Chart($("pie"), {
    type: "doughnut",
    data: {
      labels: byCat.map((c) => c.category),
      datasets: [{ data: byCat.map((c) => c.amount), backgroundColor: byCat.map((c) => CATCOLOR[c.category] || "#94a3b8") }],
    },
    options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { position: "right", labels: { boxWidth: 12, font: { size: 11 } } } } },
  });
}
function drawBar(trend) {
  if (barChart) barChart.destroy();
  barChart = new Chart($("bar"), {
    type: "bar",
    data: {
      labels: trend.map((t) => t.month.slice(5) + "月"),
      datasets: [
        { label: "支出", data: trend.map((t) => t.total), backgroundColor: "#ef4444", borderRadius: 4 },
        { label: "收入", data: trend.map((t) => t.income || 0), backgroundColor: "#22c55e", borderRadius: 4 },
      ],
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { display: true, labels: { boxWidth: 12, font: { size: 11 } } } },
      scales: { y: { beginAtZero: true } },
    },
  });
}

let accountChart = null;
function drawAccountPie(byAccount) {
  if (accountChart) accountChart.destroy();
  if (!byAccount.length) { accountChart = null; return; }
  accountChart = new Chart($("account-pie"), {
    type: "doughnut",
    data: {
      labels: byAccount.map((a) => a.account),
      datasets: [{
        data: byAccount.map((a) => a.amount),
        backgroundColor: byAccount.map((a, i) => ACCOUNT_COLOR[a.account] || ACCOUNT_PALETTE[i % ACCOUNT_PALETTE.length]),
      }],
    },
    options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { position: "right", labels: { boxWidth: 12, font: { size: 11 } } } } },
  });
}

function renderHeatmap(month, daily) {
  const box = $("heatmap");
  const [y, m] = month.split("-").map(Number);
  const daysInMonth = new Date(y, m, 0).getDate();
  const firstDow = (new Date(y, m - 1, 1).getDay() + 6) % 7;   // 周一=0
  const max = Math.max(1, ...Object.values(daily));
  const shade = (v) => {
    if (!v) return "bg-slate-100 text-slate-400";
    const r = v / max;
    if (r > 0.75) return "bg-indigo-600 text-white";
    if (r > 0.5) return "bg-indigo-500 text-white";
    if (r > 0.25) return "bg-indigo-400 text-white";
    return "bg-indigo-200 text-slate-600";
  };
  let html = '<div class="grid grid-cols-7 gap-1 text-xs max-w-sm">';
  ["一", "二", "三", "四", "五", "六", "日"].forEach((d) => {
    html += '<div class="text-center text-slate-400 pb-1">' + d + "</div>";
  });
  for (let i = 0; i < firstDow; i++) html += "<div></div>";
  for (let day = 1; day <= daysInMonth; day++) {
    const ds = month + "-" + String(day).padStart(2, "0");
    const v = daily[ds] || 0;
    const title = v ? ds + " 支出 ¥" + v.toFixed(0) : ds + " 无支出";
    html += '<div class="aspect-square rounded flex items-center justify-center ' + shade(v) +
      '" title="' + title + '">' + day + "</div>";
  }
  html += "</div>";
  box.innerHTML = html;
  $("heatmap-legend").textContent = max > 1 ? "最高 ¥" + max.toFixed(0) + "/天" : "";
}

function renderTopSpend(items) {
  const box = $("top-spend");
  box.innerHTML = "";
  if (!items.length) {
    box.innerHTML = '<div class="text-xs text-slate-400">本月还没有支出。</div>';
    return;
  }
  items.forEach((r, i) => {
    const row = document.createElement("div");
    row.className = "flex items-center gap-2 text-sm";
    row.innerHTML =
      '<span class="text-slate-400 w-4 text-right shrink-0">' + (i + 1) + "</span>" +
      '<span class="text-lg shrink-0">' + (ICONS[r.category] || "📦") + "</span>" +
      '<div class="min-w-0 flex-1"><div class="truncate">' + r.category +
      (r.note ? ' <span class="text-slate-500">' + r.note + "</span>" : "") + "</div>" +
      '<div class="text-xs text-slate-400 truncate">' + r.account + " · " + (r.occurred_at || "").slice(0, 10) + "</div></div>" +
      '<span class="ml-auto font-semibold shrink-0">¥' + Number(r.amount).toFixed(0) + "</span>";
    box.appendChild(row);
  });
}

// ---------- 预算 ----------
function renderBudgetInputs() {
  const box = $("budget-inputs");
  box.innerHTML = "";
  const items = [["总预算", "__total__"], ...EXPENSE_CATS.map((c) => [c, c])];
  items.forEach(([label, key]) => {
    const v = budgetsCache[key] || "";
    const w = document.createElement("label");
    w.className = "flex items-center gap-1 text-sm";
    w.innerHTML =
      '<span class="text-slate-500 w-12 shrink-0 truncate">' + label + "</span>" +
      '<input data-cat="' + label + '" type="number" min="0" step="50" value="' + v +
      '" placeholder="—" class="w-full border rounded px-2 py-1 text-sm">';
    box.appendChild(w);
  });
}

async function saveBudgets() {
  const inputs = $("budget-inputs").querySelectorAll("input[data-cat]");
  await Promise.all([...inputs].map((inp) =>
    fetch("/api/budgets", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ category: inp.dataset.cat, amount: parseFloat(inp.value) || 0 }),
    })
  ));
  loadAnalysis();
}

function renderBudgetBars(summary) {
  const box = $("budget-bars");
  box.innerHTML = "";
  const spent = {};
  (summary.by_category || []).forEach((c) => { spent[c.category] = c.amount; });
  const rows = [];
  if (budgetsCache["__total__"]) rows.push(["总预算", summary.total || 0, budgetsCache["__total__"]]);
  EXPENSE_CATS.forEach((c) => { if (budgetsCache[c]) rows.push([c, spent[c] || 0, budgetsCache[c]]); });
  if (!rows.length) {
    box.innerHTML = '<div class="text-xs text-slate-400">还没设预算：上方填金额后点「保存预算」。</div>';
    return;
  }
  rows.forEach(([label, s, b]) => {
    const pct = Math.min((s / b) * 100, 100);
    const over = s > b;
    const color = over ? "bg-red-500" : s >= 0.8 * b ? "bg-amber-500" : "bg-emerald-500";
    const d = document.createElement("div");
    d.innerHTML =
      '<div class="flex justify-between text-xs mb-0.5"><span>' + label + "</span>" +
      '<span class="' + (over ? "text-red-600 font-medium" : "text-slate-500") + '">¥' +
      s.toFixed(0) + " / ¥" + b.toFixed(0) + (over ? " ⚠️" : "") + "</span></div>" +
      '<div class="h-2 bg-slate-100 rounded-full overflow-hidden"><div class="h-full ' + color +
      '" style="width:' + pct + '%"></div></div>';
    box.appendChild(d);
  });
}

$("budget-save").addEventListener("click", saveBudgets);
$("ai-insight").addEventListener("click", async () => {
  const month = $("analysis-month").value || curMonth();
  const btn = $("ai-insight");
  btn.disabled = true;
  btn.textContent = "✨ 解读中…";
  try {
    const d = await (await fetch("/api/insight?month=" + month)).json();
    if (d.insight) $("a-insight").textContent = d.insight;
  } catch (e) {}
  btn.disabled = false;
  btn.textContent = "✨ AI 解读";
});

function renderPrefs(prefs) {
  const box = $("prefs");
  box.innerHTML = "";
  const keys = Object.keys(prefs);
  if (!keys.length) {
    box.innerHTML = '<div class="text-xs text-slate-400">还没学到偏好。对它说「记住星巴克算餐饮」试试。</div>';
    return;
  }
  keys.forEach((k) => {
    const chip = document.createElement("span");
    chip.className = "inline-flex items-center gap-1 bg-slate-100 rounded-full pl-2 pr-1 py-0.5 text-xs";
    chip.innerHTML = "<span>" + k + " → " + prefs[k] +
      '</span><button class="w-4 h-4 leading-none text-slate-400 hover:text-red-500" title="删除">×</button>';
    chip.querySelector("button").addEventListener("click", async () => {
      await fetch("/api/prefs/" + encodeURIComponent(k), { method: "DELETE" });
      loadAnalysis();
    });
    box.appendChild(chip);
  });
}

// ---------- 初始化 ----------
addBubble("assistant", "你好，我是记账助手 👋 直接说一句就行，例如「中午吃饭35 微信付的」，也可以点 📷 上传支付截图。");
refreshDashboard();   // 三块同屏：进页面即加载账本与分析
