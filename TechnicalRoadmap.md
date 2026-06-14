# 智能记账 Agent — 技术路线文档（Technical Roadmap）

> 配套文档：[Design.md](Design.md)。
> **分工**：`Design.md` 回答「做什么」（功能、数据模型、API 列表、前端视图、里程碑）；本文档回答「**用什么技术、怎么落地、难点如何攻克、成本与风险如何控制**」。
> 读者：实现者（Claude Code / 工程师）、汇报评审。
>
> **本版变更（重要）**：Agent 大模型由 Anthropic Claude 改为 **DeepSeek V4**。这不是简单替换——工具调用格式、Agent 循环语义、缓存机制、**多模态（截图）能力**都随之变化。下文已按 DeepSeek V4 重写，并在 §5、§11 标出受影响处。

---

## 0. 文档定位与阅读顺序

| 你想知道 | 看哪里 |
|---|---|
| 产品要实现哪些功能、验收标准 | `Design.md` §1、§7、§14 |
| 为什么选 DeepSeek V4、用哪个端点、哪个 SDK | 本文 §2 |
| Agent 循环怎么跑、OpenAI 工具调用协议长什么样 | 本文 §3 |
| 自然语言解析不准 / 批量漏记 / 去重 怎么解决 | 本文 §4 |
| **截图为什么变难了**、语音怎么接 | 本文 §5、§6 |
| 调一次模型花多少钱、DeepSeek 自动缓存怎么省 | 本文 §7 |
| 怎么测、分几步交付 | 本文 §9、§10 |

> ✅ **已与 Design.md 同步**：两份文档均已切到 DeepSeek V4。因官方 V4 为纯文本模型，截图记账改走**独立部署 OCR 服务**（图→文）再交 V4 解析的路线；Design.md §1.3 / §2 / §8 / §10 / §11 / §14.5 已相应更新。详见 §5。

---

## 1. 技术总体理念

一句话：**把"理解"交给大模型，把"确定性操作"交给代码。**

```
非结构化输入（文字/语音转写）
        │  ← DeepSeek V4 负责：理解、结构化、补全、追问、批量拆分
        ▼
结构化记账记录（amount/category/account/...）
        │  ← 代码负责：持久化、聚合、校验、幂等
        ▼
SQLite 账本  →  确定性聚合（环比/分类占比）→ 仪表盘
```

**关键边界**：确定性、可复现的事（写库、求和、算环比、分页）写在代码里；依赖语义理解、容忍模糊的事（"中午吃饭35"→餐饮/微信/今天）交给模型 + Prompt。工具（function calling）是两层之间唯一的契约。

### 1.1 关键架构决策一览

| 决策 | 选择 | 一句话理由 | 详见 |
|---|---|---|---|
| 应用形态 | 单体（后端托管前端） | demo 一条命令启动，无跨域、无构建 | §2.1 |
| Agent 大模型 | **DeepSeek V4**（默认 `deepseek-v4-flash`，难例升 `deepseek-v4-pro`/思考模式） | 解析任务足够，且比 Claude 便宜 ~20–50× | §2.2 |
| 接入方式 | **OpenAI 兼容端点 + `openai` SDK** | DeepSeek 一等公民接口、文档最全、支持 strict 工具模式 | §2.3 |
| Agent 编排 | 自写 while 循环（手动 agentic loop，OpenAI 工具语义） | 逻辑透明、可插日志/审批 | §3.2 |
| 结构化方式 | function calling（OpenAI 工具格式） | 一句多笔 = 一次回复多个 `tool_calls` | §3.1 |
| **截图记账** | **独立部署 OCR**（RapidOCR/PaddleOCR）转文字后交 Agent | V4 纯文本，靠 OCR 补多模态 | §5 |
| 语音 | 浏览器 Web Speech API（纯前端） | 零后端 ASR 成本 | §6 |
| 默认值/上下文注入 | 后端预计算后注入"首条用户消息" | 保持 system 前缀稳定→提高 DeepSeek 自动缓存命中 | §3.4 / §7.2 |

---

## 2. 技术选型论证

### 2.1 后端 Python + FastAPI，单体托管前端

- **为什么**：Agent 循环是命令式流程，Python 最直白；FastAPI 自带异步、Pydantic 校验。后端用 `StaticFiles` 托管 `frontend/`，同源拿页面又调 `/api/*`，**无 CORS、无前端构建**。
- **备选/取舍**：Node/Express、React+Vite 均引入额外构建或复杂度；demo 选「克隆即跑」。

### 2.2 大模型：DeepSeek V4

- **为什么**：满足核心需求——**工具调用**（结构化入账）、**长上下文**（多轮追问，1M）、**强 Agentic 推理**，且价格极低。
- **官方模型与定价**（来源：DeepSeek 官方 API 文档，USD/1M token）：

  | 模型 | 输入(缓存命中) | 输入(未命中) | 输出 | 上下文 | 最大输出 | 模式 |
  |---|---|---|---|---|---|---|
  | `deepseek-v4-flash` | $0.0028 | $0.14 | $0.28 | 1M | 384K | 思考/非思考 |
  | `deepseek-v4-pro`   | $0.003625 | $0.435 | $0.87 | 1M | 384K | 思考/非思考 |

  > 旧名 `deepseek-chat` / `deepseek-reasoner` 将于 **2026/07/24** 弃用，分别对应 `deepseek-v4-flash` 的**非思考 / 思考**模式。**新代码直接用 `deepseek-v4-flash` / `deepseek-v4-pro`**。

- **默认与升级策略**：
  - **默认 `deepseek-v4-flash` + 非思考模式**：记账解析（"中午吃饭35"）属轻量结构化任务，非思考模式更快更省；flash 的推理已接近 pro。
  - **难例升级**：模糊金额、复杂多笔、需多步推理的查询，可切 `deepseek-v4-pro` 或开**思考模式**。把模型做成 `MODEL` 环境变量，按需切换无需改代码。
  - 思考模式会额外返回 `reasoning_content`（思考过程）与 `content`（最终答案），**Agent 循环只取 `content`**，不要把 `reasoning_content` 当结果。

- **成本量级**：相比 Claude Sonnet（输入 $3 / 输出 $15），v4-flash 输入便宜 ~21×、输出便宜 ~53×。一笔文字记账成本低到可忽略（远小于一美分）。

### 2.3 接入方式：OpenAI 兼容端点 + `openai` SDK

DeepSeek 同时提供两个端点，**本项目采用 OpenAI 兼容路线**：

| 路线 | base_url | SDK | 工具调用格式 | 取舍 |
|---|---|---|---|---|
| **OpenAI 兼容（采用）** | `https://api.deepseek.com` | `openai` | OpenAI（`tools`/`tool_calls`/`role:"tool"`） | 一等公民、文档全、支持 strict 工具模式与自动缓存 |
| Anthropic 兼容（备选） | `https://api.deepseek.com/anthropic` | `anthropic` | Anthropic（`tool_use` 块） | 可沿用 Claude 风格写法、改动最小；但依赖兼容层，新特性可能滞后 |

客户端初始化（与 OpenAI SDK 完全一致，仅改 `base_url`）：

```python
from openai import OpenAI

client = OpenAI(
    api_key=os.environ["DEEPSEEK_API_KEY"],
    base_url="https://api.deepseek.com",        # 走代理时改这里
)
# 需要更严格的工具参数 JSON 校验时，可用 strict beta 端点：
#   base_url="https://api.deepseek.com/beta"
```

> 备选路线（Anthropic 兼容端点）适合"想最大程度保留 Claude 风格代码"的场景：把 `base_url` 指向 `.../anthropic`、模型名换成 `deepseek-v4-*` 即可，工具/消息写法几乎不动。本项目为全新仓库、无存量 Claude 代码，故选更主流、特性更全的 OpenAI 路线。

### 2.4 存储 SQLite / 前端原生 JS+Tailwind+Chart.js / 语音 Web Speech

与大模型选型无关，沿用 Design.md：SQLite 单文件零配置（§5.4 数据层）；前端无打包、CDN 引入；语音纯前端（§6）。这些**不受换模型影响**。

---

## 3. 核心技术路线：Agent 引擎（本项目的灵魂）

### 3.1 工具即契约（OpenAI function calling）

3 个工具构成"模型 ↔ 代码"的全部接口。语义同 Design.md §5.1，但**格式改为 OpenAI 风格**（外层包 `{"type":"function","function":{...}}`，入参字段名为 `parameters`）：

```python
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "add_record",
            "description": "新增一笔记账记录。一句话含多笔消费时，对每一笔分别调用一次。",
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
    # query_records / get_summary 同理，外层加 {"type":"function","function":{...}}
]
```

| 工具 | 方向 | 幂等性 | 触发时机 |
|---|---|---|---|
| `add_record` | 写 | **否**（每次插一行）| 解析出一笔消费 |
| `query_records` | 读 | 是 | 去重核对 / 查询型提问 |
| `get_summary` | 读 | 是 | 分析型提问 / 仪表盘 |

**技术要点**：
- `add_record` 的 `required` 收紧为金额/分类/账户/时间——强约束模型缺金额时**追问而非编造**。
- `category` 用 `enum` 锁定 10 个枚举值，杜绝模型自创分类。**OpenAI 工具的 enum 约束是软约束**（模型大概率遵守但非 100% 保证）；要硬保证，用 §2.3 的 **strict beta 端点**或在 `dispatch_tool` 里对枚举做兜底校验。
- **"一句多笔 = 模型在一次回复里返回多个 `tool_calls`"**——批量拆分不需要我们写拆分代码（§4 难点3）。

### 3.2 Agent 循环：手动 agentic loop（OpenAI 语义）

采用**手写 while 循环**而非 SDK 自动 runner，因为要收集 `added_records` 渲染卡片、打日志、未来可加审批。注意：相比 Claude 版本，**终止条件、消息回填、工具结果格式都变了**。

```python
def run_agent(messages):
    """messages: 含 system 作为 messages[0] 的完整对话历史"""
    added_records = []
    while True:
        resp = client.chat.completions.create(
            model=MODEL,                 # deepseek-v4-flash
            max_tokens=2000,             # 见 §3.3
            tools=TOOLS,
            messages=messages,
            # tool_choice 默认 "auto"
        )
        msg = resp.choices[0].message
        messages.append(msg)             # 整条 assistant 消息（含 tool_calls）回填

        if resp.choices[0].finish_reason != "tool_calls":
            return (msg.content or ""), added_records      # 终止：返回给前端

        for call in msg.tool_calls:
            args = json.loads(call.function.arguments)      # 必须 json.loads，参数是字符串
            result = dispatch_tool(call.function.name, args)
            if call.function.name == "add_record":
                added_records.append(result)
            messages.append({                               # 每个 tool_call 配一条 tool 消息
                "role": "tool",
                "tool_call_id": call.id,                    # 必须与 call.id 配对，否则下一轮报错
                "content": json.dumps(result, ensure_ascii=False),
            })
```

**必须守住的 5 条不变量（OpenAI 版，与 Claude 版不同）**：
1. **判终止看 `finish_reason == "tool_calls"`**（Claude 是 `stop_reason == "tool_use"`）。
2. 把**整条 assistant 消息（含 `tool_calls`）**append 回 `messages`，再 append 工具结果。
3. **每个 `tool_call` 对应一条独立的 `{"role":"tool", ...}` 消息**（Claude 是把多个 `tool_result` 合进一条 user 消息——这里相反，逐条 tool 消息）。
4. **`tool_call_id` 必须精确配对** `call.id`；漏一个或多一个，下一轮请求报错。
5. 工具参数是 **JSON 字符串**，必须 `json.loads(call.function.arguments)`；结果用 `json.dumps(..., ensure_ascii=False)` 回填（保留中文）。

### 3.3 终止条件与 `finish_reason`

| finish_reason | 含义 | 本项目处理 |
|---|---|---|
| `tool_calls` | 模型要调工具 | 执行工具，回填，继续循环 |
| `stop` | 正常说完 | 返回 `msg.content` 给前端（最常见的终止） |
| `length` | 撞到 `max_tokens` | 记账输出很短正常不触发；触发记日志、酌情提高上限 |
| `content_filter` | 内容审查命中 | 记账场景几乎不触发；兜底返回友好提示 |
| 资源类（如负载导致的非正常结束） | DeepSeek 高负载时可能返回 | **指数退避重试**该次请求 |

**`max_tokens=2000` 的理由**：记账确认（"已记：餐饮 ¥35（微信）✓"）+ 几个 `tool_calls` 块都很短，2000 给足空间且不浪费。DeepSeek 最大可达 384K，但本场景不需要。

> **硬化点**：DeepSeek 在高峰期偶发非正常 `finish_reason`/超时，建议给 `client.chat.completions.create` 包一层指数退避重试（429/5xx/资源不足时重试 2~3 次）。

### 3.4 上下文注入：今天日期 + 最常用账户（关键工程细节，换模型后仍成立）

模型补默认值需要两个运行时事实：**今天日期**（换算"昨天/上周五"）和**最常用账户**。OpenAI 格式下 system 是 `messages[0]`：

```python
SYSTEM_PROMPT = "..."          # 字节级冻结的静态常量（规则见 Design.md §5.3）
context_line = f"【上下文】今天是 {today_iso}；用户最常用账户：{top_account or '无历史'}。"
messages = [
    {"role": "system", "content": SYSTEM_PROMPT},          # 冻结 → 利于自动缓存命中
    {"role": "user",   "content": f"{context_line}\n\n{user_text}"},   # 易变上下文放这里
]
```

- `today_iso` = 服务器当天日期（ISO8601）。
- `top_account` = `SELECT account FROM records GROUP BY account ORDER BY COUNT(*) DESC LIMIT 1`。
- **不要把日期/账户写进 `SYSTEM_PROMPT`**：DeepSeek 的自动上下文缓存按**前缀**计费，system 每天/每笔变化会让缓存命中率掉到 0，白付未命中价（§7.2）。

---

## 4. 关键技术难点与解决方案

| # | 难点 | 解决方案 | 实现位置 | 验证 |
|---|---|---|---|---|
| 1 | 自然语言→结构化不准 | enum 锁分类 + required 锁金额 + system 规则 + 注入今天日期/常用账户；必要时用 strict 端点 | schema + prompt + 代码 | `Design.md` §14.1 |
| 2 | 相对时间换算 | 注入今天日期，prompt 指令"相对时间换算成绝对 ISO 日期" | 代码注入 + prompt | 断言 `occurred_at` |
| 3 | 一句多笔漏记/重复 | 依赖模型一次回复多个 `tool_calls`；后端逐个收集进 `added_records` | 工具机制 + 循环 | `Design.md` §14.2 拆 3 笔 |
| 4 | 模糊金额 | prompt 规则：取估计值 + `status=pending`，回复提示待确认 | prompt + status 字段 | `Design.md` §14.4 |
| 5 | 去重 | prompt 规则"疑似重复先 `query_records` 核对"；模型先查后写 | prompt + 工具 | 连记两笔相同 |
| 6 | **截图识别** | **独立部署 OCR 服务转文字 → 交同一 Agent 解析** | 见 §5 | `Design.md` §14.5 |
| 7 | 默认值补全 | 后端预计算 today/top_account 注入首条 user 消息 | 代码 | 不带账户也能记 |
| 8 | 并发写一致性 | SQLite WAL + 单写串行；记录天然 append-only | db 层 | §5.4 |

### 4.1 难点1 展开：怎么让解析"够准"

三道防线，从硬到软：
1. **Schema 约束**：`category` 用 `enum`、金额 `required`。注意 OpenAI 工具的 enum 是软约束——要硬保证用 **strict beta 端点**，或在 `dispatch_tool` 落库前对 `category`∈枚举、`amount>0` 做兜底校验，不合规则纠正或退回追问。
2. **Prompt 规则**：Design.md §5.3 的 8 条规则，要具体到例子（"吃饭/外卖→餐饮"）。DeepSeek 对清晰、带例子的指令服从度好。
3. **运行时上下文**：注入今天日期 + 常用账户，让默认值有依据。

### 4.2 难点6 展开：截图为什么从"免费午餐"变成"难点"

Claude 版本里，截图作为 image block 直接喂给同一 Agent，多模态直读、**无需 OCR**——这是 Design.md 的卖点之一。**换 DeepSeek V4 后此路不通**：官方 `deepseek-v4-flash` / `deepseek-v4-pro` 是**纯文本模型**，API 不接受图片输入（"DeepSeek V4 Vision"目前只见于第三方聚合站，非官方平台）。截图处置见 §5。

---

## 5. 截图记账技术路线（独立部署 OCR + DeepSeek V4）

DeepSeek V4 官方模型读不了图，故采用**独立部署的 OCR 服务**把截图转成文字，再交给同一个 V4 Agent 解析入账。核心分工：**OCR 只做"图→文"，"文→结构化"仍由 V4 完成**——两层职责分离、互不耦合。

### 5.1 端到端链路

```
前端上传截图(base64)
  → 后端 /api/chat 收到 image
  → backend/ocr_client.py 调独立 OCR 服务 (HTTP POST /ocr) → 返回识别文本
  → 文本拼进首条 user 消息（标注"以下为截图识别文本，可能排版乱、含多笔"）
  → 同一个 DeepSeek V4 Agent 解析入账（与文字记账完全同一套循环）
```

### 5.2 OCR 引擎选型

| 引擎 | 中文票据/截图 | 部署 | 取舍 |
|---|---|---|---|
| **RapidOCR**（onnxruntime） | 强（PaddleOCR 模型的 ONNX 版） | `pip install rapidocr-onnxruntime`，无 Paddle 依赖，CPU 即可 | **demo 首选**：最轻、起得快 |
| PaddleOCR | 最强（含 PP-Structure 版面分析） | Docker/pip，依赖 Paddle，镜像偏大 | 识别/生态最强，重 |
| Umi-OCR | 强，自带 HTTP 接口 | 桌面/服务，开箱即用 | 懒人方案 |

> Tesseract 中文票据效果一般，不作主选。**建议 RapidOCR（轻）起步，识别不够再上 PaddleOCR（强）。**

### 5.3 独立服务接口与部署形态

OCR 作为**单独进程/容器**（`ocr_service/`），用 FastAPI 包一层暴露：

```
POST /ocr   body: {"image": "<base64>"}
            resp: {"text": "整段识别文本", "lines": [{"text": "...", "score": 0.98}]}
```

主后端 `ocr_client.py` 通过环境变量 `OCR_URL` 调它。**分离部署的理由**：OCR 重依赖（onnx/Paddle、可能要 GPU）不污染主应用、可独立扩缩容、互不拖垮；将来换引擎只改这一个服务。

### 5.4 多笔拆分（复用文字机制，OCR 侧零逻辑）

一张账单截图可能含多笔。OCR 输出整段文本即可，**拆分交给模型**：DeepSeek V4 凭 system prompt 的"批量"规则把文本拆成多个 `add_record`（一次回复多个 `tool_calls`），与文字批量同一套机制。OCR 侧不写任何拆分代码。

### 5.5 图片预处理、准确率与降级

- **预处理**：支付截图通常清晰，默认直送 OCR；准确率不足时做尺寸归一化 / 灰度 / 对比度增强（引擎多自带检测+识别）。
- **版面线索**：纯按行文本喂 LLM 通常已够判断"哪个数字是金额"；需要时让 OCR 返回 `box` 坐标供模型参考，demo 从简。
- **低置信度 → pending**：OCR 行 `score` 低时，在 prompt 提示模型把对应字段 `status` 标 `pending`，让用户在账本页核对（呼应 Design.md 的 pending 机制）。
- **失败降级**：OCR 服务不可用/超时 → `/api/chat` 返回友好提示"截图识别暂不可用，请改用文字"，**不阻塞文字/语音闭环**。

### 5.6 隐私加分项

OCR **自部署、截图在本地处理、不传第三方**，契合 Design.md §13 的财务数据隐私关切——相比把图片发往云端多模态接口，这是一处可在汇报里强调的优势。

### 5.7 数据层与并发（不受换模型影响）

- **WAL 模式**：`PRAGMA journal_mode=WAL`，读不阻塞写。
- **写串行**：demo 单用户、写入量极低；必要时进程内一把锁串行化 `add_record`，或 `connect(timeout=...)` 重试。
- **索引**：`occurred_at`、`(type, occurred_at)`、`category`，支撑按月查询与聚合。
- **append-only**：记录只增可改删；去重靠模型软校验，不加数据库唯一约束。

---

## 6. 语音技术路线（不受换模型影响）

- **实现**：前端 `new (webkitSpeechRecognition||SpeechRecognition)(); lang='zh-CN'`，识别结果填进输入框，用户确认后再发送（不自动发送，留纠错机会）。纯前端，后端不接音频。
- **兼容矩阵与降级**：

  | 浏览器 | 支持 | 降级 |
  |---|---|---|
  | Chrome / Edge（桌面+安卓） | ✅ | — |
  | Safari | 部分/不稳定 | 提示改用文字 |
  | Firefox | ❌ | 特性检测**隐藏🎤按钮**，提示改用文字 |

  语音是增强项，不阻塞核心闭环。

---

## 7. 成本与缓存技术路线（按 DeepSeek 重写）

### 7.1 单次请求成本构成

一次 `/api/chat` 通常 1~2 轮模型调用。输入 = system + tools schema + 对话历史；输出 = 简短确认 + `tool_calls`。在 `deepseek-v4-flash`（输入未命中 $0.14/1M、输出 $0.28/1M）下，一笔文字记账成本**低到可忽略**（远小于一美分）。这是换 DeepSeek 最直接的收益：比 Claude Sonnet 便宜约 20–50×。

### 7.2 DeepSeek 自动上下文缓存（无需 `cache_control`）

**机制（与 Claude 不同）**：DeepSeek 默认开启**硬盘上下文缓存**，自动识别**重复前缀**并以更低价计费——**不需要手动打 `cache_control` 断点**。命中部分按"缓存命中"价（v4-flash $0.0028/1M，约为未命中价的 **1/50**）计费。

**对本项目的两条结论（与换模型前一致，理由同样适用）**：
1. **把 `SYSTEM_PROMPT` + `TOOLS` 放在 `messages` 最前且字节冻结**——这段最大的固定前缀会被自动缓存复用。
2. **绝不把今天日期/常用账户写进 system**（§3.4 注入"首条 user 消息"的原因）：前缀每天/每笔变化会让缓存命中率归零。

**验证命中**：DeepSeek 在响应 `usage` 中返回 `prompt_cache_hit_tokens` / `prompt_cache_miss_tokens`。反复相同前缀仍 0 命中，说明前缀混进了易变内容（最常见就是日期/账户进了 system）。

> **demo 视角**：演示几条、对话短，缓存命中与否影响都极小（绝对成本本就趋近 0）；缓存当锦上添花即可。

### 7.3 是否上流式（streaming）

记账确认输出很短，**核心闭环不需要流式**。仅当未来做"主动洞察"长文本或分析页逐字显示时，对那条路径用流式（`stream=True`）。

---

## 8. 多轮对话状态：前端持有历史（不受换模型影响）

API 无状态，历史由**前端维护一个 messages 数组**，每次发送把完整 `messages` POST 给 `/api/chat`；追问时 append 一句再 POST。后端 `run_agent` 在这份历史上跑循环。刷新丢历史（demo 可接受）；要持久会话将来加 `conversations` 表即可。

> 注意：OpenAI 格式的历史里会包含 `assistant`（含 `tool_calls`）与 `role:"tool"` 消息。前端只需透传后端返回的 `reply` 续上对话流，无需理解工具消息细节；**完整工具往返历史建议在后端内存里维护本轮即可**，不必全推给前端。

---

## 9. 测试与验收技术路线

把 Design.md §14 八场景转成分层验证：

| 层 | 测什么 | 怎么测 |
|---|---|---|
| 单元（确定性） | `tools.py` 的 add/query/summary、环比计算、`top_account` SQL | 直接调函数断言；环比测"上月为 0"除零边界 |
| Agent 行为 | 给定输入，模型是否调对工具、字段是否对 | 跑 `run_agent`，断言 `added_records` 的 amount/category/account/occurred_at |
| 端到端 | §14 八场景 | 命令行/HTTP 跑一遍人工核对 |

**注意**：模型输出有随机性，Agent 行为测试只断言**关键字段**（如 `category∈{"餐饮"}`），不断言确认文案逐字。聚合/环比这类纯代码逻辑必须有确定性单测兜底。截图相关场景（§14.5）依 §5 的处置决定是否纳入本期。

---

## 10. 技术实现里程碑（对 Design.md §12 的技术细化）

| 里程碑 | 技术产出物 | 完成判据 | 依赖 |
|---|---|---|---|
| **M1 后端骨架** | `db.py`（建表+WAL+索引）、records CRUD、FastAPI 起得来 | `GET /api/records` 返回空数组；`expense.db` 自动生成 | — |
| **M2 Agent 核心** | `config.py`（OpenAI 客户端指向 DeepSeek）、`agent.py`（TOOLS/SYSTEM_PROMPT/run_agent，**OpenAI 工具语义**）、`tools.py`、`/api/chat`、上下文注入 | **命令行**输入"中午吃饭35，微信付的"→ 库里多一行正确记录 | M1 |
| **M3 记账页** | 聊天界面 + `app.js` 调 `/api/chat` + 记账卡片 | 浏览器输入一句→入账并显示卡片，端到端打通 | M2 |
| **M4 批量与追问** | prompt 调优、pending 渲染 | §14.2 拆 3 笔 / §14.3 追问 / §14.4 pending 全过 | M3 |
| **M5 账本页** | `GET/PUT/DELETE /api/records`、列表+编辑弹窗 | 按月列表、改一笔、删一笔、pending 高亮 | M3 |
| **M6 分析页** | `get_summary`（聚合+环比+洞察）、Chart.js 图表 | 饼图、环比↑↓、主动洞察卡片 | M5 |
| **M7 多模态** | 语音按钮+兼容降级；**独立 OCR 服务**（`ocr_service/server.py`）+ 主后端 `ocr_client` 调用 | §14.6 语音入账；§14.5 截图→OCR→入账 | M3 |
| **M8 打磨** | 卡片样式、移动端、空状态 | 移动端可用、无空白页 | M5/M6 |

**关键路径**：M1→M2→M3→M4（核心闭环必达）。M2 风险最高（**OpenAI 工具循环语义**与契约），建议**先用命令行脚本验证 M2 再写前端**。M7 的截图部分取决于 §5。

---

## 11. 技术风险登记册（按 DeepSeek 更新）

| 风险 | 概率 | 影响 | 缓解 |
|---|---|---|---|
| OCR 服务依赖/部署（独立服务、模型权重、可能要 GPU） | 中 | 中（多一个运维件）| §5：RapidOCR 轻量 CPU 部署；OCR 故障降级到文字输入，不阻塞核心闭环 |
| OCR 识别错/漏（金额小字、票据排版乱） | 中 | 中（记错金额）| 图片预处理；低置信度让模型标 `pending` 待核对；难例升 `deepseek-v4-pro` |
| OpenAI 工具循环不变量写错（终止条件/`tool_call_id` 配对/逐条 tool 消息） | 中 | 高（报错或死循环）| 严守 §3.2 五条；M2 先命令行验证 |
| enum 软约束被突破（模型自创分类） | 中 | 中（落库脏数据）| strict beta 端点 或 `dispatch_tool` 兜底校验 |
| 把日期/账户写进 system，自动缓存永不命中 | 中 | 中（白付未命中价）| §3.4 注入首条 user；用 `prompt_cache_hit_tokens` 验证 |
| DeepSeek 高峰负载致超时/非正常 finish | 中 | 中（请求失败）| 指数退避重试 2~3 次（§3.3）|
| 思考模式把 `reasoning_content` 当结果 | 低 | 中（解析错乱）| 循环只取 `msg.content` |
| 模糊/相对时间解析掉点 | 中 | 中 | 难例升 v4-pro/思考模式；pending 让用户纠错 |
| Web Speech API 浏览器不支持 | 高（Firefox）| 低（增强项）| 特性检测隐藏按钮，降级文字 |
| 环比"上月为 0"除零 | 中 | 低 | 聚合函数显式处理分母为 0，单测覆盖 |

---

## 12. 技术演进路线（未来方向）

1. **账单 CSV 导入**（最近可落地）：用户导出微信/支付宝 CSV → `/api/import` 解析每行 → 复用 `add_record`，模型只做分类补全。纯文本，DeepSeek V4 完全胜任。
2. **截图能力回归**：待 DeepSeek 官方推出可用的多模态 V4 端点，可去掉 §5 方案 A 的独立视觉/OCR 组件，回到"单 Agent 直读图"的极简形态。
3. **流式输出（SSE）**：分析页长文本洞察、聊天逐字显示时启用 `stream=True`。
4. **个性化分类记忆**：把用户历史归类沉淀为 few-shot 注入 prompt，或落偏好表，默认值越用越准。
5. **去重升级**：记录量大后加"金额+时间窗+商家"候选集再交模型判定，降漏检。

---

## 13. 附：技术决策速查（评审用）

- **为什么换 DeepSeek V4？** 解析任务足够，价格便宜 ~20–50×（§2.2）。
- **用哪个端点？** OpenAI 兼容（`https://api.deepseek.com` + `openai` SDK）；Anthropic 兼容端点为备选（§2.3）。
- **工具/循环和 Claude 版差在哪？** 终止看 `finish_reason=="tool_calls"`；逐个 `tool_call` 回一条 `role:"tool"` 消息并配 `tool_call_id`；参数要 `json.loads`（§3.2）。
- **截图怎么读？** 独立部署 OCR 服务（RapidOCR/PaddleOCR）把图转成文字，再交 V4 解析；OCR 只做图→文，结构化仍由 V4 完成（§5）。
- **缓存要手动打断点吗？** 不用，DeepSeek 自动按前缀缓存；但同样**别把日期放 system**（§7.2）。
- **批量记账的拆分代码在哪？** 没有——一次回复多个 `tool_calls` 是模型原生能力（§3.1/§4 难点3）。
- **默认哪个模型/模式？** `deepseek-v4-flash` + 非思考；难例升 `deepseek-v4-pro` 或思考模式（§2.2）。
