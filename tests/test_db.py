"""数据层与聚合的确定性测试。"""
from backend import db
from tests.conftest import seed


def test_insert_and_get():
    r = seed(amount=35, category="餐饮")
    assert r["id"] >= 1 and r["amount"] == 35 and r["created_at"]
    assert db.get_record(r["id"])["category"] == "餐饮"


def test_list_by_month_filters_by_month():
    seed(amount=10, occurred_at="2026-06-01")
    seed(amount=20, occurred_at="2026-05-31")
    june = db.list_by_month("2026-06")
    assert len(june) == 1 and june[0]["amount"] == 10


def test_query_filters():
    seed(amount=10, category="餐饮", occurred_at="2026-06-10", note="午饭")
    seed(amount=20, category="交通", account="支付宝", occurred_at="2026-06-12", note="打车")
    assert len(db.query_records(category="餐饮")) == 1
    assert len(db.query_records(start_date="2026-06-11", end_date="2026-06-30")) == 1
    assert len(db.query_records(keyword="打车")) == 1


def test_update_and_delete():
    r = seed()
    u = db.update_record(r["id"], {"amount": 99, "category": "购物"})
    assert u["amount"] == 99 and u["category"] == "购物"
    assert db.delete_record(r["id"]) is True
    assert db.get_record(r["id"]) is None
    assert db.delete_record(999999) is False


def test_top_account():
    for _ in range(3):
        seed(account="微信")
    seed(account="现金")
    assert db.top_account() == "微信"


def test_shift_month_year_boundary():
    assert db._shift_month("2026-01", -1) == "2025-12"
    assert db._shift_month("2026-12", 1) == "2027-01"
    assert db._shift_month("2026-06", -5) == "2026-01"


def test_summary_total_change_ratio_trend():
    seed(amount=100, category="餐饮", occurred_at="2026-05-10")          # 上月
    seed(amount=120, category="餐饮", occurred_at="2026-06-10")          # 本月
    seed(amount=30, category="交通", occurred_at="2026-06-11")
    seed(amount=5000, category="收入", type="income", account="银行卡", occurred_at="2026-06-01")
    s = db.summary("2026-06")
    assert s["total"] == 150 and s["income_total"] == 5000
    assert s["prev_total"] == 100 and s["change_ratio"] == 0.5
    assert s["by_category"][0]["category"] == "餐饮"   # 120 > 30
    assert len(s["trend"]) == 6 and s["trend"][-1]["month"] == "2026-06"
    assert "餐饮" in s["insight"]


def test_summary_prev_zero_no_division_error():
    seed(amount=50, occurred_at="2026-06-10")
    s = db.summary("2026-06")               # 上月无记录
    assert s["prev_total"] == 0 and s["change_ratio"] is None
    assert "本月支出" in s["insight"]


def test_summary_empty_month():
    s = db.summary("2030-01")
    assert s["total"] == 0 and s["by_category"] == [] and "还没有支出" in s["insight"]


def test_summary_excludes_income_from_expense_pie():
    seed(amount=5000, category="收入", type="income", occurred_at="2026-06-01")
    s = db.summary("2026-06")
    assert all(c["category"] != "收入" for c in s["by_category"])
    assert s["total"] == 0 and s["income_total"] == 5000
