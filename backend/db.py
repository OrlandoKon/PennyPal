"""SQLite 账本：初始化（WAL + 索引）与读写。

并发策略（TechnicalRoadmap.md §5.7）：
- WAL 模式让读不阻塞写；
- demo 单用户、写入量极低，用一把进程内写锁串行化写操作；
- 每次操作开独立连接，append-only 心智，无复杂事务。
"""
import os
import sqlite3
import threading
from datetime import datetime
from typing import Optional

from .config import DATA_DIR, DB_PATH
from .validation import normalize_fields

_write_lock = threading.Lock()

_ALLOWED_UPDATE = {"amount", "type", "category", "account", "occurred_at", "note", "status"}

TOTAL_KEY = "__total__"   # budgets 表里"总预算"的特殊 category 键


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=10, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db() -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = _connect()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS records (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                amount      REAL    NOT NULL,
                type        TEXT    NOT NULL DEFAULT 'expense',
                category    TEXT    NOT NULL,
                account     TEXT    NOT NULL,
                occurred_at TEXT    NOT NULL,
                note        TEXT,
                raw_input   TEXT,
                source      TEXT    DEFAULT 'text',
                status      TEXT    DEFAULT 'confirmed',
                created_at  TEXT    NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_records_occurred ON records(occurred_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_records_type_occurred ON records(type, occurred_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_records_category ON records(category)")
        conn.execute(
            """CREATE TABLE IF NOT EXISTS budgets (
                category TEXT PRIMARY KEY,
                amount   REAL NOT NULL
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS prefs (
                keyword  TEXT PRIMARY KEY,
                category TEXT NOT NULL
            )"""
        )
        conn.commit()
    finally:
        conn.close()


def _row_to_dict(row: Optional[sqlite3.Row]) -> Optional[dict]:
    return {k: row[k] for k in row.keys()} if row is not None else None


# ---------- 写 ----------

def insert_record(rec: dict) -> dict:
    rec = normalize_fields(rec)          # 写库前统一校验/规整
    now = datetime.now().isoformat(timespec="seconds")
    with _write_lock:
        conn = _connect()
        try:
            cur = conn.execute(
                """INSERT INTO records
                   (amount, type, category, account, occurred_at, note, raw_input, source, status, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    rec["amount"],
                    rec.get("type", "expense"),
                    rec["category"],
                    rec["account"],
                    rec["occurred_at"],
                    rec.get("note"),
                    rec.get("raw_input"),
                    rec.get("source", "text"),
                    rec.get("status", "confirmed"),
                    now,
                ),
            )
            conn.commit()
            row = conn.execute("SELECT * FROM records WHERE id=?", (cur.lastrowid,)).fetchone()
            return _row_to_dict(row)
        finally:
            conn.close()


def update_record(record_id: int, fields: dict) -> Optional[dict]:
    fields = {k: v for k, v in (fields or {}).items() if k in _ALLOWED_UPDATE}
    fields = normalize_fields(fields)    # 编辑同样走校验/规整（修复 G2/G3）
    if not fields:
        return get_record(record_id)
    sets = ", ".join(f"{k}=?" for k in fields)
    params = list(fields.values()) + [record_id]
    with _write_lock:
        conn = _connect()
        try:
            cur = conn.execute(f"UPDATE records SET {sets} WHERE id=?", params)
            conn.commit()
            if cur.rowcount == 0:
                return None
            row = conn.execute("SELECT * FROM records WHERE id=?", (record_id,)).fetchone()
            return _row_to_dict(row)
        finally:
            conn.close()


def delete_record(record_id: int) -> bool:
    with _write_lock:
        conn = _connect()
        try:
            cur = conn.execute("DELETE FROM records WHERE id=?", (record_id,))
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()


# ---------- 读 ----------

def get_record(record_id: int) -> Optional[dict]:
    conn = _connect()
    try:
        row = conn.execute("SELECT * FROM records WHERE id=?", (record_id,)).fetchone()
        return _row_to_dict(row)
    finally:
        conn.close()


def list_by_month(month: str) -> list:
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT * FROM records WHERE substr(occurred_at,1,7)=? ORDER BY occurred_at DESC, id DESC",
            (month,),
        ).fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        conn.close()


def query_records(start_date=None, end_date=None, category=None, keyword=None) -> list:
    conn = _connect()
    try:
        clauses, params = [], []
        if start_date:
            clauses.append("substr(occurred_at,1,10) >= ?")
            params.append(start_date)
        if end_date:
            clauses.append("substr(occurred_at,1,10) <= ?")
            params.append(end_date)
        if category:
            clauses.append("category = ?")
            params.append(category)
        if keyword:
            clauses.append("(note LIKE ? OR raw_input LIKE ? OR category LIKE ?)")
            kw = f"%{keyword}%"
            params += [kw, kw, kw]
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        rows = conn.execute(
            f"SELECT * FROM records{where} ORDER BY occurred_at DESC, id DESC LIMIT 200",
            params,
        ).fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        conn.close()


def top_account() -> Optional[str]:
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT account FROM records GROUP BY account ORDER BY COUNT(*) DESC LIMIT 1"
        ).fetchone()
        return row["account"] if row else None
    finally:
        conn.close()


# ---------- 预算 ----------

def get_budgets() -> dict:
    conn = _connect()
    try:
        return {r["category"]: float(r["amount"]) for r in conn.execute("SELECT category, amount FROM budgets").fetchall()}
    finally:
        conn.close()


def set_budget(category: str, amount) -> None:
    """amount>0 设置/更新；<=0 或非法 视为取消该预算。"""
    try:
        amt = round(abs(float(amount)), 2)
    except (TypeError, ValueError):
        amt = 0.0
    with _write_lock:
        conn = _connect()
        try:
            if amt > 0:
                conn.execute(
                    "INSERT INTO budgets(category, amount) VALUES(?, ?) "
                    "ON CONFLICT(category) DO UPDATE SET amount=excluded.amount",
                    (category, amt),
                )
            else:
                conn.execute("DELETE FROM budgets WHERE category=?", (category,))
            conn.commit()
        finally:
            conn.close()


def category_month_spent(month: str, category: str) -> float:
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT COALESCE(SUM(amount),0) AS t FROM records "
            "WHERE type='expense' AND category=? AND substr(occurred_at,1,7)=?",
            (category, month),
        ).fetchone()
        return float(row["t"] or 0)
    finally:
        conn.close()


def month_spent(month: str) -> float:
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT COALESCE(SUM(amount),0) AS t FROM records "
            "WHERE type='expense' AND substr(occurred_at,1,7)=?",
            (month,),
        ).fetchone()
        return float(row["t"] or 0)
    finally:
        conn.close()


def category_stats(category: str, exclude_id=None):
    """某分类历史支出的（均值, 条数），用于反思校验异常笔。"""
    conn = _connect()
    try:
        sql = "SELECT COUNT(*) AS c, COALESCE(AVG(amount),0) AS a FROM records WHERE type='expense' AND category=?"
        params = [category]
        if exclude_id is not None:
            sql += " AND id != ?"
            params.append(exclude_id)
        row = conn.execute(sql, params).fetchone()
        return float(row["a"] or 0), int(row["c"] or 0)
    finally:
        conn.close()


# ---------- 个性化记忆（关键词 → 分类偏好）----------

def get_prefs() -> dict:
    conn = _connect()
    try:
        return {r["keyword"]: r["category"] for r in conn.execute("SELECT keyword, category FROM prefs").fetchall()}
    finally:
        conn.close()


def set_pref(keyword: str, category: str) -> None:
    with _write_lock:
        conn = _connect()
        try:
            conn.execute(
                "INSERT INTO prefs(keyword, category) VALUES(?, ?) "
                "ON CONFLICT(keyword) DO UPDATE SET category=excluded.category",
                (keyword, category),
            )
            conn.commit()
        finally:
            conn.close()


def delete_pref(keyword: str) -> bool:
    with _write_lock:
        conn = _connect()
        try:
            cur = conn.execute("DELETE FROM prefs WHERE keyword=?", (keyword,))
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()


# ---------- 聚合（分析页 / get_summary 工具）----------

def _shift_month(month: str, delta: int) -> str:
    y, m = int(month[:4]), int(month[5:7])
    idx = y * 12 + (m - 1) + delta
    ny, nm = divmod(idx, 12)
    return f"{ny:04d}-{nm + 1:02d}"


def _month_total(conn, month: str, rec_type: str) -> float:
    row = conn.execute(
        "SELECT COALESCE(SUM(amount),0) AS t FROM records "
        "WHERE type=? AND substr(occurred_at,1,7)=?",
        (rec_type, month),
    ).fetchone()
    return float(row["t"] or 0)


def _cat_map(conn, month: str) -> dict:
    rows = conn.execute(
        "SELECT category, COALESCE(SUM(amount),0) AS amount FROM records "
        "WHERE type='expense' AND substr(occurred_at,1,7)=? GROUP BY category",
        (month,),
    ).fetchall()
    return {r["category"]: float(r["amount"]) for r in rows}


def _max_growth_category(conn, month: str, prev_month: str):
    cur, prev = _cat_map(conn, month), _cat_map(conn, prev_month)
    best = None
    for cat, c in cur.items():
        p = prev.get(cat, 0.0)
        if p > 0 and c > p:
            ratio = (c - p) / p
            if best is None or ratio > best[3]:
                best = (cat, c, p, ratio)
    return best


def _anomaly_category(conn, month, by_category, k=3):
    """找出本月明显高于近 k 个月均值的分类（异常支出）。返回 (分类, 本月额, 均值) 或 None。"""
    prior = [_cat_map(conn, _shift_month(month, -i)) for i in range(1, k + 1)]
    best = None
    for c in by_category:
        cat, cur = c["category"], c["amount"]
        avg = sum(p.get(cat, 0.0) for p in prior) / k
        if avg > 0 and cur > avg * 1.3:
            ratio = (cur - avg) / avg
            if best is None or ratio > best[3]:
                best = (cat, cur, avg, ratio)
    return best[:3] if best else None


def _build_insight(conn, month, total, prev_total, change_ratio, by_category) -> str:
    if total == 0:
        return f"{month} 还没有支出记录。"
    head = [f"本月支出 ¥{total:.0f}"]
    if change_ratio is not None:
        arrow = "↑" if change_ratio > 0 else ("↓" if change_ratio < 0 else "—")
        head.append(f"环比上月{arrow}{abs(change_ratio) * 100:.0f}%")
    elif prev_total == 0:
        head.append("上月无支出记录")
    text = "，".join(head) + "。"
    top = by_category[0]
    text += f"其中「{top['category']}」¥{top['amount']:.0f} 占比最高（{top['amount'] / total * 100:.0f}%）。"
    anomaly = _anomaly_category(conn, month, by_category)
    if anomaly:
        cat, c, avg = anomaly
        text += f"「{cat}」¥{c:.0f}，比近 3 个月均值（¥{avg:.0f}）多 {(c - avg) / avg * 100:.0f}%，注意。"
    return text


def summary(month: str) -> dict:
    conn = _connect()
    try:
        cats = _cat_map(conn, month)
        by_category = sorted(
            ({"category": k, "amount": round(v, 2)} for k, v in cats.items()),
            key=lambda x: x["amount"],
            reverse=True,
        )
        total = round(sum(c["amount"] for c in by_category), 2)
        prev_total = round(_month_total(conn, _shift_month(month, -1), "expense"), 2)
        change_ratio = round((total - prev_total) / prev_total, 4) if prev_total > 0 else None
        income_total = round(_month_total(conn, month, "income"), 2)
        trend = []
        for i in range(5, -1, -1):
            mm = _shift_month(month, -i)
            trend.append({
                "month": mm,
                "total": round(_month_total(conn, mm, "expense"), 2),
                "income": round(_month_total(conn, mm, "income"), 2),
            })
        daily_rows = conn.execute(
            "SELECT substr(occurred_at,1,10) AS d, COALESCE(SUM(amount),0) AS t FROM records "
            "WHERE type='expense' AND substr(occurred_at,1,7)=? GROUP BY d",
            (month,),
        ).fetchall()
        daily = {r["d"]: round(float(r["t"]), 2) for r in daily_rows}
        acct_rows = conn.execute(
            "SELECT account, COALESCE(SUM(amount),0) AS t FROM records "
            "WHERE type='expense' AND substr(occurred_at,1,7)=? GROUP BY account ORDER BY t DESC",
            (month,),
        ).fetchall()
        by_account = [{"account": r["account"], "amount": round(float(r["t"]), 2)} for r in acct_rows]
        top_rows = conn.execute(
            "SELECT category, amount, account, note, occurred_at FROM records "
            "WHERE type='expense' AND substr(occurred_at,1,7)=? ORDER BY amount DESC, id DESC LIMIT 5",
            (month,),
        ).fetchall()
        top_records = [
            {"category": r["category"], "amount": round(float(r["amount"]), 2), "account": r["account"],
             "note": r["note"], "occurred_at": r["occurred_at"]}
            for r in top_rows
        ]
        insight = _build_insight(conn, month, total, prev_total, change_ratio, by_category)
        return {
            "month": month,
            "total": total,
            "income_total": income_total,
            "prev_total": prev_total,
            "change_ratio": change_ratio,
            "by_category": by_category,
            "by_account": by_account,
            "daily": daily,
            "top_records": top_records,
            "trend": trend,
            "insight": insight,
            "budgets": get_budgets(),
            "prefs": get_prefs(),
        }
    finally:
        conn.close()
