# AI聊不停 · AI Chat Nonstop

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Dependencies](https://img.shields.io/badge/dependencies-zero-brightgreen)](requirements.txt)
[![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey)]()

> 一个人 + 一堆 AI，怎么把活干完还不乱。
> 本地单人工作台：左边和 AI 对话，右边看工单台账、状态机、门禁与时间线。
> **换 AI 只改配置，不改代码。**

后端零第三方依赖（Python 标准库 + SQLite），前端纯 HTML/CSS/JS，窗口用 pywebview（Windows WebView2 / macOS WKWebView），不装 pywebview 也能跑（自动退回 Edge 应用窗口 / 系统浏览器）。

---

## 它解决什么问题

同时用好几个 AI 干活时，真正的麻烦不是「AI 不够强」，而是：

- 谁在做什么、做到哪一步，全靠脑子记和翻聊天记录；
- 同一件事先后派给三个 AI，谁改了什么没人知道；
- 改完没人验收就发出去了，出问题才发现；
- 上下文和密钥散落在各种窗口、各种配置文件里。

这个项目的答案很土但有效：**把活变成工单，让状态进数据库，让人只看一屏。**

```
需求进来 → 拆单 → 派给某个 AI → 回件 → 门禁签收 → 验收 → 发版
             ↑                                        │
             └────────── 全部落在同一张 SQLite 台账 ────┘
```

## 主要特性

| 能力 | 说明 |
|---|---|
| **三个页面** | 沟通（和 AI 对话）/ 项目群（群聊 + 工单区）/ AI岗位（谁在什么岗位、是否就绪） |
| **适配器配置驱动** | 任意 CLI、OpenAI 兼容 API、A2A、Webhook、拉取型客户端都能接；换 AI 不改代码 |
| **工单台账** | SQLite 一张库：`tickets` / `events` / `signoffs` / `chat_messages` / `jobs`，页面自动刷新 |
| **五道门禁** | 方案评审 → 代码审核 → 构建自检 → 独立验收 → 终验发版，签收落 `signoffs` |
| **四层岗位树** | 决策治理 / 产品设计 / 研发实现 / 质量交付，兼任冲突保存时校验 |
| **真实派发器** | `派发器.py` 扫工单、幂等派发、写事件流，与桌面端读写同一张库 |
| **巡检** | `巡检.py` 本机巡检：待验提醒、回执跟进、每日汇总（不联网，可挂启动流程常驻） |
| **备份** | `备份.py` 用 sqlite3 backup API 做一致性快照，保留最近 7 天 |
| **附件** | 📎 选择 / 拖入 / 粘贴图片，单文件 ≤20MB，存本机，不经过云端 |
| **本地安全** | 只监听 `127.0.0.1`，随机 token 鉴权，Host/Origin/CSP 校验，Key 只从本机文件读 |

## 快速开始

### Windows

```bat
启动.bat        :: 带控制台，能看到报错
启动.vbs        :: 后台启动，不显示控制台窗口
```

### macOS / Linux

```bash
chmod +x 启动.command 安装依赖.command     # 只需一次
./安装依赖.command                          # 可选：装 pywebview 开原生窗口
./启动.command
```

### 或者直接命令行

```bash
python 启动.py              # 原生窗口（默认新界面 web_flow/）
python 启动.py --browser    # 强制 Edge 应用窗口
python 启动.py --ui classic # 回退旧界面 web/
python 启动.py --no-window  # 只起服务，不开窗口
```

首次启动会在仓库目录下建 `数据/`，生成 `台账.sqlite3` 并写入 8 张演示工单 —— **不需要任何 Key 就能先看界面**。

## 配置

首次启动会自动生成 `配置.json`（已在 `.gitignore` 里，**含密钥，不要提交**）。
想直接照抄一份完整样例：

```bash
cp 配置.example.json 配置.json
```

支持 `~` 与 `%ENV%` 展开。

| 配置项 | 作用 |
|---|---|
| `paths.project` | AI 执行时的工作目录（cwd） |
| `paths.dsh_cli` / `codex_cli` / `workbuddy_cli` | 本机 CLI 绝对路径 |
| `paths.workbuddy_key` / `coze_token` | 平台 Key / Token 文件路径 |
| `adapters` | 每个 AI 的 `type` 与参数，`enabled: true` 才会出现在界面上 |
| `security` | command 白名单、危险 flag 黑名单、`allow_demo` |

**Key 只从本机文件读，不写进页面、不进 git、日志不打印。**

## 适配器一览

| type | 能接什么 | 关键配置 |
|---|---|---|
| `process` | **任意 CLI**（Codex、DSH、WorkBuddy、自研脚本） | `command` 数组（支持 `{prompt}` `{model}` `{cwd}`）、`stdin`、`output`、`result_path`、`env` |
| `openai` | **任意 OpenAI 兼容 API**（OpenAI、DeepSeek、Kimi、vLLM、LM Studio…） | `base_url`、`model`、`models`、`api_key_env` |
| `a2a` | **A2A Agent**（Agent-to-Agent 协议） | `url`、JSON-RPC `message/send` |
| `webhook` | **自建 HTTP Bot** | `url`、POST `{prompt, model, source}` |
| `pull` | **只能投递的客户端** | `outbox`、`reply_dir`（写文件，对方定时来拉） |
| `workbuddy` | WorkBuddy 内置 CodeBuddy CLI | `default_model`、`max_turns` |
| `coze` | Coze 云端 Bot | `bot_id`、`user_id` |
| `demo` | 本地演示，免 Key | — |

**新增一个 AI**：复制一个现有适配器块 → 改 `type`/`label`/参数 → `enabled: true` → 保存 → 刷新页面。
不需要改 `服务.py`，也不需要改 `app.js`。
完整步骤见 [`docs/适配器接入指南.md`](docs/适配器接入指南.md)（可以直接丢给 AI，让它照做）。

## 目录结构

```
.
├── 服务.py             本地 HTTP 服务 + SQLite 台账（核心）
├── 派发器.py            真实自动派发器（扫单/幂等/门禁校验）
├── 巡检.py              本机巡检（待验提醒/回执跟进/每日汇总）
├── 记忆.py              MemSearch 长期记忆接入（默认关闭）
├── 技能.py              技能可见性判定与桥接
├── 引导.py              首次引导与体检报告
├── 备份.py              每日备份（sqlite3 一致性快照）
├── 外包交办.py           协作文档交接（给无接口的桌面端 AI）
├── 启动.py              启动器（pywebview / Edge 应用窗口 / 浏览器兜底）
├── 岗位目录.json         岗位定义（四层岗位树，同源给界面与派发器）
├── 配置.example.json    配置样例
├── 自检_*.py            六个自检脚本（派发器/新前端/记忆/编排/项目归档/技能）
├── web/                旧界面（classic）
├── web_flow/           新界面（flow，默认）
└── docs/               说明文档
```

## 自检

每个模块都带一个可独立运行的自检脚本，用临时库、临时端口，不碰真实数据：

```bash
python 自检_派发器.py      # 派发器主流程
python 自检_新前端.py      # 前端接口契约
python 自检_编排接入.py     # 外部编排接口 v1
python 自检_记忆.py        # MemSearch 接入
python 自检_项目归档.py     # 项目归档与群聊追问
python 自检_技能.py        # 技能桥接判定
```

## 安全设计

- **token 鉴权**：启动时生成随机 token，URL 带 `?token=`，API 必须带 `X-Deck-Token`（SSE 用 HttpOnly + SameSite=Strict cookie）；直接访问 `http://127.0.0.1:<port>/` 无 token 时 API 返回 403。
- **只监听 127.0.0.1**，不开放公网；校验 `Host` / `Origin` / `Content-Type`，body ≤256 KB，响应带 `nosniff` + CSP。
- **`process` 适配器**：command 白名单、危险 flag 拒绝、`--` 参数隔离、cwd 限项目内、超时后杀进程树。
- **`openai` / `a2a` / `webhook`**：默认强制 https、禁环回与私网、禁重定向（本地测试需显式 `allow_local: true`）。
- **Key 生命周期**：只在需要时从本机文件读取，页面与台账都不存。

## 文档

| 文档 | 内容 |
|---|---|
| [适配器接入指南](docs/适配器接入指南.md) | 怎么加一个新 AI（可交给 AI 自己照做） |
| [配置说明](docs/配置说明_给AI.md) | `配置.json` 每个字段的含义与改法 |
| [技能桥接规则](docs/技能桥接规则.md) | 技能装在哪、能不能被某个 AI 用、怎么桥接 |
| [派发器说明](docs/派发器说明.md) | 派发器怎么扫单、幂等、写事件流 |
| [巡检说明](docs/巡检说明.md) | 巡检跑什么、怎么挂到启动流程 |
| [备份与恢复](docs/备份与恢复.md) | 备份哪些、怎么恢复 |
| [调研结论](docs/调研结论.md) | 做这套东西之前调研了什么、为什么这么设计 |

## 已知边界

- 只面向**本地单人**，没有多用户、没有权限体系、没有远程访问；
- 打包成独立 exe / .app（PyInstaller）未做，现在用 pywebview 窗口或浏览器；
- `pull` 型适配器（对方只能来拉文件）不能实时回复；
- 附件只做记录与展示，**能不能识别内容是模型自己的事**，调度器不解析；
- 云端 AI（OpenAI / Coze）跑不了本机的技能脚本，只能做 prompt 桥。

## 许可

[MIT](LICENSE) —— 可以自由使用、修改、商用、再分发。

如果它帮你省了事，回来提个 Issue 说说你拿它干了什么，比 star 更让人高兴。
