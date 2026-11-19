/* ===================================================================
   AI聊不停 · 适配层 api.js（Phase 1）
   -------------------------------------------------------------------
   暴露 window.createApi(mode, opts)：
     mode='demo'  → 包装传入的演示数据（零网络，file:// 或 ?demo=1）
     mode='real'  → 调真实后端（X-Deck-Token，token 由启动器注入 ?token=）
   所有方法均返回 Promise；real 失败抛出带 .status 的 Error。

   映射口径见任务书 §5：ROLES←state.adapters / PROJECTS←state.projects /
   TICKETS←state.tickets / THREADS 群聊←messages / 1:1←chat.history。
   =================================================================== */
(function () {
  'use strict';

  function resolve(v) { return Promise.resolve(v); }
  function P() { return Promise; }

  /* ---- 状态中文 ↔ 前端状态码 ---- */
  var ZH2CODE = { '待处理': 'cp-p', '派发中': 'cp-d', '已回件': 'cp-r', '待验': 'cp-v', '已验': 'cp-x', '失败': 'cp-f' };
  var CODE2ZH = { 'cp-p': '待处理', 'cp-d': '派发中', 'cp-r': '已回件', 'cp-v': '待验', 'cp-x': '已验', 'cp-f': '失败' };
  function statusCode(zh) { return ZH2CODE[zh] || 'cp-p'; }
  function codeZh(code) { return CODE2ZH[code] || code; }

  function fmtDate(iso) {
    if (!iso) return '';
    var s = String(iso).replace('T', ' ');
    return s.slice(0, 10);
  }
  function fmtDateTime(iso) {
    if (!iso) return '';
    var s = String(iso).replace('T', ' ');
    return s.slice(0, 16);
  }
  function hhmm(iso) {
    if (!iso) return '';
    var s = String(iso).replace('T', ' ');
    return s.slice(11, 16) || s.slice(5, 16);
  }
  function fmtSize(n) {
    if (!n && n !== 0) return '';
    n = Number(n) || 0;
    if (n >= 1048576) return (n / 1048576).toFixed(1) + ' MB';
    if (n >= 1024) return (n / 1024).toFixed(0) + ' KB';
    return n + ' B';
  }
  function hash1(s) {
    var h = 0; s = String(s || '');
    for (var i = 0; i < s.length; i++) { h = (h * 31 + s.charCodeAt(i)) | 0; }
    return Math.abs(h);
  }
  function extOf(name) {
    var m = /\.([a-z0-9]+)$/i.exec(name || '');
    return m ? m[1].slice(0, 3).toUpperCase() : '附件';
  }

  /* ---- 系统事件文案 ---- */
  var EV_LABEL = {
    'intake_seen': '派单台收到工单', 'intake': '创建工单',
    'dispatch_intent': '准备派单', 'dispatch_ok': '已派单', 'reply_seen': '已回件',
    'gate_passed': '门禁已签', 'dispatch_fail': '派单失败', 'emergency_stop': '急停',
    'model_switch': '切换模型', 'status': '状态更新', 'tick': '派发器心跳'
  };
  function evText(e) {
    var base = EV_LABEL[e.kind] || '状态更新';
    if (e.kind === 'gate_passed') return '门禁已签' + (e.detail ? ' · ' + e.detail : '');
    if (e.detail) return base + ' · ' + e.detail;
    return base;
  }

  /* ============================================================
     DEMO 实现：包装传入的演示数据，保持 ?demo=1 与原型一致
     ============================================================ */
  function createDemo(deps) {
    var D = deps.demo || {};
    var ME = D.ME || { name: '林舟', short: '林', role: '产品经理 · Admin' };
    var projects = D.PROJECTS || [];
    var roles = D.ROLES || [];
    var tickets = D.TICKETS || [];
    var threads = D.THREADS || {};   // 按引用共享，send 可回写
    var seq = 1;

    function roleById(id) { for (var i = 0; i < roles.length; i++) if (roles[i].id === id) return roles[i]; return roles[0]; }
    function projById(id) { for (var i = 0; i < projects.length; i++) if (projects[i].id === id) return projects[i]; return projects[0]; }
    function tickById(id) { for (var i = 0; i < tickets.length; i++) if (tickets[i].id === id) return tickets[i]; return null; }
    function threadOf(pid, rid) { var k = pid + ':' + rid; if (!threads[k]) threads[k] = []; return threads[k]; }

    return {
      mode: 'demo',
      health: function () { return resolve({ ok: true }); },
      loadState: function () { return resolve({}); },
      getMe: function () { return resolve(ME); },
      getProjects: function () { return resolve(projects.slice()); },
      getProject: function (id) { return resolve(projById(id)); },
      getRoles: function () { return resolve(roles.slice()); },
      getTickets: function () { return resolve(tickets.slice()); },
      getStats: function () {
        var s = { 'cp-p': 0, 'cp-d': 0, 'cp-r': 0, 'cp-v': 0, 'cp-x': 0, 'cp-f': 0 };
        tickets.forEach(function (t) { if (s[t.status] != null) s[t.status]++; });
        return resolve(s);
      },
      // demo 群聊即 prototype 的 project:role 线程（按引用返回，send 直接回写）
      getProjectMessages: function (pid, rid) { return resolve(threadOf(pid, rid || 'r2')); },
      getChatHistory: function (target) {
        var rid = target.indexOf('__global__:') === 0 ? target.slice('__global__:'.length) : target;
        var parts = rid.split(':');
        return resolve(threadOf(parts[0], parts[1] || 'r1'));
      },
      sendProjectMessage: function (pid, content, mentions, by) {
        var t = threadOf(pid, by || 'r2');
        t.push({ type: 'me', who: ME, time: nowH(), text: content });
        return resolve({ ok: true });
      },
      sendChat: function (target, message, by) {
        var rid = target.indexOf('__global__:') === 0 ? target.slice('__global__:'.length) : target;
        var parts = rid.split(':');
        var t = threadOf(parts[0], parts[1] || 'r1');
        t.push({ type: 'me', who: ME, time: nowH(), text: message });
        return resolve({ ok: true });
      },
      getTicket: function (id) { return resolve(tickById(id)); },
      signoff: function (tid, gate, signer, note) {
        var t = tickById(tid);
        if (!t) return resolve({ ok: false, status: 404, error: '工单不存在' });
        if (t.status === 'cp-x') return resolve({ ok: false, status: 409, error: '该工单已签收' });
        t.status = 'cp-x'; t.signed = true; t.signedAt = nowH();
        t.timeline.push({ t: '已签收 · 状态更新为「已验」', ts: nowH(), k: 's4' });
        return resolve({ ok: true });
      },
      reject: function (tid, reason, signer) {
        var t = tickById(tid);
        if (!t) return resolve({ ok: false, status: 404, error: '工单不存在' });
        t.status = 'cp-p'; t.failReason = '';
        t.timeline.push({ t: '已驳回 · 退回待处理 · ' + (reason || ''), ts: nowH(), k: '' });
        return resolve({ ok: true, status: '待处理' });
      },
      retry: function (tid) {
        var t = tickById(tid);
        if (!t) return resolve({ ok: false, status: 404, error: '工单不存在' });
        t.status = 'cp-p'; t.failReason = '';
        t.timeline.push({ t: '手动重新派发 · 退回待处理', ts: nowH(), k: 's2' });
        return resolve({ ok: true, status: '待处理' });
      },
      setProjectState: function (pid, action) {
        var p = projById(pid); if (!p) return resolve({ ok: false, status: 404 });
        p.status = action === 'pause' ? 'paused' : (action === 'resume' ? 'active' : p.status);
        return resolve({ ok: true });
      },
      archiveProject: function (pid, archived) {
        var p = projById(pid); if (!p) return resolve({ ok: false, status: 404 });
        p.status = archived ? 'archived' : 'active';
        p.archivedAt = archived ? '2026-11-13' : '';
        return resolve({ ok: true });
      },
      renameProject: function (pid, name) {
        var p = projById(pid); if (!p) return resolve({ ok: false, status: 404 });
        p.name = name; return resolve({ ok: true });
      },
      /* ---- v2 新增：演示侧本地变更（仅供 ?demo=1 评审，真实模式走 real 实现） ---- */
      uploadAttachment: function (file) {
        var n = (file && file.size) || 0;
        return resolve({ id: 'att-' + (seq++), name: (file && file.name) || 'file', size: n, mime: (file && file.type) || 'application/octet-stream' });
      },
      attachmentUrl: function (id) { return ''; },
      createProject: function (name, dir) {
        var id = 'p-' + (seq++);
        var p = { id: id, name: name, icon: (name || '?').slice(0, 1), theme: 't' + ((seq % 5) + 1), status: 'active', desc: '', created: nowH(), members: ['LZ'], extra: '', done: 0, signRate: '—', archivedAt: '', archiveNote: '' };
        projects.push(p); return resolve({ ok: true, project: p });
      },
      createAdapter: function (spec) {
        var id = 'ai-' + (seq++);
        var r = { id: id, name: spec.label || id, short: (spec.label || id).slice(0, 1), role: spec.role_id || '', via: spec.type, model: spec.default_model || 'auto', models: spec.default_model ? [spec.default_model] : ['auto'], fixed: '', exe: '', key: spec.key_path ? 'Key 已配置' : '', cli: '', status: 'on', desc: spec.label || '', today: 0, ok: 0, avg: '—' };
        roles.push(r); return resolve({ ok: true, id: id, adapter: r });
      },
      getRoleCatalog: function () {
        return resolve({ layers: [{ roles: [{ id: 'owner', name: '负责人', human_only: false }, { id: 'human', name: '人工中转', human_only: true }] }], rules: [] });
      },
      setAdapterModel: function (id, model) {
        var r = roleById(id); if (r) r.model = model; return resolve({ ok: true });
      },
      addMember: function (pid, aid) {
        var p = projById(pid); if (p && p.members.indexOf(aid) < 0) p.members.push(aid);
        return resolve({ ok: true, project: p });
      },
      removeMember: function (pid, aid) {
        var p = projById(pid); if (p) p.members = p.members.filter(function (x) { return x !== aid; });
        return resolve({ ok: true, project: p });
      },
      createTicket: function (spec) {
        var id = 'T-' + (seq++) + '-demo';
        var t = { id: id, pid: spec.project_id || (projects[0] && projects[0].id), title: spec.title || '未命名', status: 'cp-p', role: spec.executor || '', priority: spec.priority || '—', created: nowH(), returned: '', summary: spec.note || '', detail: '', points: [], origin: spec.note || '', gates: [], timeline: [{ t: '工单创建 · 等待派发', ts: nowH(), k: '' }], atts: [] };
        tickets.push(t); return resolve({ ok: true, ticket: t });
      },
      searchMessages: function (opts) {
        var kw = (opts.q || '').toLowerCase(); var out = [];
        Object.keys(threads).forEach(function (k) {
          threads[k].forEach(function (m) {
            var text = (m.text || (m.title || '') + ' ' + (m.sum || '') + ' ' + (m.tk || '')).toLowerCase();
            if (kw && text.indexOf(kw) < 0) return;
            out.push({ type: m.type, who: m.who ? m.who.short : '·', name: m.who ? m.who.name : '', time: m.time || '', text: m.text || '', ticket_id: m.tk || '', title: m.title || '' });
          });
        });
        return resolve({ items: out });
      }
    };
  }

  function nowH() {
    var d = new Date();
    return ('0' + d.getHours()).slice(-2) + ':' + ('0' + d.getMinutes()).slice(-2);
  }

  /* ============================================================
     REAL 实现：调真实后端
     ============================================================ */
  function createReal(opts) {
    opts = opts || {};
    var TOKEN = opts.token || '';
    var BASE = opts.base || '';
    var cached = { roles: [], projects: [], tickets: [], stats: {} };

    function req(path, method, body) {
      return new Promise(function (res, rej) {
        var xhr = new XMLHttpRequest();
        xhr.open(method || 'GET', BASE + path, true);
        xhr.setRequestHeader('X-Deck-Token', TOKEN);
        xhr.setRequestHeader('Accept', 'application/json');
        if (body !== undefined) xhr.setRequestHeader('Content-Type', 'application/json');
        xhr.onreadystatechange = function () {
          if (xhr.readyState !== 4) return;
          var data = null;
          try { data = xhr.responseText ? JSON.parse(xhr.responseText) : null; } catch (e) { data = null; }
          if (xhr.status >= 200 && xhr.status < 300) { res(data); }
          else {
            var err = new Error((data && data.error) ? data.error : ('HTTP ' + xhr.status));
            err.status = xhr.status;
            err.data = data;
            rej(err);
          }
        };
        xhr.onerror = function () { var e = new Error('网络错误'); e.status = 0; rej(e); };
        xhr.send(body !== undefined ? JSON.stringify(body) : null);
      });
    }

    function roleByAdapter(id) {
      for (var i = 0; i < cached.roles.length; i++) if (cached.roles[i].id === id) return cached.roles[i];
      return { id: id, name: id, short: (id || '?').slice(0, 1), role: '', via: '', model: '', models: [], fixed: '', exe: '', key: '', cli: '', status: 'cfg', desc: id, today: 0, ok: 0, avg: '—' };
    }

    function mapRole(aid, ad) {
      var models = (ad.models || []).map(function (m) { return m.id; });
      return {
        id: aid,
        name: ad.label || aid,
        short: (ad.label || aid).slice(0, 1),
        role: ad.role_id || ad.post || '',
        via: ad.type || 'unknown',
        model: ad.default_model || (models[0] || ''),
        models: models,
        fixed: '',
        exe: ad.hint || '',
        key: ad.ready ? '就绪' : '待配置',
        cli: (ad.type === 'process' || ad.type === 'workbuddy') ? (ad.hint || '') : '',
        status: ad.ready ? 'on' : (ad.enabled ? 'cfg' : 'off'),
        desc: ad.detail || ad.hint || '',
        today: 0, ok: 0, avg: '—'
      };
    }

    function mapProject(p) {
      var st = p.status || 'running';
      var status = (p.archived == 1) ? 'archived' : (st === 'paused' ? 'paused' : 'active');
      return {
        id: p.id, name: p.name,
        icon: (p.name || '?').slice(0, 1),
        theme: 't' + ((hash1(p.id) % 5) + 1),
        status: status,
        desc: '',
        created: fmtDate(p.created_at),
        members: p.members || [],
        extra: '', done: 0, signRate: '—',
        archivedAt: p.archived_at || '', archiveNote: ''
      };
    }

    function mapTicket(t) {
      var code = statusCode(t.status);
      var atts = [];
      try {
        var a = JSON.parse(t.attachments || '[]');
        if (typeof a === 'string') a = [a];
        (a || []).forEach(function (x) {
          if (typeof x === 'string') atts.push([extOf(x), x, '']);
          else atts.push([extOf(x.name || ''), x.name || '', fmtSize(x.size)]);
        });
      } catch (e) {}
      return {
        id: t.id, pid: t.project_id, title: t.title,
        status: code, role: t.executor || '', priority: t.priority || '—',
        created: fmtDateTime(t.created_at),
        returned: (t.updated_at && t.updated_at !== t.created_at) ? fmtDateTime(t.updated_at) : '',
        summary: t.note || '', detail: '', points: [], origin: t.note || '',
        gates: t.gate ? [t.gate] : [],
        timeline: [], atts: atts,
        signed: code === 'cp-x', failed: code === 'cp-f',
        failReason: code === 'cp-f' ? '执行失败' : '', signedAt: ''
      };
    }

    function mapTicketFull(t) {
      var base = mapTicket(t);
      var evs = (t.events || []).map(function (e) {
        return { t: evText(e), ts: hhmm(e.at) || fmtDateTime(e.at), k: e.kind === 'gate_passed' ? 's4' : 's2', signer: e.actor || '', rawKind: e.kind, rawDetail: e.detail || '' };
      });
      base.timeline = evs;
      // 回件摘要：优先取「已回件」事件的 detail，避免详情页摘要空白
      var reply = '';
      for (var i = evs.length - 1; i >= 0; i--) if (evs[i].k === 's3' || (evs[i] && evs[i].rawKind === 'returned') || (evs[i] && evs[i].rawKind === 'reply_seen')) { reply = evs[i].rawDetail || ''; break; }
      if (!reply) { for (var j = evs.length - 1; j >= 0; j--) { if (evs[j].rawKind === 'returned' || evs[j].rawKind === 'reply_seen') { reply = evs[j].rawDetail || ''; break; } } }
      if (reply) { base.summary = reply; base.detail = reply; }
      var so = (t.signoffs || []);
      if (so.length) base.signed = true, base.signedAt = hhmm(so[0].at) || fmtDateTime(so[0].at);
      return base;
    }

    function mapMsg(m) {
      if (m.type === 'system') {
        var txt = m.text || evText(m);
        if (m.ticket_id) txt = '【#' + m.ticket_id + ' ' + (m.title || '') + '】' + txt;
        return { type: 'sys', text: txt, time: fmtDateTime(m.at) || fmtDateTime(m.created_at) || '' };
      }
      var isMe = (m.role === 'user');
      var who = isMe ? { name: '林舟', short: '林', role: '产品经理' } : roleByAdapter(m.adapter);
      var att = '';
      var attList = [];
      if (m.attachments) {
        try {
          var a = JSON.parse(m.attachments);
          if (typeof a === 'string') a = [a];
          (a || []).forEach(function (x) {
            if (typeof x === 'string') { att += (att ? ' · ' : '') + x; }
            else { att += (att ? ' · ' : '') + (x.name || ''); attList.push({ id: x.id, name: x.name || '', size: x.size || 0, mime: x.mime || '' }); }
          });
        } catch (e) {}
      }
      return { type: isMe ? 'me' : 'ai', who: who, time: fmtDateTime(m.at) || fmtDateTime(m.created_at) || '', text: m.content || '', att: att, attList: attList };
    }

    return {
      mode: 'real',
      health: function () { return req('/api/health', 'GET'); },
      loadState: function () {
        return req('/api/state', 'GET').then(function (s) {
          cached.roles = Object.keys(s.adapters || {}).map(function (k) { return mapRole(k, s.adapters[k]); });
          cached.projects = (s.projects || []).map(mapProject);
          cached.tickets = (s.tickets || []).map(mapTicket);
          cached.stats = s.stats || {};
          return s;
        });
      },
      getMe: function () { return resolve({ name: '林舟', short: '林', role: '产品经理 · Admin' }); },
      getProjects: function () { return resolve(cached.projects.slice()); },
      getProject: function (id) {
        for (var i = 0; i < cached.projects.length; i++) if (cached.projects[i].id === id) return resolve(cached.projects[i]);
        return req('/api/projects/' + encodeURIComponent(id), 'GET').then(function (p) { return mapProject(p); });
      },
      getRoles: function () { return resolve(cached.roles.slice()); },
      getTickets: function () { return resolve(cached.tickets.slice()); },
      getStats: function () { return resolve(cached.stats); },
      getProjectMessages: function (pid) {
        return req('/api/projects/' + encodeURIComponent(pid) + '/messages?limit=200', 'GET')
          .then(function (r) { return (r.messages || []).map(mapMsg); });
      },
      getChatHistory: function (target) {
        return req('/api/chat/history?target=' + encodeURIComponent(target), 'GET')
          .then(function (rows) { return (rows || []).map(mapMsg); });
      },
      sendProjectMessage: function (pid, content, mentions, attachment_ids) {
        return req('/api/projects/' + encodeURIComponent(pid) + '/messages', 'POST',
          { content: content, mentions: mentions || [], attachment_ids: attachment_ids || [] });
      },
      sendChat: function (target, message, attachment_ids) {
        return req('/api/chat', 'POST', { target: target, message: message, attachment_ids: attachment_ids || [] });
      },
      getTicket: function (id) {
        return req('/api/tickets/' + encodeURIComponent(id), 'GET').then(function (t) { return mapTicketFull(t); });
      },
      signoff: function (tid, gate, signer, note) {
        return req('/api/tickets/' + encodeURIComponent(tid) + '/signoff', 'POST',
          { gate: gate, signer: signer || '主理人', note: note || '' });
      },
      reject: function (tid, reason, signer) {
        return req('/api/tickets/' + encodeURIComponent(tid) + '/reject', 'POST',
          { reason: reason, signer: signer || '主理人' });
      },
      retry: function (tid) {
        return req('/api/tickets/' + encodeURIComponent(tid) + '/retry', 'POST', {});
      },
      setProjectState: function (pid, action) {
        return req('/api/projects/' + encodeURIComponent(pid) + '/state', 'POST', { action: action });
      },
      archiveProject: function (pid, archived) {
        return req('/api/projects/' + encodeURIComponent(pid) + '/archive', 'POST', { archived: !!archived });
      },
      renameProject: function (pid, name) {
        return req('/api/projects/' + encodeURIComponent(pid) + '/rename', 'POST', { name: name });
      },
      searchMessages: function (opts) {
        opts = opts || {};
        var parts = [];
        if (opts.scope === 'chat') parts.push('scope=chat&target=' + encodeURIComponent(opts.target || ''));
        else parts.push('scope=project&project_id=' + encodeURIComponent(opts.project_id || ''));
        if (opts.q) parts.push('q=' + encodeURIComponent(opts.q));
        if (opts.limit) parts.push('limit=' + opts.limit);
        return req('/api/search/messages?' + parts.join('&'), 'GET');
      },

      /* ---- v2 新增：附件 / 项目 / 岗位 / 成员 / 建单 ---- */
      uploadAttachment: function (file) {
        return new Promise(function (res, rej) {
          var xhr = new XMLHttpRequest();
          xhr.open('POST', (BASE || '') + '/api/attachments', true);
          xhr.setRequestHeader('X-Deck-Token', TOKEN);
          xhr.setRequestHeader('X-Filename', encodeURIComponent(file.name || 'file'));
          xhr.setRequestHeader('Content-Type', file.type || 'application/octet-stream');
          xhr.onreadystatechange = function () {
            if (xhr.readyState !== 4) return;
            var data = null;
            try { data = xhr.responseText ? JSON.parse(xhr.responseText) : null; } catch (e) {}
            if (xhr.status >= 200 && xhr.status < 300) { res(data && data.attachment ? data.attachment : data); }
            else { var err = new Error((data && data.error) ? data.error : ('HTTP ' + xhr.status)); err.status = xhr.status; err.data = data; rej(err); }
          };
          xhr.onerror = function () { var e = new Error('网络错误'); e.status = 0; rej(e); };
          xhr.send(file);
        });
      },
      attachmentUrl: function (id) {
        return (BASE || '') + '/api/attachments/' + encodeURIComponent(id);
      },
      createProject: function (name, dir) {
        return req('/api/projects', 'POST', { name: name, dir: dir || '' });
      },
      createAdapter: function (spec) {
        return req('/api/adapters', 'POST', spec);
      },
      getRoleCatalog: function () {
        return req('/api/role-catalog', 'GET');
      },
      setAdapterModel: function (id, model) {
        return req('/api/adapters/' + encodeURIComponent(id) + '/model', 'POST', { model: model });
      },
      addMember: function (pid, aid) {
        return req('/api/projects/' + encodeURIComponent(pid) + '/members', 'POST', { adapter_id: aid, action: 'add' });
      },
      removeMember: function (pid, aid) {
        return req('/api/projects/' + encodeURIComponent(pid) + '/members', 'POST', { adapter_id: aid, action: 'remove' });
      },
      createTicket: function (spec) {
        return req('/api/tickets', 'POST', spec);
      }
    };
  }

  /* ============================================================ */
  window.createApi = function (mode, opts) {
    return (mode === 'demo') ? createDemo(opts || {}) : createReal(opts || {});
  };
})();
