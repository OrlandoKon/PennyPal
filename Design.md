# 智能记账 Agent — Web 应用设计方案

> 本文档为实现规格说明，可直接交付 Claude Code 按章节实现。
> 目标：做一个**基于大模型的记账 Agent 网页**，用自然语言 / 语音 / 截图记账，由 Agent 自动解析、结构化、补全、入账，并提供消费分析。

---

## 1. 项目概述

### 1.1 定位
把记账从「**用户迁就软件的字段格式**」变成「**软件迁就用户的表达习惯**」。用户随口说一句话，Agent 负责理解、结构化、补全默认值、写入账本。

### 1.2 核心价值
传统记账 App 记一笔需要 5～6 步（选支出/收入 → 选分类 → 选账户 → 填金额 → 填日期 → 备注）。本应用压缩为「**说一句话**」，其余字段由 Agent 推断与补全。

### 1.3 范围（重要：明确边界）

**实现（In Scope）**
- 自然语言文字记账（主路径）
- 语音记账（浏览器语音转文字）
- 截图记账（上传支付截图/小票，由独立部署的 OCR 服务转成文字后再解析）
- 批量记账（一句话含多笔，自动拆分）
- Agent 自动补全默认值、信息不全时主动追问
- 账本管理（列表、编辑、删除）
- 消费分析仪表盘（分类占比、月度汇总、环比、主动洞察）

**不实现，仅作讨论（Out of Scope）**
- 微信/支付宝**账单自动同步**：理想形态是 Agent 直接读平台账单自动入账、用户只做纠错；但受限于平台不开放个人接口与隐私合规边界，本项目不实现，仅在文档/汇报中作为「未来方向」讨论。

---

## 2. 技术栈

为便于 Claude Code 快速实现、且 demo 易于运行，采用**单体应用**：后端服务直接托管前端静态页，一条命令启动。

| 层 | 选型 | 说明 |
|---|---|---|
| 后端 | Python + FastAPI | Agent 循环用 Python 实现最清晰 |
| 大模型 | DeepSeek V4（function calling，OpenAI 兼容接口） | 用 openai SDK，base_url 可配置以走代理 |
| OCR | 独立部署 PaddleOCR / RapidOCR | 截图转文字，本地化处理、不依赖云服务 |
| 存储 | SQLite | 单文件，零配置，适合 demo |
| 前端 | HTML + 原生 JS + Tailwind(CDN) | 无需打包构建，直接由后端托管 |
| 图表 | Chart.js (CDN) | 仪表盘饼图/柱状图 |
| 语音 | 浏览器 Web Speech API | 前端转文字，无需后端 ASR |

> 备选：若偏好组件化前端，可用 React + Vite，但会引入 node 构建步骤；demo 场景建议用上面的零构建方案。

---

## 3. 系统架构与数据流

整体是一个「**感知 → 理解 → 决策 → 行动 → 反馈**」的闭环。

```
┌──────────────────────────────────────────────────────────┐
│                        前端（网页）                         │
│   聊天界面  │  账本列表  │  分析仪表盘                        │
│   文字输入 / 语音按钮 / 图片上传                              │
└───────────────┬──────────────────────────────────────────┘
                │ HTTP (JSON / 图片 base64)
┌───────────────▼──────────────────────────────────────────┐
│                     后端 FastAPI                           │
│  ┌────────────────────────────────────────────────────┐  │
│  │              Agent 核心（理解 + 决策）                │  │
│  │   DeepSeek V4 解析 自然语言/OCR文本 → 结构化字段      │  │
│  │   补默认值 / 追问 / 批量拆分 / 去重判断               │  │
│  └───────────────┬────────────────────────────────────┘  │
│                  │ function calling（行动）                │
│  ┌───────────────▼────────────────────────────────────┐  │
│  │   工具：add_record / query_records / get_summary     │  │
│  └───────────────┬────────────────────────────────────┘  │
└──────────────────┼───────────────────────────────────────┘
                   ▼
              SQLite（账本）
```

**一笔账的完整流程：**
```
用户说："中午吃饭35，微信付的"
  → [理解] DeepSeek V4 解析 → {amount:35, category:餐饮, account:微信, time:今天}
  → [决策] 信息完整、置信度高 → 直接入账（若缺失/模糊 → 追问）
  → [行动] 调用 add_record(...) 写库
  → [反馈] "已记：餐饮 ¥35（微信）✓"
  → [分析] 仪表盘月底主动汇总
```

---

## 4. 数据模型（SQLite）

### 4.1 records 表
```sql
CREATE TABLE records (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    amount      REAL    NOT NULL,           -- 金额（正数）
    type        TEXT    NOT NULL DEFAULT 'expense', -- expense / income
    category    TEXT    NOT NULL,           -- 分类，见 4.2
    account     TEXT    NOT NULL,           -- 账户：微信/支付宝/现金/银行卡
    occurred_at TEXT    NOT NULL,           -- 消费发生时间 ISO8601
    note        TEXT,                       -- 备注/描述
    raw_input   TEXT,                       -- 用户原始输入（便于回溯）
    source      TEXT    DEFAULT 'text',     -- text / voice / image
    status      TEXT    DEFAULT 'confirmed',-- confirmed / pending（模糊待确认）
    created_at  TEXT    NOT NULL            -- 记录创建时间
);
```

### 4.2 分类枚举（可扩展）
`餐饮`、`交通`、`购物`、`娱乐`、`居住`、`医疗`、`学习`、`人情`、`其他`、`收入`

### 4.3 用户偏好（用于补默认值）
最常用账户由 `records` 统计得出（出现频次最高的 account），无需单独建表。

---

## 5. Agent 核心设计（本项目的灵魂）

### 5.1 工具定义（function calling）

提供给模型 3 个工具：

```python
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "add_record",
            "description": "新增一笔记账记录。一句话/一段截图文本含多笔消费时，对每一笔分别调用一次。",
            "parameters": {                       # OpenAI 用 parameters（Anthropic 用 input_schema）
                "type": "object",
                "properties": {
                    "amount":   {"type": "number", "description": "金额，正数"},
                    "type":     {"type": "string", "enum": ["expense", "income"]},
                    "category": {"type": "string",
                                 "enum": ["餐饮","交通","购物","娱乐","居住","医疗","学习","人情","其他","收入"]},
                    "account":  {"type": "string", "description": "账户，如 微信/支付宝/现金"},
                    "occurred_at": {"type": "string", "description": "消费时间 ISO8601，未提及则用今天"},
                    "note":     {"type": "string", "description": "备注/原始描述"},
                    "status":   {"type": "string", "enum": ["confirmed", "pending"]}
                },
                "required": ["amount", "category", "account", "occurred_at"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "query_records",
            "description": "查询历史记录，用于去重判断或回答用户的查询型问题。",
            "parameters": {
                "type": "object",
                "properties": {
                    "start_date": {"type": "string"},
                    "end_date":   {"type": "string"},
                    "category":   {"type": "string"},
                    "keyword":    {"type": "string"}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_summary",
            "description": "获取某月的消费汇总（分类聚合、总额、环比），用于分析型回答。",
            "parameters": {
                "type": "object",
                "properties": {"month": {"type": "string", "description": "YYYY-MM"}},
                "required": ["month"]
            }
        }
    }
]
```

### 5.2 Agent 循环（后端核心逻辑）

```python
def run_agent(messages):
    """messages: 完整对话历史（messages[0] 为 system，含本轮用户输入）"""
    added_records = []
    while True:
        resp = client.chat.completions.create(
            model=MODEL,                    # 见环境配置，默认 deepseek-v4-flash
            max_tokens=2000,
            tools=TOOLS,
            messages=messages,
        )
        msg = resp.choices[0].message
        messages.append(msg)                # 整条 assistant 消息（含 tool_calls）回填

        if resp.choices[0].finish_reason != "tool_calls":
            return (msg.content or ""), added_records   # 结束：返回给前端

        for call in msg.tool_calls:
            args = json.loads(call.function.arguments)        # 参数是 JSON 字符串，必须 loads
            result = dispatch_tool(call.function.name, args)  # 执行工具
            if call.function.name == "add_record":
                added_records.append(result)
            messages.append({                                 # 每个 tool_call 回一条 tool 消息
                "role": "tool",
                "tool_call_id": call.id,                      # 必须与 call.id 精确配对
                "content": json.dumps(result, ensure_ascii=False),
            })
```

> `dispatch_tool` 根据工具名分发到对应的数据库函数（add_record 写库、query_records 查询、get_summary 聚合），并把结果回传给模型。

### 5.3 System Prompt 设计

这是决定 Agent「聪明程度」的关键，建议如下（实现时作为常量）：

```
你是一个记账助手。用户会用自然语言、语音转写文本或上传支付截图来记账，
你的任务是把它解析成结构化记账记录，并调用 add_record 工具写入。

【分类】只能从以下选择：餐饮、交通、购物、娱乐、居住、医疗、学习、人情、其他、收入。

【规则】
1. 金额：从输入中提取数字金额。若完全没有金额，向用户追问，不要编造。
2. 时间：用户未说明则默认今天；"昨天""上周五"等相对时间要换算成具体日期。
3. 账户：用户未说明则使用其最常用账户（已在上下文中提供）；若无历史，默认"现金"。
4. 分类：根据消费内容推断，如"吃饭/外卖"→餐饮，"打车/地铁"→交通。
5. 批量：一句话含多笔（如"早餐12，打车18，买菜60"）时，对每一笔分别调用 add_record。
6. 模糊处理：表达不精确（如"好像花了两百多"）时，取估计值并将 status 设为 pending，
   在回复里提示用户这是待确认的估算。
7. 截图：用户上传的截图已由 OCR 服务转成文本（排版可能杂乱、含多笔），从中提取金额/商家/时间；一段 OCR 文本可能含多笔。
8. 去重：若疑似与刚记的某笔重复，先用 query_records 核对再决定。

【回复风格】
- 入账后用一行简洁确认每一笔，格式："已记：<分类> ¥<金额>（<账户>）✓"。
- 信息缺关键项时，只针对缺失项追问一句，不要长篇大论。
- 用户问"这月花了多少""外卖花了多少"等查询/分析问题时，调用 query_records 或
  get_summary 后用自然语言回答。
```

调用前，后端把**用户最常用账户**和**今天日期**注入到**首条用户消息**（不要写进 system prompt，以保持前缀稳定、利于 DeepSeek 自动上下文缓存命中），供模型补默认值。

### 5.4 决策逻辑要点（部分在 prompt，部分在代码）

| 能力 | 实现位置 | 说明 |
|---|---|---|
| 补默认值（日期/账户） | prompt + 注入上下文 | 今天日期、最常用账户由后端算好传入 |
| 信息不全追问 | prompt | 缺金额/不明确时模型自行追问 |
| 批量拆分 | prompt（一次回复多个 tool_calls） | 模型对每笔调一次 add_record |
| 模糊标记 pending | prompt | status 字段区分确认/待确认 |
| 去重校验 | prompt + query_records 工具 | 模型主动查历史比对 |
| 时间换算 | prompt | 相对时间 → 绝对日期 |

---

## 6. 后端 API 设计（FastAPI）

| 方法 | 路径 | 功能 |
|---|---|---|
| POST | `/api/chat` | 入参 `{messages}` 或 `{message, image?}`；运行 Agent 循环；返回 `{reply, added_records}` |
| GET | `/api/records?month=YYYY-MM` | 列出记录 |
| PUT | `/api/records/{id}` | 编辑一笔（修正分类/金额等） |
| DELETE | `/api/records/{id}` | 删除一笔 |
| GET | `/api/summary?month=YYYY-MM` | 返回仪表盘数据：分类聚合、总额、环比、主动洞察文本 |
| GET | `/` | 托管前端单页 |

**/api/chat 说明**
- 文字/语音输入：`message` 为文本。
- 截图输入：`image` 为 base64，后端**先调用独立部署的 OCR 服务把图转成文本**，再把该文本拼入 user 消息，交给同一个 Agent 循环——**OCR 只负责"图→文"，"文→结构化"仍由 DeepSeek V4 完成**。
- 前端维护对话历史；支持多轮追问（用户补充信息后再次 POST 含完整 messages）。

---

## 7. 前端设计（单页，三个视图切换）

顶部 Tab 切换：`记账` / `账本` / `分析`。整体风格简洁现代（Tailwind），移动端友好（记账常在手机上用）。

### 7.1 记账页（主界面，聊天式）
- 对话流：用户消息（右）+ Agent 确认回复（左），每笔入账显示一张「记账卡片」（分类图标、金额、账户、时间）。
- 底部输入栏：
  - 文本输入框 + 发送
  - 🎤 语音按钮：按下用 Web Speech API 录音转文字，自动填入输入框
  - 📷 图片按钮：上传支付截图，转 base64 随消息发送
- 多轮：Agent 追问时，用户直接补一句即可。

### 7.2 账本页（记录列表）
- 按月份分组的记录列表，每行：分类图标 / 备注 / 账户 / 金额 / 时间。
- 单条可点击编辑（弹窗改金额、分类、账户）或删除。
- `pending` 状态的记录高亮标「待确认」，方便用户核对模糊记账。

### 7.3 分析页（仪表盘）
- 月份选择器。
- 本月总支出 + 环比上月（↑/↓ 百分比）。
- 分类占比饼图（Chart.js）。
- 近 6 个月支出趋势柱状图。
- **主动洞察卡片**：由 get_summary 生成的自然语言提示，如「本月外卖 ¥1200，比上月多 40%，注意控制」。体现 Agent 的「主动性」。

---

## 8. 多模态输入实现要点

- **语音**：前端 `webkitSpeechRecognition` / `SpeechRecognition`，中文 `lang='zh-CN'`，识别结果填入输入框，用户确认后发送。纯前端，无后端 ASR。
- **截图**：`<input type="file" accept="image/*">` 读为 base64 → POST 到 `/api/chat` 的 `image` 字段 → 后端调用**独立部署的 OCR 服务**（PaddleOCR / RapidOCR）转成文本 → 拼入 user 消息 → 交同一个 DeepSeek V4 Agent 解析入账。设计要点：**OCR 与主服务分离部署、"图→文"与"文→结构化"职责分离，且 OCR 自部署、截图不出本地，契合财务数据隐私**（见 §13）。一段 OCR 文本含多笔时，复用文字批量机制由模型自行拆分，OCR 侧无需处理拆分。

---

## 9. 主动分析能力（加分项）

`get_summary(month)` 后端逻辑：
1. 聚合本月各分类支出、总额。
2. 取上月总额算环比。
3. 找出环比增幅最大的分类，生成一句自然语言洞察（可由模型生成，也可模板拼接）。
4. 返回给分析页渲染。

---

## 10. 建议项目结构

```
expense-agent/
├── backend/
│   ├── main.py            # FastAPI 入口 + 路由 + 托管前端
│   ├── agent.py           # Agent 循环、SYSTEM_PROMPT、TOOLS（OpenAI 工具格式）
│   ├── tools.py           # dispatch_tool + add/query/summary 实现
│   ├── ocr_client.py      # 调用独立 OCR 服务（HTTP），把截图转文本
│   ├── db.py              # SQLite 初始化与读写
│   └── config.py          # 读环境变量（含 DEEPSEEK_*、OCR_URL）
├── ocr_service/           # 独立部署的 OCR 服务（单独进程/容器）
│   ├── server.py          # FastAPI 包一层，POST /ocr 收图返文本
│   ├── requirements.txt   # rapidocr-onnxruntime（或 paddleocr）, fastapi, uvicorn
│   └── Dockerfile         # 独立镜像
├── frontend/
│   ├── index.html         # 单页（三视图）
│   ├── app.js             # 交互逻辑、API 调用、语音、图片上传
│   └── styles（用 Tailwind CDN，可省）
├── data/
│   └── expense.db         # SQLite（运行时生成）
├── requirements.txt       # fastapi, uvicorn, openai, httpx
├── .env.example
└── README.md              # 启动说明
```

---

## 11. 环境配置

`.env`：
```
DEEPSEEK_API_KEY=sk-...
DEEPSEEK_BASE_URL=https://api.deepseek.com   # 走代理时改此处；严格工具校验可用 .../beta
MODEL=deepseek-v4-flash       # 解析任务用 flash 足够且省成本，可换 deepseek-v4-pro
OCR_URL=http://localhost:8001/ocr            # 独立部署的 OCR 服务地址
```

启动（两个进程/容器）：
```
# 1) 启动独立部署的 OCR 服务
pip install -r ocr_service/requirements.txt
uvicorn ocr_service.server:app --port 8001

# 2) 启动主应用
pip install -r requirements.txt
uvicorn backend.main:app --reload --port 8000
# 浏览器打开 http://localhost:8000
```

---

## 12. 实现里程碑（建议构建顺序）

1. **后端骨架**：FastAPI + SQLite 初始化 + records CRUD 接口跑通。
2. **Agent 核心**：TOOLS + SYSTEM_PROMPT + run_agent 循环 + `/api/chat`，命令行先验证「文字记一笔」能正确写库。
3. **记账页**：聊天界面 + 文本记账打通端到端。
4. **批量与追问**：验证一句多笔、信息不全追问、模糊 pending。
5. **账本页**：列表 + 编辑/删除。
6. **分析页**：get_summary + Chart.js 图表 + 主动洞察。
7. **多模态**：语音按钮 + 截图上传（独立 OCR 服务转文字 → 交 Agent 解析）。
8. **打磨**：记账卡片样式、移动端适配、空状态。

> 优先保证 1～4（核心闭环）跑通；5～8 为增强项，时间紧可按需取舍。

---

## 13. 范围外与未来方向（汇报收尾用）

- **账单自动同步**：理想终态是 Agent 定期读取微信/支付宝/银行账单，自动分类入账，用户只做纠错——绝大多数消费一个字都不用输。受限于平台不开放个人接口、隐私合规边界，本项目不实现，作为未来方向讨论。一个折中的可落地路径是：用户手动导出账单文件（CSV）后由 Agent 解析。
- **隐私**：财务数据敏感，理想上应本地化处理 / 端侧模型，避免上传云端。本项目的 OCR 已自部署、截图不出本地，是这一方向的局部实践；进一步可探索自部署 / 端侧的解析模型。
- **个性化分类**：长期可结合用户历史习惯，让分类与默认值越用越准。

---

## 14. 验收标准（demo 脚本）

实现后用以下场景自测，也是汇报现场的演示脚本：

1. 输入「中午吃饭35，微信付的」→ 正确入账餐饮 ¥35（微信）。
2. 输入「早餐12 打车18 超市买菜60」→ 自动拆成 3 笔。
3. 输入「35」→ Agent 追问「这 35 是什么消费？」。
4. 输入「昨天买衣服好像两百多」→ 记为购物、status=pending、提示待确认。
5. 上传一张支付截图 → OCR 转文字 → Agent 自动入账。
6. 语音说「打车 20」→ 转文字并入账。
7. 问「这个月花了多少？外卖花了多少？」→ 正确汇总回答。
8. 分析页显示分类饼图、环比、主动洞察。
```
