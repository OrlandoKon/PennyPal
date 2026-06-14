# PennyPal — Agent 功能与技术说明

> 本文从「**Agent 开发**」角度梳理本项目用到的 agent 能力与技术，供汇报/答辩使用。
> 配套：[Design.md](Design.md)（设计方案）· [TechnicalRoadmap.md](TechnicalRoadmap.md)（技术路线）· [BUGS.md](BUGS.md)（问题记录）。

## 0. 一句话定位

PennyPal 是一个 **单 Agent、工具增强（tool-augmented）、ReAct 式（推理 + 行动循环）的对话智能体**。
它把记账从「**用户迁就软件的字段格式**」变成「**软件迁就用户的表达习惯**」——用户随口说一句，Agent 负责理解、结构化、补全默认值、调用工具入账，并具备追问、改/删、预算提醒等主动与多轮能力。

底层模型：**DeepSeek V4**（`deepseek-v4-flash`，OpenAI 兼容接口，function calling）。

---

## 1. Agent 架构：感知 → 理解 → 决策 → 行动 → 反馈 闭环

这是标准 agent 的核心要素，本项目逐一对应：

| 要素 | 本项目实现 | 代码位置 |
|---|---|---|
| **感知 Perception** | 文字 / 语音（浏览器 Web Speech ASR 转写）/ 截图（独立 OCR 服务转文字） | [frontend/app.js](frontend/app.js)、[ocr_service/](ocr_service/) |
| **理解 Reasoning** | DeepSeek V4 把自然语言解析成结构化记账字段 | [backend/agent.py](backend/agent.py) |
| **决策 Planning** | 模型自行决定：调哪个工具、调几次、先查后改、追问还是直接记、删除是否需确认 | system prompt + 模型 |
| **行动 Action** | function calling → 调用工具操作数据库 | [backend/tools.py](backend/tools.py) |
| **反馈 Observation** | 工具结果回灌模型（`role:"tool"` 消息）、确认话术回给用户 | `run_agent` 循环 |
| **记忆 Memory** | 对话历史（短期）+ SQLite 账本（长期状态）+ 「最常用账户」（习得的默认值） | [backend/db.py](backend/db.py) |

```
用户输入（文字/语音/截图OCR）
        │  感知
        ▼
   DeepSeek V4 ── 理解+决策 ──► 选择并调用工具(function calling)  行动
        ▲                              │
        │  观察：工具结果回灌            ▼
        └──────────────────── add/query/summary/update/delete/set_budget
                                       │
                                  SQLite 账本
        最终：一行确认 / 追问 / 分析回答  反馈
```

---

## 2. 核心 Agent 技术

### 2.1 Function Calling / 工具使用（核心）
给模型 **6 个工具**，用 JSON Schema 定义（`enum` 锁分类、`required` 锁必填）。工具是「**LLM 负责理解、代码负责确定性操作**」两层之间的唯一契约。

| 工具 | 作用 | 性质 |
|---|---|---|
| `add_record` | 新增一笔（一句多笔则多次调用） | 写 |
| `update_record` | 修改某笔（先查 id 再改） | 写 |
| `delete_record` | 删除某笔（先轻确认） | 写 |
| `query_records` | 查历史（去重 / 查询型提问 / 定位改删目标） | 读 |
| `get_summary` | 月度汇总（分类/总额/环比） | 读 |
| `set_budget` | 设置分类或总预算（用于超支提醒） | 写 |

定义见 [backend/agent.py](backend/agent.py) `TOOLS`，分发见 [backend/tools.py](backend/tools.py) `dispatch_tool`。

### 2.2 Agentic Loop（手写 ReAct 循环）
`run_agent` 是一个 while 循环：调模型 → 看有没有 `tool_calls` → 执行工具 → 把结果回灌 → 再调，直到模型不再调工具。**不依赖 LangChain 等框架**，逻辑透明、可插日志/审批。

```python
def run_agent(messages):
    while True:
        resp = LLM(messages, tools=TOOLS)          # 理解 + 决策
        if not resp.tool_calls:
            return resp.text                        # 反馈：直接回话 / 追问
        for call in resp.tool_calls:                # 行动（可一次多个 = 批量/一句多笔）
            result = dispatch_tool(call.name, json.loads(call.args))
            messages.append(tool_result(result))    # 观察：结果回灌，回到循环
```

工程要点：终止看 `finish_reason != "tool_calls"`；**最大轮数兜底**防死循环；**指数退避重试**应对高峰超时；每个 `tool_call` 回一条带 `tool_call_id` 的 `role:"tool"` 消息。

### 2.3 Prompt 工程（Agent 的「灵魂」）
system prompt 是决定 agent 聪明程度的关键，写了金额/时间/账户/分类推断/批量/模糊/去重/改删/预算/超支提醒等规则。
踩过一个典型 agent 坑：模型会「**假装记账**」——只回「已记…」文字却不调用工具。靠强化 prompt（「记账的唯一方式是调用工具，严禁只回复确认」）从实测 2/5 提升到 5/5（见 [BUGS.md](BUGS.md) #1）。

### 2.4 结构化输出 + 护栏（Guardrails）
`enum` + `required` 约束模型输出；再加一层统一的 `normalize_fields` 校验（见 [backend/validation.py](backend/validation.py)）：分类越界→其他、金额取绝对值、日期非法→今天、type 由 category 推定。**保证 LLM 的非确定性输出落库前一定干净自洽**（见 [BUGS.md](BUGS.md) #6）。

### 2.5 上下文注入 / 槽位填充（Slot Filling）
后端把「**今天日期 + 用户最常用账户**」注入到首条用户消息，模型据此补默认值、把「昨天/上周五」换算成绝对 ISO 日期。（注入到 user 消息而非 system，是为了不破坏前缀缓存——见 [TechnicalRoadmap.md](TechnicalRoadmap.md) §3.4/§7.2。）

### 2.6 多模态感知
DeepSeek V4 是纯文本模型，故截图走**独立部署的 OCR 服务**（RapidOCR）先转成文字，再交给同一个 Agent——OCR 是 agent 的「感知前处理」步骤，与「理解+结构化」职责分离。语音用浏览器 Web Speech API 前端转写。

### 2.7 主动性（Proactivity）
- **超支提醒**：记账若触发超/近预算，`add_record` 的结果里带 `budget_alert`，模型在确认后追加一句 ⚠️——主动性挂在工具结果上由模型转述。
- **异常检测洞察**：汇总自动点名「某分类比近 3 个月均值多 N%」（确定性）。
- **AI 解读**：`/api/insight` 用模型把汇总数据生成更自然的几句洞察（失败回退模板）。

### 2.8 人在回路（Human-in-the-Loop）
- **删除轻确认**：删除前回显并问一句「确定删除「交通 ¥18」吗？」，用户确认后才真删。
- **信息不全追问**：缺金额/不明确时只针对缺失项问一句。

### 2.9 对话状态与记忆管理
- **短期**：前端维护对话历史，多轮追问把上下文回传；**成功写库即清空历史**（避免历史里的「已记」诱导模型只回文字不调工具）。
- **长期**：SQLite 账本是 agent 的持久状态；「最常用账户」是从历史习得的默认值（一条 SQL 统计）。

### 2.10 目标导向规划（Planning）
用户问「这个月怎么省钱 / 哪里能省」时，agent 自主调用 `get_summary` 分析分类占比、环比、预算、异常，再给出**具体可执行**的省钱方案——对模糊目标做多步规划与工具编排，而非被动记账。

### 2.11 反思校验（Reflection）
记到异常笔（金额远高于该分类历史均值）时，`add_record` 返回 `anomaly_note`，agent 在确认后追加一句核对（「这笔比你平时高不少，记错了告诉我」）——**审视自己刚做的动作**，典型 reflection 模式。

### 2.12 个性化长期记忆（Memory）
`prefs` 表存「关键词→分类」偏好；用户教（「记住星巴克算餐饮」）或纠正分类时，由 `remember` 工具学习入库；每轮把已学偏好注入上下文，让分类**越用越准**——跨会话记忆与个性化。

---

## 3. Agent 能力清单（可逐条演示）

| 能力 | 触发示例 | 背后技术 |
|---|---|---|
| 自然语言记账 | 「中午吃饭35 微信付的」 | NL 理解 + add_record |
| 一句多笔自动拆分 | 「早餐12 打车18 超市买菜60」 | 一次回复多个 tool_calls |
| 追问澄清 | 「35」→ 问「这35是什么消费？」 | 多轮 + 缺槽位追问 |
| 模糊估算 | 「昨天买衣服好像两百多」→ 服饰、pending | 不确定性处理 |
| 相对时间换算 | 「昨天」→ 2026-06-13 | 上下文注入 + prompt |
| 默认值补全 | 不说账户 → 用最常用账户 | 习得默认值 |
| **对话式改账** | 「把刚才那笔改成50」 | query_records → update_record（多步编排） |
| **对话式删账（确认）** | 「删掉刚才那笔」→「确定吗」→「确定」 | 人在回路 |
| **预算 + 超支提醒** | 「把餐饮预算设成500」→ 记到超支显 ⚠️ | set_budget + 主动提醒 |
| 自然语言查询/分析 | 「这个月花了多少？」 | query_records / get_summary |
| 多模态 | 语音说「打车20」/ 上传支付截图 | ASR / OCR 感知 |
| **省钱建议（规划）** | 「这个月怎么省钱？」 | 自主调 get_summary 分析 + 给可执行方案（Planning） |
| **反思校验** | 记一笔远超平时的支出 → 提示核对 | anomaly_note + Reflection |
| **记忆 / 越用越准** | 「记住星巴克算餐饮」→ 下次自动归类 | remember 工具 + prefs 上下文注入（Memory） |

---

## 4. 关键技术决策（汇报时讲「为什么」）

- **LLM/代码边界**：能有标准答案、必须可复现的（写库、求和、环比）交给代码；依赖语义、容忍模糊的（"中午吃饭35"→餐饮/微信/今天）交给模型。工具是两者唯一接口。
- **手写循环而非框架**：要收集入账结果渲染卡片、可插日志/审批、便于讲清「agent 循环」本质。
- **工具即契约**：一句多笔 = 一次回复多个 tool_calls，**批量拆分不需要写任何拆分代码**——这是 function calling 的原生能力。
- **DeepSeek V4 纯文本 → 截图加 OCR**：把多模态当作 agent 的感知前处理（[BUGS.md](BUGS.md) #2）。
- **删除要确认、改账不用**：破坏性操作引入人在回路，体现 agent 的安全意识。

---

## 5. 测试如何体现工程性（答辩可讲）

- **确定性 72 项** + **真实模型实测 22 项**（共 94 项）。
- 其中 **agentic loop 用假客户端做成确定性测试**（`tests/test_agent_loop.py`）：单笔/一句多笔/先查后改/删除确认两轮/纯文字不入账/最大轮数兜底/`tool_call_id` 配对契约——把「只能靠真实模型间接测」的循环逻辑变成硬护栏。
- 实测覆盖多轮：追问、改/删/确认、设预算、超支提醒、账户推断、查询答数。

---

## 6. 没用到的 / 未来方向（汇报「讨论」部分）

- **多 Agent / Agent 协作**：本项目是单 agent，可拆「解析 agent + 分析 agent」。
- **RAG / 向量检索**：去重目前靠 `query_records` 软校验，记录量大后可上向量库做候选召回再交模型判定。
- **自主性 / 定时调度（Autonomy）**：定期记账、定时主动周报/月报——无人值守自动行动（已有"主动提醒"，尚缺调度器）。
- **显式 Planner**：目前规划隐含在 LLM 里，可加独立的任务规划模块（当前省钱建议已是初步目标导向规划）。
- **账单自动同步 / CSV 导入**：把「用户一个字都不用输」推进一步（Design.md §13）。

---

## 7. 一句话总结（给 PPT）

> **PennyPal 是一个以「工具调用 + ReAct 循环」为核心的对话式记账 Agent**：自然语言一句话即可记账，模型自主完成理解、结构化、补全、追问、改删、预算提醒，体现了 function calling、agentic loop、prompt 工程、结构化护栏、多模态感知、主动性与人在回路等 agent 核心技术。
