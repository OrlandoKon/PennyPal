"""端到端流程（不经 LLM）：落账后走 API 列表/汇总/编辑/删除，验证联动一致；含聚合与边界。"""
from backend import db
from backend.tools import dispatch_tool
from tests.conftest import seed


def test_full_lifecycle(client):
    a = seed(amount=35, category="餐饮", occurred_at="2026-06-10")
    b = seed(amount=20, category="交通", occurred_at="2026-06-11")
    assert len(client.get("/api/records?month=2026-06").json()["records"]) == 2

    s = client.get("/api/summary?month=2026-06").json()
    assert s["total"] == 55 and s["by_category"][0]["category"] == "餐饮"

    # 编辑金额 → 汇总联动
    client.put(f"/api/records/{a['id']}", json={"amount": 50})
    assert client.get("/api/summary?month=2026-06").json()["total"] == 70

    # 编辑日期挪到上月 → 本月少一笔
    client.put(f"/api/records/{b['id']}", json={"occurred_at": "2026-05-31"})
    assert len(client.get("/api/records?month=2026-06").json()["records"]) == 1
    assert client.get("/api/summary?month=2026-06").json()["total"] == 50

    # 删除 → 归零
    client.delete(f"/api/records/{a['id']}")
    assert client.get("/api/summary?month=2026-06").json()["total"] == 0


def test_dispatch_add_then_api_visible(client):
    assert dispatch_tool("add_record", {"amount": 99, "category": "数码", "account": "支付宝", "occurred_at": "2026-06-14"}, {"source": "text"})["ok"]
    recs = client.get("/api/records?month=2026-06").json()["records"]
    assert any(x["category"] == "数码" and x["amount"] == 99 for x in recs)


def test_query_date_boundaries_inclusive():
    seed(occurred_at="2026-06-10", amount=1)
    seed(occurred_at="2026-06-20", amount=2)
    seed(occurred_at="2026-06-30", amount=3)
    assert len(db.query_records(start_date="2026-06-10", end_date="2026-06-30")) == 3   # 两端包含
    assert len(db.query_records(start_date="2026-06-11", end_date="2026-06-29")) == 1


def test_summary_trend_multimonth():
    for m, amt in [("2026-04", 100), ("2026-05", 200), ("2026-06", 300)]:
        db.insert_record({"amount": amt, "category": "餐饮", "account": "微信", "occurred_at": m + "-10", "type": "expense"})
    s = db.summary("2026-06")
    trend = {t["month"]: t["total"] for t in s["trend"]}
    assert trend["2026-04"] == 100 and trend["2026-05"] == 200 and trend["2026-06"] == 300
    assert s["change_ratio"] == round((300 - 200) / 200, 4)


def test_pending_roundtrip(client):
    r = dispatch_tool("add_record", {"amount": 200, "category": "服饰", "account": "微信", "occurred_at": "2026-06-14", "status": "pending"}, {})
    assert r["record"]["status"] == "pending"
    assert any(x["status"] == "pending" for x in client.get("/api/records?month=2026-06").json()["records"])


def test_datetime_occurred_at_preserved():
    r = db.insert_record({"amount": 10, "category": "餐饮", "account": "微信", "occurred_at": "2026-06-14T12:30:00", "type": "expense"})
    assert r["occurred_at"] == "2026-06-14T12:30:00"          # 合法 datetime 不被截断
    assert any(x["id"] == r["id"] for x in db.list_by_month("2026-06"))


def test_summary_includes_budgets():
    db.set_budget("餐饮", 800)
    assert db.summary("2026-06")["budgets"].get("餐饮") == 800


def test_budget_api(client):
    client.put("/api/budgets", json={"category": "餐饮", "amount": 800})
    client.put("/api/budgets", json={"category": "总预算", "amount": 5000})
    b = client.get("/api/budgets").json()["budgets"]
    assert b["餐饮"] == 800 and b[db.TOTAL_KEY] == 5000


def test_summary_by_account_and_daily():
    db.insert_record({"amount": 30, "category": "餐饮", "account": "微信", "occurred_at": "2026-06-10", "type": "expense"})
    db.insert_record({"amount": 20, "category": "交通", "account": "支付宝", "occurred_at": "2026-06-10", "type": "expense"})
    db.insert_record({"amount": 50, "category": "购物", "account": "微信", "occurred_at": "2026-06-15", "type": "expense"})
    s = db.summary("2026-06")
    acct = {a["account"]: a["amount"] for a in s["by_account"]}
    assert acct["微信"] == 80 and acct["支付宝"] == 20
    assert s["daily"]["2026-06-10"] == 50 and s["daily"]["2026-06-15"] == 50
    assert s["trend"][-1]["income"] == 0


def test_summary_top_records():
    db.insert_record({"amount": 30, "category": "餐饮", "account": "微信", "occurred_at": "2026-06-10", "type": "expense"})
    db.insert_record({"amount": 200, "category": "服饰", "account": "微信", "occurred_at": "2026-06-11", "type": "expense", "note": "外套"})
    db.insert_record({"amount": 80, "category": "数码", "account": "支付宝", "occurred_at": "2026-06-12", "type": "expense"})
    top = db.summary("2026-06")["top_records"]
    assert [t["amount"] for t in top] == [200, 80, 30]
    assert top[0]["category"] == "服饰" and top[0]["note"] == "外套"


def test_anomaly_insight():
    for m in ("2026-03", "2026-04", "2026-05"):
        db.insert_record({"amount": 100, "category": "餐饮", "account": "微信", "occurred_at": m + "-10", "type": "expense"})
    db.insert_record({"amount": 500, "category": "餐饮", "account": "微信", "occurred_at": "2026-06-10", "type": "expense"})
    s = db.summary("2026-06")
    assert "餐饮" in s["insight"] and "均值" in s["insight"]


def test_summary_includes_prefs():
    db.set_pref("星巴克", "餐饮")
    assert db.summary("2026-06")["prefs"].get("星巴克") == "餐饮"


def test_prefs_delete_api(client):
    db.set_pref("星巴克", "餐饮")
    r = client.delete("/api/prefs/星巴克").json()
    assert "星巴克" not in r["prefs"]
