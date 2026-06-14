"""run_agent 智能体循环的确定性测试（用假客户端替代真实模型，无 API 成本）。

覆盖：单笔工具调用、一次多笔(批量)、多轮(先查后记)、纯文字不入账、最大轮数兜底，
并校验循环契约：assistant(tool_calls) 回填 + 每个 tool 消息带正确 tool_call_id。
"""
import json
from types import SimpleNamespace

from backend import agent, db


def _toolcall(cid, name, args):
    return SimpleNamespace(id=cid, function=SimpleNamespace(name=name, arguments=json.dumps(args)))


def _msg(content=None, tool_calls=None):
    return SimpleNamespace(content=content, tool_calls=tool_calls)


def _resp(message):
    return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason="stop")])


def _script(monkeypatch, responses):
    it = iter(responses)
    monkeypatch.setattr(agent, "_create_with_retry", lambda messages: next(it))


def _base():
    return [{"role": "system", "content": "sys"}, {"role": "user", "content": "u"}]


def test_loop_single_add(monkeypatch):
    tc = _toolcall("call_1", "add_record", {"amount": 35, "category": "餐饮", "account": "微信", "occurred_at": "2026-06-14"})
    _script(monkeypatch, [_resp(_msg(tool_calls=[tc])), _resp(_msg(content="已记：餐饮 ¥35（微信）✓"))])
    messages = _base()
    reply, added, dirty = agent.run_agent(messages, {"source": "text"})
    assert reply == "已记：餐饮 ¥35（微信）✓" and dirty is True
    assert len(added) == 1 and added[0]["category"] == "餐饮" and added[0]["amount"] == 35
    assert len(db.query_records()) == 1
    # 循环契约：assistant 带 tool_calls，且每个 tool 消息 tool_call_id 配对
    assistant = [m for m in messages if isinstance(m, dict) and m["role"] == "assistant" and m.get("tool_calls")]
    tool = [m for m in messages if isinstance(m, dict) and m["role"] == "tool"]
    assert assistant and tool and tool[0]["tool_call_id"] == "call_1"


def test_loop_batch_three(monkeypatch):
    tcs = [_toolcall(f"c{i}", "add_record", {"amount": a, "category": c, "account": "微信", "occurred_at": "2026-06-14"})
           for i, (a, c) in enumerate([(12, "餐饮"), (18, "交通"), (60, "购物")])]
    _script(monkeypatch, [_resp(_msg(tool_calls=tcs)), _resp(_msg(content="已记 3 笔"))])
    reply, added, dirty = agent.run_agent(_base(), {})
    assert len(added) == 3 and len(db.query_records()) == 3


def test_loop_query_then_add(monkeypatch):
    q = _toolcall("q", "query_records", {"keyword": "x"})
    a = _toolcall("a", "add_record", {"amount": 35, "category": "餐饮", "account": "微信", "occurred_at": "2026-06-14"})
    _script(monkeypatch, [_resp(_msg(tool_calls=[q])), _resp(_msg(tool_calls=[a])), _resp(_msg(content="done"))])
    reply, added, dirty = agent.run_agent(_base(), {})
    assert reply == "done" and len(added) == 1 and len(db.query_records()) == 1


def test_loop_text_only_no_record(monkeypatch):
    _script(monkeypatch, [_resp(_msg(content="这 35 是什么消费？"))])
    reply, added, dirty = agent.run_agent(_base(), {})
    assert reply == "这 35 是什么消费？" and added == [] and dirty is False and len(db.query_records()) == 0


def test_loop_max_iters_guard(monkeypatch):
    tc = _toolcall("c", "query_records", {})
    monkeypatch.setattr(agent, "_create_with_retry", lambda messages: _resp(_msg(tool_calls=[tc])))
    reply, added, dirty = agent.run_agent(_base(), {})
    assert "过多" in reply   # 命中 MAX_ITERS 兜底，不死循环


def test_loop_update_sets_dirty(monkeypatch):
    rec = db.insert_record({"amount": 35, "category": "餐饮", "account": "微信", "occurred_at": "2026-06-14", "type": "expense"})
    tc = _toolcall("u", "update_record", {"id": rec["id"], "amount": 50})
    _script(monkeypatch, [_resp(_msg(tool_calls=[tc])), _resp(_msg(content="已改：餐饮 ¥50 ✓"))])
    reply, added, dirty = agent.run_agent(_base(), {})
    assert dirty is True and added == [] and db.get_record(rec["id"])["amount"] == 50


def test_loop_delete_sets_dirty(monkeypatch):
    rec = db.insert_record({"amount": 1, "category": "餐饮", "account": "微信", "occurred_at": "2026-06-14", "type": "expense"})
    tc = _toolcall("d", "delete_record", {"id": rec["id"]})
    _script(monkeypatch, [_resp(_msg(tool_calls=[tc])), _resp(_msg(content="已删 ✓"))])
    reply, added, dirty = agent.run_agent(_base(), {})
    assert dirty is True and db.get_record(rec["id"]) is None


def test_loop_delete_confirm_flow(monkeypatch):
    rec = db.insert_record({"amount": 18, "category": "交通", "account": "支付宝", "occurred_at": "2026-06-14", "type": "expense"})
    # 第 1 轮：模型只回确认问句、不调用工具 → 不删
    _script(monkeypatch, [_resp(_msg(content="确定删除「交通 ¥18（支付宝）」吗？"))])
    reply, added, dirty = agent.run_agent(_base(), {})
    assert dirty is False and db.get_record(rec["id"]) is not None
    # 第 2 轮：用户确认后，模型调用 delete_record → 删
    tc = _toolcall("d", "delete_record", {"id": rec["id"]})
    _script(monkeypatch, [_resp(_msg(tool_calls=[tc])), _resp(_msg(content="已删：交通 ¥18 ✓"))])
    reply, added, dirty = agent.run_agent(_base(), {})
    assert dirty is True and db.get_record(rec["id"]) is None


def test_loop_set_budget_sets_dirty(monkeypatch):
    tc = _toolcall("b", "set_budget", {"category": "餐饮", "amount": 1000})
    _script(monkeypatch, [_resp(_msg(tool_calls=[tc])), _resp(_msg(content="已设餐饮预算 ¥1000"))])
    reply, added, dirty = agent.run_agent(_base(), {})
    assert dirty is True and db.get_budgets().get("餐饮") == 1000
