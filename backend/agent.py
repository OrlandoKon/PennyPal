"""Agent 核心：SYSTEM_PROMPT、TOOLS（OpenAI 工具格式）、run_agent 循环。

对应 Design.md §5 与 TechnicalRoadmap.md §3：
- 工具用 OpenAI function calling 格式；
- 循环按 OpenAI 语义：finish_reason / tool_calls / role:"tool" + tool_call_id；
- create 调用带指数退避重试，循环有最大轮数兜底。
"""
import json
import time

from .config import CATEGORIES, MODEL, client
from .tools import dispatch_tool

_CATEGORY_STR = "、".join(CATEGORIES)

SYSTEM_PROMPT = f"""你是一个记账助手。用户会用自然语言、语音转写文本，或上传支付截图（已由 OCR 转成文本）来记账。

【最重要】记账/改账/删账/设预算都只能通过调用对应工具完成（add_record / update_record / delete_record / set_budget），你没有别的手段。严禁只回复“已记/已改/已删”却不调用工具。记账、改账、设预算意图明确就直接做、不要反问；唯独删除要先轻确认（见规则10）。
- 每识别出一笔消费/收入就必须调用一次 add_record；一句话含多笔就分别调用多次。
- 严禁在没有调用 add_record 的情况下就回复“已记”等确认——那会让用户的账其实没有被记录。
- 只有当工具调用成功返回后，才用一行确认每一笔，格式：“已记：<分类> ¥<金额>（<账户>）✓”。

【分类】只能从以下选择：{_CATEGORY_STR}。

【规则】
1. 金额：从输入中提取数字金额。只有完全没有金额时才向用户追问，不要编造，也不要调用工具。
2. 时间：用户未说明则默认今天；“昨天”“上周五”等相对时间换算成具体 ISO 日期（如 2026-06-14）。
3. 账户：用户未说明则使用其最常用账户（已在上下文中提供）；若无历史，默认“现金”。
4. 分类：根据消费内容推断，如“吃饭/外卖”→餐饮，“打车/地铁”→交通，“衣服/鞋/包”→服饰，“手机/电脑/摄像头/数码配件”→数码，其它日用百货→购物。
5. 模糊处理：表达不精确（如“好像花了两百多”）时取估计值并将 status 设为 pending，在确认里提示这是待确认估算。
6. 截图：OCR 文本可能排版杂乱、含多笔，逐笔提取金额、商家、时间；OCR 不确定的字段把 status 设为 pending。
7. 去重：仅当用户明显在重复同一笔时才用 query_records 核对；正常的新消费一律直接记，不要因为像之前的某笔就跳过。
8. 查询/分析：用户问“这月花了多少”“外卖花了多少”等，调用 query_records 或 get_summary 后用自然语言回答。
9. 改账：用户要改某笔（如“把刚才那笔改成50”），先用 query_records 找到目标记录的 id（“刚才/最近”取返回里最新一条），再调用 update_record，一行确认（如“已改：餐饮 ¥50 ✓”）。
10. 删账（先轻确认）：用户要删某笔（如“删掉刚才那笔”），先用 query_records 找到它，回显并问一句确认（如“确定删除「交通 ¥18（支付宝）」吗？”），这一步先不要删；等用户回复“是/对/删/确定/嗯”等之后，再 query_records 取 id 并调用 delete_record，确认“已删：交通 ¥18 ✓”。
11. 预算：用户说“把餐饮预算设成1000”“总预算5000”等，调用 set_budget。
12. 超支提醒：若 add_record 的结果里带 budget_alert，就在那笔确认后面追加一句以 ⚠️ 开头的提醒（转述 budget_alert 内容）。
13. 理财建议（省钱/分析）：用户问“这个月怎么省钱”“哪里能省”“帮我分析消费”等，先调用 get_summary，再据分类占比、环比、预算、Top 支出、异常，给出 2-3 条**具体可执行**的建议（指名分类和金额，如“外卖占了 40% ¥1200、环比涨 30%，建议每周自己做饭 2 次、目标省 ¥300”），别泛泛而谈。
14. 反思校验：若 add_record 的结果里带 anomaly_note，就在那笔确认后面追加一句温和的核对（如“这笔比你平时高不少，记错了说一声，我可以帮你改”）。
15. 记忆/个性化：用户教你规则（如“记住星巴克算餐饮”“以后房租都算居住”）或纠正某关键词的分类时，调用 remember(keyword, category)。首条消息的【记忆/偏好】里若有匹配关键词，优先用记住的分类。

【注意】历史对话里你之前的“已记”只是文字回执，不代表本轮已经记账；本轮每识别出的一笔仍必须重新调用 add_record。
"""


def _fn(name, description, properties, required=None):
    schema = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return {"type": "function", "function": {"name": name, "description": description, "parameters": schema}}


TOOLS = [
    _fn(
        "add_record",
        "新增一笔记账记录。一句话/一段截图文本含多笔消费时，对每一笔分别调用一次。",
        {
            "amount": {"type": "number", "description": "金额，正数"},
            "type": {"type": "string", "enum": ["expense", "income"]},
            "category": {"type": "string", "enum": CATEGORIES},
            "account": {"type": "string", "description": "账户，如 微信/支付宝/现金/银行卡"},
            "occurred_at": {"type": "string", "description": "消费时间 ISO8601，未提及则用今天"},
            "note": {"type": "string", "description": "备注/原始描述"},
            "status": {"type": "string", "enum": ["confirmed", "pending"]},
        },
        ["amount", "category", "account", "occurred_at"],
    ),
    _fn(
        "query_records",
        "查询历史记录，用于去重判断或回答用户的查询型问题。",
        {
            "start_date": {"type": "string", "description": "起始日期 YYYY-MM-DD"},
            "end_date": {"type": "string", "description": "结束日期 YYYY-MM-DD"},
            "category": {"type": "string", "description": "分类筛选"},
            "keyword": {"type": "string", "description": "关键词，匹配备注/原始输入/分类"},
        },
    ),
    _fn(
        "get_summary",
        "获取某月的消费汇总（分类聚合、总额、环比），用于分析型回答。",
        {"month": {"type": "string", "description": "YYYY-MM"}},
        ["month"],
    ),
    _fn(
        "update_record",
        "修改一笔已存在的记录。先用 query_records 找到目标记录的 id，再调用本工具。",
        {
            "id": {"type": "integer", "description": "要修改的记录 id"},
            "amount": {"type": "number"},
            "category": {"type": "string", "enum": CATEGORIES},
            "account": {"type": "string"},
            "occurred_at": {"type": "string", "description": "消费时间 ISO8601"},
            "note": {"type": "string"},
            "status": {"type": "string", "enum": ["confirmed", "pending"]},
        },
        ["id"],
    ),
    _fn(
        "delete_record",
        "删除一笔已存在的记录。先用 query_records 找到目标记录的 id，再调用本工具。",
        {"id": {"type": "integer", "description": "要删除的记录 id"}},
        ["id"],
    ),
    _fn(
        "set_budget",
        "设置某分类或总预算的月度预算金额（用于超支提醒）；金额设为 0 表示取消。",
        {
            "category": {"type": "string", "description": "分类名，或'总预算'表示总额"},
            "amount": {"type": "number", "description": "月度预算金额"},
        },
        ["category", "amount"],
    ),
    _fn(
        "remember",
        "记住一个关键词→分类的偏好（用户教你规则或纠正分类时调用），以后遇到该关键词优先用此分类。",
        {
            "keyword": {"type": "string", "description": "商家/关键词，如 星巴克、楼下便利店、房租"},
            "category": {"type": "string", "enum": CATEGORIES},
        },
        ["keyword", "category"],
    ),
]

MAX_ITERS = 8


def _create_with_retry(messages, retries: int = 3):
    last_exc = None
    for attempt in range(retries):
        try:
            return client.chat.completions.create(
                model=MODEL,
                max_tokens=2000,
                tools=TOOLS,
                messages=messages,
            )
        except Exception as exc:  # noqa: BLE001 - DeepSeek 高峰可能超时/限流，退避重试
            last_exc = exc
            time.sleep(1.5 * (2 ** attempt))
    raise last_exc


def run_agent(messages, ctx=None):
    """messages: 含 system 的完整对话历史。返回 (reply_text, added_records, dirty)。

    dirty: 本轮是否发生写库（新增/修改/删除任一），供前端决定是否刷新账本与图表。
    """
    added_records = []
    dirty = False
    for _ in range(MAX_ITERS):
        resp = _create_with_retry(messages)
        msg = resp.choices[0].message

        # 回填整条 assistant 消息（含 tool_calls）
        assistant_msg = {"role": "assistant", "content": msg.content}
        if msg.tool_calls:
            assistant_msg["tool_calls"] = [
                {
                    "id": c.id,
                    "type": "function",
                    "function": {"name": c.function.name, "arguments": c.function.arguments},
                }
                for c in msg.tool_calls
            ]
        messages.append(assistant_msg)

        # 无工具调用 → 本轮结束
        if not msg.tool_calls:
            return (msg.content or ""), added_records, dirty

        # 逐个 tool_call 执行，并回一条 role:"tool" 消息（tool_call_id 精确配对）
        for call in msg.tool_calls:
            try:
                args = json.loads(call.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            result = dispatch_tool(call.function.name, args, ctx)
            name = call.function.name
            if name == "add_record" and result.get("ok") and result.get("record"):
                added_records.append(result["record"])
            if name in ("add_record", "update_record", "delete_record", "set_budget", "remember") and result.get("ok"):
                dirty = True
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": json.dumps(result, ensure_ascii=False),
                }
            )

    return "（处理步骤过多，请把输入拆短后重试）", added_records, dirty


def generate_insight(summary: dict) -> str:
    """用模型根据本月汇总数据生成 2–3 句中文洞察（无工具）；失败回退模板洞察。"""
    data = {k: summary.get(k) for k in ("month", "total", "income_total", "change_ratio", "by_category", "budgets")}
    try:
        resp = client.chat.completions.create(
            model=MODEL,
            max_tokens=400,
            messages=[
                {"role": "system", "content": "你是记账助手的分析模块。根据给定的本月消费汇总，用中文写 2–3 句简洁、具体、口语化的洞察与建议：挑分类占比/环比/异常或超预算的重点说，别复述全部数字，别客套，直接给结论。"},
                {"role": "user", "content": json.dumps(data, ensure_ascii=False)},
            ],
        )
        return (resp.choices[0].message.content or "").strip() or summary.get("insight", "")
    except Exception:  # noqa: BLE001 - 洞察失败回退模板，不影响主流程
        return summary.get("insight", "")
