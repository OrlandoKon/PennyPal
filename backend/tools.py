"""工具分发：把模型的 function call 路由到数据库操作。

add_record 在落库前做兜底校验（分类枚举、金额、时间、账户），
对应 TechnicalRoadmap.md §3.1/§4.1「enum 是软约束，需代码兜底」。
"""
from datetime import date
from typing import Optional

from . import db
from .config import CATEGORIES


def _coerce_record(args: dict, ctx: dict) -> dict:
    """组装待写入记录；字段校验/规整统一在 db 写库前完成（validation.normalize_fields）。"""
    return {
        "amount": args.get("amount"),
        "type": args.get("type"),
        "category": args.get("category"),
        "account": args.get("account"),
        "occurred_at": args.get("occurred_at"),
        "note": args.get("note"),
        "status": args.get("status"),
        "raw_input": (ctx or {}).get("raw_input"),
        "source": (ctx or {}).get("source", "text"),
    }


def _budget_alert(rec: dict):
    """记一笔支出后，按月对比预算，返回提醒文案或 None。"""
    if rec.get("type") != "expense":
        return None
    month = (rec.get("occurred_at") or "")[:7]
    if not month:
        return None
    budgets = db.get_budgets()
    parts = []
    cb = budgets.get(rec["category"])
    if cb:
        spent = db.category_month_spent(month, rec["category"])
        if spent > cb:
            parts.append(f"本月{rec['category']}已花¥{spent:.0f}，超预算{(spent / cb - 1) * 100:.0f}%")
        elif spent >= 0.8 * cb:
            parts.append(f"本月{rec['category']}已用{spent / cb * 100:.0f}%预算")
    tb = budgets.get(db.TOTAL_KEY)
    if tb:
        spent = db.month_spent(month)
        if spent > tb:
            parts.append(f"本月总支出¥{spent:.0f}，超总预算{(spent / tb - 1) * 100:.0f}%")
        elif spent >= 0.8 * tb:
            parts.append(f"本月总支出已用{spent / tb * 100:.0f}%总预算")
    return "；".join(parts) if parts else None


def _add_record(args: dict, ctx: dict) -> dict:
    try:
        amount = float(args.get("amount"))
    except (TypeError, ValueError):
        amount = 0.0
    if amount <= 0:
        return {"ok": False, "error": "缺少有效金额，请向用户追问这笔是多少钱，不要编造。"}
    saved = db.insert_record(_coerce_record(args, ctx))
    result = {"ok": True, "record": saved}
    alert = _budget_alert(saved)
    if alert:
        result["budget_alert"] = alert
    avg, cnt = db.category_stats(saved["category"], exclude_id=saved["id"])   # 反思：与历史对比
    if cnt >= 3 and avg > 0 and saved["amount"] > avg * 2:
        result["anomaly_note"] = f"这笔¥{saved['amount']:.0f}明显高于你平时「{saved['category']}」的均值¥{avg:.0f}"
    return result


def _query_records(args: dict, ctx: dict) -> dict:
    rows = db.query_records(
        start_date=args.get("start_date"),
        end_date=args.get("end_date"),
        category=args.get("category"),
        keyword=args.get("keyword"),
    )
    return {"ok": True, "count": len(rows), "records": rows}


def _get_summary(args: dict, ctx: dict) -> dict:
    month = args.get("month") or date.today().strftime("%Y-%m")
    return {"ok": True, "summary": db.summary(month)}


def _update_record(args: dict, ctx: dict) -> dict:
    rid = args.get("id")
    if not rid:
        return {"ok": False, "error": "缺少要修改的记录 id，请先用 query_records 找到它。"}
    fields = {k: args[k] for k in ("amount", "type", "category", "account", "occurred_at", "note", "status")
              if k in args and args[k] is not None}
    if not fields:
        return {"ok": False, "error": "没有要修改的字段。"}
    rec = db.update_record(int(rid), fields)
    return {"ok": True, "record": rec} if rec else {"ok": False, "error": f"记录 {rid} 不存在。"}


def _delete_record(args: dict, ctx: dict) -> dict:
    rid = args.get("id")
    if not rid:
        return {"ok": False, "error": "缺少要删除的记录 id，请先用 query_records 找到它。"}
    return {"ok": True} if db.delete_record(int(rid)) else {"ok": False, "error": f"记录 {rid} 不存在。"}


_TOTAL_ALIASES = {"总", "总预算", "全部", "总额", "整体", db.TOTAL_KEY}


def _set_budget(args: dict, ctx: dict) -> dict:
    cat = (args.get("category") or "").strip()
    if cat in _TOTAL_ALIASES:
        cat = db.TOTAL_KEY
    elif cat not in CATEGORIES:
        return {"ok": False, "error": "分类必须是给定枚举之一，或'总预算'。"}
    try:
        amount = float(args.get("amount"))
    except (TypeError, ValueError):
        return {"ok": False, "error": "预算金额无效。"}
    db.set_budget(cat, amount)
    return {"ok": True, "category": ("总预算" if cat == db.TOTAL_KEY else cat), "amount": round(abs(amount), 2)}


def _remember(args: dict, ctx: dict) -> dict:
    keyword = (args.get("keyword") or "").strip()
    category = args.get("category")
    if not keyword:
        return {"ok": False, "error": "缺少要记住的关键词。"}
    if category not in CATEGORIES:
        return {"ok": False, "error": "分类必须是给定枚举之一。"}
    db.set_pref(keyword, category)
    return {"ok": True, "keyword": keyword, "category": category}


_DISPATCH = {
    "add_record": _add_record,
    "query_records": _query_records,
    "get_summary": _get_summary,
    "update_record": _update_record,
    "delete_record": _delete_record,
    "set_budget": _set_budget,
    "remember": _remember,
}


def dispatch_tool(name: str, args: dict, ctx: Optional[dict] = None) -> dict:
    fn = _DISPATCH.get(name)
    if fn is None:
        return {"ok": False, "error": f"未知工具 {name}"}
    try:
        return fn(args or {}, ctx or {})
    except Exception as exc:  # noqa: BLE001 - 工具错误回传给模型，让它自行调整
        return {"ok": False, "error": str(exc)}
