"""写库前校验/规整的护栏（对应 BUGS.md G1–G4）。

这些点曾经是已知缺陷（xfail），修复后转为回归护栏。
"""
from datetime import date

from backend import db
from backend.config import CATEGORIES
from tests.conftest import seed


def test_bad_occurred_at_is_normalized_to_today():
    # G1：模型没把"昨天"换算成日期、原样传入 → 规整为今天，且能在本月视图看到（不再"消失"）
    r = db.insert_record({"amount": 10, "category": "餐饮", "account": "微信",
                          "occurred_at": "昨天", "type": "expense"})
    today = date.today().isoformat()
    assert r["occurred_at"] == today
    assert any(x["id"] == r["id"] for x in db.list_by_month(today[:7]))


def test_put_invalid_category_normalized(client):
    # G2：编辑写入枚举外分类 → 规整为「其他」
    rec = seed()
    body = client.put(f"/api/records/{rec['id']}", json={"category": "乱写分类"}).json()
    assert body["category"] in CATEGORIES


def test_put_negative_amount_normalized(client):
    # G2/G3：编辑写入负金额 → 取绝对值
    rec = seed()
    body = client.put(f"/api/records/{rec['id']}", json={"amount": -50}).json()
    assert body["amount"] == 50.0


def test_put_bad_type_normalized(client):
    # G2：编辑写入非法 type → 规整为合法值
    rec = seed()
    body = client.put(f"/api/records/{rec['id']}", json={"type": "乱写"}).json()
    assert body["type"] in ("expense", "income")


def test_type_follows_category_consistency():
    # G4：type 由 category 决定，杜绝 income+餐饮 这类不一致
    r1 = db.insert_record({"amount": 10, "category": "餐饮", "type": "income",
                           "account": "微信", "occurred_at": "2026-06-14"})
    assert r1["type"] == "expense"
    r2 = db.insert_record({"amount": 9999, "category": "收入", "type": "expense",
                           "account": "银行卡", "occurred_at": "2026-06-14"})
    assert r2["type"] == "income"
