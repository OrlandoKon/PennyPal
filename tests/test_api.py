"""HTTP 端点（不经 LLM）的确定性测试。"""
from tests.conftest import seed


def test_records_endpoint(client):
    seed(occurred_at="2026-06-14")
    r = client.get("/api/records?month=2026-06")
    assert r.status_code == 200 and len(r.json()["records"]) == 1


def test_records_default_month(client):
    r = client.get("/api/records")
    assert r.status_code == 200 and "records" in r.json()


def test_summary_endpoint(client):
    r = client.get("/api/summary?month=2026-06")
    assert r.status_code == 200 and "by_category" in r.json() and "trend" in r.json()


def test_put_record(client):
    rec = seed()
    r = client.put(f"/api/records/{rec['id']}", json={"amount": 88})
    assert r.status_code == 200 and r.json()["amount"] == 88


def test_put_missing_returns_404(client):
    assert client.put("/api/records/999999", json={"amount": 1}).status_code == 404


def test_delete_record(client):
    rec = seed()
    assert client.delete(f"/api/records/{rec['id']}").status_code == 200
    assert client.delete(f"/api/records/{rec['id']}").status_code == 404


def test_chat_requires_input(client):
    assert client.post("/api/chat", json={}).status_code == 400


def test_index_served(client):
    r = client.get("/")
    assert r.status_code == 200 and "智能记账" in r.text


def test_static_app_js_served(client):
    r = client.get("/static/app.js")
    assert r.status_code == 200 and "CATCOLOR" in r.text
