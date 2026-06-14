"""工具分发层（add_record 兜底校验等）的确定性测试。"""
from backend import db
from backend.tools import dispatch_tool


def test_add_record_missing_amount_is_rejected():
    r = dispatch_tool("add_record", {"category": "餐饮", "account": "微信", "occurred_at": "2026-06-14"}, {})
    assert r["ok"] is False
    assert len(db.query_records()) == 0


def test_add_record_invalid_category_coerced_to_other():
    r = dispatch_tool("add_record", {"amount": 10, "category": "乱写", "account": "微信", "occurred_at": "2026-06-14"}, {})
    assert r["ok"] and r["record"]["category"] == "其他"


def test_add_record_defaults_account_status_source():
    r = dispatch_tool("add_record", {"amount": 10, "category": "餐饮", "occurred_at": "2026-06-14"}, {"source": "text"})
    rec = r["record"]
    assert rec["account"] == "现金" and rec["status"] == "confirmed" and rec["source"] == "text"


def test_add_record_status_pending_passthrough():
    r = dispatch_tool("add_record", {"amount": 10, "category": "餐饮", "account": "微信",
                                     "occurred_at": "2026-06-14", "status": "pending"}, {})
    assert r["record"]["status"] == "pending"


def test_add_record_negative_amount_rejected():
    # 当前实现：amount<=0 直接拒绝（_coerce 里的 abs() 对 add 路径其实是死代码）
    r = dispatch_tool("add_record", {"amount": -30, "category": "餐饮", "account": "微信", "occurred_at": "2026-06-14"}, {})
    assert r["ok"] is False


def test_get_summary_tool():
    db.insert_record({"amount": 10, "category": "餐饮", "account": "微信", "occurred_at": "2026-06-14", "type": "expense"})
    r = dispatch_tool("get_summary", {"month": "2026-06"}, {})
    assert r["ok"] and r["summary"]["total"] == 10


def test_query_records_tool():
    db.insert_record({"amount": 10, "category": "餐饮", "account": "微信", "occurred_at": "2026-06-14", "type": "expense"})
    r = dispatch_tool("query_records", {"category": "餐饮"}, {})
    assert r["ok"] and r["count"] == 1


def test_unknown_tool_returns_error():
    assert dispatch_tool("nope", {}, {})["ok"] is False


def test_update_record_dispatch():
    rec = db.insert_record({"amount": 35, "category": "餐饮", "account": "微信", "occurred_at": "2026-06-14", "type": "expense"})
    r = dispatch_tool("update_record", {"id": rec["id"], "amount": 50, "category": "购物"}, {})
    assert r["ok"] and r["record"]["amount"] == 50 and r["record"]["category"] == "购物"


def test_update_record_missing_id():
    assert dispatch_tool("update_record", {"amount": 1}, {})["ok"] is False


def test_delete_record_dispatch():
    rec = db.insert_record({"amount": 1, "category": "餐饮", "account": "微信", "occurred_at": "2026-06-14", "type": "expense"})
    assert dispatch_tool("delete_record", {"id": rec["id"]}, {})["ok"] is True
    assert dispatch_tool("delete_record", {"id": rec["id"]}, {})["ok"] is False   # 已删，再删失败


def test_set_budget_dispatch():
    assert dispatch_tool("set_budget", {"category": "餐饮", "amount": 1000}, {})["ok"]
    assert db.get_budgets().get("餐饮") == 1000
    assert dispatch_tool("set_budget", {"category": "总预算", "amount": 5000}, {})["ok"]
    assert db.get_budgets().get(db.TOTAL_KEY) == 5000
    dispatch_tool("set_budget", {"category": "餐饮", "amount": 0}, {})              # 0 取消
    assert "餐饮" not in db.get_budgets()


def test_set_budget_invalid_category():
    assert dispatch_tool("set_budget", {"category": "乱写", "amount": 100}, {})["ok"] is False


def test_add_record_budget_alert_over():
    db.set_budget("餐饮", 100)
    dispatch_tool("add_record", {"amount": 80, "category": "餐饮", "account": "微信", "occurred_at": "2026-06-14"}, {})
    r = dispatch_tool("add_record", {"amount": 50, "category": "餐饮", "account": "微信", "occurred_at": "2026-06-14"}, {})
    assert "budget_alert" in r and "超预算" in r["budget_alert"]                    # 80+50=130 > 100


def test_remember_dispatch():
    r = dispatch_tool("remember", {"keyword": "星巴克", "category": "餐饮"}, {})
    assert r["ok"] and db.get_prefs().get("星巴克") == "餐饮"


def test_remember_invalid_category():
    assert dispatch_tool("remember", {"keyword": "X", "category": "乱写"}, {})["ok"] is False
