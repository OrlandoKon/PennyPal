"""字段校验/规整：所有写库（新增 + 编辑）都过一遍，避免脏数据进库。

对应 BUGS.md G1–G4：
- occurred_at 非法（如模型没换算的"昨天"）→ 回退今天，避免记录在按月视图里"消失"。
- category 越界 → 其他；amount → 取绝对值并保留两位；status/type/account 规整。
- type 由 category 决定，杜绝 type/category 不一致。
"""
from datetime import date

from .config import CATEGORIES

_TYPES = ("expense", "income")
_STATUS = ("confirmed", "pending")


def normalize_date(value) -> str:
    """规整为合法 ISO 日期；非法或空值回退今天。"""
    s = str(value or "").strip()
    try:
        date.fromisoformat(s[:10])      # 接受 'YYYY-MM-DD' 或 'YYYY-MM-DDT...'
        return s
    except ValueError:
        return date.today().isoformat()


def normalize_fields(fields: dict) -> dict:
    """对 dict 中**出现**的字段做校验/规整（其余不动）。

    新增与编辑共用：新增时所有字段都在；编辑时只规整传入的部分字段。
    """
    out = dict(fields or {})

    if "amount" in out and out["amount"] is not None:
        try:
            out["amount"] = round(abs(float(out["amount"])), 2)
        except (TypeError, ValueError):
            out.pop("amount")                       # 非数字 → 视为未提供

    if "category" in out:
        if out["category"] not in CATEGORIES:
            out["category"] = "其他"
        # type 由 category 决定，杜绝 type/category 不一致（G4）
        out["type"] = "income" if out["category"] == "收入" else "expense"
    elif "type" in out and out["type"] not in _TYPES:
        out["type"] = "expense"

    if "status" in out and out["status"] not in _STATUS:
        out["status"] = "confirmed"

    if "account" in out and not str(out["account"] or "").strip():
        out["account"] = "现金"

    if "occurred_at" in out:
        out["occurred_at"] = normalize_date(out["occurred_at"])

    return out
