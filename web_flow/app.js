/* ===================================================================
   AI聊不停 · 交互脚本（前端版 v2 · Phase 1 接后端）
   -------------------------------------------------------------------
   纯原生 JS，零依赖、零构建、不引 CDN。
   演示模式（file:// 或 ?demo=1）：原样走演示数据（数据在 api.js 的 demo 实现内）。
   真实模式（启动器 ?token= 打开）：所有数据经 api.js 的 real 实现拉取真实后端。
   所有页面只调用 api.*，不直接写 URL（见 任务书 §3 / §5）。
   =================================================================== */
(function () {
  'use strict';

  /* ================= 演示数据（原样保留，供 ?demo=1 / file:// 独立评审） ================= */
  var ME = { name: '林舟', short: '林', role: '产品经理 · Admin' };

  var PROJECTS = [
    { id: 'p1', name: '产品设计周会', icon: '产', theme: 't1', status: 'active',
      desc: 'Q4 首页改版设计协作，包含 PRD、视觉稿、文案三个子方向。',
      created: '2026-10-08', members: ['LZ', 'D', 'W', 'C', 'A'], extra: '+3', done: 18, signRate: '94%' },
    { id: 'p2', name: '营销活动 Q4', icon: '营', theme: 't2', status: 'active',
      desc: '双 11 预热活动 + 双 12 返场运营页，覆盖 H5 与小程序双端。',
      created: '2026-10-15', members: ['WY', 'D', 'K', 'C'], extra: '+4', done: 31, signRate: '91%' },
    { id: 'p3', name: '数据看板 V2', icon: '数', theme: 't3', status: 'paused',
      desc: '内部 BI 工具重构，新增实时指标和自定义看板模块。',
      created: '2026-09-22', members: ['ZQ', 'A'], extra: '', done: 9, signRate: '88%' },
    { id: 'p4', name: '客户周报机器人', icon: '客', theme: 't4', status: 'active',
      desc: '自动汇总周度客户数据，并通过内部机器人推送周报。',
      created: '2026-10-02', members: ['LZ', 'W', 'A'], extra: '', done: 12, signRate: '97%' },
    { id: 'a1', name: '社区激励 V1', icon: '社', theme: 't5', status: 'archived',
      desc: '用户社区激励体系首个版本，已完成全量上线。',
      created: '2026-07-11', archivedAt: '2026-10-22', members: ['LZ', 'D', 'C'], extra: '', done: 28, signRate: '95%',
      archiveNote: '已全量上线，进入维护期' },
    { id: 'a2', name: '落地页重构', icon: '路', theme: 't2', status: 'archived',
      desc: '官网落地页 SEO 重构，PV 提升 34%。',
      created: '2026-06-03', archivedAt: '2026-09-18', members: ['WY', 'K'], extra: '', done: 14, signRate: '93%',
      archiveNote: '目标达成，转入常规维护' },
    { id: 'a3', name: 'A/B 实验平台', icon: 'A', theme: 't3', status: 'archived',
      desc: '内部 AB 测试能力建设，已迁移至数据平台。',
      created: '2026-05-20', archivedAt: '2026-08-30', members: ['ZQ', 'K'], extra: '', done: 33, signRate: '90%',
      archiveNote: '能力合并至数据平台' }
  ];

  var ROLES = [
    { id: 'r1', name: 'WorkBuddy', short: 'W', role: '构建测试 / DevOps', via: 'workbuddy',
      model: 'auto', models: ['自动（默认）', 'model-a', 'model-b'], fixed: '',
      exe: 'node.exe', key: 'Key 已配置', cli: 'CLI 存在',
      status: 'on', desc: '构建、排查、跑回归，是群里的第一响应人。', today: 5, ok: 96, avg: '9s' },
    { id: 'r2', name: 'DesignBuddy', short: 'D', role: '代码开发（通用）', via: 'process',
      model: 'deepseek-flash', models: ['自动（默认）', 'deepseek-flash', 'model-x', 'model-y'], fixed: '',
      exe: 'codex.exe', key: 'Key 已配置', cli: 'CLI 可执行',
      status: 'on', desc: '写页面、出视觉稿、产出 PRD 大纲。', today: 4, ok: 92, avg: '12s' },
    { id: 'r3', name: 'DataBuddy', short: 'A', role: '数据分析', via: 'coze',
      model: '', models: [], fixed: '模型由 Bot 决定',
      exe: '', key: 'Token 已配置', cli: '',
      status: 'on', desc: '埋点与漏斗分析、指标异常归因。', today: 6, ok: 98, avg: '15s' },
    { id: 'r4', name: 'CopyBuddy', short: 'C', role: '外包 / 文档（人工中转）', via: 'human',
      model: '', models: [], fixed: '人工中转，无固定模型',
      exe: '', key: '', cli: '',
      status: 'on', desc: '页面文案、活动话术，由人工中转交付。', today: 3, ok: 89, avg: '—' },
    { id: 'r5', name: 'CodeBuddy', short: 'K', role: '小任务 / 临时工', via: 'process',
      model: '', models: [], fixed: '模型由该 CLI / 配置决定',
      exe: 'node.exe', key: 'Key 未配置', cli: 'CLI 可执行',
      status: 'cfg', desc: '组件实现、样式修复。当前待补充仓库权限。', today: 2, ok: 85, avg: '20s' }
  ];

  var ST = {
    'cp-p': { text: '待处理', badge: 'wait' },
    'cp-d': { text: '派发中', badge: 'doing' },
    'cp-r': { text: '已回件', badge: 'sign' },
    'cp-v': { text: '待验', badge: 'wait' },
    'cp-x': { text: '已验', badge: 'done' },
    'cp-f': { text: '失败', badge: 'fail' }
  };

  var TICKETS = [
    { id: 'T-241113-0017', pid: 'p1', title: '整理 Q4 首页改版 PRD 大纲', status: 'cp-r',
      role: 'r2', priority: 'P1 · 高', created: '2026-11-13 14:05', returned: '14:22 · 17 min',
      summary: '5 条用户旅程 · 8 个关键页面 · AB 测试框架 · 附件 PRD大纲_v0.1.md',
      detail: '本次 PRD 大纲共 12 页 Markdown，核心交付：',
      points: ['5 条核心用户旅程（含新客首次访问、老客复购）', '8 个关键页面清单（首页首屏、商详、购物车、结算等）',
        'AB 测试框架（基于三个竞品首屏结构的对比基准）', '14 处文案位预留 · 待 CopyBuddy 跟进'],
      origin: '整理 Q4 首页改版 PRD 大纲，重点关注：① 核心用户旅程 ② 关键页面清单 ③ AB 测试框架。格式用 Markdown。补充需求：用户旅程覆盖「新客首次访问」和「老客复购」两条路径。',
      gates: ['内容完整覆盖所有任务要求', '无明显格式错误或断章取义', '覆盖所有指定页面和旅程路径'],
      timeline: [ { t: '用户派发任务', ts: '14:05:00', k: '' },
        { t: 'DesignBuddy 接单 · 开始分析竞品数据', ts: '14:05:02', k: 's2' },
        { t: '用户补充需求：新增「新客首次访问」旅程', ts: '14:18:33', k: 's3' },
        { t: 'AI 提交回件 · 等待签收', ts: '14:22:10', k: 's4' } ],
      atts: [['MD', 'PRD大纲_v0.1.md', '12 KB'], ['CSV', 'Q3首页数据.csv', '48 KB'], ['PNG', '竞品首屏对比.png', '224 KB']] },
    { id: 'T-241113-0016', pid: 'p1', title: '分析上周首页 CTR 下降原因', status: 'cp-r',
      role: 'r3', priority: 'P1 · 高', created: '2026-11-13 09:40', returned: '10:08 · 28 min',
      summary: 'CTR 环比 −12%，定位到 Banner 区配色调整与首屏加载 1.9s 两个主因。',
      detail: '数据分析结论摘要：',
      points: ['Banner 区配色调整后点击率下降 12%', '首屏加载中位数 1.9s，超过 1.5s 预警线',
        '移动端影响大于桌面端（−16% vs −7%）', '建议下一轮 AB 测试只改 Banner 对比度'],
      origin: '分析上周首页 CTR 下降的原因，给出可执行结论，附原始数据表。',
      gates: ['数据口径与埋点口径一致', '结论有对应数据支撑', '给出可执行建议'],
      timeline: [ { t: '用户派发任务', ts: '09:40:00', k: '' },
        { t: 'DataBuddy 拉取埋点数据 · 7 日窗口', ts: '09:41:12', k: 's2' },
        { t: 'AI 提交回件 · 等待签收', ts: '10:08:40', k: 's4' } ],
      atts: [['CSV', '首页CTR_7d.csv', '96 KB'], ['PNG', '分端趋势.png', '180 KB']] },
    { id: 'T-241113-0022', pid: 'p2', title: '设计双 11 预热页首屏', status: 'cp-r',
      role: 'r2', priority: 'P1 · 高', created: '2026-11-13 11:20', returned: '15:02 · 3.7h',
      summary: '首屏 2 个方案 · 含倒计时组件与主 KV 位 · 附 1440/375 双端切图说明。',
      detail: '交付内容：',
      points: ['方案 A：倒计时居中 + 主 KV 全宽', '方案 B：左右分栏 + 商品瀑布流前置',
        '两套均给出 1440 与 375 两档尺寸标注', '主 KV 预留可替换图层，方便换季复用'],
      origin: '设计双 11 预热页首屏，两个方向，需要包含倒计时，考虑移动端信息密度。',
      gates: ['两个方案均可独立评审', '关键尺寸标注齐全', '移动端信息密度已评估'],
      timeline: [ { t: '用户派发任务', ts: '11:20:00', k: '' },
        { t: 'DesignBuddy 接单 · 输出线框', ts: '11:22:31', k: 's2' },
        { t: 'AI 提交回件 · 等待签收', ts: '15:02:09', k: 's4' } ],
      atts: [['PNG', '预热页_方案A.png', '410 KB'], ['PNG', '预热页_方案B.png', '388 KB']] },
    { id: 'T-241113-0018', pid: 'p1', title: '导出上月转化漏斗数据', status: 'cp-p',
      role: 'r3', priority: 'P2 · 中', created: '2026-11-13 09:50', returned: '',
      summary: '', detail: '', points: [], origin: '导出 10 月整月转化漏斗数据，按端拆开。',
      gates: ['时间范围正确', '分端口径对齐', '数据可复核'],
      timeline: [{ t: '工单创建 · 等待派发', ts: '09:50:00', k: '' }], atts: [] },
    { id: 'T-241113-0019', pid: 'p1', title: '生成 Banner 区 AB 测试方案', status: 'cp-d',
      role: 'r2', priority: 'P1 · 高', created: '2026-11-13 10:01', returned: '',
      summary: '', detail: '', points: [], origin: '针对 Banner 区做一轮 AB 测试，含假设、变量、样本量估算。',
      gates: ['假设可证伪', '变量单一', '样本量给出口径'],
      timeline: [{ t: '工单创建 · 已派发至 DesignBuddy', ts: '10:01:00', k: 's2' }], atts: [] },
    { id: 'T-241113-0020', pid: 'p1', title: '撰写邮件营销文案 3 版本', status: 'cp-v',
      role: 'r4', priority: 'P2 · 中', created: '2026-11-12 18:10', returned: '18:30 · 20 min',
      summary: '3 版本文案，分别偏理性、情绪、利益点，均已控制在 90 字内。',
      detail: '三个版本：', points: ['版本 A：理性参数型', '版本 B：情绪共鸣型', '版本 C：利益点直给型'],
      origin: '写 3 版邮件营销文案，每版不超过 90 字，主题行各配一条。',
      gates: ['三版差异明确', '字数合规', '主题行完整'],
      timeline: [ { t: '用户派发任务', ts: '18:10:00', k: '' },
        { t: 'AI 提交回件', ts: '18:30:12', k: 's4' } ],
      atts: [['TXT', '邮件文案_3版.txt', '3 KB']] },
    { id: 'T-241112-0015', pid: 'p2', title: '修复登录按钮 hover 态错位', status: 'cp-x',
      role: 'r5', priority: 'P1 · 高', created: '2026-11-12 13:40', returned: '14:20 · 40 min',
      summary: '已修复 hover 态 1px 错位，回归 3 个断点全部通过。',
      detail: '修复说明：', points: ['问题定位到 transform 与 border 叠加', '改为 box-shadow 描边，避免位移',
        '1440 / 1280 / 375 三档回归通过'],
      origin: '登录按钮在 hover 时会往下跳 1px，帮我修一下。',
      gates: ['问题复现路径清楚', '修复方案不引入新问题', '多断点回归'],
      timeline: [ { t: '用户派发任务', ts: '13:40:00', k: '' },
        { t: 'AI 提交回件', ts: '14:20:00', k: 's4' },
        { t: '已签收', ts: '14:25:10', k: 's4' } ],
      atts: [], signed: true },
    { id: 'T-241113-0021', pid: 'p2', title: '梳理竞品首屏结构对比', status: 'cp-f',
      role: 'r3', priority: 'P3 · 低', created: '2026-11-13 15:42', returned: '',
      summary: '', detail: '', points: [], origin: '抓取 5 个竞品首屏结构做对比表。',
      gates: ['样本量足够', '结构维度统一', '结论可追溯'],
      timeline: [ { t: '用户派发任务', ts: '15:42:00', k: '' },
        { t: '执行失败 · 上游网关超时 504', ts: '15:47:04', k: '' } ],
      atts: [], failReason: 'API 返回超时（504），已重试 0/3' },
    { id: 'T-241112-0011', pid: 'p3', title: '输出周会纪要并同步群', status: 'cp-x',
      role: 'r1', priority: 'P2 · 中', created: '2026-11-12 09:10', returned: '09:25 · 15 min',
      summary: '纪要 6 条决议 · 3 个待办 · 已同步到项目群。',
      detail: '纪要结构：', points: ['6 条会议决议', '3 个待办与责任人', '下次会议时间'],
      origin: '把今天周会内容整理成纪要，同步到群里，列出待办。',
      gates: ['决议完整', '待办有责任人', '已同步群'],
      timeline: [ { t: '用户派发任务', ts: '09:10:00', k: '' },
        { t: 'AI 提交回件', ts: '09:25:00', k: 's4' },
        { t: '已签收', ts: '09:28:00', k: 's4' } ],
      atts: [], signed: true }
  ];

  var THREADS = {
    'p1:r2': [
      { type: 'sys', text: '🗓 今天 10:00 · 派发器自动启动，已扫描 3 条待处理工单' },
      { type: 'me', who: ME, time: '10:00', text: '@DesignBuddy 帮我把 Q4 首页改版的需求整理成一份 PRD 大纲，突出用户旅程和关键页面。' },
      { type: 'ai', who: ROLE('r2'), time: '10:02', att: '📎 prd_outline_v1.md · ⏱ 47s',
        text: 'PRD 大纲已完成，包含 5 个用户旅程节点、8 个关键页面规格，以及首页 AB 测试方案的对比框架。建议下一步由设计同学接手原型。' },
      { type: 'card', who: ROLE('r2'), time: '10:02', tk: 'T-241113-0017', urg: true,
        title: 'Q4 首页改版 PRD 大纲 v1',
        sum: '回件摘要：5 旅程 8 页面，含 AB 方案对比框架。签收依据：结构完整，可进入原型阶段。' },
      { type: 'ai', who: ROLE('r2'), time: '10:06',
        text: '另补了一份竞品首屏对比，放在回件附件里，方便判断信息层级。' }
    ],
    'p1:r3': [
      { type: 'sys', text: '🗓 今天 09:40 · 数据分析窗口已就绪' },
      { type: 'me', who: ME, time: '09:40', text: '分析上周首页 CTR 下降的原因，给可执行结论，附原始数据表。' },
      { type: 'ai', who: ROLE('r3'), time: '10:08', att: '📎 首页CTR_7d.csv · 96 KB',
        text: '上周首页 CTR 数据已导出：Banner 区域 CTR 下降 12%，可能与配色调整有关；首屏加载中位数 1.9s，超过 1.5s 预警线。完整结论在工单回件里。' },
      { type: 'card', who: ROLE('r3'), time: '10:08', tk: 'T-241113-0016', urg: true,
        title: '首页 CTR 下降归因',
        sum: '回件摘要：定位到 Banner 配色与首屏加载两个主因，附分端数据。' }
    ],
    'p1:r4': [
      { type: 'sys', text: '🗓 今天 10:04 · 文案窗口已就绪' },
      { type: 'me', who: ME, time: '10:04', text: 'PRD 里涉及的外部图片，帮我核一下版权声明。' },
      { type: 'ai', who: ROLE('r4'), time: '10:05',
        text: '已检查 PRD 大纲中涉及的图片版权声明 —— 全部使用内部素材库，无第三方风险。' }
    ],
    'p1:r1': [
      { type: 'sys', text: '🗓 今天 08:30 · 派发器已启动，扫描到 3 条待处理工单' },
      { type: 'ai', who: ROLE('r1'), time: '08:30',
        text: '3 条待处理工单已按岗位编排：设计类给 DesignBuddy，数据类给 DataBuddy。有阻塞我会在这里报。' }
    ],
    'p2:r2': [
      { type: 'sys', text: '🗓 今天 11:20 · 预热页设计需求已派发' },
      { type: 'me', who: ME, time: '11:20', text: '双 11 预热页首屏出两个方向，倒计时必须有，注意移动端信息密度。' },
      { type: 'ai', who: ROLE('r2'), time: '15:02', att: '📎 预热页_方案A.png · 388 KB',
        text: '两个方向都出了：A 是倒计时居中 + 主 KV 全宽，B 是左右分栏 + 商品瀑布流前置。双端尺寸标注都在回件里。' },
      { type: 'card', who: ROLE('r2'), time: '15:02', tk: 'T-241113-0022', urg: true,
        title: '双 11 预热页首屏 · 方案 A/B',
        sum: '回件摘要：首屏 2 个方案，含倒计时组件与主 KV 位，附双端切图说明。' }
    ],
    'p2:r5': [
      { type: 'sys', text: '🗓 窗口已就绪 · 待补仓库权限后可直接提交' },
      { type: 'me', who: ME, time: '13:40', text: '登录按钮 hover 时往下跳 1px，修一下。' },
      { type: 'ai', who: ROLE('r5'), time: '14:20',
        text: '已修复：根因是 transform 与 border 叠加导致位移，改成 box-shadow 描边，1440 / 1280 / 375 三档回归通过。' }
    ],
    'p3:r1': [
      { type: 'sys', text: '🗓 项目已暂停派发 · 剩余 2 条工单待人工确认' },
      { type: 'ai', who: ROLE('r1'), time: '昨天 09:25',
        text: '周会纪要已整理，6 条决议 3 个待办都在回件里。项目暂停后我把派发器也停了。' }
    ],
    'p4:r1': [
      { type: 'sys', text: '🗓 每周一 08:30 自动生成客户周报' },
      { type: 'ai', who: ROLE('r1'), time: '周一 08:31',
        text: '本周客户周报已生成：新增 12 家、活跃 86 家、风险客户 3 家。已按模板推送。' }
    ]
  };

  /* ================= 模式判断 + api 实例 ================= */
  var qp = new URLSearchParams(location.search);
  var IS_DEMO = (location.protocol === 'file:') || (qp.get('demo') === '1');
  var TOKEN = '';
  if (!IS_DEMO) {
    TOKEN = qp.get('token') || '';
    try { TOKEN = TOKEN || (sessionStorage.getItem('deck_token') || ''); } catch (e) {}
    if (TOKEN) { try { sessionStorage.setItem('deck_token', TOKEN); } catch (e) {} }
  }
  var api = window.createApi(IS_DEMO ? 'demo' : 'real',
    IS_DEMO ? { demo: { ME: ME, PROJECTS: PROJECTS, ROLES: ROLES, ST: ST, TICKETS: TICKETS, THREADS: THREADS } }
            : { token: TOKEN, base: '' });

  /* ================= 运行时状态 ================= */
  var state = {
    view: 'chat', pid: 'p1', chatRole: 'r2', tid: null,
    filter: 'all', roleId: 'r2', pjTab: 'active', pjSel: 'p1', pjOpen: true,
    stTab: 'all', snoozed: {}, drafts: {}, error: null,
    pendingAtts: [], mentions: []
  };

  /* ================= 数据外观（MODEL）：demo 直连常量引用，real 由 api 填充 ================= */
  var MODEL = {
    me: ME, roles: IS_DEMO ? ROLES : [], projects: IS_DEMO ? PROJECTS : [],
    tickets: IS_DEMO ? TICKETS : [], threads: IS_DEMO ? THREADS : {}, stats: {}
  };

  function ROLE(id) {
    // 定义期（THREADS 常量）MODEL 尚未赋值，回退到 ROLES 演示常量；运行期优先用真实适配器列表
    var list = (typeof MODEL !== 'undefined' && MODEL && MODEL.roles && MODEL.roles.length) ? MODEL.roles : ROLES;
    for (var i = 0; i < list.length; i++) if (list[i].id === id) return list[i];
    return list[0] || { id: id, name: id, short: (id || '?').slice(0, 1), role: '', via: '', model: '', models: [], fixed: '', exe: '', key: '', cli: '', status: 'off', desc: id, today: 0, ok: 0, avg: '—' };
  }
  function PJ(id) {
    for (var i = 0; i < MODEL.projects.length; i++) if (MODEL.projects[i].id === id) return MODEL.projects[i];
    return MODEL.projects[0] || { id: id, name: id, icon: '?', theme: 't1', status: 'active', desc: '', created: '', members: [], extra: '', done: 0, signRate: '—', archivedAt: '', archiveNote: '' };
  }
  function TK(id) {
    for (var i = 0; i < MODEL.tickets.length; i++) if (MODEL.tickets[i].id === id) return MODEL.tickets[i];
    return null;
  }
  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c];
    });
  }
  function now() { var d = new Date(); return ('0' + d.getHours()).slice(-2) + ':' + ('0' + d.getMinutes()).slice(-2); }
  function el(id) { return document.getElementById(id); }
  function pjTickets(pid) { return MODEL.tickets.filter(function (t) { return t.pid === pid; }); }
  function countBy(list, key) { var n = 0; for (var i = 0; i < list.length; i++) if (list[i].status === key) n++; return n; }
  function pad2(n) { return ('0' + (n || 0)).slice(-2); }
  function fmtSize(n) {
    n = Number(n) || 0;
    if (n < 1024) return n + ' B';
    if (n < 1024 * 1024) return (n / 1024).toFixed(1) + ' KB';
    if (n < 1024 * 1024 * 1024) return (n / 1024 / 1024).toFixed(1) + ' MB';
    return (n / 1024 / 1024 / 1024).toFixed(2) + ' GB';
  }
  function memberLabel(id) { var r = ROLE(id); return r.short || (id || '?').slice(0, 1); }
  function inboxStatus() { return IS_DEMO ? 'cp-r' : 'cp-v'; }

  function threadFor(pid, rid) {
    if (IS_DEMO) { var k = pid + ':' + rid; if (!MODEL.threads[k]) MODEL.threads[k] = []; return MODEL.threads[k]; }
    var key = (rid === '__group__') ? (pid + ':__group__') : ('__global__:' + rid);
    if (!MODEL.threads[key]) MODEL.threads[key] = [];
    return MODEL.threads[key];
  }
  function threadKey(pid, rid) { return IS_DEMO ? (pid + ':' + rid) : ((rid === '__group__') ? (pid + ':__group__') : ('__global__:' + rid)); }

  function ensureThread(pid, rid) {
    if (IS_DEMO) return Promise.resolve();
    var key = threadKey(pid, rid);
    if (MODEL.threads[key]) return Promise.resolve();
    if (rid === '__group__') return api.getProjectMessages(pid).then(function (ms) { MODEL.threads[key] = ms; });
    return api.getChatHistory(rid).then(function (ms) { MODEL.threads[key] = ms; });
  }

  /* ================= 工具 UI ================= */
  var toastTimer = null;
  function toast(msg) {
    var t = el('toast'); if (!t) return;
    t.textContent = msg; t.classList.add('on');
    clearTimeout(toastTimer); toastTimer = setTimeout(function () { t.classList.remove('on'); }, 2600);
  }
  function openModal(html, wide) {
    var m = el('modal');
    m.innerHTML = '<div class="veil" data-act="close-modal"></div>' +
      '<div class="card' + (wide ? ' wide' : '') + '" role="dialog" aria-modal="true">' + html + '</div>';
    m.classList.add('open');
  }
  function closeModal() { var m = el('modal'); m.classList.remove('open'); m.innerHTML = ''; }

  /* ================= v2 交互辅助：附件 / @ / 新建 / 成员 ================= */
  function paintPendingAtts() {
    var box = el('attPending'); if (!box) return;
    if (!state.pendingAtts || !state.pendingAtts.length) { box.innerHTML = ''; return; }
    var h = '<span class="att-cap">待发附件</span>';
    state.pendingAtts.forEach(function (a, i) {
      h += '<span class="atchip pending" data-act="rm-att" data-idx="' + i + '">' +
        '<span class="atchip-name">' + esc(a.name) + '</span>' +
        '<span class="atchip-size">' + esc(fmtSize(a.size)) + '</span>' +
        '<span class="atchip-x">✕</span></span>';
    });
    box.innerHTML = h;
  }
  function paintMentionChips() {
    var box = el('mentionPending'); if (!box) return;
    if (!state.mentions || !state.mentions.length) { box.innerHTML = ''; return; }
    var h = '<span class="ment-cap">提醒</span>';
    state.mentions.forEach(function (id, i) {
      var r = ROLE(id);
      h += '<span class="mention-chip" data-act="rm-mention" data-idx="' + i + '">@' +
        esc(r.name || id) + ' <span class="atchip-x">✕</span></span>';
    });
    box.innerHTML = h;
  }
  function handleFileSelect(files) {
    if (!files || !files.length) return;
    var arr = Array.prototype.slice.call(files);
    if (state.pendingAtts.length + arr.length > 10) {
      toast('一次最多 10 个附件，已截取前 ' + (10 - state.pendingAtts.length) + ' 个');
      arr = arr.slice(0, 10 - state.pendingAtts.length);
    }
    arr.forEach(function (file) {
      state.pendingAtts.push({ name: file.name, size: file.size, mime: file.type, id: null, _file: file });
    });
    paintPendingAtts();
    var seq = Promise.resolve();
    state.pendingAtts.forEach(function (a) {
      if (a.id) return;
      seq = seq.then(function () {
        if (IS_DEMO) { a.id = 'att-' + Math.random().toString(36).slice(2, 10); paintPendingAtts(); return Promise.resolve(); }
        return api.uploadAttachment(a._file).then(function (rec) {
          a.id = rec.id; a.name = rec.name || a.name; a.size = (rec.size != null) ? rec.size : a.size;
          a.mime = rec.mime || a.mime; paintPendingAtts();
        }).catch(function (e) {
          var idx = state.pendingAtts.indexOf(a); if (idx >= 0) state.pendingAtts.splice(idx, 1);
          paintPendingAtts();
          toast('附件上传失败：' + (e.message || e.status || e));
        });
      });
    });
  }
  function openMentionPicker() {
    var p = PJ(state.pid);
    var members = (p.members || []).slice();
    var h = '<div class="h">@ 选择要提醒的成员</div>';
    h += '<div class="sub">勾选后会随本条消息通知对应成员；留空则通知项目群全体 AI。</div>';
    h += '<div class="mempick">';
    if (!members.length) h += '<div class="plain-note">该项目暂无成员可提醒</div>';
    members.forEach(function (aid) {
      var r = ROLE(aid);
      var on = state.mentions.indexOf(aid) >= 0;
      h += '<label class="memopt' + (on ? ' on' : '') + '"><input type="checkbox" data-mem="' + esc(aid) + '"' + (on ? ' checked' : '') + '>' +
        '<span class="av">' + esc(r.short || (aid || '?').slice(0, 1)) + '</span>' +
        '<span class="mn">' + esc(r.name) + '</span><span class="mr">' + esc(r.role) + '</span></label>';
    });
    h += '</div>';
    h += '<div class="actions"><button class="btn" data-act="close-modal">取消</button>' +
      '<button class="btn ok" data-act="do-mention">确定</button></div>';
    openModal(h, true);
  }
  function doMention() {
    var checked = document.querySelectorAll('.mempick input[data-mem]');
    var sel = [];
    checked.forEach(function (c) { if (c.checked) sel.push(c.getAttribute('data-mem')); });
    state.mentions = sel; closeModal(); paintMentionChips();
    toast(sel.length ? ('已选 ' + sel.length + ' 位成员提醒') : '已清空提醒（将通知项目群全体）');
  }
  function openNewProject() {
    var h = '<div class="h">＋ 新建项目</div>';
    h += '<div class="sub">建好后会自动把「人工席」加为成员。</div>';
    h += '<div class="box"><div class="k">项目名称（必填）</div><input id="npName" class="inp" maxlength="80" placeholder="例如：双 12 大促专题"></div>';
    h += '<div class="box"><div class="k">项目目录（可选，本机路径）</div><input id="npDir" class="inp" placeholder="D:\\你的项目（留空则不设）"></div>';
    h += '<div class="hint-gate" id="npErr" style="display:none"></div>';
    h += '<div class="actions"><button class="btn" data-act="close-modal">取消</button>' +
      '<button class="btn ok" data-act="do-new-pj">创建项目</button></div>';
    openModal(h);
  }
  function doNewProject() {
    var name = (el('npName') || {}).value;
    name = (name || '').trim();
    if (!name) { var e = el('npErr'); if (e) { e.style.display = 'block'; e.textContent = '请填写项目名称'; } return; }
    var dir = (el('npDir') || {}).value || '';
    if (IS_DEMO) {
      api.createProject(name, dir).then(function (r) {
        state.pid = (r && r.project && r.project.id) || state.pid;
        closeModal(); render(); toast('已新建项目 · ' + name);
      });
      return;
    }
    api.createProject(name, dir).then(function (r) {
      var nid = r && r.project && r.project.id;
      return reloadAll().then(function () {
        if (nid) state.pid = nid;
        closeModal(); render();
        toast('已新建项目 · ' + name + (nid ? '（' + nid + '）' : ''));
      });
    }).catch(function (e) { var er = el('npErr'); if (er) { er.style.display = 'block'; er.textContent = '创建失败：' + (e.message || e.status || e); } });
  }
  function openNewRole() {
    var h = '<div class="h">＋ 新建 AI 岗位</div>';
    h += '<div class="sub">一个岗位 = 一个独立窗口；填完会自动加入派发编排。</div>';
    h += '<div class="box"><div class="k">接入方式（必填）</div><select id="nrType" class="inp">' +
      '<option value="process">process（本地 CLI）</option>' +
      '<option value="workbuddy">workbuddy（协作客户端）</option>' +
      '<option value="coze">coze（Bot）</option>' +
      '<option value="demo">demo（演示）</option>' +
      '<option value="human">human（人工中转）</option></select></div>';
    h += '<div class="box"><div class="k">名称（必填）</div><input id="nrName" class="inp" maxlength="60" placeholder="例如：设计小助手"></div>';
    h += '<div class="box"><div class="k">默认岗位（来自岗位目录）</div><select id="nrRole" class="inp"><option value="">加载中…</option></select></div>';
    h += '<div class="box" id="nrCmdBox"><div class="k">命令（process 必填，每行一个参数，禁止 Shell 拼接）</div><textarea id="nrCmd" class="reason" placeholder="node.exe&#10;main.js&#10;--task"></textarea></div>';
    h += '<div class="box"><div class="k">默认模型（可选）</div><input id="nrModel" class="inp" maxlength="60" placeholder="例如：deepseek-chat"></div>';
    h += '<div class="box"><div class="k">Key 路径（可选，本机密钥文件绝对路径或 {paths} 占位）</div><input id="nrKey" class="inp" placeholder="D:\\keys\\my.key 或 {paths.my_key}"></div>';
    h += '<label class="sw on" id="nrEn" data-sw="1" role="switch" aria-checked="true" tabindex="0" style="margin:2px 0 4px"><span style="margin-left:8px;font-size:13px">启用该岗位</span></label>';
    h += '<div class="hint-gate" id="nrErr" style="display:none"></div>';
    h += '<div class="actions"><button class="btn" data-act="close-modal">取消</button>' +
      '<button class="btn ok" data-act="do-new-role">创建岗位</button></div>';
    openModal(h, true);
    api.getRoleCatalog().then(function (cat) {
      var sel = el('nrRole'); if (!sel) return;
      var roles = [];
      (cat.layers || []).forEach(function (ly) { (ly.roles || []).forEach(function (r) { roles.push(r); }); });
      if (!roles.length) { sel.innerHTML = '<option value="">（无可用岗位）</option>'; return; }
      sel.innerHTML = roles.map(function (r) {
        return '<option value="' + esc(r.id) + '"' + (r.human_only ? ' data-human="1"' : '') + '>' + esc(r.name) + (r.human_only ? '（人工岗）' : '') + '</option>';
      }).join('');
    }).catch(function () { var sel = el('nrRole'); if (sel) sel.innerHTML = '<option value="">（加载失败）</option>'; });
  }
  function doNewRole() {
    var type = (el('nrType') || {}).value;
    var label = ((el('nrName') || {}).value || '').trim();
    var roleId = (el('nrRole') || {}).value || '';
    var cmdRaw = (el('nrCmd') || {}).value || '';
    var model = ((el('nrModel') || {}).value || '').trim();
    var keyPath = ((el('nrKey') || {}).value || '').trim();
    var enabled = el('nrEn') ? el('nrEn').classList.contains('on') : true;
    var spec = { type: type, label: label, role_id: roleId, enabled: enabled };
    if (model) spec.default_model = model;
    if (keyPath) spec.key_path = keyPath;
    if (type === 'process') {
      var lines = cmdRaw.split('\n').map(function (s) { return s.trim(); }).filter(Boolean);
      if (!lines.length) { var e = el('nrErr'); if (e) { e.style.display = 'block'; e.textContent = 'process 类型必须填写命令（每行一个参数）'; } return; }
      spec.command = lines;
    }
    if (IS_DEMO) {
      api.createAdapter(spec).then(function () { closeModal(); render(); toast('已新建岗位 · ' + label); })
        .catch(function (e) { var er = el('nrErr'); if (er) { er.style.display = 'block'; er.textContent = '创建失败：' + (e.message || e.status || e); } });
      return;
    }
    api.createAdapter(spec).then(function () {
      return reloadAll().then(function () { closeModal(); render(); toast('已新建岗位 · ' + label); });
    }).catch(function (e) {
      var er = el('nrErr'); var msg = (e && e.data && (e.data.error || (e.data.errors && e.data.errors.join('；')))) || (e.message) || ('HTTP ' + (e.status || ''));
      if (er) { er.style.display = 'block'; er.textContent = '创建失败：' + msg; }
      else toast('创建失败：' + msg);
    });
  }
  function openMemberManager(pid) {
    var p = PJ(pid || state.pjSel);
    if (!p) { toast('请先选择一个项目'); return; }
    var h = '<div class="h">成员管理 · ' + esc(p.name) + '</div>';
    h += '<div class="sub">勾选加入项目的 AI / 人工席；保存时按岗位目录校验（人工岗只能由 human 担任等）。</div>';
    h += '<div class="mempick">';
    MODEL.roles.forEach(function (r) {
      var on = (p.members || []).indexOf(r.id) >= 0;
      h += '<label class="memopt' + (on ? ' on' : '') + '"><input type="checkbox" data-mem="' + esc(r.id) + '"' + (on ? ' checked' : '') + '>' +
        '<span class="av">' + esc(r.short || (r.id || '?').slice(0, 1)) + '</span>' +
        '<span class="mn">' + esc(r.name) + '</span><span class="mr">' + esc(r.role) + '</span></label>';
    });
    h += '</div>';
    h += '<div class="hint-gate" id="memErr" style="display:none"></div>';
    h += '<div class="actions"><button class="btn" data-act="close-modal">取消</button>' +
      '<button class="btn ok" data-act="do-members">保存成员</button></div>';
    openModal(h, true);
  }
  function doMemberSave() {
    var pid = state.pjSel;
    var p = PJ(pid); if (!p) return;
    var wanted = [];
    document.querySelectorAll('.mempick input[data-mem]').forEach(function (c) { if (c.checked) wanted.push(c.getAttribute('data-mem')); });
    var current = p.members || [];
    var adds = wanted.filter(function (id) { return current.indexOf(id) < 0; });
    var removes = current.filter(function (id) { return wanted.indexOf(id) < 0; });
    if (!adds.length && !removes.length) { closeModal(); toast('成员未变动'); return; }
    if (IS_DEMO) {
      p.members = wanted.slice();
      closeModal(); render(); toast('已更新成员 · ' + wanted.length + ' 人');
      return;
    }
    var chain = Promise.resolve();
    var failed = [];
    adds.concat(removes).forEach(function (aid) {
      var isAdd = adds.indexOf(aid) >= 0;
      chain = chain.then(function () {
        return (isAdd ? api.addMember(pid, aid) : api.removeMember(pid, aid)).then(function () {}).catch(function (e) {
          failed.push((ROLE(aid).name || aid) + '：' + ((e && e.data && (e.data.error || (e.data.errors && e.data.errors.join('；')))) || e.message || ('HTTP ' + (e.status || ''))));
        });
      });
    });
    chain.then(function () { return reloadAll(); }).then(function () {
      closeModal(); render();
      if (failed.length) toast('部分保存失败（已回滚到真实状态）：' + failed.join('；'));
      else toast('成员已更新 · ' + wanted.length + ' 人');
    }).catch(function () { toast('保存失败，请重试'); });
  }
  function doRetry(tid) {
    if (IS_DEMO) {
      var t = TK(tid); if (!t) return;
      t.status = 'cp-p'; t.failReason = '';
      t.timeline.push({ t: '手动重新派发 · 退回待处理', ts: now(), k: 's2' });
      closeModal(); render(); toast('已重新派发 #' + tid);
      return;
    }
    api.retry(tid).then(function () {
      toast('已重新派发 · #' + tid + ' 状态更新为「待处理」');
      closeModal(); return reloadAll();
    }).then(function () { render(); })
      .catch(function (e) { toast('重新派发失败：' + (e.message || e.status || e)); });
  }

  var CHEV = '<svg class="chev" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M6 9c2 2 4 4 6 4s4-2 6-4"/></svg>';

  /* ================= 左侧导航 ================= */
  function renderSide() {
    var inbox = MODEL.tickets.filter(function (t) { return t.status === inboxStatus(); }).length;
    var members = MODEL.roles.filter(function (r) { return r.status === 'on'; }).length;
    var h = '';
    h += '<div class="lb">工作区</div>';
    h += navItem('chat', '沟通', '<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>', '');
    h += navItem('inbox', '待签收', '<path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.7 21a2 2 0 0 1-3.4 0"/>', inbox, true);
    h += navItem('members', 'AI 岗位', '<circle cx="12" cy="8" r="4"/><path d="M4 21v-1a6 6 0 0 1 6-6h4a6 6 0 0 1 6 6v1"/>', members);
    h += navItem('projects', '项目与归档', '<path d="M3 7h18M3 7v10a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V7"/><path d="M3 7l2-3h14l2 3"/>', '');
    h += navItem('states', '空态与错误态', '<circle cx="12" cy="12" r="9"/><path d="M12 8v4M12 16h.01"/>', '');

    h += '<div class="lb fold' + (state.pjOpen ? '' : ' closed') + '" data-act="toggle-pj" role="button" tabindex="0">' +
      '<span>我的项目</span>' + CHEV + '</div>';
    if (state.pjOpen) {
      MODEL.projects.forEach(function (p) {
        if (p.status === 'archived') return;
        var c = pjTickets(p.id).filter(function (t) { return t.status !== 'cp-x'; }).length;
        h += '<div class="pj' + (p.id === state.pid && (state.view === 'chat' || state.view === 'ticket') ? ' on' : '') + '" data-pj="' + p.id + '">' +
          '<span class="dot' + (p.status === 'paused' ? ' pause' : '') + '"></span>' +
          '<span class="nm2">' + esc(p.name) + '</span>' +
          '<span class="cnt2">' + c + '</span></div>';
      });
      h += '<div class="pj" data-act="new-pj"><span class="dot" style="background:var(--line-dash)"></span><span class="nm2">＋ 新建项目</span></div>';
    }
    el('nav').innerHTML = h;
  }

  function navItem(view, label, icon, cnt, hot) {
    var cur = state.view === 'ticket' ? 'chat' : state.view;
    var on = cur === view;
    return '<div class="it' + (on ? ' on' : '') + '" data-view="' + view + '">' +
      '<svg class="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">' + icon + '</svg>' +
      '<span class="tx">' + label + '</span>' +
      (cnt === '' ? '' : '<span class="cnt' + (hot && cnt > 0 ? ' hot' : '') + '">' + cnt + '</span>') +
      '</div>';
  }

  /* ================= 聊天视图 ================= */
  function renderChat() {
    var p = PJ(state.pid);
    var isGroup = (state.chatRole === '__group__');
    var list = pjTickets(p.id);
    if (IS_DEMO) list = list.filter(function (t) { return t.role === state.chatRole; });
    var shown = state.filter === 'all' ? list : list.filter(function (t) { return t.status === state.filter; });
    var thread = threadFor(p.id, state.chatRole);
    var ctx = isGroup ? null : ROLE(state.chatRole);

    var h = '<div class="view view-chat">';
    h += '<header class="headbar">';
    h += '<div class="t">💬 沟通</div>';
    h += '<div class="tools">';
    h += '<div class="rolepick"><span class="cap">AI 岗位</span>';
    h += '<label class="sel"><select id="rolePicker" aria-label="选择 AI 岗位">';
    if (!IS_DEMO) h += '<option value="__group__"' + (isGroup ? ' selected' : '') + '>💬 群聊（项目群）</option>';
    if (IS_DEMO) {
      MODEL.roles.forEach(function (r) {
        h += '<option value="' + r.id + '"' + (r.id === state.chatRole ? ' selected' : '') + '>' + esc(r.name) + '</option>';
      });
    } else {
      p.members.forEach(function (aid) {
        var r = ROLE(aid);
        h += '<option value="' + esc(aid) + '"' + (aid === state.chatRole ? ' selected' : '') + '>' + esc(r.name) + '</option>';
      });
    }
    h += '</select>' + CHEV + '</label>';
    h += '<span class="ready"><span class="d"></span>' + (isGroup ? '群聊在线' : (ctx.status === 'on' ? '已就绪' : (ctx.status === 'cfg' ? '待配置' : '离线'))) + '</span></div>';
    h += '<label class="sel"><select id="pjPicker" aria-label="切换项目">';
    MODEL.projects.forEach(function (x) {
      if (x.status === 'archived') return;
      h += '<option value="' + x.id + '"' + (x.id === p.id ? ' selected' : '') + '>' + esc(x.name) + '</option>';
    });
    h += '</select>' + CHEV + '</label>';
    h += '<button class="btn" data-act="pause">' + (p.status === 'paused' ? '▶ 继续' : '⏸ 暂停') + '</button>';
    h += '<div class="uava">' + esc(MODEL.me.short) + '</div>';
    h += '</div></header>';

    h += '<div class="chat-body"><section class="stream">';
    h += '<div class="sbar"><span class="ld"></span><span>' + (isGroup ? '项目群 · ' + esc(p.name) : esc(ctx.name) + ' · ' + esc(ctx.role)) + '</span>' +
      (isGroup ? '' : '<span class="glob" title="该会话跨项目共享，同一 AI 在任何项目的 1:1 都在这里">🌐 全局会话（跨项目）</span>') +
      '<span class="sp"></span><span>模型 · ' + esc(isGroup ? '—' : modelLabel(ctx)) + '</span>' +
      '<span class="sp"></span><span>接入 · ' + esc(isGroup ? '群聊' : ctx.via) + '</span>' +
      '<span class="sp"></span><span>上下文 ' + thread.length + ' 条</span></div>';
    h += '<div class="feed" id="feed">';
    if (!thread.length) {
      h += emptyInline(isGroup ? '项目群还没有消息' : '这个岗位还没有对话', isGroup ? '发第一条消息启动项目群协作。' : '发第一条消息启动与 ' + ctx.name + ' 的独立协作窗口。');
    } else {
      thread.forEach(function (m) { h += msgHTML(m); });
    }
    h += '</div></section>';

    var stage = activeStage(list);
    h += '<aside class="rail">';
    h += '<div class="rh">📋 工单面板 <span class="cnt">· ' + shown.length + ' 条</span>' +
      '<span class="stage-now ' + stage + '">当前 · ' + ST[stage].text + '</span>' +
      '<span class="more" data-filter="all">全部</span></div>';
    h += '<div class="pgwrap">' + progressHTML(list) + '</div>';
    h += '<div class="score">' + scoreCellsHTML(list, stage) + '</div>';
    h += '<div class="scroll"><div class="tlist">' + ticketListHTML(shown) + '</div></div>';
    h += '</aside></div>';

    h += '<div class="composer">';
    h += '<div class="t1"><span class="hi">' + (isGroup ? '项目群 · 全体 AI' : esc(ctx.name) + ' 独立窗口 · Enter 发送 · Shift+Enter 换行') + '</span>';
    h += '<span class="sp"></span>';
    h += '<button class="btn" data-act="attach">📎 附件</button>';
    if (isGroup) h += '<button class="btn" data-act="mention">@ 成员</button>';
    h += '<button class="btn" data-act="hist">🕐 历史记录</button>';
    h += '</div>';
    h += '<div class="att-pending" id="attPending"></div>';
    if (isGroup) h += '<div class="mention-pending" id="mentionPending"></div>';
    h += '<div class="row"><textarea id="input" maxlength="2000" placeholder="' + (isGroup ? '给项目群发消息…' : '给 ' + esc(ctx.name) + ' 发消息…') + '">' +
      esc(state.drafts[p.id + ':' + state.chatRole] || '') + '</textarea>';
    h += '<input type="file" id="fileInput" multiple style="display:none">';
    h += '<div class="col"><span class="ch" id="counter">0 / 2000</span>' +
      '<button class="send" data-act="send">发送 →</button></div></div>';
    h += '</div>';

    h += '</div>';
    return h;
  }

  function modelLabel(r) {
    if (r.models && r.models.length) return r.model === 'auto' ? '自动（默认）' : (r.model || r.models[0]);
    return r.fixed || '—';
  }
  function emptyInline(t, d) { return '<div class="empty-inline"><div class="t">' + esc(t) + '</div><div>' + esc(d) + '</div></div>'; }
  /* 当前环节：按「最需要人看的」优先级挑一个状态；派发中=AI 正在跑，待验=等我签 */
  function activeStage(list) {
    var order = ['cp-d', 'cp-v', 'cp-p', 'cp-r', 'cp-f'];
    for (var i = 0; i < order.length; i++) {
      for (var j = 0; j < list.length; j++) if (list[j].status === order[i]) return order[i];
    }
    return 'cp-x';
  }
  function scoreCell(status, cls, label, list, stage) {
    var n = countBy(list, status);
    return '<div class="cel ' + cls + (stage === status ? ' live' : '') +
      (state.filter === status ? ' on' : '') + '" data-filter="' + status + '" title="点击筛选">' +
      '<div class="nm">' + pad2(n) + '</div><div class="lb">' + label + '</div></div>';
  }
  function scoreCellsHTML(list, stage) {
    return scoreCell('cp-p', 'lp', '待处理', list, stage) +
      scoreCell('cp-d', 'ld', '派发中', list, stage) +
      scoreCell('cp-r', 'lr', '已回件', list, stage) +
      scoreCell('cp-v', 'lv', '待验', list, stage) +
      scoreCell('cp-x', 'lx', '已验', list, stage) +
      scoreCell('cp-f', 'lf', '失败', list, stage);
  }
  /* 总进度条：一条按环节占比分段的细条 + 图例（方案 A） */
  function progressHTML(list) {
    var total = list.length;
    var done = countBy(list, 'cp-x');
    var pct = total ? Math.round(done / total * 100) : 0;
    var segs = [['cp-p', 'lp', '待处理'], ['cp-d', 'ld', '派发中'], ['cp-r', 'lr', '已回件'],
                ['cp-v', 'lv', '待验'], ['cp-x', 'lx', '已验'], ['cp-f', 'lf', '失败']];
    var bar = '', lg = '';
    segs.forEach(function (s) {
      var n = countBy(list, s[0]);
      if (n && total) bar += '<i class="' + s[1] + '" style="width:' + (n / total * 100).toFixed(2) + '%"></i>';
      lg += '<span><em class="' + s[1] + '"></em>' + s[2] + ' ' + n + '</span>';
    });
    return '<div class="pg">' +
      '<div class="top"><span>总进度 <b>' + pct + '%</b></span><span>已验 ' + done + ' / 共 ' + total + '</span></div>' +
      '<div class="bar">' + (bar || '<i class="none" style="width:100%"></i>') + '</div>' +
      '<div class="lg">' + lg + '</div>' +
      '</div>';
  }
  function ticketListHTML(shown) {
    if (!shown.length) return emptyInline('没有匹配的工单', '点右上「全部」清除筛选。');
    var h = '';
    shown.forEach(function (t) {
      var r = ROLE(t.role);
      h += '<div class="ti" data-tk="' + t.id + '">' +
        '<div class="ln1"><span class="td">#' + t.id + '</span>' +
        '<span class="cp ' + t.status + '">' + ST[t.status].text + '</span></div>' +
        '<div class="tt">' + esc(t.title) + '</div>' +
        '<div class="mt"><span>' + esc(r.name) + ' · ' + esc(r.role) + '</span><span>' + esc(t.status === 'cp-p' ? (t.created || '').slice(0, 16) : (t.returned ? t.returned.slice(0, 16) : '—')) + '</span></div>' +
        '</div>';
    });
    return h;
  }
  /* v2.4：只刷新右侧工单面板（不动聊天滚动位置），让环节高亮实时跟上 */
  function railList() {
    var p = PJ(state.pid);
    var list = pjTickets(p.id);
    if (IS_DEMO) list = list.filter(function (t) { return t.role === state.chatRole; });
    return list;
  }
  function updateRail() {
    if (state.view !== 'chat') return;
    var rail = document.querySelector('.rail');
    if (!rail) return;
    var list = railList();
    var shown = state.filter === 'all' ? list : list.filter(function (t) { return t.status === state.filter; });
    var stage = activeStage(list);
    var cnt = rail.querySelector('.rh .cnt');
    if (cnt) cnt.textContent = '· ' + shown.length + ' 条';
    var pill = rail.querySelector('.stage-now');
    if (pill) {
      pill.className = 'stage-now ' + stage;
      pill.textContent = '当前 · ' + ST[stage].text;
    }
    var score = rail.querySelector('.score');
    if (score) score.innerHTML = scoreCellsHTML(list, stage);
    var pw = rail.querySelector('.pgwrap');
    if (pw) pw.innerHTML = progressHTML(list);
    var tl = rail.querySelector('.tlist');
    if (tl) tl.innerHTML = ticketListHTML(shown);
  }
  var railTimer = null, railBusy = false;
  function railRefresh() {
    if (railBusy || document.hidden || !TOKEN) return;
    if (state.error && state.view === 'error') return;
    if (state.view !== 'chat') return;
    var m = el('modal');
    if (m && m.classList.contains('open')) return;
    var inp = el('input');
    if (inp && (document.activeElement === inp || (inp.value || '').trim())) return;
    railBusy = true;
    /* 注意：api.getTickets() 只读 cached（只有 loadState 才会刷新缓存），必须先 loadState */
    api.loadState().then(function (s) {
      if (s && s.stats) MODEL.stats = s.stats;
      return api.getTickets();
    }).then(function (ts) {
      MODEL.tickets = ts;
      updateRail();
      try { renderSide(); } catch (e) { /* 侧栏重绘失败不影响主流程 */ }
    }).catch(function () {
      /* 静默：下一轮再试 */
    }).then(function () {
      railBusy = false;
    });
  }
  function startRailRefresh() {
    if (railTimer) clearInterval(railTimer);
    railTimer = setInterval(railRefresh, 6000);
  }

  function msgHTML(m) {
    if (m.type === 'sys') return '<div class="msg sys"><div class="bubble">' + esc(m.text) +
      (m.time ? '<span class="sys-ts">' + esc(m.time) + '</span>' : '') + '</div></div>';
    if (m.type === 'card') {
      var tk = m.tk; var done = tk && (function () { var t = TK(tk); return t && t.status === 'cp-x'; })();
      var h = '<div class="msg card ai"><div class="av">' + esc((m.who || {}).short || '?') + '</div><div class="body">';
      h += '<div class="meta"><span class="n">' + esc((m.who || {}).name || '') + ' · ' + esc((m.who || {}).role || '') + '</span><span class="ts">' + esc(m.time || '') + '</span></div>';
      h += '<div class="bubble' + (m.urg ? ' urg' : '') + '">';
      h += '<div class="card-head"><span class="tag">' + (done ? '✓ 已签收' : '🔔 待签收') + '</span>' +
        '<span class="tid num">#' + esc(tk || '') + '</span></div>';
      h += '<div class="card-title">' + esc(m.title || '') + '</div>';
      h += '<div class="card-sum">' + esc(m.sum || '') + '</div>';
      h += '<div class="card-acts">';
      if (!done) h += '<button class="btn ok" data-act="sign" data-tk="' + esc(tk || '') + '">✓ 签收通过</button>';
      h += '<button class="btn" data-act="open-ticket" data-tk="' + esc(tk || '') + '">📄 查看回件</button>';
      if (!done) h += '<button class="btn dng" data-act="reject" data-tk="' + esc(tk || '') + '">✗ 驳回</button>';
      h += '</div></div></div></div>';
      return h;
    }
    var mine = m.type === 'me';
    var h2 = '<div class="msg ' + (mine ? 'me' : 'ai') + '">';
    h2 += '<div class="av">' + esc((m.who || {}).short || '?') + '</div><div class="body">';
    h2 += '<div class="meta"><span class="n">' + esc((m.who || {}).name || '') + (mine ? '' : ' · ' + esc((m.who || {}).role || '')) + '</span>' +
      '<span class="ts">' + esc(m.time || '') + '</span></div>';
    var attHtml = '';
    if (m.attList && m.attList.length) {
      attHtml = '<div class="att-chips">';
      m.attList.forEach(function (x) {
        var isImg = (x.mime || '').indexOf('image/') === 0;
        var url = api.attachmentUrl(x.id);
        attHtml += '<a class="atchip" href="' + esc(url) + '" target="_blank" rel="noopener" title="点击下载 / 打开">' +
          (isImg ? '<img class="atchip-img" src="' + esc(url) + '" alt="' + esc(x.name) + '">' : '') +
          '<span class="atchip-body"><span class="atchip-name">' + esc(x.name) + '</span>' +
          '<span class="atchip-size">' + esc(fmtSize(x.size)) + '</span></span>' +
          '<span class="atchip-dl">下载 ⤓</span></a>';
      });
      attHtml += '</div>';
    } else if (m.att) {
      attHtml = '<div class="meta-row">' + esc(m.att) + '</div>';
    }
    h2 += '<div class="bubble">' + attHtml + (m.text ? esc(m.text) : '') + '</div>';
    h2 += '</div></div>';
    return h2;
  }

  /* ================= 工单详情 ================= */
  function renderTicket() {
    var t = TK(state.tid);
    if (!t) return '<div class="view"><div class="empty-inline"><div class="t">工单不存在</div>' +
      '<div>请从工单面板重新进入。</div><div style="margin-top:12px"><button class="btn" data-act="back">返回沟通</button></div></div></div>';
    var r = ROLE(t.role);
    var s = ST[t.status];
    var signed = t.status === 'cp-x';
    var failed = t.status === 'cp-f';
    var realSignable = (!IS_DEMO && t.status === 'cp-v');

    var h = '<div class="view view-ticket">';
    h += '<header class="headbar">';
    h += '<button class="btn ghost" data-act="back">← 返回</button>';
    h += '<div class="t"><span class="sub">工单详情</span><span class="sep">/</span>' +
      '<span class="ct" data-pj="' + t.pid + '">' + esc(PJ(t.pid).name) + '</span></div>';
    h += '</header>';
    h += '<div class="scroll"><div class="ticket-page">';

    h += '<div class="tk-head">';
    h += '<div class="tk-top"><div class="tk-main">';
    h += '<div class="tid num">#' + esc(t.id) + '</div>';
    h += '<div class="title">' + esc(t.title) + '</div>';
    h += '<span class="badge ' + s.badge + '">' + (signed ? '✓ 已签收' : (failed ? '⚠ 执行失败' : '🔔 ' + s.text + ' · 等待确认')) + '</span>';
    h += '</div><div class="tk-meta">';
    h += '<div class="row"><span class="k">优先级</span><span class="v">' + esc(t.priority) + '</span></div>';
    h += '<div class="row"><span class="k">派单</span><span class="v">主理人 → ' + esc(r.name) + '收单 · ' + esc((t.created || '').slice(0, 16) || '—') + '</span></div>';
    h += '<div class="row"><span class="k">回件</span><span class="v">' + esc(r.name) + ' → 主理人收单 · ' + esc(t.returned ? t.returned.slice(0, 16) : '—') + '</span></div>';
    h += '</div></div>';
    h += '<div class="tk-acts">';
    if (signed) {
      h += '<span class="tk-note">✓ 已于 ' + esc(t.signedAt || '—') + ' 签收</span>';
    } else if (failed) {
      h += '<button class="btn pri" data-act="retry" data-tk="' + esc(t.id) + '">↻ 重新派发</button>';
    } else if (IS_DEMO || realSignable) {
      h += '<button class="btn dng" data-act="reject" data-tk="' + esc(t.id) + '">✗ 驳回</button>';
      h += '<button class="btn pri" data-act="sign" data-tk="' + esc(t.id) + '">✓ 签收</button>';
    } else {
      h += '<span class="tk-note">' + esc(s.text) + ' · 暂不可签收</span>';
    }
    h += '</div></div>';

    h += '<div class="sec"><h4>📋 时间线</h4>';
    t.timeline.forEach(function (x) {
      h += '<div class="tl-step"><span class="dot ' + (x.k || '') + '"></span><span class="t">' + esc(x.t) + '</span>' +
        '<span class="ts">' + esc(x.ts || '') + '</span></div>';
    });
    h += '</div>';

    if (t.summary) {
      h += '<div class="sec"><h4>🎯 回件摘要</h4><div class="summary-box">';
      h += '<span class="tag">✦ ' + esc(r.name) + ' · ' + esc(r.model || '') + ' · 提交于 ' + esc((t.returned || '').split(' ·')[0]) + '</span>';
      h += esc(t.detail || '');
      if (t.points && t.points.length) { h += '<ul>'; t.points.forEach(function (p) { h += '<li>' + esc(p) + '</li>'; }); h += '</ul>'; }
      h += '</div></div>';
    } else if (failed) {
      h += '<div class="sec"><h4>⚠ 失败原因</h4><div class="summary-box" style="border-color:rgba(239,68,68,.3);background:var(--danger-soft)">' +
        esc(t.failReason || '未知错误') + '</div></div>';
    }

    h += '<div class="sec"><h4>📝 原始任务</h4><div class="origin-task"><div class="from">派单人 · ' + esc(MODEL.me.name) + ' · ' + esc((t.created || '').slice(11, 16) || '') + '</div>' + esc(t.origin || '') + '</div></div>';

    h += '<div class="sec"><h4>💬 评论与协作</h4><div class="plain-note">暂无额外评论 · 在群聊中 @ 相关成员跟进</div></div>';
    h += '</div></div></div>';
    return h;
  }

  /* ================= 待签收 ================= */
  function renderInbox() {
    var list = MODEL.tickets.filter(function (t) { return t.status === inboxStatus(); });
    var h = '<div class="view view-inbox">';
    h += '<header class="headbar"><div class="t">🔔 待签收 <span class="sub">· ' + list.length + ' 条等你确认</span></div>';
    h += '<div class="tools"><button class="btn" data-view="states">状态说明</button></div></header>';
    h += '<div class="view-split"><div class="col-scroll">';
    h += '<div class="pend-grid">';
    if (!list.length) h += '<div class="empty-inline" style="grid-column:1/-1"><div class="t">全部签收完了</div><div>暂无等待确认的回件。</div></div>';
    list.forEach(function (t) {
      var r = ROLE(t.role);
      var snz = !!state.snoozed[t.id];
      h += '<div class="pend-card' + (snz ? ' mute' : '') + '">';
      h += '<div class="top"><span class="tid num">#' + esc(t.id) + '</span>' +
        '<span class="badge sign">' + (snz ? '已稍后提醒' : '待签收') + '</span></div>';
      h += '<div class="ttl">' + esc(t.title) + '</div>';
      h += '<div class="ds">' + esc(t.summary) + '</div>';
      h += '<div class="from"><span class="av">' + esc(r.short) + '</span><span>' + esc(r.name) + ' · ' + esc(r.role) + ' · ' + esc(t.returned || '') + '</span></div>';
      h += '<div class="acts">';
      h += '<button class="btn pri" data-act="sign" data-tk="' + esc(t.id) + '">去签收</button>';
      h += '<button class="btn" data-act="open-ticket" data-tk="' + esc(t.id) + '">查看详情</button>';
      h += '<button class="btn ghost" data-act="snooze" data-tk="' + esc(t.id) + '">' + (snz ? '取消稍后' : '稍后提醒') + '</button>';
      h += '</div></div>';
    });
    h += '</div></div>';
    h += '<aside class="rail"><div class="rh">提醒设置</div><div class="scroll">';
    h += settingRow('到期提醒', '回件 30 分钟未签收时提醒', true);
    h += settingRow('声音提示', '签收提醒播放轻提示音', false);
    h += settingRow('汇总摘要', '每天 18:00 汇总未签收工单', true);
    h += '<div class="rh" style="margin-top:6px">签收说明</div>';
    h += '<div class="plain-note" style="text-align:left;line-height:1.8">签收前需逐项勾选门禁检查项，三项全勾选后「确认签收」才会激活。签收后工单状态变为「已验」，并写回项目时间线。</div>';
    h += '</div></aside></div></div>';
    return h;
  }
  function settingRow(t, d, on) {
    return '<div class="setting-row"><span class="tx">' + esc(t) + '<span class="d">' + esc(d) + '</span></span>' +
      '<span class="sw' + (on ? ' on' : '') + '" data-sw="1" role="switch" aria-checked="' + (on ? 'true' : 'false') + '" tabindex="0"></span></div>';
  }

  /* ================= AI 岗位 ================= */
  function renderMembers() {
    var detail = ROLE(state.roleId);
    var online = MODEL.roles.filter(function (r) { return r.status === 'on'; }).length;
    var h = '<div class="view view-members">';
    h += '<header class="headbar"><div class="t">🧩 AI 岗位 <span class="sub">· ' + MODEL.roles.length + ' 个岗位 · ' + online + ' 台在线</span></div>';
    h += '<div class="tools"><button class="btn" data-act="new-role">＋ 新建岗位</button></div></header>';
    h += '<div class="view-split"><div class="col-scroll">';

    h += '<div class="ai-grid">';
    MODEL.roles.forEach(function (r) {
      h += '<div class="ai-card' + (r.id === state.roleId ? ' on' : '') + '" data-role="' + esc(r.id) + '">';
      h += '<div class="hd"><span class="av ' + (r.id === 'r1' ? 'warm' : (r.id === 'r3' ? 'cool' : '')) + '">' + esc(r.short) + '</span>';
      h += '<span class="who"><span class="n">' + esc(r.name) + '</span><span class="r">' + esc(r.role) + '</span></span>';
      h += '<span class="via">' + esc(r.via) + '</span></div>';
      h += '<div class="stat-line">' + [r.key, r.cli].filter(Boolean).join(' · ') +
        (r.key || r.cli ? ' · ' : '') +
        (r.status === 'on' ? '<span class="ok">● 在线</span>' : (r.status === 'cfg' ? '<span class="no">◐ 待配置</span>' : '<span class="no">○ 离线</span>')) + '</div>';
      h += '<div class="model-row"><span class="k">模型</span>';
      if (r.models && r.models.length) {
        h += '<label class="sel"><select data-model="' + esc(r.id) + '" aria-label="' + esc(r.name) + ' 模型">';
        r.models.forEach(function (m) {
          var v = (typeof m === 'string' && m === '自动（默认）') ? 'auto' : m;
          h += '<option value="' + esc(v) + '"' + (v === r.model ? ' selected' : '') + '>' + esc(m) + '</option>';
        });
        h += '</select>' + CHEV + '</label>';
      } else { h += '<span class="model-fixed" title="' + esc(r.fixed) + '">' + esc(r.fixed) + '</span>'; }
      h += '</div>';
      if (r.exe) h += '<div class="exe">' + esc(r.exe) + '</div>';
      h += '<div class="desc">' + esc(r.desc) + '</div>';
      h += '<div class="ops"><button class="btn" data-act="toggle-role" data-role="' + esc(r.id) + '">' +
        (r.status === 'on' ? '停用岗位' : '启用岗位') + '</button>' +
        '<button class="btn ghost" data-act="dispatch" data-role="' + esc(r.id) + '">派发工单</button></div>';
      h += '</div>';
    });
    h += '</div>';

    h += '<div class="block guide">';
    h += '<div class="bh"><h4>AI 岗位怎么配</h4></div>';
    h += '<div class="bsub" style="margin-bottom:2px">一个岗位 = 一个独立窗口。岗位决定谁干活、怎么接活、跑在哪。</div>';
    h += '<p>接入方式决定了这个岗位怎么被调用：<code>workbuddy</code> 走协作客户端，<code>coze</code> 走 Bot，<code>process</code> 走本地 CLI，<code>human</code> 由人工中转。</p>';
    h += '<div class="sub-t">新增一个 AI 岗位</div>';
    h += '<ul><li>选接入方式，填 Key 或 CLI 可执行文件</li>' +
      '<li>绑定项目，指定它在项目里顶哪个层</li>' +
      '<li>先试跑一条小工单，确认能回件再开自动派发</li></ul>';
    h += '<div class="sub-t">技能安装顺序</div>';
    h += '<ul><li>基础技能：读文件、发消息、写回件</li>' +
      '<li>项目技能：按项目需要再装，不要一次全开</li>' +
      '<li>最后开自动派发，避免空跑消耗额度</li></ul>';
    h += '<div class="sub-t">当前名单（示例）</div>';
    h += '<p><code>owner-a</code> 负责决策治理层 · <code>owner-b</code> 负责质量交付层 · <code>owner-c</code> 备用</p>';
    h += '</div>';
    h += '</div>';

    h += '<aside class="rail"><div class="rh">岗位详情</div><div class="scroll"><div class="ai-detail">';
    h += '<div class="head"><span class="av">' + esc(detail.short) + '</span><div><div class="n">' + esc(detail.name) + '</div>' +
      '<div class="r">' + esc(detail.role) + '</div></div></div>';
    h += '<div class="kv"><span class="k">接入方式</span><span class="v">' + esc(detail.via) + '</span></div>';
    h += '<div class="kv"><span class="k">模型</span><span class="v">' + esc(modelLabel(detail)) + '</span></div>';
    if (detail.exe) h += '<div class="kv"><span class="k">可执行</span><span class="v exe">' + esc(detail.exe) + '</span></div>';
    h += '<div class="kv"><span class="k">状态</span><span class="v">' + (detail.status === 'on' ? '在线可用' : (detail.status === 'cfg' ? '待配置' : '离线')) + '</span></div>';
    h += '<div class="kv"><span class="k">所属项目</span><span class="v">' + esc((MODEL.projects[0] || {}).name || '—') + '</span></div>';
    h += '<div class="plain-note" style="text-align:left;margin-top:10px;line-height:1.8">' + esc(detail.desc) + '</div>';
    h += '<div class="sec-h" style="margin:14px 0 8px">最近工单</div>';
    var rec = MODEL.tickets.filter(function (t) { return t.role === detail.id; }).slice(0, 3);
    if (!rec.length) h += emptyInline('暂无工单', '该岗位还没有接单记录。');
    rec.forEach(function (t) {
      h += '<div class="ti" data-tk="' + t.id + '" style="margin-bottom:8px">' +
        '<div class="ln1"><span class="td">#' + esc(t.id) + '</span><span class="cp ' + t.status + '">' + ST[t.status].text + '</span></div>' +
        '<div class="tt">' + esc(t.title) + '</div></div>';
    });
    h += '</div></div></aside></div></div>';
    return h;
  }
  /* ================= 项目与归档 ================= */
  function renderProjects() {
    var tab = state.pjTab;
    var list = MODEL.projects.filter(function (p) {
      if (tab === 'active') return p.status !== 'archived';
      if (tab === 'archived') return p.status === 'archived';
      return false;
    });
    if (!list.some(function (p) { return p.id === state.pjSel; }) && list.length) state.pjSel = list[0].id;
    var sel = null;
    for (var i = 0; i < MODEL.projects.length; i++) if (MODEL.projects[i].id === state.pjSel) sel = MODEL.projects[i];

    var h = '<div class="view view-projects">';
    h += '<header class="headbar"><div class="t">🗂 项目与归档 <span class="sub">· 归档后只读，可恢复</span></div>';
    h += '<div class="tools"><button class="btn pri" data-act="new-pj">＋ 新建项目</button></div></header>';
    h += '<div class="view-split"><div class="left-col">';
    h += '<div class="tabs">';
    h += tabItem('active', '进行中', MODEL.projects.filter(function (p) { return p.status !== 'archived'; }).length);
    h += tabItem('archived', '归档', MODEL.projects.filter(function (p) { return p.status === 'archived'; }).length);
    h += tabItem('deleted', '已删除', 0);
    h += '</div>';
    h += '<div class="col-scroll" style="padding-top:0">';
    if (!list.length) {
      h += '<div class="empty-inline"><div class="t">这里还没有项目</div><div>删除的项目会在这里保留 30 天，可恢复。</div></div>';
    } else {
      h += '<div class="pj-grid">';
      list.forEach(function (p) {
        var n = pjTickets(p.id).filter(function (t) { return t.status !== 'cp-x'; }).length;
        var arc = p.status === 'archived';
        h += '<div class="pj-card' + (p.id === state.pjSel ? ' on' : '') + (arc ? ' arc' : '') + '" data-pjsel="' + p.id + '">';
        h += '<div class="pico ' + p.theme + '">' + esc(p.icon) + '</div>';
        h += '<div class="n">' + esc(p.name) + '</div>';
        h += '<div class="desc">' + esc(p.desc) + '</div>';
        h += '<div class="row2">';
        h += arc ? '<span class="badge done">✔ 已完成</span>'
          : (p.status === 'paused' ? '<span class="badge pause">⏸ 暂停</span>' : '<span class="badge done">● 进行中</span>');
        h += '<span class="cnt-chip">' + (arc ? '工单历史 ' + p.done : '活跃工单 ' + n) + '</span>';
        h += '</div>';
        h += '<div class="heads">';
        p.members.slice(0, 4).forEach(function (m, idx) {
          h += '<span class="h' + (idx ? ' a' : '') + '">' + esc(memberLabel(m)) + '</span>';
        });
        if (p.extra) h += '<span class="h more">' + esc(p.extra) + '</span>';
        h += '</div>';
        h += '<div class="acts">';
        if (arc) {
          h += '<button class="btn" data-act="restore" data-pjid="' + p.id + '">恢复项目</button>';
          h += '<button class="btn ghost" data-act="open-pj" data-pjid="' + p.id + '">详情</button>';
        } else {
          h += '<button class="btn" data-act="open-pj" data-pjid="' + p.id + '">管理</button>';
          h += '<button class="btn" data-act="archive" data-pjid="' + p.id + '">归档</button>';
        }
        h += '</div></div>';
      });
      h += '</div>';
    }
    h += '</div></div>';

    h += '<aside class="rail"><div class="rh">项目详情</div><div class="scroll">';
    if (sel) {
      var _tk = pjTickets(sel.id);
      var n2 = _tk.filter(function (t) { return t.status !== 'cp-x'; }).length;
      var done2 = _tk.filter(function (t) { return t.status === 'cp-x'; }).length;
      var arc2 = sel.status === 'archived';
      h += '<div class="pj-detail"><div class="top"><span class="ico">' + esc(sel.icon) + '</span>';
      h += '<div class="info"><div class="n">' + esc(sel.name) + '</div><div class="sub">创建于 ' + esc(sel.created) +
        (arc2 ? ' · 归档于 ' + esc(sel.archivedAt || '—') : ' · 进行中') + '</div></div>';
      h += arc2 ? '<span class="badge done">✔ 已归档</span>' : (sel.status === 'paused' ? '<span class="badge pause">⏸ 暂停</span>' : '<span class="badge done">● 进行中</span>');
      h += '</div>';
      h += '<div class="desc">' + esc(sel.desc) + '</div>';
      if (arc2) {
        h += '<div class="kv"><span class="k">归档原因</span><span class="v">' + esc(sel.archiveNote || '—') + '</span></div>';
        h += '<div class="kv"><span class="k">归档操作人</span><span class="v">' + esc(MODEL.me.name) + ' · Admin</span></div>';
        h += '<div class="kv"><span class="k">工单总数</span><span class="v">' + _tk.length + '</span></div>';
      } else {
        h += '<h3>关键指标</h3><div class="stats-big">';
        h += '<div class="b"><div class="v">' + n2 + '</div><div class="l">活跃工单</div></div>';
        h += '<div class="b coral"><div class="v">' + done2 + '</div><div class="l">已完成</div></div>';
        h += '</div>';
        var memRoles = (sel.members || []).map(function (m) { return ROLE(m); }).filter(function (x) { return !!x; });
        h += '<h3>成员（' + (memRoles.length + (MODEL.me ? 1 : 0)) + '）</h3><div class="member-list">';
        if (MODEL.me) h += '<div class="m"><span class="av">' + esc(MODEL.me.short) + '</span><span>' + esc(MODEL.me.name) + '</span><span class="role">主理人</span></div>';
        memRoles.forEach(function (r) {
          h += '<div class="m a"><span class="av">' + esc(r.short) + '</span><span>' + esc(r.name) + '</span>' +
            '<span class="role">' + esc(r.role) + '</span></div>';
        });
        h += '</div>';
      }
      h += '<div class="acts" style="display:flex;gap:8px;margin-top:14px;flex-wrap:wrap">';
      if (arc2) h += '<button class="btn" data-act="restore" data-pjid="' + sel.id + '">恢复项目</button>';
      else {
        h += '<button class="btn" data-act="manage-members" data-pjid="' + sel.id + '">管理成员</button>';
        h += '<button class="btn" data-act="archive" data-pjid="' + sel.id + '">归档项目</button>';
      }
      h += '</div>';
      h += '</div>';
    } else { h += emptyInline('未选择项目', '从左侧列表选一个项目查看详情。'); }
    h += '</div></aside></div></div>';
    return h;
  }
  function tabItem(k, label, n) {
    return '<div class="t' + (state.pjTab === k ? ' on' : '') + '" data-pjtab="' + k + '">' + label +
      '<span class="ctn">' + n + '</span></div>';
  }

  /* ================= 空态 / 错误态 ================= */
  function renderStates() {
    var all = state.stTab === 'all';
    var emp = state.stTab === 'empty';
    var err = state.stTab === 'err';
    var h = '<div class="view view-states">';
    h += '<header class="headbar"><div class="t">🫧 空态 & 错误态 <span class="sub">· 空态 4 种 · 错误态 3 种</span></div>';
    h += '<div class="tools">';
    h += '<button class="btn' + (all ? ' pri' : '') + '" data-stab="all">全部</button>';
    h += '<button class="btn' + (emp ? ' pri' : '') + '" data-stab="empty">空态</button>';
    h += '<button class="btn' + (err ? ' pri' : '') + '" data-stab="err">错误态</button>';
    h += '</div></header>';
    h += '<div class="states-scroll">';
    if (all || emp) {
      h += '<div class="section-t">空态（Empty States）<div class="bar"></div></div>';
      h += '<div class="stages">';
      h += stage('① 空聊天',
        '<rect x="24" y="16" width="72" height="68" rx="14" stroke="var(--lavender)" stroke-width="2.2"/>' +
        '<polygon points="38,84 32,98 48,84" fill="var(--white)" stroke="var(--lavender)" stroke-width="2.2" stroke-linejoin="round"/>' +
        '<circle cx="72" cy="48" r="18" stroke="var(--coral)" stroke-width="2.6"/>' +
        '<line x1="85" y1="61" x2="96" y2="72" stroke="var(--coral)" stroke-width="2.6" stroke-linecap="round"/>' +
        '<circle cx="46" cy="48" r="2" fill="var(--teal)"/><circle cx="54" cy="48" r="2" fill="var(--teal)"/><circle cx="62" cy="48" r="2" fill="var(--teal)"/>',
        '还没有消息', '这是一个全新的项目群。发第一条消息启动协作，AI 会自动加入。',
        '<button class="btn" data-view="chat">发第一条试试</button>');
      h += stage('② 空工单',
        '<rect x="14" y="18" width="72" height="60" rx="10" stroke="var(--teal)" stroke-width="2.2"/>' +
        '<rect x="14" y="18" width="72" height="14" rx="10" stroke="var(--teal)" stroke-width="2.2"/>' +
        '<line x1="14" y1="32" x2="86" y2="32" stroke="var(--teal)" stroke-width="2.2"/>' +
        '<circle cx="50" cy="54" r="8" stroke="var(--coral)" stroke-width="2.2"/>' +
        '<line x1="56" y1="60" x2="64" y2="68" stroke="var(--coral)" stroke-width="2.2" stroke-linecap="round"/>' +
        '<path d="M24 46 L30 52 M30 46 L24 52" stroke="var(--teal)" stroke-width="2" stroke-linecap="round"/>' +
        '<path d="M70 46 L76 52 M76 46 L70 52" stroke="var(--teal)" stroke-width="2" stroke-linecap="round"/>',
        '暂无工单', '工单中心空空如也。从 AI 发起你的第一条任务。',
        '<button class="btn" data-act="new-pj">发起工单</button>');
      h += stage('③ 空搜索结果',
        '<circle cx="46" cy="44" r="22" stroke="var(--teal)" stroke-width="2.4"/>' +
        '<line x1="61" y1="59" x2="84" y2="82" stroke="var(--teal)" stroke-width="2.4" stroke-linecap="round"/>' +
        '<line x1="35" y1="33" x2="57" y2="55" stroke="var(--coral)" stroke-width="2.6" stroke-linecap="round"/>' +
        '<line x1="57" y1="33" x2="35" y2="55" stroke="var(--coral)" stroke-width="2.6" stroke-linecap="round"/>' +
        '<path d="M88 22 L90 28 L96 30 L90 32 L88 38 L86 32 L80 30 L86 28 Z" fill="var(--lavender)"/>',
        '没找到「首页改版」', '换个关键词试试，或检查拼写是否正确。',
        '<button class="btn ghost" data-act="clear-search">清除搜索</button>');
      h += stage('④ 归档空态',
        '<rect x="22" y="30" width="96" height="72" rx="6" stroke="var(--lavender)" stroke-width="2.2"/>' +
        '<rect x="22" y="30" width="96" height="8" stroke="var(--lavender)" stroke-width="2.2"/>' +
        '<rect x="68" y="30" width="4" height="72" stroke="var(--lavender)" stroke-width="2.2"/>' +
        '<line x1="22" y1="56" x2="118" y2="56" stroke="var(--lavender)" stroke-width="2.2"/>' +
        '<line x1="22" y1="82" x2="118" y2="82" stroke="var(--lavender)" stroke-width="2.2"/>' +
        '<path d="M42 46 Q42 34 50 34 Q56 34 56 42 Q56 46 50 48 L50 52" stroke="var(--coral)" stroke-width="2.4" fill="none" stroke-linecap="round"/>' +
        '<circle cx="50" cy="58" r="2" fill="var(--coral)"/>' +
        '<path d="M92 20 L94 26 L100 28 L94 30 L92 36 L90 30 L84 28 L90 26 Z" fill="var(--teal)"/>',
        '还没有归档的项目', '项目归档是被动行为 —— 当一个项目完成生命周期或暂停时，管理员可以手动归档。', '');
      h += '</div>';
    }
    if (all || err) {
      h += '<div class="section-t err">错误态（Error States）<div class="bar"></div></div>';
      h += '<div class="stages">';
      h += stage('⑤ 网络错误',
        '<path d="M30 100 L46 40 L62 100 Z" stroke="var(--ink)" stroke-width="2.2" fill="none"/>' +
        '<line x1="46" y1="60" x2="46" y2="76" stroke="var(--ink)" stroke-width="2.2"/>' +
        '<line x1="38" y1="100" x2="54" y2="100" stroke="var(--ink)" stroke-width="2.2"/>' +
        '<path d="M80 50 Q96 50 96 66" stroke="var(--danger)" stroke-width="2.4" stroke-dasharray="3 4" fill="none"/>' +
        '<path d="M74 44 Q102 44 102 72" stroke="var(--danger)" stroke-width="2.4" stroke-dasharray="3 4" fill="none"/>' +
        '<path d="M68 38 Q108 38 108 78" stroke="var(--danger)" stroke-width="2.4" stroke-dasharray="3 4" fill="none"/>' +
        '<line x1="72" y1="44" x2="102" y2="80" stroke="var(--danger)" stroke-width="3" stroke-linecap="round"/>',
        '连接断开', '无法连接到 AI聊不停 服务，请检查网络连接后重试。你的本地数据已缓存，不会丢失。',
        '<button class="btn ghost" data-act="back">取消</button><button class="btn" data-act="retry-net">↻ 检查网络后重试</button>',
        '最近一次同步：2026-11-13 15:32:18 · 自动重试间隔 10s');
      h += stage('⑥ AI 执行失败',
        '<path d="M70 40 L76 22 L82 42 L100 30 L88 48 L110 54 L90 60 L102 76 L82 68 L80 90 L72 72 L62 88 L62 68 L42 76 L58 60 L36 54 L58 48 L46 30 L64 42 Z" stroke="var(--danger)" stroke-width="2.2" fill="none"/>' +
        '<circle cx="72" cy="56" r="10" stroke="var(--danger)" stroke-width="2.2" fill="var(--white)"/>' +
        '<line x1="72" y1="50" x2="72" y2="56" stroke="var(--danger)" stroke-width="2.6" stroke-linecap="round"/>' +
        '<circle cx="72" cy="62" r="1.6" fill="var(--danger)"/>' +
        '<circle cx="32" cy="30" r="3" fill="var(--peach)"/><circle cx="118" cy="22" r="2" fill="var(--peach)"/>' +
        '<circle cx="120" cy="96" r="3" fill="var(--peach)"/>',
        'AI 执行失败', 'DataBuddy 在处理工单 #T-241113-0021 时遇到异常，原因为 API 返回超时（504）。',
        '<button class="btn ghost" data-act="back">忽略</button><button class="btn" data-tk="T-241113-0021">查看工单</button>');
      h += stage('⑦ 服务不可用',
        '<rect x="22" y="20" width="96" height="28" rx="4" stroke="var(--ink)" stroke-width="2.2"/>' +
        '<rect x="22" y="20" width="96" height="4" fill="var(--ink)"/>' +
        '<circle cx="32" cy="34" r="3" fill="var(--danger)"/><circle cx="44" cy="34" r="3" fill="var(--warning)"/>' +
        '<rect x="22" y="60" width="96" height="28" rx="4" stroke="var(--ink)" stroke-width="2.2"/>' +
        '<rect x="22" y="60" width="96" height="4" fill="var(--ink)"/>' +
        '<circle cx="32" cy="74" r="3" fill="var(--danger)"/><circle cx="44" cy="74" r="3" fill="var(--warning)"/>' +
        '<circle cx="70" cy="96" r="10" stroke="var(--danger)" stroke-width="2.4" fill="var(--white)"/>' +
        '<line x1="70" y1="90" x2="70" y2="96" stroke="var(--danger)" stroke-width="2.6" stroke-linecap="round"/>' +
        '<circle cx="70" cy="102" r="1.6" fill="var(--danger)"/>',
        '本地服务未启动', 'AI聊不停 依赖的本地协作服务未运行。请启动服务后刷新页面。',
        '<button class="btn ghost" data-act="help">帮助文档</button><button class="btn" data-act="start-svc">▶ 启动服务</button>');
      h += '</div>';
    }
    h += '</div></div>';
    return h;
  }
  function stage(label, svg, t, d, acts, extra) {
    var h = '<div class="stage"><span class="lbl">' + esc(label) + '</span>';
    h += '<div class="ilust"><svg viewBox="0 0 140 120" fill="none" xmlns="http://www.w3.org/2000/svg">' + svg + '</svg></div>';
    h += '<div class="t">' + esc(t) + '</div><div class="d">' + esc(d) + '</div>';
    if (extra) h += '<div class="hint">' + esc(extra) + '</div>';
    if (acts) h += '<div class="acts">' + acts + '</div>';
    h += '</div>';
    return h;
  }

  /* ================= 错误页（真实模式无法连接时） ================= */
  function renderError() {
    var msg = state.error || '本地服务未启动或无法连接。';
    var h = '<div class="view view-error" style="padding:48px;max-width:640px;margin:0 auto">';
    h += '<div style="font-size:42px;margin-bottom:12px">⚠️</div>';
    h += '<h2 style="margin:0 0 8px">无法连接到 AI聊不停 服务</h2>';
    h += '<p style="color:var(--muted);line-height:1.8">' + esc(msg) + '</p>';
    h += '<div class="box" style="margin:16px 0;padding:14px 16px;background:var(--surface-2);border-radius:12px;font-size:13px">';
    h += '<div class="k">启动方式</div><div class="v">在「AI聊不停 启动器」中点击打开，本前端会从启动器获得 <code>?token=</code> 自动鉴权。</div>';
    h += '<div class="k" style="margin-top:8px">本地端口</div><div class="v">默认 <code>127.0.0.1</code>，由启动器随机分配；请勿手动改 host。</div>';
    h += '</div>';
    h += '<div class="acts"><button class="btn" data-act="retry-net">↻ 重新连接</button>';
    h += '<button class="btn ghost" data-act="demo-fallback">改用演示模式查看界面</button></div>';
    h += '</div>';
    return h;
  }

  /* ================= 弹窗：签收 / 驳回 / 历史 ================= */
  function openTicketModal(tid) {
    var t = TK(tid); if (!t) return;
    var r = ROLE(t.role);
    var done = t.status === 'cp-x';
    var realSignable = (!IS_DEMO && t.status === 'cp-v');
    var h = '<div class="h">📄 回件 <span class="cp ' + t.status + '">' + ST[t.status].text + '</span></div>';
    h += '<div class="sub">#' + esc(t.id) + ' · ' + esc(t.title) + '</div>';
    var sentAt = (t.created || '').slice(0, 16) || '—';
    var backAt = t.returned ? t.returned.slice(0, 16) : '—';
    h += '<div class="box"><div class="k">派单 / 收单</div><div class="v">' +
      '主理人派单 → ' + esc(r.name) + '收单 · 派发 ' + esc(sentAt) + '<br>' +
      esc(r.name) + '交单 → 主理人收单 · 回件 ' + esc(backAt) + '<br>' +
      '<span style="opacity:.75">' + esc(r.name) + ' · ' + esc(r.role) + '</span></div></div>';
    h += '<div class="box"><div class="k">回件摘要</div><div class="v">' + esc(t.summary || '该工单暂无回件。') + '</div></div>';
    if (t.points && t.points.length) {
      h += '<div class="box"><div class="k">要点</div><div class="v"><ul style="margin:0;padding-left:18px">';
      t.points.forEach(function (x) { h += '<li>' + esc(x) + '</li>'; });
      h += '</ul></div></div>';
    }
    h += '<div class="actions">';
    if (!done && (IS_DEMO || realSignable)) {
      h += '<button class="btn dng" data-act="reject" data-tk="' + esc(t.id) + '">✗ 驳回</button>' +
        '<button class="btn ok" data-act="sign" data-tk="' + esc(t.id) + '">✓ 签收</button>';
    }
    h += '<button class="btn" data-act="close-modal">关闭</button></div>';
    openModal(h);
  }

  function openSign(tid) {
    var t = TK(tid); if (!t) return;
    if (!IS_DEMO && t.status !== 'cp-v') { toast('该工单当前状态为「' + (ST[t.status] ? ST[t.status].text : t.status) + '」，只有「待验」可签收'); return; }
    var r = ROLE(t.role);
    var h = '<div class="h">✓ 签收工单 <span class="badge sign">5 项检查</span></div>';
    h += '<div class="sub">请在确认前核对以下模块，确保回件符合要求</div>';
    h += '<div class="box"><div class="k">① 任务</div><div class="v">' + esc(t.title) + '</div></div>';
    h += '<div class="box"><div class="k">② 回件摘要</div><div class="v">' + esc(t.summary || '—') + '</div></div>';
    h += '<div class="box"><div class="k">③ 签收依据</div><div class="v">结构与任务要求逐条对应，关键页面与旅程路径均已覆盖。</div></div>';
    var gates = (t.gates && t.gates.length) ? t.gates : [];
    if (gates.length) {
      h += '<div class="gates"><div class="k">④ 门禁（需全部勾选）</div>';
      gates.forEach(function (g, i) {
        h += '<label><input type="checkbox" data-gate="' + i + '"><span>' + esc(g) + '</span></label>';
      });
      h += '</div>';
    } else {
      h += '<div class="hint-gate bad">⚠ 本单没有门禁记录（可能是旧数据），不能签收；请联系主理人补录后再试</div>';
    }
    /* R7 出门条三勾（软提醒）：复用 /api/state 的同一份 skills.tripcard 配置，前端不复制关键词表。
       门禁判据不动；三勾用 data-trip 属性，不参与 gateOk 启用。命中判定在异步 loadState 后回填。 */
    h += '<div id="tripBox" class="tripbox" hidden></div>';
    h += '<div class="box"><div class="k">⑤ 派单 / 收单</div><div class="v">' +
      '主理人派单 → ' + esc(r.name) + '收单 · 派发 ' + esc((t.created || '').slice(0, 16) || '—') + '<br>' +
      esc(r.name) + '交单 → 主理人签收 · 回件 ' + esc(t.returned ? t.returned.slice(0, 16) : '—') + '<br>' +
      '<span style="opacity:.75">' + esc(r.role) + ' · ' + esc(modelLabel(r)) + '</span></div></div>';
    h += '<div class="hint-gate" id="gateHint">' + (gates.length ? ('⚠ 门禁全部勾选后确认按钮才会激活（0 / ' + gates.length + '）') : '⚠ 缺门禁记录，无法签收') + '</div>';
    h += '<div class="actions"><button class="btn" data-act="close-modal">取消</button>' +
      '<button class="btn ok" id="gateOk" data-act="do-sign" data-tk="' + esc(t.id) + '" disabled>✓ 确认签收</button></div>';
    openModal(h);
    /* R7：命中判定复用后端下发的同一份 keywords（标题+备注+下一步任一命中）。
       无 skills 字段 / enabled=false / 未命中 → 三勾不显示；异常一律静默降级，不阻塞签收。 */
    api.loadState().then(function (s) {
      var tc = (s && s.skills && s.skills.tripcard) || null;
      if (!tc || tc.enabled === false || !tc.keywords || !tc.keywords.length) return;
      var text = ((t.title || '') + ' ' + (t.note || '') + ' ' + (t.next_action || '')).toLowerCase();
      var hit = tc.keywords.some(function (k) { return text.indexOf(String(k).toLowerCase()) >= 0; });
      if (!hit) return;
      var box = el('tripBox'); if (!box) return;
      box.innerHTML = '<div class="k">出门条（软提醒，不勾也能签）</div>' +
        '<label><input type="checkbox" data-trip="0"><span>每个框都说得出出处（文件 + 行号）</span></label>' +
        '<label><input type="checkbox" data-trip="1"><span>没有把「只是配了」画成「在跑」</span></label>' +
        '<label><input type="checkbox" data-trip="2"><span>有人真的用眼睛看过图</span></label>';
      box.hidden = false;
      box.setAttribute('data-trip-hit', '1');
    }).catch(function () { /* 配置缺失/异常：不显示三勾，照常签收 */ });
  }

  function openReject(tid) {
    var t = TK(tid); if (!t) return;
    if (t.status === 'cp-x') { toast('该工单已签收，不能再驳回'); return; }
    var h = '<div class="h">✗ 驳回工单 <span class="cp cp-d">待签收</span></div>';
    h += '<div class="sub">#' + esc(t.id) + ' · ' + esc(t.title) + '</div>';
    h += '<div class="box"><div class="k">驳回原因（必填，会写回工单时间线）</div>';
    h += '<textarea class="reason" id="rejectReason" placeholder="例如：缺少移动端尺寸标注，无法直接评审"></textarea></div>';
    h += '<div class="hint-gate">⚠ 驳回后工单退回「待处理」，由派发器重新派发（原因会写回工单时间线）</div>';
    h += '<div class="actions"><button class="btn" data-act="close-modal">取消</button>' +
      '<button class="btn dng" data-act="do-reject" data-tk="' + esc(t.id) + '">确认驳回</button></div>';
    openModal(h);
  }

  function openRetry(tid) {
    var t = TK(tid); if (!t) return;
    if (t.status !== 'cp-f') { toast('仅「失败」状态的工单可重新派发'); return; }
    var h = '<div class="h">↻ 重新派发 <span class="cp cp-f">失败</span></div>';
    h += '<div class="sub">#' + esc(t.id) + ' · ' + esc(t.title) + '</div>';
    h += '<div class="hint-gate">⚠ 确认后工单退回「待处理」，由派发器重新派发（旧回件摘要保留在时间线）</div>';
    h += '<div class="actions"><button class="btn" data-act="close-modal">取消</button>' +
      '<button class="btn pri" data-act="do-retry" data-tk="' + esc(t.id) + '">确认重新派发</button></div>';
    openModal(h);
  }

  function openHistory() {
    var thread = threadFor(state.pid, state.chatRole);
    var h = '<div class="h">🕐 历史记录 <span class="badge doing">' + esc(PJ(state.pid).name) + '</span></div>';
    h += '<div class="search-box"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">' +
      '<circle cx="11" cy="11" r="7"/><path d="M20 20l-3.5-3.5"/></svg>' +
      '<input id="histSearch" placeholder="搜索消息 / 工单号 / 关键词" autocomplete="off"></div>';
    h += '<div class="hist-list" id="histList">';
    h += histItems(thread, '');
    h += '</div>';
    h += '<div class="actions"><button class="btn" data-act="close-modal">关闭</button></div>';
    openModal(h, true);
  }
  function histItems(thread, kw) {
    var out = ''; var n = 0;
    thread.forEach(function (m) {
      var text = m.text || (m.title || '') + ' ' + (m.sum || '');
      var tk = m.tk || '';
      if (kw && (text + tk).toLowerCase().indexOf(kw.toLowerCase()) < 0) return;
      n++;
      out += '<div class="hist-item" data-act="locate" data-tk="' + esc(tk) + '">' +
        '<span class="ico">' + (m.type === 'me' ? MODEL.me.short : ((m.who && m.who.short) || '·')) + '</span>' +
        '<span class="bd"><span class="tt">' + esc(text.slice(0, 46)) + '</span>' +
        '<span class="mt">' + (m.type === 'sys' ? '系统' : esc(m.who ? m.who.name : '')) + ' · ' + esc(m.time || '—') +
        (tk ? ' · #' + esc(tk) : '') + '</span></span></div>';
    });
    if (!n) out = '<div class="empty-inline"><div class="t">没有匹配的记录</div><div>换个关键词再试。</div></div>';
    return out;
  }

  /* ================= 动作 ================= */
  function reloadAll() {
    // 重新拉取 /api/state 再赋值，确保签收/归档等写操作后在 UI 上即时反映
    return api.loadState().then(function () {
      return Promise.all([api.getProjects(), api.getRoles(), api.getTickets(), api.getStats()]);
    }).then(function (r) {
      MODEL.projects = r[0]; MODEL.roles = r[1]; MODEL.tickets = r[2]; MODEL.stats = r[3];
    });
  }

  function doSign(tid) {
    if (IS_DEMO) {
      var t = TK(tid); if (!t) return;
      t.status = 'cp-x'; t.signed = true; t.signedAt = now();
      t.timeline.push({ t: '已签收 · 状态更新为「已验」', ts: now(), k: 's4' });
      closeModal(); render(); toast('已签收 · 工单 #' + t.id + ' 状态更新为「已验」');
      return;
    }
    api.getTicket(tid).then(function (t) {
      var gate = (t.gates && t.gates[0]) || '';
      /* R7：命中出门条的单，把勾选数拼到 note 前；未命中保持原样（空 note）。 */
      var note = '';
      var box = el('tripBox');
      if (box && box.getAttribute('data-trip-hit') === '1') {
        var n = 0;
        var bs = document.querySelectorAll('#tripBox input[data-trip]');
        for (var i = 0; i < bs.length; i++) if (bs[i].checked) n++;
        note = '[出门条 ' + n + '/3] ';
      }
      return api.signoff(tid, gate, '主理人', note).then(function () {
        toast('已签收 · #' + tid + ' 状态更新为「已验」');
        closeModal();
        return reloadAll().then(render);
      }).catch(function (e) {
        if (e.status === 409) { toast('该工单已被其他人处理，正在刷新'); closeModal(); return reloadAll().then(render); }
        if (e.status === 403) toast('无权签收：signer 不在允许名单');
        else if (e.status === 400) toast('门禁不一致：' + (e.message || ''));
        else if (e.status === 0) toast('网络错误，签收失败');
        else toast('签收失败：' + (e.message || e.status));
      });
    }).catch(function () { toast('读取工单失败'); });
  }

  function doReject(tid) {
    if (IS_DEMO) {
      var t = TK(tid); if (!t) return;
      var box = el('rejectReason');
      var reason = box ? box.value.trim() : '';
      if (!reason) { if (box) { box.focus(); box.style.borderColor = 'var(--danger)'; } toast('请填写驳回原因'); return; }
      t.status = 'cp-p'; t.failReason = '';
      t.timeline.push({ t: '已驳回 · 退回待处理 · ' + reason, ts: now(), k: '' });
      closeModal(); render(); toast('已驳回 · 工单 #' + t.id + ' 退回「待处理」');
      return;
    }
    var box = el('rejectReason');
    var reason = box ? box.value.trim() : '';
    if (!reason) { if (box) { box.focus(); box.style.borderColor = 'var(--danger)'; } toast('请填写驳回原因（必填，≤200 字）'); return; }
    api.reject(tid, reason, '主理人').then(function () {
      toast('已驳回 · #' + tid + ' 退回「待处理」，派发器将重新派发');
      closeModal(); return reloadAll();
    }).then(function () { render(); })
      .catch(function (e) {
        if (e.status === 409) toast('该工单当前状态不允许驳回（仅「待验」可驳回）');
        else if (e.status === 400) toast('驳回失败：' + (e.message || '参数错误'));
        else toast('驳回失败：' + (e.message || e.status || e));
        render();
      });
  }

  function send() {
    var input = el('input'); if (!input) return;
    var text = input.value.trim();
    if (!text) { toast('先写点什么再发送'); input.focus(); return; }
    var key = state.pid + ':' + state.chatRole;
    delete state.drafts[key];
    var who = { name: MODEL.me.name, short: MODEL.me.short, role: MODEL.me.role };
    var attIds = (state.pendingAtts || []).map(function (a) { return a.id; }).filter(Boolean);
    var um = { type: 'me', who: who, time: now(), text: text };
    if (attIds.length) um.attList = state.pendingAtts.map(function (a) { return { id: a.id, name: a.name, size: a.size, mime: a.mime }; });

    function clearDraftState() {
      state.pendingAtts = []; state.mentions = [];
      paintPendingAtts(); paintMentionChips();
    }

    if (IS_DEMO) {
      var thread = threadFor(state.pid, state.chatRole);
      var ctx = ROLE(state.chatRole);
      thread.push(um);
      input.value = '';
      var c = el('counter'); if (c) c.textContent = '0 / 2000';
      clearDraftState();
      var feed = el('feed');
      if (feed) { feed.insertAdjacentHTML('beforeend', msgHTML(um)); feed.scrollTop = feed.scrollHeight; }
      setTimeout(function () {
        thread.push({ type: 'ai', who: ctx, time: now(), text: '收到，我先把这条拆成子任务，完成后在这个窗口回件。' });
        if (state.view === 'chat' && state.pid + ':' + state.chatRole === key) {
          var f = el('feed'); if (f) { f.insertAdjacentHTML('beforeend', msgHTML(thread[thread.length - 1])); f.scrollTop = f.scrollHeight; }
        }
      }, 700);
      return;
    }

    // 真实模式
    input.value = '';
    var c2 = el('counter'); if (c2) c2.textContent = '0 / 2000';
    // 先把乐观消息插进 feed，但保留节点引用；发送失败可撤回，避免“假成功”
    var feed2 = el('feed');
    var optNodes = [];
    if (feed2) {
      var _tmp = document.createElement('div'); _tmp.innerHTML = msgHTML(um);
      while (_tmp.firstChild) { optNodes.push(_tmp.firstChild); feed2.appendChild(_tmp.firstChild); }
      feed2.scrollTop = feed2.scrollHeight;
    }
    toast('发送中…');
    var pid = state.pid, rid = state.chatRole, tkey = threadKey(pid, rid);
    // 记录发送前的 AI 回件数：轮询时只认“新增的回件”，避免旧回件让轮询提前停
    var baseAi = countReplies(MODEL.threads[tkey] || []);
    var mentions = (state.mentions || []).slice();
    clearDraftState();
    function undoOpt() { optNodes.forEach(function (n) { if (n && n.parentNode) n.parentNode.removeChild(n); }); }
    function onOk() { return pollThread((rid === '__group__') ? pid : rid, rid, tkey, baseAi); }
    if (rid === '__group__') {
      // 无 @ 时不通知任何 AI，也就不必等回件：发送成功后只刷新群消息
      var waitAi = !!(mentions && mentions.length);
      api.sendProjectMessage(pid, text, mentions, attIds)
        .then(function () {
          if (waitAi) return onOk();
          return api.getProjectMessages(pid).then(function (ms) { MODEL.threads[tkey] = ms; render(); });
        })
        .catch(function (e) { undoOpt(); toast('发送失败：' + (e.message || e.status || e)); });
    } else {
      api.sendChat(rid, text, attIds)
        .then(onOk)
        .catch(function (e) { undoOpt(); toast('发送失败：' + (e.message || e.status || e)); });
    }
  }

  function pollThread(fetchPid, fetchRid, tkey, baseAi) {
    var tries = 0, max = 24, delay = 5000;
    function tick() {
      if (tries >= max) { toast('仍未收到回件，可点「刷新」再查看'); return; }
      tries++;
      var p = (fetchRid === '__group__') ? api.getProjectMessages(fetchPid) : api.getChatHistory(fetchRid);
      p.then(function (ms) {
        MODEL.threads[tkey] = ms;
        if (state.view === 'chat' && threadKey(state.pid, state.chatRole) === tkey) renderFeedOnly(ms);
        if (countReplies(ms) > baseAi) { toast('回件已到'); return; }
        setTimeout(tick, delay);
      }).catch(function () { /* 停止轮询 */ });
    }
    setTimeout(tick, 1500);
  }
  function countReplies(ms) {
    var n = 0;
    for (var i = 0; i < ms.length; i++) if (ms[i].type === 'ai' || ms[i].type === 'card') n++;
    return n;
  }
  function renderFeedOnly(ms) {
    var feed = el('feed'); if (!feed) return;
    if (!ms.length) { feed.innerHTML = emptyInline('这个岗位还没有对话', '发第一条消息启动协作。'); return; }
    var h = ''; ms.forEach(function (m) { h += msgHTML(m); });
    feed.innerHTML = h; feed.scrollTop = feed.scrollHeight;
  }

  function selectProject(pid) {
    state.pid = pid; state.filter = 'all'; state.tid = null;
    state.chatRole = IS_DEMO ? state.chatRole : '__group__';
    ensureThread(state.pid, state.chatRole).then(function () { go('chat'); });
  }

  function go(view) {
    var target = view === 'chat' ? '#/chat' : '#/' + view;
    if (location.hash === target) route(); else location.hash = target;
  }

  function render() {
    if (state.error && state.view === 'error') { el('main').innerHTML = renderError(); return; }
    renderSide();
    var html = '';
    if (state.view === 'ticket') html = renderTicket();
    else if (state.view === 'inbox') html = renderInbox();
    else if (state.view === 'members') html = renderMembers();
    else if (state.view === 'projects') html = renderProjects();
    else if (state.view === 'states') html = renderStates();
    else html = renderChat();
    el('main').innerHTML = html;
    var feed = el('feed'); if (feed) feed.scrollTop = feed.scrollHeight;
    if (state.view === 'chat') { paintPendingAtts(); paintMentionChips(); }
  }

  /* ================= 路由 ================= */
  function route() {
    var h = location.hash.replace(/^#\/?/, '');
    var parts = h.split('/');
    var view = parts[0] || 'chat';
    var known = { chat: 1, ticket: 1, inbox: 1, members: 1, projects: 1, states: 1 };
    if (!known[view]) view = 'chat';
    state.view = view;
    if (view === 'ticket') {
      state.tid = decodeURIComponent(parts[1] || '');
      if (!TK(state.tid)) { state.view = 'chat'; }
      // 真实模式：详情页首次进入补拉 events（时间线/签收页需要）
      var t0 = TK(state.tid);
      if (!IS_DEMO && t0 && !t0._full) {
        api.getTicket(state.tid).then(function (full) {
          full._full = true; full.pid = full.pid || t0.pid;
          for (var i = 0; i < MODEL.tickets.length; i++) if (MODEL.tickets[i].id === full.id) MODEL.tickets[i] = full;
          render();
        }).catch(function () { toast('工单详情加载失败'); render(); });
        return;
      }
    }
    render();
  }

  /* ================= 事件 ================= */
  document.addEventListener('click', function (e) {
    var t; if (!e.target || !e.target.closest) return;
    if ((t = e.target.closest('[data-act]'))) {
      var act = t.getAttribute('data-act');
      var tk = t.getAttribute('data-tk');
      switch (act) {
        case 'send': send(); break;
        case 'hist': openHistory(); break;
        case 'attach': { var fi = el('fileInput'); if (fi) fi.click(); else toast('当前视图不支持附件'); break; }
        case 'mention': openMentionPicker(); break;
        case 'do-mention': doMention(); break;
        case 'rm-att': {
          var ai = parseInt(t.getAttribute('data-idx'), 10);
          if (!isNaN(ai) && state.pendingAtts[ai]) { state.pendingAtts.splice(ai, 1); paintPendingAtts(); }
          break;
        }
        case 'rm-mention': {
          var mi = parseInt(t.getAttribute('data-idx'), 10);
          if (!isNaN(mi) && state.mentions[mi]) { state.mentions.splice(mi, 1); paintMentionChips(); }
          break;
        }
        case 'do-new-pj': doNewProject(); break;
        case 'do-new-role': doNewRole(); break;
        case 'manage-members': openMemberManager(t.getAttribute('data-pjid')); break;
        case 'do-members': doMemberSave(); break;
        case 'sign': openSign(tk); break;
        case 'reject': openReject(tk); break;
        case 'do-sign': doSign(tk); break;
        case 'do-reject': doReject(tk); break;
        case 'close-modal': closeModal(); break;
        case 'toggle-pj': state.pjOpen = !state.pjOpen; renderSide(); break;
        case 'open-ticket': openTicketModal(tk); break;
        case 'bot-refresh': toast('Bot 信息已刷新 · ' + now()); break;
        case 'back': closeModal(); go('chat'); break;
        case 'pause':
          var p = PJ(state.pid);
          if (IS_DEMO) { p.status = p.status === 'paused' ? 'active' : 'paused'; render(); toast(p.status === 'paused' ? '已暂停派发 · ' + p.name : '已恢复派发 · ' + p.name); break; }
          api.setProjectState(p.id, p.status === 'paused' ? 'resume' : 'pause')
            .then(function () { return reloadAll(); }).then(function () { render(); toast(p.status === 'paused' ? '已恢复派发 · ' + p.name : '已暂停派发 · ' + p.name); })
            .catch(function (e) { toast('操作失败：' + (e.message || e.status)); });
          break;
        case 'snooze':
          if (state.snoozed[tk]) { delete state.snoozed[tk]; toast('已取消稍后提醒'); } else { state.snoozed[tk] = 1; toast('已稍后提醒 · 30 分钟后再次提示'); }
          render(); break;
        case 'toggle-role':
          var r = ROLE(t.getAttribute('data-role'));
          if (IS_DEMO) { r.status = r.status === 'on' ? 'off' : 'on'; state.roleId = r.id; render(); toast(r.name + (r.status === 'on' ? ' 已启用' : ' 已停用')); break; }
          toast('启停按钮下一批提供（只读展示）'); break;
        case 'dispatch':
          state.chatRole = t.getAttribute('data-role');
          toast('已切到 ' + ROLE(state.chatRole).name + ' 的独立窗口 · 可派发工单');
          ensureThread(state.pid, state.chatRole).then(function () { go('chat'); });
          break;
        case 'new-role': openNewRole(); break;
        case 'new-pj': openNewProject(); break;
        case 'archive':
          var ap = PJ(t.getAttribute('data-pjid'));
          if (IS_DEMO) { ap.status = 'archived'; ap.archivedAt = '2026-11-13'; ap.archiveNote = '手动归档'; render(); toast('「' + ap.name + '」已归档 · 可在归档页恢复'); break; }
          api.archiveProject(ap.id, true).then(function () { return reloadAll(); }).then(function () { render(); toast('「' + ap.name + '」已归档'); })
            .catch(function (e) { toast('归档失败：' + (e.message || e.status)); });
          break;
        case 'restore':
          var rp = PJ(t.getAttribute('data-pjid'));
          if (IS_DEMO) { rp.status = 'active'; rp.archivedAt = ''; state.pjTab = 'active'; state.pjSel = rp.id; render(); toast('「' + rp.name + '」已恢复至进行中'); break; }
          api.archiveProject(rp.id, false).then(function () { return reloadAll(); }).then(function () { render(); toast('「' + rp.name + '」已恢复'); })
            .catch(function (e) { toast('恢复失败：' + (e.message || e.status)); });
          break;
        case 'open-pj':
          var op = t.getAttribute('data-pjid'); state.pid = op; state.filter = 'all';
          state.chatRole = IS_DEMO ? state.chatRole : '__group__';
          ensureThread(op, state.chatRole).then(function () { go('chat'); });
          toast('已切换到「' + PJ(op).name + '」项目群'); break;
        case 'retry':
          openRetry(tk);
          break;
        case 'do-retry':
          doRetry(tk);
          break;
        case 'retry-net':
          if (IS_DEMO) { toast('演示版：未连接真实本地服务'); break; }
          boot(); break;
        case 'start-svc': toast('演示版：未连接真实本地服务'); break;
        case 'help': toast('演示版：帮助文档未接入'); break;
        case 'clear-search': toast('已清除搜索条件'); break;
        case 'demo-fallback': IS_DEMO = true; state.error = null; render(); toast('已切换到演示模式'); break;
        case 'locate':
          closeModal(); if (tk) { openTicketModal(tk); } else { toast('已定位到该消息'); } break;
      }
      return;
    }
    if ((t = e.target.closest('[data-tk]'))) {
      var tid = t.getAttribute('data-tk');
      if (state.view === 'chat' && TK(tid)) { openTicketModal(tid); }
      else { location.hash = '#/ticket/' + tid; }
      return;
    }
    if ((t = e.target.closest('[data-pjsel]'))) { state.pjSel = t.getAttribute('data-pjsel'); render(); return; }
    if ((t = e.target.closest('[data-pj]'))) { selectProject(t.getAttribute('data-pj')); return; }
    if ((t = e.target.closest('[data-role]'))) { state.roleId = t.getAttribute('data-role'); render(); return; }
    if ((t = e.target.closest('[data-pjtab]'))) { state.pjTab = t.getAttribute('data-pjtab'); render(); return; }
    if ((t = e.target.closest('[data-stab]'))) { state.stTab = t.getAttribute('data-stab'); render(); return; }
    if ((t = e.target.closest('[data-sw]'))) {
      t.classList.toggle('on'); t.setAttribute('aria-checked', t.classList.contains('on') ? 'true' : 'false');
      if (t.id !== 'nrEn') toast('提醒设置已更新（仅本页演示）');
      return;
    }
    if ((t = e.target.closest('[data-filter]'))) {
      var f = t.getAttribute('data-filter');
      state.filter = (f === 'all' || state.filter === f) ? 'all' : f;
      render(); return;
    }
    if ((t = e.target.closest('[data-view]'))) { go(t.getAttribute('data-view')); return; }
  });

  document.addEventListener('change', function (e) {
    var t = e.target;
    if (t.id === 'pjPicker') {
      var cur0 = el('input'); if (cur0) state.drafts[state.pid + ':' + state.chatRole] = cur0.value;
      var np = t.value; state.pid = np; state.filter = 'all'; state.tid = null;
      ensureThread(np, state.chatRole).then(render); return;
    }
    if (t.id === 'rolePicker') {
      var cur1 = el('input'); if (cur1) state.drafts[state.pid + ':' + state.chatRole] = cur1.value;
      state.chatRole = t.value; state.tid = null; state.filter = 'all';
      ensureThread(state.pid, state.chatRole).then(render); return;
    }
    if (t.id === 'fileInput') { handleFileSelect(t.files); t.value = ''; return; }
    if (t.hasAttribute && t.hasAttribute('data-model')) {
      var mr = ROLE(t.getAttribute('data-model'));
      var newModel = t.value;
      if (!IS_DEMO) {
        api.setAdapterModel(mr.id, newModel).then(function () {
          return reloadAll();
        }).then(function () { render(); toast(mr.name + ' 模型已切换 · ' + newModel); })
          .catch(function (e) { toast('模型切换失败：' + (e.message || e.status || e)); render(); });
        return;
      }
      if (mr) { mr.model = newModel; render(); toast(mr.name + ' 模型已切换 · ' + modelLabel(mr)); }
      return;
    }
    if (t.hasAttribute && t.hasAttribute('data-gate')) {
      var ok = el('gateOk'); if (!ok) return;
      var boxes = document.querySelectorAll('.gates input[data-gate]');
      var n = 0; boxes.forEach(function (b) { if (b.checked) n++; });
      var done = boxes.length > 0 && n === boxes.length;
      ok.disabled = !done;
      var hint = el('gateHint');
      if (hint) { hint.className = 'hint-gate' + (done ? ' ok' : ''); hint.textContent = done ? '✓ 门禁已全部勾选，可确认签收' : '⚠ 门禁需全部勾选（' + n + ' / ' + boxes.length + '）后确认按钮才会激活'; }
    }
  });

  document.addEventListener('input', function (e) {
    var t = e.target;
    if (t.id === 'input') {
      var c = el('counter'); if (c) c.textContent = t.value.length + ' / 2000';
      state.drafts[state.pid + ':' + state.chatRole] = t.value;
    }
    if (t.id === 'histSearch') {
      var list = el('histList');
      if (list) list.innerHTML = histItems(threadFor(state.pid, state.chatRole), t.value.trim());
    }
  });

  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') { closeModal(); return; }
    if (e.target.id === 'input' && e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); return; }
    if (e.target.getAttribute && e.target.getAttribute('data-sw') && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); e.target.classList.toggle('on'); }
  });

  window.addEventListener('hashchange', route);

  /* ================= 启动 ================= */
  function boot() {
    if (IS_DEMO) { render(); return; }
    if (!TOKEN) { state.error = '未从启动器获得 token。请从「AI聊不停 启动器」打开本页面（URL 需带 ?token=）。'; state.view = 'error'; render(); return; }
    api.health().then(function () { return api.loadState(); }).then(function () {
      return Promise.all([api.getProjects(), api.getRoles(), api.getTickets(), api.getStats()]);
    }).then(function (r) {
      MODEL.projects = r[0]; MODEL.roles = r[1]; MODEL.tickets = r[2]; MODEL.stats = r[3];
      // BUGFIX: PJ() 永远返回兜底对象，不能当存在性判断；真实项目 id 不是 p1
      var ids = MODEL.projects.map(function (p) { return p.id; });
      if (ids.indexOf(state.pid) < 0) state.pid = ids[0] || '';
      if (ids.indexOf(state.pjSel) < 0) state.pjSel = state.pid;
      if (!state.pid) { state.view = 'projects'; if (!location.hash) location.hash = '#/projects'; render(); return; }
      if (state.chatRole === 'r2' || !state.chatRole) state.chatRole = '__group__';
      return ensureThread(state.pid, state.chatRole);
    }).then(function () {
      // BUGFIX: 首屏必须按 URL hash 路由（深链 #/members、#/projects、#/ticket 之前被忽略）
      route();
    }).catch(function (e) {
      var msg = '本地服务连接失败';
      if (e && e.status === 0) msg = '网络错误，无法连接本地服务（127.0.0.1）。请确认服务已启动。';
      else if (e && e.status === 403) msg = '未授权：请从「AI聊不停 启动器」打开（token 无效）。';
      else if (e && e.message) msg = e.message;
      state.error = msg; state.view = 'error'; render();
    });
  }

  /* ================= 自动刷新（v2.3，每 60s） ================= */
  // 老板要求：工单面板与左侧项目计数每 1 分钟自动刷新。
  // 保护：页面不可见/弹窗打开/输入框有草稿或聚焦时跳过，避免打断正在输入的消息。
  var autoTimer = null;
  var autoBusy = false;
  function autoRefresh() {
    if (autoBusy || document.hidden || !TOKEN) return;
    if (state.error && state.view === 'error') return;
    var m = el('modal');
    if (m && m.classList.contains('open')) return;
    var inp = el('input');
    if (inp && (document.activeElement === inp || (inp.value || '').trim())) return;
    autoBusy = true;
    reloadAll().then(function () {
      if (state.view !== 'error') render();
    }).catch(function () {
      /* 自动刷新失败静默，不打扰；下一轮继续 */
    }).then(function () {
      autoBusy = false;
    });
  }
  function startAutoRefresh() {
    if (autoTimer) clearInterval(autoTimer);
    autoTimer = setInterval(autoRefresh, 60000);
  }

  if (!location.hash) location.hash = '#/chat';
  boot();
  startAutoRefresh();
  startRailRefresh();
})();
