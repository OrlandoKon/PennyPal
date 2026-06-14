"""Agent 行为评测（真实调用 DeepSeek，非确定性）。

默认跳过；要跑需同时满足 DEEPSEEK_API_KEY 已配 + RUN_AGENT=1：
    RUN_AGENT=1 pytest tests/test_agent.py -q
这些用例编码"合理预期"，失败即暴露不合理的点。
"""
import os
from datetime import date, timedelta

import pytest

from backend import db

pytestmark = pytest.mark.skipif(
    not (os.environ.get("DEEPSEEK_API_KEY") and os.environ.get("RUN_AGENT")),
    reason="需要 DEEPSEEK_API_KEY 且 RUN_AGENT=1（会真实调用模型，非确定性）",
)


def _chat(client, message, history=None):
    return client.post("/api/chat", json={"message": message, "history": history or []}).json()


def test_single_expense(client):
    a = (_chat(client, "中午吃饭35，微信付的")["added_records"] or [{}])[0]
    assert a.get("category") == "餐饮" and a.get("amount") == 35 and a.get("account") == "微信"


def test_batch_splits_three(client):
    d = _chat(client, "早餐12 打车18 超市买菜60")
    assert len(d["added_records"]) == 3


def test_missing_amount_asks_not_records(client):
    d = _chat(client, "35")
    assert not d["added_records"]


def test_new_category_clothing(client):
    a = (_chat(client, "买条裤子150 微信")["added_records"] or [{}])[0]
    assert a.get("category") == "服饰"


def test_new_category_digital(client):
    a = (_chat(client, "买个电脑摄像头350 支付宝")["added_records"] or [{}])[0]
    assert a.get("category") == "数码"


def test_relative_date_yesterday(client):
    a = (_chat(client, "昨天打车20 微信")["added_records"] or [{}])[0]
    assert (a.get("occurred_at") or "")[:10] == (date.today() - timedelta(days=1)).isoformat()


def test_fuzzy_marks_pending(client):
    a = (_chat(client, "昨天买衣服好像两百多")["added_records"] or [{}])[0]
    assert a.get("status") == "pending"


def test_query_then_answer(client):
    _chat(client, "中午吃饭35 微信")
    d = _chat(client, "这个月吃饭花了多少？")
    assert d["reply"] and not d["added_records"]   # 查询不应新增记录


class _Conv:
    """模拟前端多轮：成功入账即清空历史，否则保留（用于追问）。"""

    def __init__(self, client):
        self.client = client
        self.history = []

    def say(self, msg):
        d = self.client.post("/api/chat", json={"message": msg, "history": self.history}).json()
        if d.get("added_records"):
            self.history = []
        else:
            self.history.append({"role": "user", "content": msg})
            if d.get("reply"):
                self.history.append({"role": "assistant", "content": d["reply"]})
        return d


def test_multiturn_clarification_then_record(client):
    conv = _Conv(client)
    assert not conv.say("35").get("added_records")            # 缺消费内容 → 追问
    a = (conv.say("午饭").get("added_records") or [{}])[0]      # 补一句 → 记成餐饮35
    assert a.get("category") == "餐饮" and a.get("amount") == 35


def test_multiturn_clarification_amount(client):
    conv = _Conv(client)
    assert not conv.say("打车").get("added_records")           # 缺金额 → 追问
    a = (conv.say("20块").get("added_records") or [{}])[0]
    assert a.get("category") == "交通" and a.get("amount") == 20


def test_sequential_all_recorded(client):
    conv = _Conv(client)
    items = ["中午吃饭35 微信", "打车18 支付宝", "买条裤子150 微信", "看电影45 微信", "超市买菜60 微信"]
    n = sum(1 for it in items if conv.say(it).get("added_records"))
    assert n == len(items) and len(db.query_records()) == len(items)


def test_account_inference_from_history(client):
    conv = _Conv(client)
    for _ in range(3):
        conv.say("买奶茶15 微信")
    a = (conv.say("买包子8").get("added_records") or [{}])[0]   # 不说账户 → 默认最常用 微信
    assert a.get("account") == "微信"


def test_income_recorded(client):
    a = (_Conv(client).say("发工资8000 到银行卡").get("added_records") or [{}])[0]
    assert a.get("category") == "收入" and a.get("type") == "income" and a.get("amount") == 8000


def test_query_total_mentions_number(client):
    conv = _Conv(client)
    conv.say("中午吃饭35 微信")
    conv.say("晚饭40 微信")
    d = conv.say("这个月吃饭一共花了多少？")
    assert not d.get("added_records")            # 查询不新增
    assert "75" in (d.get("reply") or "")        # 35 + 40 = 75


def test_conversational_edit(client):
    conv = _Conv(client)
    conv.say("中午吃饭35 微信")
    d = conv.say("把刚才那笔改成50")
    assert d.get("dirty") is True
    assert db.query_records(category="餐饮")[0]["amount"] == 50


def test_conversational_delete_with_confirm(client):
    conv = _Conv(client)
    conv.say("打车18 支付宝")
    d1 = conv.say("把刚才那笔删了")          # 先轻确认，不删
    assert not d1.get("dirty") and len(db.query_records()) == 1
    d2 = conv.say("确定")                     # 确认后才删
    assert d2.get("dirty") is True and len(db.query_records()) == 0


def test_conversational_set_budget(client):
    _Conv(client).say("把餐饮预算设成1000")
    assert db.get_budgets().get("餐饮") == 1000


def test_overspend_alert_mentioned(client):
    db.set_budget("餐饮", 100)
    conv = _Conv(client)
    conv.say("中午吃饭80 微信")
    reply = conv.say("晚饭又花了50 微信").get("reply") or ""    # 80+50=130 > 100
    assert ("超" in reply) or ("⚠️" in reply) or ("预算" in reply)


def test_ai_insight_endpoint(client):
    db.insert_record({"amount": 100, "category": "餐饮", "account": "微信",
                      "occurred_at": date.today().isoformat(), "type": "expense"})
    r = client.get("/api/insight?month=" + date.today().strftime("%Y-%m")).json()
    assert r.get("insight")


def test_planning_savings_advice(client):
    conv = _Conv(client)
    conv.say("中午吃饭35 微信")
    conv.say("晚饭外卖80 微信")
    d = conv.say("这个月怎么省钱？")
    assert not d.get("added_records")               # 建议类不新增记录
    assert d.get("reply") and len(d["reply"]) > 10


def test_reflection_anomaly_mentioned(client):
    conv = _Conv(client)
    for _ in range(3):
        conv.say("中午吃饭30 微信")
    reply = conv.say("晚饭花了300 微信").get("reply") or ""   # 300 远高于均值30
    assert ("高" in reply) or ("平时" in reply) or ("核对" in reply) or ("确认" in reply)


def test_memory_learn_and_apply(client):
    conv = _Conv(client)
    conv.say("记住星巴克算餐饮")
    assert db.get_prefs().get("星巴克") == "餐饮"
    a = (conv.say("星巴克35").get("added_records") or [{}])[0]
    assert a.get("category") == "餐饮"
