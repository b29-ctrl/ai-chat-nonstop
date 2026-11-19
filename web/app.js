const $ = (s) => document.querySelector(s);
const $$ = (s) => Array.from(document.querySelectorAll(s));

const DECK_TOKEN = (() => {
  const q = new URLSearchParams(location.search).get("token");
  if (q) localStorage.setItem("deck_token", q);
  return q || localStorage.getItem("deck_token") || "";
})();

const state = {
  view: "chat",
  target: "workbuddy",
  appState: null,
  selected: null,
  sending: false,
  es: null,
  pollTimer: null,
  elapsedTimer: null,
  statusBase: "",
  statusStart: 0,
  pendingAttachments: [],
  projectId: localStorage.getItem("deck_project") || "proj-default",
  groupMentions: ["all"],
  groupPoll: null,
  groupFastPoll: null,
  groupScrollProject: null,
  groupAttachments: [],
  projectsOpen: localStorage.getItem("deck_projects_open") !== "0",
  renaming: false,
  adapterRenaming: false,
  adapterShowAll: localStorage.getItem("deck_adapter_show_all") === "1",
  roleCatalog: null,
  roleMembers: [],
  roleAll: [],
  roleShowAll: false,
  coverageOpen: (() => { try { return JSON.parse(localStorage.getItem("deck_coverage_open") || "{}"); } catch (e) { return {}; } })(),
  coverageShowEmpty: false,
  roleDraft: {},
  onlyPendingSignoff: false,
  signoffBusy: false,
  signoffCardCache: {},
  _advanceTicketId: null,
  projRenamePid: null,
};

function adapters() {
  return state.appState?.adapters || {};
}
function adapterEntry(target) {
  const ads = adapters();
  const key = Object.keys(ads).find((k) => k.toLowerCase() === String(target || "").toLowerCase());
  return key ? { key, ad: ads[key] } : null;
}
function modelsFor(target) {
  return adapterEntry(target)?.ad.models || [];
}
function adapterLabel(target) {
  return adapterEntry(target)?.ad.label || target || "";
}
function defaultModel(target) {
  const e = adapterEntry(target);
  return e?.ad.default_model || (e?.ad.models?.[0]?.id || "");
}
function modelName(id, target = state.target) {
  const m = modelsFor(target).find((x) => x.id === id);
  return m ? m.name : id || "";
}

function optionHtml(list) {
  return list.map((m) => `<option value="${esc(m.id)}" title="${esc(m.desc || "")}">${esc(m.name)}</option>`).join("");
}

function populateTicketModels() {
  const fm = $("#fModel");
  const wrap = $("#fModelWrap");
  if (!fm || !wrap) return;
  const exec = $("#fExecutor")?.value || "";
  const list = modelsFor(exec);
  wrap.classList.toggle("hidden", !list.length);
  if (!list.length) return;
  const ids = list.map((x) => x.id).join(",");
  if (fm.dataset.ids !== ids) {
    fm.innerHTML = optionHtml(list);
    fm.dataset.ids = ids;
  }
  const def = defaultModel(exec);
  if (!fm.value || !list.some((m) => m.id === fm.value)) fm.value = def || list[0].id;
}

function populateModels() {
  const list = modelsFor(state.target);
  const wrap = $("#modelWrap");
  const sel = $("#chatModel");
  const refresh = $("#btnModels");
  if (wrap) wrap.classList.toggle("hidden", !list.length);
  if (refresh) refresh.classList.toggle("hidden", !adapterEntry(state.target)?.ad.refresh_models);
  if (sel && list.length) {
    const ids = list.map((x) => x.id).join(",");
    if (sel.dataset.ids !== ids) {
      sel.innerHTML = optionHtml(list);
      sel.dataset.ids = ids;
    }
    const saved = localStorage.getItem("model:" + state.target) || defaultModel(state.target);
    if (Array.from(sel.options).some((o) => o.value === saved)) sel.value = saved;
    else sel.value = list[0].id;
  }
  populateTicketModels();
}

function syncExecutorOptions() {
  const sel = $("#fExecutor");
  if (!sel) return;
  const ads = adapters();
  const cur = sel.value;
  sel.innerHTML = Object.entries(ads)
    .filter(([, a]) => a.enabled !== false && ((a.type || "").toLowerCase() !== "human"))
    .map(([id, a]) => `<option value="${esc(id)}">${esc(a.label || id)}</option>`)
    .join("");
  if (!sel.options.length) {
    Object.entries(ads).filter(([, a]) => ((a.type || "").toLowerCase() !== "human")).forEach(([id, a]) => {
      const o = document.createElement("option");
      o.value = id;
      o.textContent = a.label || id;
      sel.appendChild(o);
    });
  }
  if (cur && Array.from(sel.options).some((o) => o.value === cur)) sel.value = cur;
  else if (ads.workbuddy) sel.value = "workbuddy";
  populateTicketModels();
}

function syncFormModelVisibility() {
  populateTicketModels();
}
function syncModelVisibility() {
  populateModels();
}

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function fmtTime(t) {
  if (!t) return "—";
  const d = new Date(t);
  if (isNaN(d)) return t;
  return d.toLocaleString("zh-CN", { hour12: false });
}
function toast(msg, ms = 2200) {
  const el = $("#toast");
  el.textContent = msg;
  el.classList.add("show");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => el.classList.remove("show"), ms);
}

const GATE_NAMES = {
  "门①": "方案评审",
  "门②": "代码审核",
  "门③": "构建自检",
  "门④": "独立验收",
  "门⑤": "终验发版",
  "无": "无",
};
function gateLabel(g) {
  if (!g) return "—";
  return GATE_NAMES[g] || g;
}
function gateText(s) {
  let out = String(s ?? "");
  for (const [code, label] of Object.entries(GATE_NAMES)) out = out.split(code).join(label);
  return out;
}

async function copyText(text) {
  const s = String(text ?? "");
  try {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      await navigator.clipboard.writeText(s);
    } else {
      throw new Error("no clipboard api");
    }
  } catch {
    const ta = document.createElement("textarea");
    ta.value = s;
    ta.style.position = "fixed";
    ta.style.left = "-9999px";
    ta.style.top = "0";
    document.body.appendChild(ta);
    ta.focus();
    ta.select();
    try {
      document.execCommand("copy");
    } catch {}
    ta.remove();
  }
  toast("已复制");
}

function setStatus(text) {
  state.statusBase = text || "";
  state.statusStart = Date.now();
  $("#chatStatus").textContent = state.statusBase;
}
function startElapsed() {
  clearInterval(state.elapsedTimer);
  state.elapsedTimer = setInterval(() => {
    if (!state.sending || !state.statusBase) return;
    const s = Math.max(0, Math.floor((Date.now() - state.statusStart) / 1000));
    $("#chatStatus").textContent = `${state.statusBase}（已 ${s}s）`;
  }, 1000);
}
function clearSendTimers() {
  clearInterval(state.pollTimer);
  clearInterval(state.elapsedTimer);
  state.pollTimer = null;
  state.elapsedTimer = null;
  state.statusBase = "";
  state.statusStart = 0;
}

function fmtBytes(n) {
  const x = Number(n) || 0;
  if (x < 1024) return x + " B";
  if (x < 1024 * 1024) return (x / 1024).toFixed(1) + " KB";
  return (x / 1024 / 1024).toFixed(1) + " MB";
}
function parseMsgAttachments(m) {
  if (!m || !m.attachments) return [];
  try {
    const v = typeof m.attachments === "string" ? JSON.parse(m.attachments) : m.attachments;
    return Array.isArray(v) ? v : [];
  } catch {
    return [];
  }
}
function inlineAttachments(atts) {
  if (!atts || !atts.length) return "";
  return `<div class="chat-attach-list">${atts
    .map((a) => {
      const isImg = (a.mime || "").startsWith("image/");
      return `<a class="chat-attach" href="${esc(a.url || "/api/attachments/" + a.id)}" target="_blank" title="${esc(a.name)}">${isImg ? `<img src="${esc(a.url || "/api/attachments/" + a.id)}" alt="" />` : "📎"} ${esc(a.name)}</a>`;
    })
    .join("")}</div>`;
}
async function uploadAttachment(file) {
  if (file.size > 20 * 1024 * 1024) throw new Error(`${file.name} 超过 20MB`);
  const res = await fetch("/api/attachments", {
    method: "POST",
    headers: {
      "X-Deck-Token": DECK_TOKEN,
      "X-Filename": encodeURIComponent(file.name),
      "Content-Type": file.type || "application/octet-stream",
    },
    body: file,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data.attachment;
}
async function addFiles(files) {
  const list = Array.from(files || []);
  for (const f of list) {
    try {
      const a = await uploadAttachment(f);
      state.pendingAttachments.push(a);
      renderPendingAttachments();
      toast(`已上传 ${a.name}`);
    } catch (e) {
      toast("上传失败：" + e.message);
    }
  }
}
function renderPendingAttachments() {
  const box = $("#attachList");
  if (!box) return;
  box.innerHTML = state.pendingAttachments
    .map((a, i) => {
      const isImg = (a.mime || "").startsWith("image/");
      return `<div class="attach-chip">
        ${isImg ? `<img src="${esc(a.url)}" alt="" />` : "📎"}
        <span class="nm" title="${esc(a.name)}">${esc(a.name)} · ${fmtBytes(a.size)}</span>
        <button class="x" data-i="${i}" title="移除">×</button>
      </div>`;
    })
    .join("");
  $$("#attachList .x").forEach((b) => {
    b.onclick = () => {
      state.pendingAttachments.splice(Number(b.dataset.i), 1);
      renderPendingAttachments();
    };
  });
}
async function addGroupFiles(files) {
  const list = Array.from(files || []);
  for (const f of list) {
    try {
      const a = await uploadAttachment(f);
      state.groupAttachments.push(a);
      renderGroupAttachments();
      toast(`已上传 ${a.name}`);
    } catch (e) {
      toast("上传失败：" + e.message);
    }
  }
}
function renderGroupAttachments() {
  const box = $("#groupAttachList");
  if (!box) return;
  box.innerHTML = state.groupAttachments
    .map((a, i) => {
      const isImg = (a.mime || "").startsWith("image/");
      return `<div class="attach-chip">
        ${isImg ? `<img src="${esc(a.url)}" alt="" />` : "📎"}
        <span class="nm" title="${esc(a.name)}">${esc(a.name)} · ${fmtBytes(a.size)}</span>
        <button class="x" data-i="${i}" title="移除">×</button>
      </div>`;
    })
    .join("");
  $$("#groupAttachList .x").forEach((b) => {
    b.onclick = () => {
      state.groupAttachments.splice(Number(b.dataset.i), 1);
      renderGroupAttachments();
    };
  });
}

async function finishSend(target, reload = true) {
  if (state.target !== target || !state.sending) return;
  state.sending = false;
  $("#btnSend").disabled = false;
  if (state.es) {
    state.es.close();
    state.es = null;
  }
  clearSendTimers();
  $("#chatStatus").textContent = "";
  if (reload) {
    await loadHistory();
    loadState(true);
  }
}

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json", "X-Deck-Token": DECK_TOKEN },
    ...opts,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const err = new Error(data.error || `HTTP ${res.status}`);
    err.status = res.status;
    throw err;
  }
  return data;
}

function setView(view) {
  state.view = view;
  $$(".nav-item").forEach((b) => b.classList.toggle("active", b.dataset.view === view));
  $$(".view").forEach((v) => v.classList.add("hidden"));
  $(`#view-${view}`).classList.remove("hidden");
  if (view === "adapters") {
    const card = $("#cozeCard");
    const hasCoze = Boolean(adapterEntry("coze"));
    if (card) card.classList.toggle("hidden", !hasCoze);
    if (hasCoze) loadCozeBot();
    loadRoleCoverage();
  }
  if (view === "projects") {
    if (!state.groupPoll) {
      state.groupPoll = setInterval(() => {
        if (state.view === "projects") loadProjectFeed();
      }, 60000);
    }
    loadProjectFeed();
  } else if (state.groupPoll) {
    clearInterval(state.groupPoll);
    state.groupPoll = null;
  }
  if (view === "archive") renderArchiveView();
  const titles = {
    chat: ["沟通", "和 AI 对话，顺便看工单流转"],
    projects: ["项目群", "群聊 + 派单摘要 + 工单"],
    adapters: ["AI岗位", "谁在岗、怎么配、能干什么"],
    archive: ["归档项目", "归档不删除数据；恢复后回到项目列表并恢复派单"],
  };
  $("#pageTitle").textContent = titles[view][0];
  $("#pageSub").textContent = titles[view][1];
}

/* ---------- load ---------- */

async function loadState(silent = false) {
  try {
    const s = await api("/api/state");
    state.appState = s;
    if (!s.projects?.length) {
      s.projects = [{ id: "proj-default", name: "默认项目", members: [], member_count: 0 }];
    }
    renderArchiveMeta(s.projects);
    if (!s.projects.some((p) => p.id === state.projectId)) {
      const visible = s.projects.filter((p) => !p.archived);
      state.projectId = (visible[0] || s.projects[0]).id;
    }
    // 工单按当前项目收口；项目群只看本项目
    const allTickets = s.tickets || [];
    s.tickets = allTickets.filter((t) => (t.project_id || "proj-default") === state.projectId);
    s.stats = {};
    for (const t of s.tickets) s.stats[t.status] = (s.stats[t.status] || 0) + 1;
    if (state.selected && !s.tickets.some((t) => t.id === state.selected)) state.selected = null;
    if (!state.renaming) renderProjectControls(s);
    if (state.view === "projects") loadProjectFeed();
    syncExecutorOptions();
    populateModels();
    syncModelVisibility();
    // silent 模式下若第一次进入 board，自动打开第一张单，避免右侧空白
    // silent 模式下不自动展开工单；用户点行才展开
    $("#healthDot").className = "ok";
    $("#healthText").textContent = "本地服务在线";
    $("#lastTick").textContent = "最近 tick " + (s.meta?.last_tick ? fmtTime(s.meta.last_tick) : "—");
    if ($("#navCount")) $("#navCount").textContent = s.tickets.length;
    renderStats(s.stats);
    renderTargets();
    renderTickets(s.tickets);
    renderChatSide(s);
    if (!state.adapterRenaming) renderAdapters(s.adapters);
    if (state.view === "adapters") loadRoleCoverage();
    syncTicketSelect(s.tickets);
    if (state.selected) {
      const t = s.tickets.find((x) => x.id === state.selected);
      if (t) await openTicket(t.id, true);
    }
    if (!silent) return s;
  } catch (e) {
    $("#healthDot").className = "bad";
    $("#healthText").textContent = "服务未连接";
    if (!silent) toast("本地服务连接失败：" + e.message);
  }
}

function renderStats(stats) {
  const order = ["待处理", "派发中", "已回件", "待验", "已验", "失败"];
  $("#stats").innerHTML = order
    .map((k) => `<div class="stat ${k}"><b>${stats[k] ?? 0}</b><span>${k}</span></div>`)
    .join("");
}


/* ---------- AI 头像（2026-11-13：50 张按顺序分配；有官方 LOGO 时在 AV_MAP 覆盖） ---------- */
const AV_MAP = {
  // "dsh": "avatars/logo-dsh.png",  // 示例：拿到官方 LOGO 后填这里
};
const AV_ALIAS = {
  "boss": "boss", "大boss": "boss", "主理人": "boss", "用户": "boss",
  "workbuddy": "workbuddy", "wk": "workbuddy",
  "coze": "coze", "扣子": "coze",
  "dsh": "dsh", "codex": "codex",
  "codebuddy": "codebuddy-cn", "codebuddycn": "codebuddy-cn", "codebuddy-cn": "codebuddy-cn",
  "minimax": "minimax", "豆包": "doubao", "doubao": "doubao", "trae": "trae",
};
function avKeyOf(id) {
  const raw = String(id || "").trim();
  if (!raw) return "";
  const low = raw.toLowerCase();
  const ids = Object.keys(adapters() || {});
  if (ids.includes(raw)) return raw;
  if (ids.includes(low)) return low;
  const alias = AV_ALIAS[low] || AV_ALIAS[raw];
  if (alias && ids.includes(alias)) return alias;
  return "";
}
function avSrc(id) {
  const key = avKeyOf(id);
  if (!key) return "";
  if (AV_MAP[key]) return AV_MAP[key];
  const ids = Object.keys(adapters() || {});
  const i = ids.indexOf(key);
  if (i < 0) return "";
  return "avatars/av" + String((i % 50) + 1).padStart(2, "0") + ".png";
}
function av(id, name, cls) {
  const src = avSrc(id);
  const label = String(name || id || "AI");
  if (!src) return `<span class="${cls || "ai-avatar-wrap"} letter">${esc(label.slice(0, 1))}</span>`;
  return `<span class="${cls || "ai-avatar-wrap"}"><img src="${src}" alt="${esc(label)}" loading="lazy" /></span>`;
}

function renderTargets() {
  const a = adapters();
  let ids = Object.keys(a).filter((id) => a[id].enabled !== false && (a[id].type || "").toLowerCase() !== "human");
  if (!ids.length) ids = Object.keys(a);
  if (!ids.length) {
    $("#targets").innerHTML = "";
    return;
  }
  if (!ids.some((id) => id.toLowerCase() === String(state.target).toLowerCase())) {
    const firstReady = Object.entries(a).find(([id, it]) => it.ready && it.enabled !== false && (it.type || "").toLowerCase() !== "human");
    state.target = firstReady ? firstReady[0] : ids[0];
  }
  const items = ids.map((id) => {
    const it = a[id];
    return {
      id,
      ready: it.ready && it.enabled !== false,
      active: String(state.target).toLowerCase() === id.toLowerCase(),
      label: (it.label || id).split(/[（(]/)[0],
    };
  });
  $("#targets").innerHTML = `
    <label class="target-picker" title="选择要对话的 AI 岗位">
      ${av(state.target, adapterLabel(state.target), "avatar-sm")}<span>AI 岗位</span>
      <select id="targetSelect">
        ${items
          .map(
            (x) =>
              `<option value="${esc(x.id)}" ${x.active ? "selected" : ""}>${x.ready ? "" : "⚠ "}${esc(x.label)}</option>`
          )
          .join("")}
      </select>
    </label>
    <span class="target-hint" id="targetHint"></span>`;
  const sel = $("#targetSelect");
  const hint = $("#targetHint");
  const paintHint = () => {
    const x = items.find((it) => String(it.id).toLowerCase() === String(state.target).toLowerCase());
    if (!hint) return;
    hint.textContent = !x ? "" : x.ready ? "已就绪" : "未启用/未配置";
    hint.classList.toggle("bad", !!x && !x.ready);
  };
  paintHint();
  sel.onchange = () => {
    const it = items.find((x) => x.id === sel.value);
    if (!it) return;
    if (!it.ready) {
      toast("这个 AI 岗位还没配置好");
      sel.value = state.target;
      return;
    }
    if (state.es) {
      state.es.close();
      state.es = null;
    }
    clearSendTimers();
    state.sending = false;
    $("#btnSend").disabled = false;
    $("#chatStatus").textContent = "";
    state.target = it.id;
    renderTargets();
    syncModelVisibility();
    loadHistory();
    renderChatAdapterInfo();
  };
}

function renderChatAdapterInfo() {
  const e = adapterEntry(state.target);
  if (!e) {
    $("#chatAdapterInfo").innerHTML = "—";
    return;
  }
  const a = e.ad;
  const list = a.models || [];
  const modelLine = list.length
    ? `<div class="hint" style="margin-top:6px">模型：${esc(modelName($("#chatModel")?.value, e.key))}</div>`
    : "";
  const ta = $("#chatText");
  if (ta) ta.placeholder = `给 ${adapterLabel(state.target)} 发消息…（Enter 发送，Shift+Enter 换行）`;
  $("#chatAdapterInfo").innerHTML = `<div class="ai-row">${av(e.key, a.label, "avatar-md")}<span>${esc(a.label)}</span></div>
       <div>${esc(a.detail)}</div><div class="hint" style="margin-top:6px">${esc(a.type)} 型 · ${a.ready ? "可用" : "未就绪"}${list.length ? " · " + list.length + " 个模型" : ""}</div>${modelLine}`;
}

function renderChatSide(s) {
  $("#chatTickets").innerHTML =
    (s.tickets || [])
      .slice(0, 5)
      .map(
        (t) => `<button class="mini" data-open-ticket="${esc(t.id)}">
        <b>${esc(t.title)}</b><span>${esc(t.id)} · ${esc(t.status)}</span></button>`
      )
      .join("") || '<div class="muted">暂无工单</div>';
  $$("#chatTickets [data-open-ticket]").forEach((b) => {
    b.onclick = () => {
      setView("projects");
      openTicket(b.dataset.openTicket);
    };
  });
}

function shortUpdated(iso) {
  if (!iso) return "更新 —";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return "更新 —";
  const pad = (n) => String(n).padStart(2, "0");
  const hm = `${pad(d.getHours())}:${pad(d.getMinutes())}`;
  const now = new Date();
  const yest = new Date(now.getTime() - 86400000);
  const sameDay = (a, b) => a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
  if (sameDay(d, now)) return `更新 ${hm}`;
  if (sameDay(d, yest)) return `更新 昨天 ${hm}`;
  return `更新 ${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${hm}`;
}

function renderTickets(tickets) {
  const all = tickets || [];
  const pending = all.filter((t) => t.status === "待验");
  const btn = $("#btnPendingSignoff");
  if (btn) {
    btn.textContent = `待签收 ${pending.length}`;
    btn.classList.toggle("on", state.onlyPendingSignoff);
    btn.setAttribute("aria-pressed", state.onlyPendingSignoff ? "true" : "false");
  }
  const shown = state.onlyPendingSignoff ? pending : all;
  const tb = $("#ticketRows");
  tb.innerHTML = shown
    .map((t) => {
      const expanded = state.selected === t.id;
      return `<tr data-id="${esc(t.id)}" title="${esc(t.updated_at || "")}" class="${expanded ? "sel" : ""}">
        <td class="num">${esc(t.id)}</td>
        <td class="title"><span class="ticket-title-main">${esc(t.title)}</span><small>${esc(gateText(t.note || t.next_action || ""))}${t.executor ? " · " + esc(t.executor) : ""} · ${esc(shortUpdated(t.updated_at))}</small></td>
        <td><span class="badge ${esc(t.status)}">${esc(t.status)}</span></td>
      </tr>` + (expanded
        ? `<tr class="detail-tr"><td colspan="3"><div class="inline-detail" id="inlineDetail"><div class="empty"><div class="empty-orb small">▦</div><p>加载中…</p></div></div></td></tr>`
        : "");
    })
    .join("") || (state.onlyPendingSignoff ? '<tr><td colspan="3" class="muted">暂无待签收工单</td></tr>' : "");
  $$("#ticketRows tr[data-id]").forEach((tr) => {
    tr.onclick = () => toggleTicket(tr.dataset.id);
  });
  if ($("#ticketPaneCount")) $("#ticketPaneCount").textContent = state.onlyPendingSignoff ? `${shown.length}/${all.length} 张` : `${all.length} 张`;
  renderTicketProgress(all);
}

function renderTicketProgress(tickets) {
  const total = tickets.length;
  const done = tickets.filter((t) => t.status === "已验").length;
  const active = [...new Set(tickets.filter((t) => t.status === "派发中").map((t) => t.executor).filter(Boolean))];
  if ($("#ticketWho")) $("#ticketWho").textContent = "工作中：" + (active.length ? active.join(" · ") : "空闲");
  if ($("#ticketProgressBar")) $("#ticketProgressBar").style.width = (total ? Math.round((done / total) * 100) : 0) + "%";
  if ($("#ticketProgressText")) $("#ticketProgressText").textContent = `${done}/${total}`;
}

function toggleTicket(id) {
  if (state.selected === id) {
    state.selected = null;
    renderTickets(state.appState?.tickets || []);
    return;
  }
  openTicket(id);
}

function syncTicketSelect(tickets) {
  const sel = $("#chatTicket");
  const cur = sel.value;
  sel.innerHTML =
    '<option value="">不关联</option>' +
    (tickets || []).map((t) => `<option value="${esc(t.id)}">${esc(t.id)} · ${esc(t.title)}</option>`).join("");
  if (cur) sel.value = cur;
  $$("#chatTickets .mini").forEach(() => {});
}

function renderAdapters(adapters) {
  const entries = Object.entries(adapters || {});
  const LIMIT = 12;
  const showAll = !!state.adapterShowAll;
  const visible = showAll ? entries : entries.slice(0, LIMIT);
  $("#adapterCards").innerHTML = visible
    .map(([id, a]) => {
      const dot = a.enabled === false ? "warn" : a.ready ? "ok" : "bad";
      const models = Array.isArray(a.models) ? a.models : [];
      const curModel = a.default_model || (models[0] && models[0].id) || "";
      const modelRow = models.length
        ? `<div class="adapter-model-row"><span>模型</span><select class="adapter-model" data-aid="${esc(id)}">${models.map((m) => `<option value="${esc(m.id)}" ${m.id === curModel ? "selected" : ""}>${esc(m.name || m.id)}</option>`).join("")}</select></div>`
        : "";
      const modelNote = !models.length && a.type !== "human"
        ? `<div class="adapter-model-note">${a.type === "coze" ? "模型由 COZE Bot 决定" : "模型由该 CLI/配置决定"}</div>`
        : "";
      return `<div class="adapter" data-adapter="${esc(id)}">
        <div class="adapter-top">${av(id, a.label, "adapter-avatar")}<i class="dot ${dot}"></i><b>${esc(a.post || a.label)}</b><span class="type">${esc(a.type)}</span></div>
        <p><span class="adapter-alias" title="双击改 AI 名称">${esc(a.label)}</span> · ${esc(a.detail)}</p>
        ${modelRow}${modelNote}
        <code>${esc(a.hint || "")}</code>
      </div>`;
    })
    .join("");
  if (entries.length > LIMIT) {
    const more = document.createElement("div");
    more.className = "adapter-more";
    more.innerHTML = `<button class="ghost" id="btnAdapterMore">${
      showAll ? `收起（只显示前 ${LIMIT} 个）` : `显示全部（共 ${entries.length} 个）`
    }</button><span class="muted">${
      showAll ? `已展开全部 ${entries.length} 个` : `还有 ${entries.length - LIMIT} 个已折叠`
    }</span>`;
    $("#adapterCards").appendChild(more);
    more.querySelector("#btnAdapterMore").onclick = () => {
      if (state.adapterRenaming) state.adapterRenaming = false; // BUG-B1：折叠重绘会销毁改名输入框，必须先复位
      state.adapterShowAll = !state.adapterShowAll;
      try { localStorage.setItem("deck_adapter_show_all", state.adapterShowAll ? "1" : "0"); } catch (e) { /* 隐私模式忽略 */ }
      renderAdapters(state.appState?.adapters || {});
    };
  }
  $$("#adapterCards .adapter-alias").forEach((el) => {
    el.ondblclick = () => beginRenameAdapter(el.closest(".adapter").dataset.adapter);
  });
  $$("#adapterCards .adapter-model").forEach((el) => {
    el.onchange = () => setAdapterModel(el.dataset.aid, el.value);
  });
}
async function setAdapterModel(id, model) {
  try {
    await api(`/api/adapters/${encodeURIComponent(id)}/model`, { method: "POST", body: JSON.stringify({ model }) });
    toast("模型已切换，群聊/派单同步使用");
    await loadState(true);
  } catch (e) {
    toast("切换模型失败：" + e.message);
    await loadState(true);
  }
}
function beginRenameAdapter(id) {
  const card = document.querySelector(`.adapter[data-adapter="${CSS.escape(id)}"]`);
  const b = card?.querySelector(".adapter-alias") || card?.querySelector("b");
  if (!b || b.querySelector("input")) return;
  const old = b.textContent.trim();
  state.adapterRenaming = true;
  b.innerHTML = `<input class="adapter-name-input" maxlength="60" value="${esc(old)}" />`;
  const inp = b.querySelector("input");
  inp.focus();
  inp.select();
  let done = false;
  const finish = async (save) => {
    if (done) return;
    done = true;
    const val = inp.value.trim();
    state.adapterRenaming = false;
    if (save && val && val !== old) {
      try {
        await api(`/api/adapters/${encodeURIComponent(id)}/rename`, { method: "POST", body: JSON.stringify({ label: val }) });
        toast("AI 名称已改");
        await loadState(true);
        return;
      } catch (e) {
        toast("改名失败：" + e.message);
      }
    }
    renderAdapters(state.appState?.adapters || {});
  };
  inp.onkeydown = (e) => {
    if (e.key === "Enter") { e.preventDefault(); finish(true); }
    if (e.key === "Escape") { e.preventDefault(); finish(false); }
  };
  inp.onblur = () => finish(true);
}

/* ---------- R1 新增 AI / R2 导出 AI 岗位配置 ---------- */
function showAIError(msg) {
  const el = $("#aiError");
  if (!el) return;
  el.textContent = msg || "";
  el.classList.toggle("hidden", !msg);
  el.classList.toggle("bad", !!msg);   // 弹窗错误一律红字，含白名单拒绝指引
}
function syncAIAllowVisibility() {
  $("#aiAllowWrap").classList.toggle("hidden", $("#aiType").value !== "process");
}
function parseCommand(text) {
  const t = (text || "").trim();
  if (!t) return null;
  if (t.startsWith("[")) {
    try {
      const a = JSON.parse(t);
      return Array.isArray(a) && a.length ? a.map(String) : null;
    } catch (e) { return null; }
  }
  const lines = t.split(/\r?\n/).map((s) => s.trim()).filter(Boolean);
  return lines.length ? lines : null;
}
async function openAIModal() {
  showAIError("");
  $("#aiLabel").value = "";
  $("#aiId").value = "";
  $("#aiType").value = "process";
  $("#aiCommand").value = "";
  $("#aiModel").value = "";
  $("#aiEnabled").value = "true";
  $("#aiKeyPath").value = "";
  $("#aiAllowExecutable").checked = false;   // R1-B 默认不勾
  syncAIAllowVisibility();
  try {
    const cat = await api("/api/role-catalog");
    const opts = [];
    for (const layer of cat.layers || []) {
      for (const r of layer.roles || []) {
        opts.push(`<option value="${esc(r.id)}">${esc(layer.name)} · ${esc(r.name)}${r.human_only ? "（人工岗）" : ""}</option>`);
      }
    }
    $("#aiRole").innerHTML = opts.join("");
    const first = (cat.layers || []).flatMap((l) => l.roles || []).find((r) => !r.human_only);
    if (first) $("#aiRole").value = first.id;
  } catch (e) {
    showAIError("岗位目录读取失败：" + e.message);
  }
  $("#aiModal").classList.remove("hidden");
}
async function saveAI() {
  showAIError("");
  const label = $("#aiLabel").value.trim();
  const type = $("#aiType").value;
  const role_id = $("#aiRole").value;
  if (!label) return showAIError("名称 / label 必填");
  if (!role_id) return showAIError("默认岗位必填");
  const body = { label, type, role_id, enabled: $("#aiEnabled").value === "true" };
  const id = $("#aiId").value.trim();
  if (id) body.id = id;
  const model = $("#aiModel").value.trim();
  if (model) body.default_model = model;
  const keyPath = $("#aiKeyPath").value.trim();
  if (keyPath) {
    if (/sk-|bearer |api_key|codebuddy_api_key|token=/i.test(keyPath)) {
      return showAIError("Key/Token 只填本机密钥文件路径，禁止粘贴明文");
    }
    body.key_path = keyPath;
  }
  if (type === "process") {
    const cmd = parseCommand($("#aiCommand").value);
    if (!cmd) return showAIError("process 类型必须填 command（每行一个参数或 JSON 数组），不许 Shell 拼接");
    body.command = cmd;
    body.allow_executable = $("#aiAllowExecutable").checked;
  }
  try {
    const r = await api("/api/adapters", { method: "POST", body: JSON.stringify(body) });
    $("#aiModal").classList.add("hidden");
    await loadState(true);
    toast(`已新增 AI：${r.id}（备份 ${r.backup || "—"}${r.whitelisted ? " · 已加入执行白名单" : ""}）`);
  } catch (e) {
    showAIError("保存失败：" + e.message);
  }
}
function rosterFilename() {
  const d = new Date();
  const p = (n) => String(n).padStart(2, "0");
  return `ai-roster-${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}-${p(d.getHours())}${p(d.getMinutes())}.json`;
}
async function exportRoster() {
  try {
    const roster = await api("/api/roster");
    const text = JSON.stringify(roster, null, 2);
    const url = URL.createObjectURL(new Blob([text], { type: "application/json;charset=utf-8" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = rosterFilename();
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    toast("已导出 " + a.download);
  } catch (e) {
    toast("导出被拒绝：" + e.message);
  }
}

const COZE_MODEL_NAMES = { "1772700462": "豆包·2.0·pro" };
async function loadCozeBot() {
  const box = $("#cozeInfo");
  if (!box) return;
  if (!adapterEntry("coze")) {
    $("#cozeCard")?.classList.add("hidden");
    return;
  }
  box.textContent = "读取中…";
  try {
    const r = await api("/api/coze/bot");
    if (!r.ok) {
      box.innerHTML = `<span style="color:#b03b3b">${esc(r.error || "读取失败")}</span>`;
      return;
    }
    const mname = COZE_MODEL_NAMES[r.model_id] || r.model_id || "未知";
    box.innerHTML = `<div><b>Bot：</b>${esc(r.name || r.bot_id)}（已发布版本 ${esc(r.version || "—")}）</div>
      <div><b>模型：</b>${esc(mname)} <span class="hint">id ${esc(r.model_id || "—")}</span></div>
      <div><b>上下文轮数：</b>${esc(r.context_round ?? "—")} · <b>知识库：</b>${r.knowledge_count || 0} 个</div>`;
  } catch (e) {
    box.innerHTML = `<span style="color:#b03b3b">${esc(e.message)}</span>`;
  }
}

/* ---------- ticket detail ---------- */

async function openTicket(id, silent = false) {
  try {
    const t = await api("/api/tickets/" + encodeURIComponent(id));
    state.selected = id;
    renderTickets(state.appState?.tickets || []);
    const evs = (t.events || []).map((e) => {
      const cls = e.kind.includes("gate") || e.kind === "gate_passed" ? "gate" : e.kind.includes("fail") ? "fail" : "";
      return `<div class="ev ${cls}">
        <b>${esc(e.kind)}</b> <time>${fmtTime(e.at)}</time>
        <p>${esc(e.actor || "")}${e.detail ? " · " + esc(gateText(e.detail)) : ""}</p></div>`;
    });
    const reply = await fetchReplySummary(t);
    const tmodels = modelsFor(t.executor);
    const hasModel = tmodels.length > 0;
    $("#inlineDetail").innerHTML = `
      <div class="detail-head">
        <button class="detail-close" data-close-detail>收起 ↑</button>
        <div class="num">${esc(t.id)} <button class="copy-inline" id="copyTicketId" title="复制单号" aria-label="复制单号">⧉</button></div>
        <h2>${esc(t.title)}</h2>
        <div class="detail-meta">
          <span class="badge ${esc(t.status)}">${esc(t.status)}</span>
          <span class="chip">${av(t.executor, t.executor, "chip-avatar")}${esc(t.executor || "未指定执行端")}</span>
          ${hasModel ? `<span class="chip">模型 ${esc(modelName(t.model || defaultModel(t.executor), t.executor) || t.model || "默认")}</span>` : ""}
          <span class="chip">${esc(gateLabel(t.gate))}</span>
          <span class="chip">${esc(t.priority || "P2")}</span>
        </div>
      </div>
      <div class="detail-body">
        <dl class="kv">
          <dt>收件人</dt><dd>${av(t.recipient, t.recipient, "chip-avatar")}${esc(t.recipient || "—")}</dd>
          <dt>下一步</dt><dd>${esc(gateText(t.next_action || "—"))}</dd>
          <dt>备注</dt><dd>${esc(t.note || "—")}</dd>
          <dt>创建</dt><dd>${fmtTime(t.created_at)}</dd>
          <dt>更新</dt><dd>${fmtTime(t.updated_at)}</dd>
        </dl>
        ${
          hasModel
            ? `<div class="model-switch">
                <label>执行模型
                  <select id="switchModel">${tmodels
                    .map((m) => `<option value="${esc(m.id)}" ${m.id === (t.model || defaultModel(t.executor)) ? "selected" : ""}>${esc(m.name)}</option>`)
                    .join("")}</select>
                </label>
                <button class="ghost" id="btnSwitchModel">切换（下一轮生效）</button>
              </div>
              <div class="hint" style="margin:6px 0 12px">本轮已启动的进程不会中途换模型；切换用于下一次派发/重试。</div>`
            : ""
        }
        ${signoffReplyHtml(reply)}
        <h3 style="font-size:12px;color:var(--ink2);margin:0 0 9px">时间线</h3>
        <div class="timeline">${evs.join("") || '<div class="muted">暂无事件</div>'}</div>
        <div class="detail-actions">
          ${t.status === "待验" ? '<button class="primary" id="btnSignoff">签收</button>' : ""}
          ${t.status !== "待验" ? '<button class="ghost" id="btnAdvance">演示推进 →</button>' : ""}
          <button class="ghost" id="btnChatAbout">在群聊中追问</button>
        </div>
        <div class="hint" style="margin-top:8px">${t.status === "待验" ? "「待验」单请由主理人亲自签收；演示推进在此状态下已隐藏，避免产生「演示签收」记录。" : "「演示推进」只模拟台账状态，不产生真实签收；真实派发器接入后由台账自动更新。"}</div>
        ${
          (t.signoffs || []).length
            ? `<h3 style="font-size:12px;color:var(--ink2);margin:14px 0 7px">签收</h3>` +
              t.signoffs.map((s) => `<div class="muted">${esc(gateLabel(s.gate))} · ${esc(s.signer)} · ${fmtTime(s.at)}</div>`).join("")
            : ""
        }
      </div>`;
    if ($("#btnAdvance")) $("#btnAdvance").onclick = () => openAdvanceConfirm(id, t);
    if ($("#btnSignoff")) $("#btnSignoff").onclick = () => openSignoffModal(t, reply);
    const closeDetail = $("[data-close-detail]");
    if (closeDetail) closeDetail.onclick = () => {
      state.selected = null;
      renderTickets(state.appState?.tickets || []);
    };
    if ($("#copyTicketId")) $("#copyTicketId").onclick = () => copyText(t.id);
    if ($("#btnSwitchModel")) {
      $("#btnSwitchModel").onclick = () => switchTicketModel(id, $("#switchModel").value);
    }
    $("#btnChatAbout").onclick = async () => {
      const pid = t.project_id || "proj-default";
      if (!(state.appState?.projects || []).some((p) => p.id === pid)) {
        toast("切换到项目失败：项目不存在");
        return;
      }
      if (pid !== state.projectId) {
        try {
          await switchProject(pid);
        } catch (e) {
          toast("切换到项目失败：" + e.message);
          return;
        }
      }
      if (state.projectId !== pid) {
        toast("切换到项目失败：项目切换未完成");
        return;
      }
      setView("projects");
      const label = adapterLabelSafe(t.executor) || t.executor || "";
      const box = $("#groupText");
      if (box) {
        box.value = `@${label} 关于 ${t.id}：`;
        state.groupMentions = t.executor ? [t.executor] : ["all"];
        renderProjectControls(state.appState || {});
        box.focus();
        const len = box.value.length;
        try { box.setSelectionRange(len, len); } catch (_) { /* 输入框禁用时忽略 */ }
      }
      toast("已引用 " + t.id + "，接着写要追问的内容");
    };
    if (!silent) {
      const row = $(`#ticketRows tr[data-id="${CSS.escape(id)}"]`);
      if (row) row.scrollIntoView({ block: "nearest", behavior: "smooth" });
      if (state.view !== "projects") setView("projects");
    }
  } catch (e) {
    toast("打开工单失败：" + e.message);
  }
}

/* ---------- 界面签收 CB-UI-SIGNOFF-20261112（仅前端交互，不改后端状态机） ---------- */

async function fetchReplySummary(t) {
  const model = t.model || "—";
  const evs = (t.events || []).filter(
    (e) => e.detail && /回件|reply|dispatch_ok|answer|output/i.test(String(e.kind || "")) && String(e.detail).length > 12
  );
  const last = evs[evs.length - 1];
  if (last) return { text: String(last.detail), at: fmtTime(last.at), model };
  try {
    const pid = t.project_id || "proj-default";
    const r = await api(`/api/projects/${encodeURIComponent(pid)}/messages?limit=120`);
    const msgs = (r.messages || [])
      .filter((m) => m.role === "assistant" && (!t.executor || String(m.adapter || "").toLowerCase() === String(t.executor).toLowerCase()))
      .sort((a, b) => (Number(a.id) || 0) - (Number(b.id) || 0));
    const m = msgs[msgs.length - 1];
    if (m && m.content) return { text: String(m.content), at: fmtTime(m.at || m.created_at), model: m.model || model };
  } catch (e) {
    /* 取不到回件就走兜底文案，不阻断详情打开 */
  }
  return { text: "", at: "", model };
}

function signoffReplyHtml(reply) {
  const r = reply || { text: "", at: "", model: "" };
  const full = String(r.text || "");
  const cut = full.slice(0, 1200);
  const long = full.length > 1200;
  return `<div class="reply-summary">
    <div class="reply-summary-head"><b>回件摘要</b><span class="hint">${esc(r.at || "—")} · 模型 ${esc(r.model || "—")}</span></div>
    ${
      full
        ? `<div class="reply-text">${esc(cut)}${long ? '<span class="muted"> …（已截断 1200 字）</span>' : ""}</div>` +
          (long ? `<details class="reply-full"><summary>展开全文</summary><div class="reply-text">${esc(full)}</div></details>` : "")
        : '<div class="muted">回件内容见群聊/沟通记录</div>'
    }
  </div>`;
}

function signoffError(e) {
  if (e && e.status === 403) return "签收人不在允许名单（security.allowed_signers）";
  if (e && e.status === 409) return "状态已被更新（可能已签或已被其它请求处理），刷新后再看";
  if (e && e.status === 400) return e.message || "签收参数有误";
  if (e && e.status === 404) return "工单不存在，请刷新后再看";
  return "签收失败，请重试";
}

function closeSignoffModal() {
  $("#signoffModal").classList.add("hidden");
  state._signoffTicket = null;
  state.signoffBusy = false;
  const b = $("#btnSignoffConfirm");
  b.disabled = false;
  b.textContent = "确认签收";
}

function openSignoffModal(t, reply) {
  if (state.signoffBusy) return;
  if (t.status !== "待验") return toast("当前状态不可签收");
  state._signoffTicket = t;
  $("#soTicketId").textContent = t.id || "—";
  $("#soTitle").textContent = t.title || "—";
  $("#soGate").textContent = t.gate && t.gate !== "无" ? `${t.gate} ${gateLabel(t.gate)}` : gateLabel(t.gate || "无");
  $("#soExecutor").innerHTML = av(t.executor, t.executor, "chip-avatar") + esc(t.executor || "未指定");
  $("#soTask").textContent = `${t.title || "—"} · ${noteSummary(t)}`;
  $("#soReply").textContent = reply && reply.text
    ? (reply.text.length > 1200 ? reply.text.slice(0, 1200) + " …" : reply.text)
    : "回件内容见群聊/沟通记录";
  $("#soBasis").textContent = `${gateFull(t)} · 执行端 ${t.executor || "—"} · 回件 ${(reply && reply.at) || "—"} · 核对回件无异议后签收`;
  $("#soSigner").value = "主理人";
  $("#soNote").value = "";
  const b = $("#btnSignoffConfirm");
  b.disabled = false;
  b.textContent = "确认签收";
  $("#signoffModal").classList.remove("hidden");
  $("#btnSignoffCancel").focus();
}

async function submitSignoff() {
  const t = state._signoffTicket;
  if (!t || state.signoffBusy) return;
  const signer = $("#soSigner").value.trim();
  if (!signer) return toast("请填写签收人");
  const b = $("#btnSignoffConfirm");
  state.signoffBusy = true;
  b.disabled = true;
  b.textContent = "签收中…";
  try {
    await api(`/api/tickets/${encodeURIComponent(t.id)}/signoff`, {
      method: "POST",
      body: JSON.stringify({ gate: t.gate || "无", signer, note: $("#soNote").value.trim() }),
    });
    closeSignoffModal();
    delete state.signoffCardCache[t.id];
    const label = gateFull(t);
    toast(`已签收：${label}`);
    try {
      await loadState(true);
      await openTicket(t.id, true);
      await renderSignoffCards(true);
      const badge = $("#inlineDetail .detail-meta .badge");
      if (!badge || badge.textContent.trim() !== "已验") toast("已签收，请手动刷新");
    } catch (e2) {
      toast("已签收，请手动刷新");
    }
  } catch (e) {
    state.signoffBusy = false;
    b.disabled = false;
    b.textContent = "确认签收";
    toast(signoffError(e));
  }
}

function parseAlertTicketId(content) {
  const s = String(content || "");
  const wo = s.match(/WO-[A-Za-z0-9-]+/);
  if (wo) return wo[0];
  const zh = s.match(/单号\s*([^\s，,。;；]+)/);
  return zh ? zh[1] : "";
}

function goSignoff(ticketId) {
  setView("projects");
  if (ticketId) {
    openTicket(ticketId);
    return;
  }
  state.onlyPendingSignoff = true;
  renderTickets(state.appState?.tickets || []);
  toast("已筛选待签收工单");
}

/* ---------- 界面签收 II CB-UI-SIGNOFF-2-20261112（演示推进保护 + 群聊签收卡） ---------- */

const DEMO_NEXT = { "待处理": "派发中", "派发中": "已回件", "已回件": "待验", "待验": "已验", "失败": "待处理" };

function closeAdvanceConfirm() {
  $("#advanceConfirmModal").classList.add("hidden");
  state._advanceTicketId = null;
}

function openAdvanceConfirm(id, t) {
  const nxt = DEMO_NEXT[t.status] || "";
  state._advanceTicketId = id;
  $("#advTicketId").textContent = id || "—";
  $("#advStatus").textContent = t.status || "—";
  $("#advNext").textContent = nxt || "（无路径）";
  $("#advanceWarn").classList.toggle("hidden", nxt !== "已验");
  $("#advanceConfirmModal").classList.remove("hidden");
  $("#btnAdvanceCancel").focus();
}

function noteSummary(t, cap = 160) {
  const s = String(t.note || t.next_action || "");
  if (!s) return "（无备注）";
  return s.length > cap ? s.slice(0, cap) + " …" : s;
}

function gateFull(t) {
  return t.gate && t.gate !== "无" ? `${t.gate} ${gateLabel(t.gate)}` : gateLabel(t.gate || "无");
}

async function cardReply(t) {
  const evs = (t.events || []).filter((e) => e.detail && /reply_seen|回件/i.test(String(e.kind || "")));
  const last = evs[evs.length - 1];
  if (last) return { text: String(last.detail), at: fmtTime(last.at), model: t.model || "—" };
  try {
    const pid = t.project_id || "proj-default";
    const r = await api(`/api/projects/${encodeURIComponent(pid)}/messages?limit=120`);
    const msgs = (r.messages || [])
      .filter((m) => m.role === "assistant" && (!t.executor || String(m.adapter || "").toLowerCase() === String(t.executor).toLowerCase()))
      .sort((a, b) => (Number(a.id) || 0) - (Number(b.id) || 0));
    const m = msgs[msgs.length - 1];
    if (m && m.content) return { text: String(m.content), at: fmtTime(m.at || m.created_at), model: m.model || t.model || "—" };
  } catch (e) { /* 取不到回件走占位，不抛错 */ }
  return { text: "", at: "", model: t.model || "—" };
}

function signoffCardHtml(d) {
  if (!d || !d.t) return '<div class="signoff-card empty">点击查阅详情</div>';
  const t = d.t, reply = d.reply || { text: "", at: "", model: "" };
  const full = String(reply.text || "");
  const cut = full.slice(0, 400);
  const long = full.length > 400;
  const done = t.status !== "待验";
  return `<div class="signoff-card">
    <div class="sc-head"><b>待签收卡 · ${esc(t.id)}</b><span class="sc-badge ${done ? "done" : ""}">${esc(t.status || "—")}</span></div>
    <div class="sc-row"><b>任务</b><div>${esc(t.title || "—")}<div class="muted">${esc(noteSummary(t))}</div></div></div>
    <div class="sc-row"><b>结果</b><div>${full ? esc(cut) + (long ? " …" : "") : '<span class="muted">回件内容见群聊/沟通记录</span>'}${long ? `<details class="sc-full"><summary>展开全文</summary><div>${esc(full)}</div></details>` : ""}</div></div>
    <div class="sc-row"><b>签收依据</b><div>${esc(gateFull(t))} · 执行端 ${av(t.executor, t.executor, "chip-avatar")}${esc(t.executor || "—")} · 回件 ${esc(reply.at || "—")}<div class="muted">核对回件无异议后签收</div></div></div>
    <div class="sc-actions">
      <button class="ghost" data-card-detail="${esc(t.id)}">查阅详情</button>
      ${done ? '<span class="sc-done">已验 · 不可签收</span>' : `<button class="primary" data-card-signoff="${esc(t.id)}">签收</button>`}
    </div>
  </div>`;
}

async function renderSignoffCards(force = false) {
  const boxes = $$("#groupFeed [data-card-ticket]");
  if (!boxes.length) return;
  for (const box of boxes) {
    const id = box.dataset.cardTicket;
    let d = state.signoffCardCache[id];
    if (force || !d || Date.now() - d.ts > 60000) {
      let data = { t: null, reply: null, ts: Date.now() };
      try {
        const t = await api(`/api/tickets/${encodeURIComponent(id)}`);
        data.t = t;
        data.reply = await cardReply(t);
      } catch (e) { /* 取不到走占位，不阻断群聊渲染 */ }
      d = data;
      state.signoffCardCache[id] = d;
    }
    box.innerHTML = signoffCardHtml(d);
  }
  $$("#groupFeed [data-card-detail]").forEach((b) => {
    b.onclick = () => { setView("projects"); openTicket(b.dataset.cardDetail); };
  });
  $$("#groupFeed [data-card-signoff]").forEach((b) => {
    b.onclick = () => {
      const d = state.signoffCardCache[b.dataset.cardSignoff];
      if (d && d.t) openSignoffModal(d.t, d.reply || { text: "", at: "", model: "" });
      else { setView("projects"); openTicket(b.dataset.cardSignoff); toast("点击查阅详情"); }
    };
  });
}

async function advance(id) {
  try {
    const r = await api(`/api/tickets/${encodeURIComponent(id)}/advance`, { method: "POST", body: "{}" });
    toast(`已推进到「${r.status}」`);
    await loadState(true);
    await openTicket(id, true);
  } catch (e) {
    toast("推进失败：" + e.message);
  }
}

async function switchTicketModel(id, model) {
  try {
    await api(`/api/tickets/${encodeURIComponent(id)}/model`, { method: "POST", body: JSON.stringify({ model }) });
    toast("模型已切换，下一轮派发/重试生效");
    await loadState(true);
    await openTicket(id, true);
  } catch (e) {
    toast("切换失败：" + e.message);
  }
}

/* ---------- chat ---------- */

async function loadHistory() {
  try {
    if (!state.sending) $("#chatStatus").textContent = "";
    const msgs = await api("/api/chat/history?target=" + encodeURIComponent(state.target));
    const log = $("#chatLog");
    if (!msgs.length) {
      log.innerHTML = `<div class="empty-chat">
        <div class="empty-orb">◉</div><h2>从一个问题开始</h2>
        <p>当前：${esc(adapterLabel(state.target))}。发消息后会显示在这里。</p></div>`;
      return;
    }
    log.innerHTML = msgs
      .map((m, i) => {
        const err = m.role === "assistant" && /^【.*(错误|失败|未配置|超时|无输出|未接入)/.test(m.content || "");
        const mn = m.model ? " · " + modelName(m.model, m.adapter) : "";
        return `<div class="msg ${m.role === "user" ? "user" : ""}" data-mid="${m.id}">
          <div class="avatar">${av(m.role === "user" ? "" : (m.adapter || ""), m.role === "user" ? "我" : "AI")}</div>
          <div class="msg-col">
            <div class="bubble ${err ? "err" : ""}">${esc(m.content)}${inlineAttachments(parseMsgAttachments(m))}<div class="meta">${esc((m.adapter || "") + mn)} · ${fmtTime(m.created_at)}</div></div>
            <div class="msg-actions"><button class="copy-btn" data-i="${i}" title="复制" aria-label="复制这条消息">⧉</button></div>
          </div>
        </div>`;
      })
      .join("");
    $$("#chatLog .copy-btn").forEach((b) => {
      b.onclick = () => copyText(msgs[Number(b.dataset.i)]?.content || "");
    });
    // 没有手动选过模型时，用最后一条消息的模型回填下拉
    if (!localStorage.getItem("model:" + state.target)) {
      const lm = [...msgs].reverse().find((m) => m.model);
      const sel = $("#chatModel");
      if (lm && sel && Array.from(sel.options).some((o) => o.value === lm.model)) {
        sel.value = lm.model;
        renderChatAdapterInfo();
      }
    }
    log.scrollTop = log.scrollHeight;
  } catch (e) {
    toast("读取聊天记录失败：" + e.message);
  }
}

function appendMsg(role, content, err = false, adapter = "", attachments = []) {
  const log = $("#chatLog");
  const empty = log.querySelector(".empty-chat");
  if (empty) empty.remove();
  const div = document.createElement("div");
  div.className = "msg " + (role === "user" ? "user" : "");
  div.innerHTML = `<div class="avatar">${av(role === "user" ? "" : adapter, role === "user" ? "我" : "AI")}</div>
    <div class="msg-col">
      <div class="bubble ${err ? "err" : ""}">${esc(content)}${inlineAttachments(attachments)}<div class="meta">${esc(adapter)} · ${fmtTime(new Date())}</div></div>
      <div class="msg-actions"><button class="copy-btn" title="复制" aria-label="复制这条消息">⧉</button></div>
    </div>`;
  const btn = div.querySelector(".copy-btn");
  if (btn) btn.onclick = () => copyText(content);
  log.appendChild(div);
  log.scrollTop = log.scrollHeight;
}

async function send() {
  const text = $("#chatText").value.trim();
  if (!text || state.sending) return;
  const target = state.target;
  const model = modelsFor(target).length ? $("#chatModel")?.value || null : null;
  const attachments = state.pendingAttachments.slice();
  let baselineId = null;
  try {
    const h = await api("/api/chat/history?target=" + encodeURIComponent(target));
    baselineId = h.length ? Number(h[h.length - 1].id) : 0;
  } catch {}
  state.sending = true;
  $("#btnSend").disabled = true;
  setStatus("发送中…");
  startElapsed();
  appendMsg("user", text, false, target, attachments);
  $("#chatText").value = "";
  try {
    const r = await api("/api/chat", {
      method: "POST",
      body: JSON.stringify({
        target,
        message: text,
        ticket_id: $("#chatTicket").value || null,
        model,
        attachment_ids: attachments.map((a) => a.id),
      }),
    });
    state.pendingAttachments = [];
    renderPendingAttachments();
    if (state.target !== target) return;
    if (state.es) state.es.close();
    const es = new EventSource(`/api/jobs/${r.job_id}/events`);
    state.es = es;
    es.onmessage = (ev) => {
      if (state.target !== target) {
        es.close();
        if (state.es === es) state.es = null;
        return;
      }
      let d;
      try { d = JSON.parse(ev.data); } catch { return; }
      if (d.type === "status") {
        setStatus(d.content);
      } else if (d.type === "reply" || d.type === "done") {
        finishSend(target);
      }
    };
    es.onerror = () => {
      if (state.target !== target) {
        es.close();
        if (state.es === es) state.es = null;
        return;
      }
      // SSE 断了，但后台任务可能还在跑；由轮询兜底，不立即判失败
      if (state.sending) setStatus("连接中断，等待后台结果…");
    };
    const deadline = Date.now() + 180000;
    state.pollTimer = setInterval(async () => {
      if (!state.sending || state.target !== target) return;
      if (Date.now() > deadline) {
        setStatus("等待超时，请刷新查看历史");
        finishSend(target);
        return;
      }
      try {
        const h = await api("/api/chat/history?target=" + encodeURIComponent(target));
        const last = h.length ? h[h.length - 1] : null;
        if (baselineId !== null && last && Number(last.id) > baselineId && last.role === "assistant") {
          finishSend(target);
        }
      } catch {}
    }, 2000);
  } catch (e) {
    appendMsg("assistant", "【发送失败】" + e.message, true, target);
    state.sending = false;
    $("#btnSend").disabled = false;
    clearSendTimers();
    $("#chatStatus").textContent = "";
  }
}

/* ---------- projects / group ---------- */

function projects() {
  return state.appState?.projects || [];
}
function currentProject() {
  return projects().find((p) => p.id === state.projectId) || projects()[0] || null;
}
function adapterLabelSafe(id) {
  const e = adapterEntry(id);
  return e ? e.ad.label || id : id;
}
function isHumanAdapter(id) {
  const e = adapterEntry(id);
  return Boolean(e && (e.ad.type || "").toLowerCase() === "human");
}
function renderProjectControls(s) {
  const list = (s.projects || []).filter((p) => !p.archived);
  const sel = $("#projectSelect");
  if (sel) {
    sel.innerHTML = list.map((p) => `<option value="${esc(p.id)}">${esc(p.name)}</option>`).join("");
    sel.value = state.projectId;
  }
  const p = currentProject();
  if (!p) return;
  if ($("#projectTitle")) $("#projectTitle").textContent = p.name;
  const st = p.status || "running";
  const stEl = $("#projectStatus");
  if (stEl) {
    if (p.archived) {
      stEl.className = "badge arch-badge";
      stEl.textContent = "已归档·只读";
    } else {
      stEl.className = "badge " + (st === "running" ? "已验" : st === "paused" ? "待验" : "失败");
      stEl.textContent = st === "running" ? "进行中" : st === "paused" ? "已暂停" : "已停止";
    }
  }
  if ($("#btnPauseProject")) $("#btnPauseProject").disabled = st !== "running";
  if ($("#btnResumeProject")) $("#btnResumeProject").disabled = st === "running";
  if ($("#btnStopProject")) $("#btnStopProject").disabled = st === "stopped";
  if ($("#groupText")) {
    $("#groupText").disabled = st !== "running";
    $("#groupText").placeholder = st === "running" ? "发消息…（输入 @ 选择成员）" : st === "paused" ? "项目已暂停，点「继续」恢复" : "项目已停止，点「继续」恢复";
  }
  if ($("#btnGroupSend")) $("#btnGroupSend").disabled = st !== "running";
  if ($("#projectMeta")) {
    const n = (s.tickets || []).filter((t) => (t.project_id || "proj-default") === p.id).length;
    $("#projectMeta").textContent = `${p.member_count || 0} 个 AI · ${n} 张工单 · 群聊 + 派单摘要`;
  }
  const box = $("#groupMembers");
  if (box) {
    box.innerHTML = (p.members || []).length
      ? p.members.map((id) => {
          const human = isHumanAdapter(id);
          const on = human ? state.groupMentions.includes(id) : (state.groupMentions.includes("all") || state.groupMentions.includes(id));
          return `<div class="member-pill ${on ? "on" : ""}" data-mention="${esc(id)}" title="点一下 @/取消 @">${av(id, adapterLabelSafe(id), "member-avatar")}<span>${esc(adapterLabelSafe(id))}</span></div>`;
        }).join("")
      : '<div class="muted">还没有 AI，点「AI 成员」加入</div>';
    $$("#groupMembers [data-mention]").forEach((b) => {
      b.onclick = () => toggleMention(b.dataset.mention);
    });
  }
  renderSideProjects(list);
}
function renderSideProjects(list) {
  const box = $("#sideProjects");
  if (!box) return;
  box.innerHTML = (list || []).filter((p) => !p.archived).map((p) => `<button class="side-project ${p.id === state.projectId ? "active" : ""}" data-pid="${esc(p.id)}" title="${esc(p.name)}">${esc(p.name)}</button>`).join("");
  box.classList.toggle("hidden", !state.projectsOpen);
  const caret = $("#projCaret");
  if (caret) caret.textContent = state.projectsOpen ? "▾" : "▸";
  $$("#sideProjects [data-pid]").forEach((b) => {
    b.onclick = async () => {
      await switchProject(b.dataset.pid);
      setView("projects");
    };
    b.ondblclick = async (e) => {
      e.stopPropagation();
      await switchProject(b.dataset.pid);
      setView("projects");
      beginRenameProject();
    };
    b.oncontextmenu = (e) => openProjCtxMenu(e, b.dataset.pid);
  });
}

/* ---------- 项目归档 / 右键菜单 CB-UI-PROJ-20261112 ---------- */

function renderArchiveMeta(list) {
  const n = (list || []).filter((p) => p.archived).length;
  const b = $("#archivedCount");
  if (b) {
    b.textContent = String(n);
    b.classList.toggle("hidden", n === 0);
  }
  if (state.view === "archive") renderArchiveView();
}
function renderArchiveView() {
  const box = $("#archiveList");
  if (!box) return;
  const list = projects().filter((p) => p.archived);
  const cnt = $("#archivePaneCount");
  if (cnt) cnt.textContent = list.length + " 个";
  if (!list.length) {
    box.innerHTML = '<div class="muted archive-empty">暂无归档项目</div>';
    return;
  }
  box.innerHTML = '<table class="arch-table"><thead><tr><th>名称</th><th>存储位置</th><th>归档时间</th><th>更新时间</th><th>操作</th></tr></thead><tbody>' +
    list.map((p) => `<tr data-pid="${esc(p.id)}">
      <td><b>${esc(p.name)}</b><small>${esc(p.id)}</small></td>
      <td class="arch-dir">${esc(p.dir || "—")}</td>
      <td>${esc(fmtTime(p.archived_at))}</td>
      <td>${esc(fmtTime(p.updated_at))}</td>
      <td class="archive-actions"><button class="ghost" data-arch-act="open">打开</button><button class="ghost" data-arch-act="rename">重命名</button><button class="primary" data-arch-act="restore">恢复</button></td>
    </tr>`).join("") + "</tbody></table>";
  $$("#archiveList [data-arch-act]").forEach((b) => {
    b.onclick = (e) => {
      e.stopPropagation();
      projCtxAction(b.dataset.archAct, b.closest("tr[data-pid]").dataset.pid);
    };
  });
  $$("#archiveList tr[data-pid]").forEach((tr) => {
    tr.oncontextmenu = (e) => openProjCtxMenu(e, tr.dataset.pid);
  });
}
function projCtxProject(pid) {
  return projects().find((p) => p.id === pid) || null;
}
function hideProjCtxMenu() {
  const m = $("#projCtxMenu");
  if (m) m.classList.add("hidden");
}
function openProjCtxMenu(e, pid) {
  const p = projCtxProject(pid);
  const m = $("#projCtxMenu");
  if (!p || !m) return;
  e.preventDefault();
  e.stopPropagation();
  const third = p.archived
    ? '<button data-pact="restore">恢复项目</button>'
    : '<button data-pact="archive">归档项目</button>';
  m.innerHTML = '<button data-pact="open">打开项目</button><button data-pact="rename">重命名</button>' + third;
  m.style.left = Math.max(4, Math.min(e.clientX, window.innerWidth - 150)) + "px";
  m.style.top = Math.max(4, Math.min(e.clientY, window.innerHeight - 110)) + "px";
  m.classList.remove("hidden");
  $$("#projCtxMenu [data-pact]").forEach((b) => {
    b.onclick = (ev) => {
      ev.stopPropagation();
      hideProjCtxMenu();
      projCtxAction(b.dataset.pact, pid);
    };
  });
}
async function projCtxAction(act, pid) {
  if (act === "open") {
    await switchProject(pid);
    setView("projects");
    return;
  }
  if (act === "rename") { openProjRename(pid); return; }
  if (act === "archive") {
    if (!confirm("归档后从项目列表移出、群聊停止派单（状态置为暂停），数据不删除；可在『归档项目』里恢复。")) return;
    await setProjectArchived(pid, true);
    return;
  }
  if (act === "restore") await setProjectArchived(pid, false);
}
async function setProjectArchived(pid, archived) {
  try {
    const r = await api(`/api/projects/${encodeURIComponent(pid)}/archive`, {
      method: "POST", body: JSON.stringify({ archived }),
    });
    toast(archived ? "已归档" : "已恢复");
    await loadState(true);
    renderArchiveMeta(projects());
    return r;
  } catch (e) {
    toast((archived ? "归档" : "恢复") + "失败：" + e.message);
    return null;
  }
}
function openProjRename(pid) {
  const p = projCtxProject(pid);
  if (!p) return;
  state.projRenamePid = pid;
  const inp = $("#projRenameInput");
  inp.value = p.name;
  $("#projRenameError").classList.add("hidden");
  $("#projRenameModal").classList.remove("hidden");
  inp.focus();
  inp.select();
}
function closeProjRename() {
  state.projRenamePid = null;
  const m = $("#projRenameModal");
  if (m) m.classList.add("hidden");
}
async function submitProjRename() {
  const pid = state.projRenamePid;
  if (!pid) return;
  const name = String($("#projRenameInput").value || "").trim();
  const err = $("#projRenameError");
  if (!name || name.length > 80) {
    err.textContent = !name ? "项目名不能为空" : "项目名最多 80 字";
    err.classList.remove("hidden");
    return;
  }
  try {
    await api(`/api/projects/${encodeURIComponent(pid)}/rename`, {
      method: "POST", body: JSON.stringify({ name }),
    });
    closeProjRename();
    toast("已重命名");
    await loadState(true);
    renderArchiveMeta(projects());
  } catch (e) {
    err.textContent = "重命名失败：" + e.message;
    err.classList.remove("hidden");
  }
}
function mentionCandidates(q) {
  const p = currentProject();
  if (!p) return [];
  const query = (q || "").toLowerCase();
  const pool = [{ id: "all", label: "全部", sub: "@所有 AI 成员", all: true }]
    .concat((p.members || []).filter((id) => !isHumanAdapter(id))
      .map((id) => ({ id, label: adapterLabelSafe(id), sub: "AI 成员" })))
    .concat((p.members || []).filter((id) => isHumanAdapter(id))
      .map((id) => ({ id, label: adapterLabelSafe(id), sub: "拍板 / 人工", boss: true })));
  if (!query) return pool;
  return pool.filter((x) => x.id.toLowerCase().includes(query) || x.label.toLowerCase().includes(query));
}
function hideMentionPop() {
  state._mention = { open: false, items: [], act: 0, from: -1, to: -1 };
  const pop = $("#mentionPop");
  if (pop) { pop.classList.add("hidden"); pop.innerHTML = ""; }
}
function renderMentionPop() {
  const pop = $("#mentionPop");
  if (!pop) return;
  const m = state._mention;
  if (!m || !m.open || !m.items.length) { hideMentionPop(); return; }
  pop.innerHTML = m.items.map((x, i) =>
    `<div class="mention-item ${i === m.act ? "act" : ""} ${x.boss ? "boss" : ""}" data-i="${i}"><b>@${esc(x.label)}</b><span class="sub">${esc(x.sub)}</span></div>`
  ).join("");
  pop.classList.remove("hidden");
  $$("#mentionPop .mention-item").forEach((el) => {
    el.onmousedown = (ev) => { ev.preventDefault(); pickMention(Number(el.dataset.i)); };
    el.onmouseenter = () => {
      state._mention.act = Number(el.dataset.i);
      $$("#mentionPop .mention-item").forEach((n, i) => n.classList.toggle("act", i === state._mention.act));
    };
  });
}
function updateMentionPop() {
  const input = $("#groupText");
  if (!input || input.disabled) { hideMentionPop(); return; }
  const pos = input.selectionStart == null ? input.value.length : input.selectionStart;
  const before = input.value.slice(0, pos);
  const hit = before.match(/(^|\s)@([^\s@]*)$/);
  if (!hit) { hideMentionPop(); return; }
  const items = mentionCandidates(hit[2]);
  if (!items.length) { hideMentionPop(); return; }
  state._mention = { open: true, items, act: 0, from: pos - hit[2].length - 1, to: pos };
  renderMentionPop();
}
function pickMention(i) {
  const m = state._mention;
  const input = $("#groupText");
  if (!m || !m.open || !input || !m.items[i]) { hideMentionPop(); return; }
  const x = m.items[i];
  const v = input.value;
  input.value = v.slice(0, m.from) + "@" + x.label + " " + v.slice(m.to);
  const caret = m.from + x.label.length + 2;
  input.focus();
  try { input.setSelectionRange(caret, caret); } catch (_) {}
  if (x.all) state.groupMentions = ["all"];
  else if (isHumanAdapter(x.id) && state.groupMentions.includes("all")) {
    if (!state.groupMentions.includes(x.id)) state.groupMentions = ["all", x.id];
  } else {
    const cur = state.groupMentions.filter((k) => k !== "all");
    if (!cur.includes(x.id)) cur.push(x.id);
    state.groupMentions = cur.length ? cur : ["all"];
  }
  hideMentionPop();
  renderProjectControls(state.appState || {});
}
function toggleMention(id) {
  if (id === "all") {
    state.groupMentions = ["all"];
    renderProjectControls(state.appState || {});
    return;
  }
  const human = isHumanAdapter(id);
  const hadAll = state.groupMentions.includes("all");
  if (human && hadAll) {
    state.groupMentions = state.groupMentions.includes(id) ? ["all"] : ["all", id];
  } else {
    let cur = state.groupMentions.filter((x) => x !== "all");
    if (cur.includes(id)) cur = cur.filter((x) => x !== id);
    else cur.push(id);
    state.groupMentions = cur.length ? cur : ["all"];
  }
  renderProjectControls(state.appState || {});
}
function renderGroupMessages(messages, opts = {}) {
  const box = $("#groupFeed");
  if (!box) return;
  if (!messages || !messages.length) {
    box.innerHTML = '<div class="muted">暂无消息。输入 @ 选择成员，发第一条，等派单摘要落进来。</div>';
    return;
  }
  box.innerHTML = messages
    .map((m) => {
      if (m.role === "alert") {
        const tid = parseAlertTicketId(m.content);
        const card = tid ? `<div class="signoff-card-slot" data-card-ticket="${esc(tid)}"><div class="signoff-card empty">点击查阅详情</div></div>` : "";
        return `<div class="galert">⚠ ${esc(m.content || "")}<button class="go-signoff" data-signoff-ticket="${esc(tid)}">去签收 →</button></div>${card}`;
      }
      if (m.type === "system") {
        return `<div class="gsys ${m.kind === "dispatch_fail" ? "fail" : ""}" data-ticket="${esc(m.ticket_id || "")}" data-sys="${esc((m.kind || "") + "::" + (m.ticket_id || ""))}">
          <span>${esc(m.text)} · ${esc(m.ticket_id || "")} · ${esc(m.status || "")}</span>
          <time>${fmtTime(m.at)}</time>
        </div>`;
      }
      const isUser = m.role === "user";
      const mentions = isUser ? (m.mentions || []).map((x) => (x === "all" ? "@全部" : "@" + adapterLabelSafe(x))).join(" ") : "";
      const userMeta = isUser ? ("我" + (mentions ? " → " + mentions : "")) : "";
      return `<div class="gmsg ${isUser ? "user" : ""}" data-mid="${m.id}">
        <div class="gavatar">${av(isUser ? "" : (m.adapter || ""), isUser ? "我" : "AI")}</div>
        <div class="gbody">
          <div class="gmeta">${isUser ? esc(userMeta) : esc(adapterLabelSafe(m.adapter || "AI"))} · ${fmtTime(m.at || m.created_at)}</div>
          <div class="gbubble">${esc(m.content || "")}${inlineAttachments(parseMsgAttachments(m))}</div>
        </div>
      </div>`;
    })
    .join("");
  $$("#groupFeed [data-ticket]").forEach((b) => {
    if (!b.dataset.ticket) return;
    b.onclick = () => {
      setView("projects");
      openTicket(b.dataset.ticket);
    };
  });
  $$("#groupFeed [data-signoff-ticket]").forEach((b) => {
    b.onclick = (e) => {
      e.stopPropagation();
      goSignoff(b.dataset.signoffTicket);
    };
  });
  renderSignoffCards();
  // 只有本来就在底部、或用户主动发消息时才滚到底；拉到顶部看历史时不要弹回去
  if (opts.forceBottom || opts.atBottom) box.scrollTop = box.scrollHeight;
  else box.scrollTop = opts.prevTop || 0;
}
async function loadProjectFeed(forceBottom = false) {
  const p = currentProject();
  if (!p) return;
  try {
    const box = $("#groupFeed");
    const nearBottom = box ? (box.scrollHeight - box.scrollTop - box.clientHeight < 48) : true;
    const prevTop = box ? box.scrollTop : 0;
    const projectChanged = state.groupScrollProject !== p.id;
    const r = await api(`/api/projects/${encodeURIComponent(p.id)}/messages?limit=120`);
    renderGroupMessages(r.messages || [], {
      atBottom: forceBottom || projectChanged || nearBottom,
      prevTop,
    });
    state.groupScrollProject = p.id;
  } catch (e) {
    renderGroupMessages([]);
    toast("读取项目群失败：" + e.message);
  }
}
function startGroupFastPoll() {
  clearInterval(state.groupFastPoll);
  state.groupFastPoll = setInterval(() => {
    if (state.view === "projects") loadProjectFeed();
  }, 2000);
  setTimeout(() => {
    clearInterval(state.groupFastPoll);
    state.groupFastPoll = null;
  }, 45000);
}
const SEND_CONFIRM_KEY = "deck_send_confirm";
function sendGroupMessage() {
  const p = currentProject();
  const text = $("#groupText").value.trim();
  if (!p) return;
  if ((p.status || "running") !== "running") return toast("项目已暂停/停止，先点「继续」");
  if (!text) return toast("先写消息");
  if (!(p.members || []).length) return toast("项目群里还没有 AI，先加成员");
  const mentions = state.groupMentions.length ? state.groupMentions : ["all"];
  const attachments = state.groupAttachments.slice();
  const pref = localStorage.getItem(SEND_CONFIRM_KEY);
  if (pref === "skip") { doSendGroup(p, text, mentions, attachments); return; }
  showSendConfirm(p, text, mentions, attachments);
}
function showSendConfirm(p, text, mentions, attachments) {
  const ads = adapters();
  const isAll = (mentions[0] || "").toLowerCase() === "all";
  const targets = isAll
    ? (p.members || []).filter((id) => (ads[id]?.type || "").toLowerCase() !== "human")
    : mentions.filter((id) => (ads[id]?.type || "").toLowerCase() !== "human");
  $("#sendConfirmMsg").textContent = text;
  $("#sendConfirmTargets").innerHTML = targets.length
    ? targets.map((id) => {
        const e = adapterEntry(id);
        const m = e?.ad?.default_model ? `（默认模型：${e.ad.default_model}）` : "";
        return `<div class="confirm-target"><b>${esc(e?.ad?.label || id)}</b> ${esc(m)}</div>`;
      }).join("")
    : '<div class="muted">将发给人工席位（不自动回复）</div>';
  $("#sendConfirmExpect").value = "";
  $("#sendConfirmRemember").checked = false;
  state._pendingSend = { p, text, mentions, attachments };
  $("#sendConfirmModal").classList.remove("hidden");
}
async function doSendGroup(p, text, mentions, attachments) {
  try {
    $("#groupStatus").textContent = "发送中…";
    const r = await api(`/api/projects/${encodeURIComponent(p.id)}/messages`, {
      method: "POST",
      body: JSON.stringify({ content: text, mentions, attachment_ids: attachments.map((a) => a.id) }),
    });
    $("#groupText").value = "";
    hideMentionPop();
    state.groupAttachments = [];
    renderGroupAttachments();
    state.groupMentions = ["all"];
    renderProjectControls(state.appState || {});
    $("#groupStatus").textContent = r.targets.length
      ? `已发送，等待 ${r.targets.length} 个 AI 回复…`
      : "已发送给人工席位，等待人工处理";
    await loadProjectFeed(true);
    if (r.targets.length) startGroupFastPoll();
    setTimeout(() => { if ($("#groupStatus")) $("#groupStatus").textContent = ""; }, 8000);
  } catch (e) {
    $("#groupStatus").textContent = "";
    toast("群消息发送失败：" + e.message);
  }
}
function openProjectModal() {
  const p = currentProject();
  if (!p) return;
  $("#pDir").value = p.dir || "";
  $("#pInjectProfile").checked = !!(p.inject_profile);
  const a = adapters();
  const members = new Set(p.members || []);
  $("#pMemberChecks").innerHTML = Object.entries(a).length
    ? Object.entries(a).map(([id, it]) => `<label class="member-check"><input type="checkbox" data-aid="${esc(id)}" ${members.has(id) ? "checked" : ""}/> ${esc(it.label || id)} <small>${it.enabled !== false ? (it.ready ? "可用" : "未就绪") : "未启用"}</small></label>`).join("")
    : '<div class="muted">没有 AI 岗位</div>';
  showMemberStatus("", false);
  $("#projectModal").classList.remove("hidden");
}

// ---------------- 岗位编制 ----------------
function roleFlat() {
  const out = [];
  for (const layer of state.roleCatalog?.layers || []) {
    for (const role of layer.roles || []) out.push({ ...role, layer: layer.name });
  }
  return out;
}
function roleAssigned(rid) {
  return state.roleDraft[rid] || [];
}
function memberLabel(aid) {
  const e = adapterEntry(aid);
  return e?.ad?.label || aid;
}
function memberPost(aid) {
  const e = adapterEntry(aid);
  return e?.ad?.post || e?.ad?.label || aid;
}
function roleDisplayName(role) {
  if (role.id === "boss") return "主理人/大BOSS（你）";
  if (role.id === "lead") return "调度协调岗（DSH AI）";
  return role.name;
}
function roleAttrText(role) {
  const a = [];
  if (role.can_plan) a.push("规划");
  if (role.can_coordinate) a.push("协调");
  if (role.can_write) a.push("写码");
  if (role.can_build) a.push("构建");
  if (role.can_audit) a.push("审核");
  if (role.can_accept) a.push("验收");
  if (role.can_release) a.push("发布");
  if (role.can_doc) a.push("文档");
  if (role.human_only) a.push("必须人工");
  return a.join(" · ") || "执行";
}
function roleState(role) {
  const picked = roleAssigned(role.id);
  if (picked.length) return { cls: "ok", text: picked.map(memberLabel).join(" / ") };
  if (role.human_only) return { cls: "req", text: "必须指定人工" };
  if (role.allow_acting === false) return { cls: "req", text: "必须指定" };
  return { cls: "empty", text: "未配置" };
}
const CORE_ROLE_IDS = new Set(["boss", "lead", "dev_general", "build_devops", "audit", "independent_acceptor", "qa", "docs"]);
function visibleRoles(roles) {
  if (state.roleShowAll) return roles;
  return roles.filter((r) => CORE_ROLE_IDS.has(r.id) || roleAssigned(r.id).length);
}
function renderRoleTree() {
  const layers = state.roleCatalog?.layers || [];
  const all = state.roleAll.length ? state.roleAll : state.roleMembers;
  const members = new Set(state.roleMembers || []);
  const shownRoles = layers.flatMap((layer) => visibleRoles(layer.roles || []));
  const total = shownRoles.filter((r) => !r.human_only).length;
  const filled = shownRoles.filter((r) => !r.human_only && roleAssigned(r.id).length).length;
  $("#roleSummary").textContent = state.roleShowAll
    ? `全部岗位：已填 ${filled}/${total}；空岗不自动兼`
    : `核心岗位：已填 ${filled}/${total}；项目变大再展开全部`;
  const showBtn = $("#btnRoleShowAll");
  if (showBtn) showBtn.textContent = state.roleShowAll ? "只看核心岗位" : "显示全部岗位";
  $("#roleStatus").textContent = "";
  $("#roleLayers").innerHTML = layers.map((layer) => {
    const shown = visibleRoles(layer.roles || []);
    if (!shown.length) return "";
    const rows = shown.map((role) => {
      const picked = roleAssigned(role.id);
      const st = roleState(role);
      const eligible = all.filter((aid) => {
        const human = (adapters()[aid]?.type || "").toLowerCase() === "human";
        return role.human_only ? human : !human;
      });
      const chips = eligible.length
        ? eligible.map((aid) => {
            const a = adapters()[aid] || {};
            const human = (a.type || "").toLowerCase() === "human";
            return `<button type="button" class="role-pick ${picked.includes(aid) ? "on" : ""} ${human ? "human" : ""} ${members.has(aid) ? "" : "not-member"}" data-role="${esc(role.id)}" data-aid="${esc(aid)}" title="${esc(memberPost(aid))}${members.has(aid) ? "" : " · 保存后自动加入项目群"}">${esc(a.label || aid)}</button>`;
          }).join("")
        : `<span class="hint">${role.human_only ? "没有可填的人工席位" : "项目里没有可填的 AI"}</span>`;
      return `<div class="role-row">
        <div class="role-name"><b>${esc(roleDisplayName(role))}</b><small>${esc(roleAttrText(role))}</small></div>
        <div class="role-picks">${chips}</div>
        <div class="role-state ${st.cls}">${esc(st.text)}</div>
      </div>`;
    }).join("");
    const layerFilled = shown.filter((r) => !r.human_only && roleAssigned(r.id).length).length;
    const layerTotal = shown.filter((r) => !r.human_only).length;
    return `<details class="role-layer" open><summary>${esc(layer.name)} <span>${layerFilled}/${layerTotal}</span></summary>${rows}</details>`;
  }).join("");
  $$("#roleLayers .role-pick").forEach((btn) => {
    btn.onclick = () => toggleRolePick(btn.dataset.role, btn.dataset.aid);
  });
}
function toggleRolePick(rid, aid) {
  const role = roleFlat().find((r) => r.id === rid);
  if (!role) return;
  const cur = roleAssigned(rid);
  if (cur.includes(aid)) {
    state.roleDraft[rid] = cur.filter((x) => x !== aid);
  } else {
    state.roleDraft[rid] = role.unique ? [aid] : [...cur, aid];
  }
  renderRoleTree();
}
function recommendRoles() {
  const members = state.roleAll.length ? state.roleAll : state.roleMembers;
  const ads = adapters();
  state.roleDraft = {};
  const used = new Set();
  const human =
    members.find((id) => (ads[id]?.role_id || "") === "boss") ||
    members.find((id) => (ads[id]?.type || "").toLowerCase() === "human");
  const lead = members.find((id) => (ads[id]?.role_id || "") === "lead") || members.find((id) => id === "dsh");
  if (human) state.roleDraft.boss = [human];
  if (lead) state.roleDraft.lead = [lead];
  if (lead) used.add(lead);
  if (human) used.add(human);
  for (const role of roleFlat()) {
    if (["boss", "lead", "independent_acceptor"].includes(role.id)) continue;
    if (state.roleDraft[role.id]) continue;
    const byRole = members.filter((id) => !used.has(id) && (ads[id]?.role_id || "") === role.id);
    const byDefault = members.filter((id) => !used.has(id) && (role.adapter_defaults || []).includes(id));
    const pick = (byRole.length ? byRole : byDefault).filter(Boolean);
    if (pick.length) {
      state.roleDraft[role.id] = role.unique ? [pick[0]] : pick;
      pick.forEach((id) => used.add(id));
    }
  }
  // 只按 AI 自身岗位匹配预填；不把空岗硬塞给主理人
  if (!state.roleDraft.docs) {
    const doc = members.find((id) => !used.has(id) && (ads[id]?.role_id || "") === "docs");
    if (doc) state.roleDraft.docs = [doc];
  }
  // 测试/文档可由现有 AI 兼任（不违反写审分离）；独立验收优先由非写码/构建的真人担任
  const fallback = {
    qa: ["workbuddy", "codebuddy-cn", "codex"],
    docs: ["coze", "codebuddy-cn", "dsh"],
  };
  for (const [rid, prefs] of Object.entries(fallback)) {
    if (state.roleDraft[rid]?.length) continue;
    const aid = prefs.find((x) => members.includes(x));
    if (aid) state.roleDraft[rid] = [aid];
  }
  // 单人/小团队：大BOSS 本人不是写码/构建者，可兼独立验收（门④、门⑤分两次签）
  if (!state.roleDraft.independent_acceptor && human) {
    state.roleDraft.independent_acceptor = [human];
  }
  renderRoleTree();
}
async function openRoleModal() {
  const p = currentProject();
  if (!p) return toast("先选项目");
  try {
    const r = await api(`/api/projects/${encodeURIComponent(p.id)}/roles`);
    state.roleCatalog = r.catalog || { layers: [] };
    state.roleMembers = r.members || [];
    state.roleAll = Object.keys(adapters());
    state.roleShowAll = false;
    state.roleDraft = {};
    for (const [rid, aids] of Object.entries(r.assignments || {})) state.roleDraft[rid] = aids.slice();
    renderRoleTree();
    $("#roleModal").classList.remove("hidden");
  } catch (e) {
    toast("读取岗位失败：" + e.message);
  }
}
async function saveRoles() {
  const p = currentProject();
  if (!p) return;
  const assignments = {};
  for (const [rid, aids] of Object.entries(state.roleDraft)) {
    if (aids && aids.length) assignments[rid] = aids;
  }
  try {
    const r = await api(`/api/projects/${encodeURIComponent(p.id)}/roles`, { method: "POST", body: JSON.stringify({ assignments }) });
    toast(r.joined?.length ? `岗位已保存，${r.joined.length} 个 AI 已加入项目群` : "岗位编制已保存");
    $("#roleModal").classList.add("hidden");
    await loadState(true);
    await loadRoleCoverage();
  } catch (e) {
    $("#roleStatus").textContent = e.message || "保存失败";
    toast("岗位有冲突，按红字改");
  }
}

async function loadRoleCoverage() {
  const p = currentProject();
  if (!p) return;
  try {
    const r = await api(`/api/projects/${encodeURIComponent(p.id)}/roles`);
    renderRoleCoverage(r.catalog || { layers: [] }, r.assignments || {});
  } catch (e) {
    if ($("#roleCoverage")) $("#roleCoverage").innerHTML = `<span class="hint">岗位读取失败：${esc(e.message)}</span>`;
  }
}
function renderRoleCoverage(catalog, assignments) {
  const layers = catalog.layers || [];
  if (!layers.length) {
    $("#roleCoverage").innerHTML = '<span class="hint">岗位目录为空，检查 岗位目录.json</span>';
    return;
  }
  const allLayers = layers.map((layer) => {
    const roles = layer.roles || [];
    const filled = roles.filter((role) => (assignments[role.id] || []).length).length;
    return { layer, roles, filled };
  });
  const shownLayers = state.coverageShowEmpty ? allLayers : allLayers.filter((x) => x.filled > 0);
  const emptyButton = $("#btnCoverageEmpty");
  if (emptyButton) emptyButton.textContent = state.coverageShowEmpty ? "隐藏空层" : "显示空层";
  if (!shownLayers.length) {
    $("#roleCoverage").innerHTML = '<span class="hint">当前没有已配置岗位，点「显示空层」查看全部，或去「岗位编制」分配 AI。</span>';
    return;
  }
  $("#roleCoverage").innerHTML = shownLayers.map(({ layer, roles, filled }) => {
    const rows = roles.map((role) => {
      const aids = assignments[role.id] || [];
      let cls = "empty", text = "未配置";
      if (aids.length) {
        cls = "ok";
        text = aids.map(memberLabel).join(" / ");
      } else if (role.human_only) {
        cls = "req"; text = "必须指定人工";
      } else if (role.allow_acting === false) {
        cls = "req"; text = "必须指定";
      }
      return `<div class="coverage-role"><span class="nm">${esc(roleDisplayName(role))}</span><span class="st ${cls}">${esc(text)}</span></div>`;
    }).join("");
    return `<details class="coverage-layer" data-layer="${esc(layer.id)}" ${state.coverageOpen[layer.id] ? "open" : ""}><summary><b>${esc(layer.name)}</b><span>${filled}/${roles.length}</span></summary><div class="coverage-body">${rows}</div></details>`;
  }).join("");
  $$("#roleCoverage details.coverage-layer").forEach((el) => {
    el.ontoggle = () => { state.coverageOpen[el.dataset.layer] = el.open; localStorage.setItem("deck_coverage_open", JSON.stringify(state.coverageOpen)); };
  });
}
async function saveProjectDir() {
  const p = currentProject();
  if (!p) return;
  const dir = $("#pDir").value.trim();
  if (!dir) return toast("先填项目存储位置");
  try {
    await api(`/api/projects/${encodeURIComponent(p.id)}/dir`, { method: "POST", body: JSON.stringify({ dir }) });
    const inj = $("#pInjectProfile").checked ? 1 : 0;
    await api(`/api/projects/${encodeURIComponent(p.id)}/settings`, { method: "POST", body: JSON.stringify({ inject_profile: inj }) });
    toast("项目设置已保存");
    await loadState(true);
    $("#pDir").value = currentProject()?.dir || dir;
  } catch (e) {
    toast("保存失败：" + e.message);
  }
}
async function switchProject(pid) {
  state.projectId = pid;
  state.groupMentions = ["all"];
  state.groupScrollProject = null;
  localStorage.setItem("deck_project", pid);
  renderProjectControls(state.appState || {});
  await loadState(true);
  if (state.view === "projects") await loadProjectFeed();
  if (state.view === "adapters") loadRoleCoverage();
}
async function setProjectState(action) {
  const p = currentProject();
  if (!p) return;
  try {
    await api(`/api/projects/${encodeURIComponent(p.id)}/state`, { method: "POST", body: JSON.stringify({ action }) });
    toast(action === "pause" ? "项目已暂停" : action === "stop" ? "项目已停止" : "项目已继续");
    await loadState(true);
    await loadProjectFeed();
  } catch (e) {
    toast("操作失败：" + e.message);
  }
}
async function emergencyStop() {
  const p = currentProject();
  if (!p) return;
  if (!confirm("确认急停？会终止派发器进程，并把本项目「派发中」的单转失败。")) return;
  try {
    const r = await api("/api/emergency-stop", { method: "POST", body: JSON.stringify({ project_id: p.id }) });
    toast(`已急停：派发器${r.killed ? "已终止" : "未在运行"}，转失败 ${r.marked_failed} 张`);
    await loadState(true);
    await loadProjectFeed();
  } catch (e) {
    toast("急停失败：" + e.message);
  }
}
async function createProject() {
  try {
    const r = await api("/api/projects", { method: "POST", body: JSON.stringify({ name: "新项目" }) });
    state.projectId = r.project.id;
    localStorage.setItem("deck_project", state.projectId);
    toast("项目已创建，填存储位置、加主理人");
    await loadState(true);
    openProjectModal();
    await loadProjectFeed();
  } catch (e) {
    toast("新建项目失败：" + e.message);
  }
}
async function renameProject(name) {
  const p = currentProject();
  if (!p) return;
  name = String(name || "").trim();
  if (!name) return;
  try {
    await api(`/api/projects/${encodeURIComponent(p.id)}/rename`, { method: "POST", body: JSON.stringify({ name }) });
    toast("已重命名");
    await loadState(true);
  } catch (e) {
    toast("重命名失败：" + e.message);
  }
}
function beginRenameProject() {
  const p = currentProject();
  if (!p) return;
  const el = $("#projectTitle");
  if (!el) return;
  state.renaming = true;
  const old = p.name;
  el.innerHTML = `<input id="inlineProjectName" class="inline-project-name" maxlength="80" value="${esc(old)}" />`;
  const inp = $("#inlineProjectName");
  inp.focus();
  inp.select();
  let done = false;
  const finish = async (save) => {
    if (done) return;
    done = true;
    state.renaming = false;
    const val = inp.value.trim();
    if (save && val && val !== old) await renameProject(val);
    else renderProjectControls(state.appState || {});
  };
  inp.onkeydown = (e) => {
    if (e.key === "Enter") { e.preventDefault(); finish(true); }
    if (e.key === "Escape") { e.preventDefault(); finish(false); }
  };
  inp.onblur = () => finish(true);
}
function showMemberStatus(msg, bad) {
  const el = $("#memberStatus");
  if (!el) return;
  el.textContent = msg || "";
  el.classList.toggle("hidden", !msg);
  el.classList.toggle("bad", !!bad);
}
async function saveProjectMembers() {
  const p = currentProject();
  if (!p) return;
  // R3：整单原子提交，任一冲突 → 后端 400，不写一半
  const member_ids = $$("#pMemberChecks input[data-aid]")
    .filter((x) => x.checked).map((x) => x.dataset.aid);
  showMemberStatus("", false);
  try {
    const r = await api(`/api/projects/${encodeURIComponent(p.id)}/members/batch`, {
      method: "POST", body: JSON.stringify({ member_ids }),
    });
    const notes = [];
    for (const [aid, info] of Object.entries(r.inherited || {})) {
      notes.push(`${info.label || aid} → 继承默认岗位：${info.post}`);
    }
    for (const [aid, info] of Object.entries(r.skipped || {})) {
      notes.push(`${aid}：${info.reason}（${info.post}）`);
    }
    showMemberStatus(notes.length ? notes.join("；") : "群成员已保存（无默认岗位变更）", false);
    toast("群成员已保存");
    await loadState(true);
    await loadProjectFeed();
  } catch (e) {
    showMemberStatus("保存失败（整单未写入）：" + e.message, true);
  }
}

/* ---------- new ticket ---------- */

function openModal() {
  $("#modal").classList.remove("hidden");
  syncFormModelVisibility();
  $("#fTitle").focus();
}
function closeModal() {
  $("#modal").classList.add("hidden");
}
async function createTicket() {
  const title = $("#fTitle").value.trim();
  if (!title) return toast("先写个标题");
  try {
    const body = {
      title,
      recipient: $("#fRecipient").value,
      executor: $("#fExecutor").value,
      gate: $("#fGate").value,
      priority: $("#fPriority").value,
      note: $("#fNote").value.trim(),
      project_id: state.projectId,
      model: modelsFor($("#fExecutor").value).length ? $("#fModel").value : null,
    };
    const r = await api("/api/tickets", { method: "POST", body: JSON.stringify(body) });
    closeModal();
    toast("已创建 " + r.id);
    $("#fTitle").value = "";
    $("#fNote").value = "";
    await loadState(true);
    setView("projects");
    await openTicket(r.id);
  } catch (e) {
    toast("创建失败：" + e.message);
  }
}

/* ---------- 历史消息搜索 / 时间轴（v1） ---------- */

// 注意：下面只新增 state.search 字段，不改 renderGroupMessages 的入参形状与现有渲染路径，
// 仅在渲染结果 div 上追加 data-mid / data-sys 锚点用于点击定位。
state.search = { scope: "chat", mode: "search", q: "", role: "", since: "", until: "", limit: 50, offset: 0, total: 0 };

function todayStr() {
  const d = new Date();
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}
function addDays(iso, n) {
  const [y, m, d] = iso.split("-").map(Number);
  const dt = new Date(y, m - 1, d + n);
  const p = (x) => String(x).padStart(2, "0");
  return `${dt.getFullYear()}-${p(dt.getMonth() + 1)}-${p(dt.getDate())}`;
}
function searchRangeToBounds() {
  const v = $("#searchRange").value;
  const t = todayStr();
  if (v === "today") return { since: t, until: t };
  if (v === "7") return { since: addDays(t, -6), until: t };
  if (v === "30") return { since: addDays(t, -29), until: t };
  if (v === "custom") return { since: $("#searchSince").value || "", until: $("#searchUntil").value || "" };
  return {};
}
function highlight(text, kw) {
  const s = String(text ?? "");
  if (!kw) return esc(s);
  const low = s.toLowerCase(), k = kw.toLowerCase();
  let out = "", i = 0;
  while (true) {
    const idx = low.indexOf(k, i);
    if (idx < 0) { out += esc(s.slice(i)); break; }
    out += esc(s.slice(i, idx)) + "<mark>" + esc(s.slice(idx, idx + k.length)) + "</mark>";
    i = idx + k.length;
  }
  return out;
}
function roleLabelFor(it) {
  if (it.type === "system") return "系统";
  if (it.role === "user") return "我";
  return esc(adapterLabel(it.adapter || "AI") || "AI");
}
function readSearchControls() {
  state.search.q = $("#searchKeyword").value.trim();
  state.search.role = $("#searchRole").value;
}
function openSearch(scope, mode) {
  state.search.scope = scope;
  $("#searchTitle").textContent = (scope === "chat" ? "沟通" : "项目群") + " · 历史消息";
  $("#searchKeyword").value = "";
  $("#searchRange").value = "all";
  $("#searchSince").value = ""; $("#searchUntil").value = "";
  $("#searchRole").value = "";
  $("#searchCustom").classList.add("hidden");
  state.search.q = ""; state.search.role = ""; state.search.offset = 0;
  $("#searchModal").classList.remove("hidden");
  toggleSearchMode(mode || "search");
  if ((mode || "search") !== "timeline") setTimeout(() => $("#searchKeyword").focus(), 50);
}
function closeSearch() {
  $("#searchModal").classList.add("hidden");
}
function toggleSearchMode(mode) {
  state.search.mode = mode;
  $$(".search-tab").forEach((b) => b.classList.toggle("active", b.dataset.mode === mode));
  if (mode === "timeline") runTimeline();
  else runSearch(true);
}
async function runSearch(reset) {
  readSearchControls();
  if (reset) state.search.offset = 0;
  const s = state.search;
  const p = new URLSearchParams();
  p.set("scope", s.scope);
  if (s.scope === "chat") p.set("target", state.target);
  else p.set("project_id", state.projectId);
  if (s.q) p.set("q", s.q);
  if (s.role) p.set("role", s.role);
  const b = searchRangeToBounds();
  if (b.since) p.set("since", b.since);
  if (b.until) p.set("until", b.until);
  p.set("limit", String(s.limit));
  p.set("offset", String(s.offset));
  $("#searchList").innerHTML = '<div class="muted search-empty">搜索中…</div>';
  try {
    const r = await api("/api/search/messages?" + p.toString());
    state.search.total = r.total;
    renderSearchItems(r.items || [], r.total, r.limit, r.offset);
  } catch (e) {
    $("#searchList").innerHTML = `<div class="muted search-empty">搜索失败：${esc(e.message)}</div>`;
  }
}
function renderSearchItems(items, total, limit, offset) {
  const box = $("#searchList");
  $("#searchCount").textContent = `共 ${total} 条` + (total ? `（第 ${offset + 1}–${offset + items.length} 条）` : "");
  $("#btnSearchPrev").disabled = offset <= 0;
  $("#btnSearchNext").disabled = offset + items.length >= total;
  if (!total) {
    box.innerHTML = '<div class="muted search-empty">没有匹配的消息</div>';
    return;
  }
  box.innerHTML = items.map((it) => {
    const label = roleLabelFor(it);
    const snippet = it.type === "system" ? (it.text || "") : (it.content || "");
    const ds = it.type === "system"
      ? `data-stype="system" data-sys="${esc((it.kind || "") + "::" + (it.ticket_id || ""))}"`
      : `data-stype="chat" data-mid="${esc(it.id)}"`;
    return `<div class="sr-item" ${ds}>
      <div class="sr-head"><span class="sr-role">${label}</span><time>${fmtTime(it.at)}</time></div>
      <div class="sr-snippet">${highlight(snippet, state.search.q)}</div>
    </div>`;
  }).join("");
  $$("#searchList .sr-item").forEach((el) => { el.onclick = () => locateMessage(el); });
}
async function runTimeline() {
  readSearchControls();
  const s = state.search;
  const p = new URLSearchParams();
  p.set("scope", s.scope);
  if (s.scope === "chat") p.set("target", state.target);
  else p.set("project_id", state.projectId);
  if (s.q) p.set("q", s.q);
  if (s.role) p.set("role", s.role);
  const b = searchRangeToBounds();
  if (b.since) p.set("since", b.since);
  if (b.until) p.set("until", b.until);
  $("#searchList").innerHTML = '<div class="muted search-empty">加载时间轴…</div>';
  try {
    const r = await api("/api/timeline?" + p.toString());
    renderTimeline(r.days || []);
  } catch (e) {
    $("#searchList").innerHTML = `<div class="muted search-empty">时间轴加载失败：${esc(e.message)}</div>`;
  }
}
function renderTimeline(days) {
  const box = $("#searchList");
  const sumc = days.reduce((a, d) => a + d.count, 0);
  $("#searchCount").textContent = `共 ${days.length} 天 · ${sumc} 条消息`;
  $("#btnSearchPrev").disabled = true;
  $("#btnSearchNext").disabled = true;
  if (!days.length) { box.innerHTML = '<div class="muted search-empty">这段时间没有消息。</div>'; return; }
  box.innerHTML = `<div class="tl-axis">` + days.map((d) => `
    <div class="tl-day" data-day="${d.date}">
      <div class="tl-dot"></div>
      <div class="tl-main">
        <div class="tl-date">${d.date} <span class="tl-count">${d.count} 条</span></div>
        <div class="tl-break"><span class="b-user">我 ${d.user}</span><span class="b-ai">AI ${d.assistant}</span><span class="b-sys">系统 ${d.system}</span></div>
      </div>
    </div>`).join("") + `</div>`;
  $$("#searchList .tl-day").forEach((el) => {
    el.onclick = () => {
      const day = el.dataset.day;
      $("#searchRange").value = "custom";
      $("#searchSince").value = day; $("#searchUntil").value = day;
      toggleSearchMode("search");
    };
  });
}
function locateMessage(el) {
  const stype = el.dataset.stype;
  const mid = el.dataset.mid;
  const sys = el.dataset.sys;
  closeSearch();
  if (state.search.scope === "chat") {
    setView("chat");
    locateChat(mid);
  } else {
    setView("projects");
    locateGroup(stype, mid, sys);
  }
}
async function locateChat(mid) {
  try {
    await loadHistory();
    const el = Array.from(document.querySelectorAll("#chatLog .msg")).find((e) => e.dataset.mid === String(mid));
    if (el) { el.scrollIntoView({ block: "center" }); flashEl(el); }
    else toast("该消息不在当前视图，已按时间切换");
  } catch (e) { toast("定位失败：" + e.message); }
}
async function locateGroup(stype, mid, sys) {
  const p = currentProject();
  if (!p) return;
  try {
    // 缺陷1 已修复：limit 可拉到 1000，确保更早的历史也在视图内；找不到再明确提示
    const r = await api(`/api/projects/${encodeURIComponent(p.id)}/messages?limit=1000`);
    renderGroupMessages(r.messages || [], { forceBottom: false, prevTop: 0 });
    let el = null;
    if (stype === "system") {
      el = Array.from(document.querySelectorAll("#groupFeed .gsys")).find((e) => e.dataset.sys === sys);
    } else {
      el = Array.from(document.querySelectorAll("#groupFeed .gmsg")).find((e) => e.dataset.mid === String(mid));
    }
    if (el) { el.scrollIntoView({ block: "center" }); flashEl(el); }
    else toast("该消息不在当前视图，已按时间切换");
  } catch (e) { toast("定位失败：" + e.message); }
}
function flashEl(el) {
  el.classList.add("locate-flash");
  setTimeout(() => el.classList.remove("locate-flash"), 1800);
}

/* ---------- wire ---------- */

$$(".nav-item").forEach((b) => (b.onclick = () => setView(b.dataset.view)));
$("#btnRefresh").onclick = async () => {
  await loadState();
  if (state.view === "chat") await loadHistory();
  toast("已刷新");
};
$("#btnProjects").onclick = openProjectModal;
$("#btnSideNewProject").onclick = createProject;
$("#btnArchiveRefresh").onclick = async () => {
  await loadState(true);
  renderArchiveView();
  toast("已刷新");
};
$("#btnSaveProjRename").onclick = submitProjRename;
$("#btnCancelProjRename").onclick = closeProjRename;
$("#btnCloseProjRename").onclick = closeProjRename;
$("#projRenameModal").onclick = (e) => { if (e.target.id === "projRenameModal") closeProjRename(); };
$("#projRenameInput").onkeydown = (e) => { if (e.key === "Enter") { e.preventDefault(); submitProjRename(); } };
$("#btnProjectMembers").onclick = openProjectModal;
$("#btnProjectRoles").onclick = openRoleModal;
$("#btnRoleCoverageEdit").onclick = openRoleModal;
$("#btnCoverageEmpty").onclick = () => {
  state.coverageShowEmpty = !state.coverageShowEmpty;
  loadRoleCoverage();
};
$("#btnCloseRoleModal").onclick = () => $("#roleModal").classList.add("hidden");
$("#btnRoleRecommend").onclick = recommendRoles;
$("#btnRoleShowAll").onclick = () => { state.roleShowAll = !state.roleShowAll; renderRoleTree(); };
$("#btnSaveRoles").onclick = saveRoles;
$("#btnPauseProject").onclick = () => setProjectState("pause");
$("#btnResumeProject").onclick = () => setProjectState("resume");
$("#btnEmergencyStop").onclick = emergencyStop;
$("#projectTitle").ondblclick = beginRenameProject;$("#btnCloseProjectModal").onclick = () => $("#projectModal").classList.add("hidden");
$("#projectSelect").onchange = (e) => switchProject(e.target.value);
$("#btnSaveDir").onclick = saveProjectDir;
$("#btnSaveMembers").onclick = saveProjectMembers;
$("#btnAddAI").onclick = openAIModal;
$("#btnExportRoster").onclick = exportRoster;
$("#btnCloseAIModal").onclick = () => $("#aiModal").classList.add("hidden");
$("#btnSaveAI").onclick = saveAI;
$("#aiType").onchange = syncAIAllowVisibility;
$("#projCaret").onclick = (e) => {
  e.stopPropagation();
  state.projectsOpen = !state.projectsOpen;
  localStorage.setItem("deck_projects_open", state.projectsOpen ? "1" : "0");
  renderProjectControls(state.appState || {});
};
$("#btnGroupSend").onclick = sendGroupMessage;
$("#groupText").addEventListener("input", updateMentionPop);
$("#groupText").addEventListener("click", updateMentionPop);
$("#groupText").addEventListener("blur", () => setTimeout(hideMentionPop, 150));
$("#groupText").addEventListener("keydown", (e) => {
  const m = state._mention;
  if (m && m.open && m.items.length) {
    if (e.key === "ArrowDown") { e.preventDefault(); m.act = (m.act + 1) % m.items.length; renderMentionPop(); return; }
    if (e.key === "ArrowUp") { e.preventDefault(); m.act = (m.act - 1 + m.items.length) % m.items.length; renderMentionPop(); return; }
    if (e.key === "Enter") { e.preventDefault(); pickMention(m.act); return; }
    if (e.key === "Escape") { hideMentionPop(); return; }
  }
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    sendGroupMessage();
  }
});
$("#btnSendConfirm").onclick = () => {
  const s = state._pendingSend; if (!s) return;
  if ($("#sendConfirmRemember").checked) localStorage.setItem(SEND_CONFIRM_KEY, "skip");
  $("#sendConfirmModal").classList.add("hidden");
  doSendGroup(s.p, s.text, s.mentions, s.attachments);
};
$("#btnSendSkip").onclick = () => {
  const s = state._pendingSend; if (!s) return;
  $("#sendConfirmModal").classList.add("hidden");
  doSendGroup(s.p, s.text, s.mentions, s.attachments);
};
$("#btnCloseSendConfirm").onclick = () => { state._pendingSend = null; $("#sendConfirmModal").classList.add("hidden"); };
$("#sendConfirmModal").onclick = (e) => { if (e.target.id === "sendConfirmModal") { state._pendingSend = null; $("#sendConfirmModal").classList.add("hidden"); } };
$("#btnCloseModal").onclick = closeModal;

/* ---------- 界面签收 II：演示推进二次确认（确认后才调用原有 advance） ---------- */
$("#btnCloseAdvanceModal").onclick = closeAdvanceConfirm;
$("#btnAdvanceCancel").onclick = closeAdvanceConfirm;
$("#advanceConfirmModal").onclick = (e) => { if (e.target.id === "advanceConfirmModal") closeAdvanceConfirm(); };
$("#btnAdvanceConfirm").onclick = () => {
  const id = state._advanceTicketId;
  closeAdvanceConfirm();
  if (id) advance(id);
};

/* ---------- 界面签收 CB-UI-SIGNOFF-20261112：筛选入口 / 确认框 / 键盘 ---------- */
$("#btnPendingSignoff").onclick = () => {
  state.onlyPendingSignoff = !state.onlyPendingSignoff;
  renderTickets(state.appState?.tickets || []);
};
$("#btnCloseSignoffModal").onclick = closeSignoffModal;
$("#btnSignoffCancel").onclick = closeSignoffModal;
$("#btnSignoffConfirm").onclick = submitSignoff;
$("#signoffModal").onclick = (e) => { if (e.target.id === "signoffModal") closeSignoffModal(); };
document.addEventListener("keydown", (e) => {
  const so = $("#signoffModal");
  const adv = $("#advanceConfirmModal");
  const soOpen = so && !so.classList.contains("hidden");
  const advOpen = adv && !adv.classList.contains("hidden");
  if (e.key === "Escape") {
    if (soOpen) { e.preventDefault(); closeSignoffModal(); return; }
    if (advOpen) { e.preventDefault(); closeAdvanceConfirm(); return; }
  }
  if (e.key === "Enter" && soOpen && document.activeElement === $("#btnSignoffConfirm")) {
    e.preventDefault();
    submitSignoff();
    return;
  }
  if (e.key === "Enter" && advOpen && document.activeElement === $("#btnAdvanceConfirm")) {
    e.preventDefault();
    $("#btnAdvanceConfirm").click();
  }
});
$("#btnCreate").onclick = createTicket;
$("#modal").onclick = (e) => { if (e.target.id === "modal") closeModal(); };
$("#btnSend").onclick = send;
$("#btnAttach").onclick = () => $("#fileInput").click();
$("#fileInput").onchange = (e) => {
  addFiles(e.target.files);
  e.target.value = "";
};
const dropZone = $("#chatInput");
["dragenter", "dragover"].forEach((ev) => dropZone.addEventListener(ev, (e) => {
  e.preventDefault();
  e.stopPropagation();
  dropZone.classList.add("drag-over");
}));
["dragleave", "drop"].forEach((ev) => dropZone.addEventListener(ev, (e) => {
  e.preventDefault();
  e.stopPropagation();
  dropZone.classList.remove("drag-over");
}));
dropZone.addEventListener("drop", (e) => {
  if (e.dataTransfer?.files?.length) addFiles(e.dataTransfer.files);
});
$("#btnGroupAttach").onclick = () => $("#groupFileInput").click();
$("#groupFileInput").onchange = (e) => {
  addGroupFiles(e.target.files);
  e.target.value = "";
};
const groupDropZone = $("#groupCompose");
["dragenter", "dragover"].forEach((ev) => groupDropZone.addEventListener(ev, (e) => {
  e.preventDefault();
  e.stopPropagation();
  groupDropZone.classList.add("drag-over");
}));
["dragleave", "drop"].forEach((ev) => groupDropZone.addEventListener(ev, (e) => {
  e.preventDefault();
  e.stopPropagation();
  groupDropZone.classList.remove("drag-over");
}));
groupDropZone.addEventListener("drop", (e) => {
  if (e.dataTransfer?.files?.length) addGroupFiles(e.dataTransfer.files);
});
document.addEventListener("paste", (e) => {
  if (!e.target.closest) return;
  const inChat = e.target.closest("#chatInput");
  const inGroup = e.target.closest("#groupCompose") || e.target.closest("#groupText");
  if (!inChat && !inGroup) return;
  const files = Array.from(e.clipboardData?.items || [])
    .filter((it) => it.kind === "file")
    .map((it) => it.getAsFile())
    .filter(Boolean);
  if (files.length) {
    e.preventDefault();
    if (inGroup) addGroupFiles(files);
    else addFiles(files);
  }
});
const ctxMenu = $("#ctxMenu");
document.addEventListener("contextmenu", (e) => {
  if (!e.target.closest || !e.target.closest("#chatLog")) return;
  const sel = window.getSelection()?.toString() || "";
  if (!sel.trim()) return;
  e.preventDefault();
  ctxMenu.style.left = Math.min(e.clientX, window.innerWidth - 130) + "px";
  ctxMenu.style.top = Math.min(e.clientY, window.innerHeight - 50) + "px";
  ctxMenu.classList.remove("hidden");
});
document.addEventListener("click", () => ctxMenu.classList.add("hidden"));
document.addEventListener("scroll", () => ctxMenu.classList.add("hidden"), true);
/* 项目右键菜单：点击外部 / 滚动 / Esc 关闭，与复制菜单互不干扰 */
document.addEventListener("click", hideProjCtxMenu);
document.addEventListener("scroll", hideProjCtxMenu, true);
document.addEventListener("keydown", (e) => {
  if (e.key !== "Escape") return;
  hideProjCtxMenu();
  const rn = $("#projRenameModal");
  if (rn && !rn.classList.contains("hidden")) closeProjRename();
});
$("#ctxCopy").onclick = () => {
  copyText(window.getSelection().toString());
  ctxMenu.classList.add("hidden");
};
$("#btnCozeRefresh").onclick = loadCozeBot;
$("#fExecutor").addEventListener("change", syncFormModelVisibility);
$("#chatModel").addEventListener("change", () => {
  localStorage.setItem("model:" + state.target, $("#chatModel").value);
  renderChatAdapterInfo();
});
$("#btnModels").onclick = async () => {
  const btn = $("#btnModels");
  btn.classList.add("loading");
  btn.textContent = "…";
  try {
    const r = await api("/api/models/refresh", { method: "POST", body: "{}" });
    if (r.models?.length && state.appState) {
      const e = adapterEntry("workbuddy");
      if (e) e.ad.models = r.models;
      else state.appState.models = { workbuddy: r.models };
    }
    populateModels();
    renderChatAdapterInfo();
    toast(r.message || "模型列表已刷新");
  } catch (e) {
    toast("刷新模型失败：" + e.message);
  } finally {
    btn.classList.remove("loading");
    btn.textContent = "↻";
  }
};
$("#chatText").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    send();
  }
});

/* 历史搜索 / 时间轴 入口 */
$("#btnChatSearch").onclick = () => openSearch("chat", "search");
$("#btnGroupSearch").onclick = () => openSearch("project", "search");
$("#btnGroupTimeline").onclick = () => openSearch("project", "timeline");
$("#btnCloseSearch").onclick = closeSearch;
$("#searchModal").onclick = (e) => { if (e.target.id === "searchModal") closeSearch(); };
$("#btnDoSearch").onclick = () => runSearch(true);
$("#btnSearchPrev").onclick = () => { if (state.search.offset > 0) { state.search.offset = Math.max(0, state.search.offset - state.search.limit); runSearch(false); } };
$("#btnSearchNext").onclick = () => { if (state.search.offset + state.search.limit < state.search.total) { state.search.offset += state.search.limit; runSearch(false); } };
$$(".search-tab").forEach((b) => (b.onclick = () => toggleSearchMode(b.dataset.mode)));
$("#searchRange").onchange = (e) => { $("#searchCustom").classList.toggle("hidden", e.target.value !== "custom"); };
$("#searchKeyword").addEventListener("keydown", (e) => { if (e.key === "Enter") runSearch(true); });

(async function boot() {
  const q = new URLSearchParams(location.search);
  const hashView = (location.hash || "").replace("#", "");
  const want = q.get("view") || hashView;
  setView(["chat", "projects", "adapters"].includes(want) ? want : "chat");
  await loadState();
  const a = state.appState?.adapters || {};
  const wantTarget = q.get("target");
  if (!wantTarget && state.appState?.chat?.default_target) {
    state.target = state.appState.chat.default_target;
  }
  const wt = wantTarget ? adapterEntry(wantTarget) : null;
  if (wt) state.target = wt.key;
  const wantModel = q.get("model");
  if (wantModel) localStorage.setItem("model:" + state.target, wantModel);
  renderTargets();
  populateModels();
  syncModelVisibility();
  const cur = adapterEntry(state.target);
  if (!cur || !cur.ad.ready || cur.ad.enabled === false) {
    const first = Object.entries(a).find(([, it]) => it.ready && it.enabled !== false);
    if (first) state.target = first[0];
    renderTargets();
  }
  renderChatAdapterInfo();
  await loadHistory();
  setInterval(() => { if (state.view !== "chat") loadState(true); }, 60000);
})();
