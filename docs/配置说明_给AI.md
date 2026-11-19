# 配置说明 · 给 AI 用

> 把这份文件整个交给对方的 AI，让它照着做。不需要你解释，也不需要你操作。
> 适用版本：AI聊不停 v0.6.0（原工单调度台）

---

## 0. 你的任务与边界

你在帮使用者把「AI聊不停」桌面工作台配到**能发第一条工单**的状态。

你要做三件事：**探测环境 → 改配置 → 验证**。

**不要做**：不要改 `服务.py`、`派发器.py`、`app.js`、`web/index.html`；不要新增功能；不要删配置里的适配器块；不要碰 `数据\台账.sqlite3`。

**你可以改的只有两个文件**：`桌面端\配置.json`，以及必要时新建 `桌面端\配置.local.json`（不推荐，除非原文件已损坏）。

---

## 1. 先做三个探测，不要一上来就改文件

### 探测 A：确认目录

```powershell
Test-Path "<安装目录>\桌面端\配置.json"
Test-Path "<安装目录>\桌面端\引导.py"
Test-Path "<安装目录>\桌面端\岗位目录.json"
```

三个都要 `True`。任何一个 `False`，说明安装目录不对或安装包缺文件，先解决这个，别往下走。

### 探测 B：确认 Python

```powershell
python -c "import sys;print(sys.version);print(sys.executable)"
```

要求 **3.10 以上**。记下 `sys.executable` 的完整路径——第 4 步要写进启动脚本。

如果 `python` 命令报"找不到"，试 `py -3 --version`，仍失败就让使用者先装 Python 3.12（勾选 Add to PATH）。

### 探测 C：跑一次自带自检

```powershell
cd "<安装目录>\桌面端"
python 引导.py
```

它会打印一份 JSON 检测报告。**这是唯一可信的配置判断依据，不要靠猜。**

看这几个字段：

| 字段 | 含义 | 你要的结果 |
|---|---|---|
| `summary.fail` | 致命项数 | 越小越好，最后必须是 `0` |
| `summary.warn` | 提醒项数 | 允许非 0 |
| `checks[].status` | 每项 `ok`/`warn`/`fail` | 关注 `fail` 的那些 |
| `checks[].fix` | 这项怎么修 | 直接照它做 |
| `first_run` | 是否首次 | 改完仍然 `true` 属正常，点过引导才会变 `false` |

**在改任何配置之前先跑一次，把它当"体检报告"。**

---

## 2. 配置.json 字段说明

### 2.1 三类字段，先分清

| 类别 | 怎么对待 |
|---|---|
| **必须改** | 不改就用不了（路径指向别人的机器） |
| **按需改** | 使用者想接别的 AI 才改 |
| **别动** | 安全与调度参数，非必要不改 |

### 2.2 必须改（`paths` 段）

这些在原机器上是绝对路径，**在别人机器上 99% 不存在，必须全部核一遍**。

| 字段 | 是什么 | 怎么改 |
|---|---|---|
| `paths.project` | 默认工作目录，AI 执行时的 cwd | 改成使用者自己的项目根，如 `D:\我的项目` |
| `paths.node` | node 命令名或绝对路径 | 如果装的是标准 Node，就写 `node`；找不到就填绝对路径 |
| `paths.codex_cli` | Codex 可执行文件 | 使用者不用 Codex 就把对应适配器 `enabled` 改 `false` |
| `paths.codex_home` | Codex 的 home 目录 | 同上 |
| `paths.dsh_cli` | DSH 的 cli | 同上 |
| `paths.dsh_credentials` | DSH 凭据文件 | 同上 |
| `paths.workbuddy_cli` | WorkBuddy 的 CLI 路径 | 同上 |
| `paths.workbuddy_key` | WorkBuddy 的 Key 文本文件 | 同上 |

**判断规则：使用者机器上没有哪个 AI，就把那个适配器的 `enabled` 改成 `false`，并且不要给它填路径。留一个指向不存在文件的路径，比直接关掉更容易在排错时误导人。**

### 2.3 按需改（`adapters` 段）

每个适配器是一个块。**要接一个 AI，就复制一个现有块，改 `type`/`label`/参数，`enabled: true`。**

四种最省事的接法：

| 场景 | 用哪个 type | 最少要填什么 |
|---|---|---|
| 使用者有 Codex / DSH / 任何 CLI | `process` | `command` 数组（第一项必须是可执行文件），`stdin: true` |
| 使用者只有 API Key | `openai` | `base_url`、`model`、`api_key_env` 或 `api_key_file` |
| 使用者用 Coze | `coze` | `bot_id`、`user_id` |
| 使用者只有桌面客户端、不能实时回 | `pull` | `outbox`、`reply_dir` |

`process` 类型有一条硬约束：**`command[0]` 必须在 `paths` 里出现过，或者在 `security.process_allowed_executables` 白名单里**，否则会被拒。加新命令时别忘了同步白名单。

### 2.4 别动

| 字段 | 为什么 |
|---|---|
| `security.allowed_signers` | 决定谁有权签门 |
| `security.process_forbidden_flags` | 危险参数黑名单 |
| `security.allow_demo` | 演示模式开关，改错会让人以为配好了其实没配 |
| `dispatcher.*` | 派发器节奏、并发、熔断，非必要不动；要动先说明理由 |

---

## 3. 记住一条：密钥不进对话

`workbuddy_key`、`coze_token`、`DEEPSEEK_API_KEY` 这类，**让使用者自己填到文件里，你不要把它们抄进聊天记录、不要写进给别人的回报**。

正确做法：你告诉使用者"把 Key 存到 `<某个文件>`"，然后配置里只填这个文件的路径。

---

## 4. 改完必须做的三件验证

### 验证 1：配置能被解析

```powershell
cd "<安装目录>\桌面端"
python -c "import json;json.load(open('配置.json',encoding='utf-8-sig'));print('JSON OK')"
```

必须打印 `JSON OK`。报错就是逗号/括号写坏了，改回去。

### 验证 2：自检没有 fail

```powershell
python 引导.py
```

看输出里的 `summary.fail`，**必须等于 0**。

- `adapters` 是 `fail` → 一个能用的执行端都没有，工单发出去没人接
- `data` 是 `fail` → 数据目录不可写
- `roles` 是 `fail` → `岗位目录.json` 缺失或被改坏
- `memory` 是 `warn` 属正常，首次归档时会自动建目录

### 验证 3：服务真的能起来

```powershell
cd "<安装目录>\桌面端"
python 启动.py --no-window
```

看到这两行就算成功：

```
AI聊不停 v0.6.0
本地地址：http://127.0.0.1:<端口>/
```

然后用这个端口请求一次健康检查：

```powershell
python -c "import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:<端口>/api/health').read().decode())"
```

返回 `{"ok": true, ...}` 就通了。测完 `Ctrl+C` 关掉。

**如果窗口版打不开，先别怀疑配置**——`启动.py` 有三级降级（pywebview → Edge 应用窗口 → 浏览器），前两级失败会自动往下一级走，最后一定能用浏览器打开。

---

## 5. 排错对照表

| 现象 | 最可能的原因 | 怎么修 |
|---|---|---|
| 双击 bat 闪退 | bat 里的 Python 路径是别人的 | 改 `启动桌面端.bat` 第 3 行的 `set PY=` |
| 页面打开但一片空白 | `web\` 目录缺文件 | 检查 `web\index.html`、`web\app.js` 在不在 |
| 报"端口被占用" | 上一次没关干净 | 换端口：`python 启动.py --port 8899` |
| 工单发出去一直"待派" | 没有可用执行端 | 跑 `python 引导.py` 看 `adapters` 项 |
| AI 回了"命令找不到" | `command[0]` 不在 PATH 也不在白名单 | 填绝对路径，或加进 `security.process_allowed_executables` |
| 改了配置没生效 | 服务没重启 | 关掉窗口重开；配置是启动时读的 |
| 首次引导不弹 | 配置里已有 `onboarded: true` | 删掉这个字段再重启 |

---

## 6. 如果你不能改文件（重要）

有些 AI 客户端不允许读写本地文件。**这种情况下不要硬来，也不要谎称"已配置好"。**

改成输出一份变更清单，让使用者手动改：

```
文件：<安装目录>\桌面端\配置.json

1) paths.project
   旧值：G:\别人的项目
   新值：D:\我的项目
   位置：第 __ 行

2) adapters.codex.enabled
   旧值：true
   新值：false
   位置：第 __ 行

3) 新增：adapters.myllm（完整块，粘到 adapters 最后一个块后面）
   { ...完整 JSON... }
```

然后让使用者改完再跑一次 `python 引导.py`，把结果贴回来，你核对 `summary.fail` 是不是 0。

**判断自己能不能改文件的方法**：试着在安装目录写一个 `_write_test.txt`，能写就是能改；报错就只能出清单。

---

## 7. 完成标准（五条，逐条可判）

全部满足才算配好：

1. `python -c "import json;json.load(open('配置.json',encoding='utf-8-sig'));print('JSON OK')"` 打印 `JSON OK`
2. `python 引导.py` 输出里 `summary.fail` 等于 `0`
3. `python 启动.py --no-window` 能打出本地地址，且 `/api/health` 返回 `ok: true`
4. 使用者的机器上，至少有一个**非人类**执行端在 `引导.py` 报告里是 `ok`
5. 打开界面能发出一条消息，并收到回复（或明确看到"待派"在推进）

**任一条没做到，不要说"配置完成"。说清卡在哪一条、报错原文是什么。**

---

## 8. 最后一句

这份文件解决的是"装完到能用"。要新增别的 AI 接入方式，去看同目录的 `适配器接入指南.md`；要看平台整体怎么用，看 `README.md`。

**配完记得提醒使用者一件事**：任务结束时让执行 AI 交一条回执（类型 / 项目 / 摘要 / 产物路径），否则产物进不了记忆空间，只能落在「待归档区」等人工确认。
