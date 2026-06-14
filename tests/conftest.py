"""测试公共夹具：强制用临时库（绝不碰 data/expense.db），每个用例前清空 records。"""
import os
import tempfile

# 必须在导入 backend 之前设好 DB_PATH（config 在导入时读取）
os.environ["DB_PATH"] = os.path.join(tempfile.gettempdir(), "pennypal_pytest.db")

import pytest  # noqa: E402

from backend import db  # noqa: E402
from backend.main import app  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_db():
    db.init_db()
    conn = db._connect()
    conn.execute("DELETE FROM records")
    conn.execute("DELETE FROM budgets")
    conn.commit()
    conn.close()
    yield


@pytest.fixture
def client():
    return TestClient(app, raise_server_exceptions=False)


def seed(**kw):
    """插入一条带默认值的记录，用例可覆盖任意字段。"""
    rec = {"amount": 10.0, "type": "expense", "category": "餐饮",
           "account": "微信", "occurred_at": "2026-06-14"}
    rec.update(kw)
    return db.insert_record(rec)
