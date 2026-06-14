"""更全面的边界与综合场景测试（确定性）。"""
from datetime import date
from unittest.mock import patch

from backend import agent, db
from backend.tools import dispatch_tool
from tests.conftest import seed


def test_insight_fallback_when_model_fails():
    # AI 洞察的模型调用失败时，回退到模板洞察
    seed(amount=50, category="餐饮", occurred_at="2026-06-10")
    s = db.summary("2026-06")
    with patch.object(agent.client.chat.completions, "create", side_effect=RuntimeError("boom")):
        assert agent.generate_insight(s) == s["insight"]


def test_budget_alert_at_80pct_warns_not_over():
    db.set_budget("餐饮", 100)
    r = dispatch_tool("add_record", {"amount": 80, "category": "餐饮", "account": "微信", "occurred_at": "2026-06-14"}, {})
    assert "budget_alert" in r and "80%" in r["budget_alert"] and "超" not in r["budget_alert"]


def test_budget_alert_below_threshold_silent():
    db.set_budget("餐饮", 100)
    r = dispatch_tool("add_record", {"amount": 30, "category": "餐饮", "account": "微信", "occurred_at": "2026-06-14"}, {})
    assert "budget_alert" not in r            # 30% < 80%，不提醒


def test_budget_alert_total():
    db.set_budget(db.TOTAL_KEY, 100)
    dispatch_tool("add_record", {"amount": 120, "category": "购物", "account": "微信", "occurred_at": "2026-06-14"}, {})
    r = dispatch_tool("add_record", {"amount": 1, "category": "餐饮", "account": "微信", "occurred_at": "2026-06-14"}, {})
    assert "budget_alert" in r and "总预算" in r["budget_alert"]


def test_update_category_to_income_flips_type():
    rec = seed(amount=100, category="餐饮", type="expense")
    u = dispatch_tool("update_record", {"id": rec["id"], "category": "收入"}, {})
    assert u["record"]["type"] == "income"


def test_delete_nonexistent_returns_error():
    assert dispatch_tool("delete_record", {"id": 999999}, {})["ok"] is False


def test_full_scenario(client):
    # 综合：记多笔 → 设预算 → 改 → 删，汇总/预算全程一致
    for amt, cat in [(35, "餐饮"), (18, "交通"), (200, "服饰")]:
        dispatch_tool("add_record", {"amount": amt, "category": cat, "account": "微信", "occurred_at": "2026-06-14"}, {})
    db.set_budget("餐饮", 30)                       # 35 > 30 已超
    s = client.get("/api/summary?month=2026-06").json()
    assert s["total"] == 253 and s["budgets"]["餐饮"] == 30

    fu = db.query_records(category="服饰")[0]        # 改服饰 200 → 150
    client.put(f"/api/records/{fu['id']}", json={"amount": 150})
    tr = db.query_records(category="交通")[0]        # 删交通
    client.delete(f"/api/records/{tr['id']}")

    s2 = client.get("/api/summary?month=2026-06").json()
    assert s2["total"] == 185 and all(c["category"] != "交通" for c in s2["by_category"])


def test_category_stats():
    db.insert_record({"amount": 10, "category": "餐饮", "account": "微信", "occurred_at": "2026-06-10", "type": "expense"})
    db.insert_record({"amount": 30, "category": "餐饮", "account": "微信", "occurred_at": "2026-06-11", "type": "expense"})
    avg, cnt = db.category_stats("餐饮")
    assert cnt == 2 and avg == 20


def test_add_record_anomaly_note():
    for _ in range(3):
        dispatch_tool("add_record", {"amount": 30, "category": "餐饮", "account": "微信", "occurred_at": "2026-06-10"}, {})
    r = dispatch_tool("add_record", {"amount": 200, "category": "餐饮", "account": "微信", "occurred_at": "2026-06-12"}, {})
    assert "anomaly_note" in r and "均值" in r["anomaly_note"]


def test_no_anomaly_when_little_history():
    r = dispatch_tool("add_record", {"amount": 200, "category": "餐饮", "account": "微信", "occurred_at": "2026-06-12"}, {})
    assert "anomaly_note" not in r            # 历史不足 3 条，不触发
