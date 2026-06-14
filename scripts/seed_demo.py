#!/usr/bin/env python3
"""一键灌入演示样例数据 —— 让仪表盘一打开就「满」。

铺设内容（配套 DEMO.md「演示前准备」）：
- **当前月**：从 1 号到今天，每天 2~3 笔，覆盖全部 11 个支出分类 + 4 个账户
  （微信/支付宝/银行卡/现金）+ 收入，热力图、分类饼图、账户环形图、Top 支出榜全部填满。
- **近 5 个月**：每月一套基线（餐饮/居住/交通/购物/娱乐 + 工资），让「近 6 月收支对比」
  柱状图、环比、以及「某分类比近 3 月均值多 N%」的异常洞察都有料。
- **预算**：总预算 + 餐饮/交通/娱乐/购物，进度条呈现绿/黄/红不同状态。
- **记忆偏好**：星巴克→餐饮 等，🧠 偏好卡有内容。

默认会**清空已有的全部数据**（记录 / 预算 / 偏好）再灌入。用法（PennyPal 环境，仓库根目录）：

    /root/.local/share/mamba/envs/PennyPal/bin/python scripts/seed_demo.py
    python scripts/seed_demo.py            # 清空并灌入
    python scripts/seed_demo.py --keep     # 不清空，只追加

目标数据库由 backend.config.DB_PATH 决定（与线上服务同一个库），刷新页面即可看到。
"""
import argparse
import os
import sys
from datetime import date

# 允许 `python scripts/seed_demo.py` 直接运行：把仓库根目录加入 import 路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend import db                       # noqa: E402
from backend.config import DB_PATH           # noqa: E402

ACCOUNTS_NOTE = "演示样例数据，可随时用 seed_demo.py 重置"


# ---------------- 日期工具 ----------------

def _iso(year: int, month: int, day: int) -> str:
    return date(year, month, day).isoformat()


def _prior_month(today: date, delta: int) -> tuple:
    """today 往前 delta 个月，返回 (year, month)。"""
    idx = today.year * 12 + (today.month - 1) - delta
    y, m0 = divmod(idx, 12)
    return y, m0 + 1


# ---------------- 样例数据模板 ----------------

# 当前月：每天的午饭（note, amount），按天轮换
_LUNCHES = [
    ("午饭", 35), ("午饭", 28), ("工作餐", 42), ("午饭", 30), ("食堂", 19),
    ("午饭", 48), ("外卖", 33), ("午饭", 25), ("简餐", 22), ("午饭", 38),
]
# 当前月：每天的第二笔（note, amount, category, account），按天轮换
_SECONDARY = [
    ("早餐", 12, "餐饮", "微信"),
    ("打车", 24, "交通", "支付宝"),
    ("奶茶", 16, "餐饮", "微信"),
    ("地铁", 6, "交通", "现金"),
    ("超市", 66, "购物", "支付宝"),
    ("咖啡", 22, "餐饮", "微信"),
    ("公交", 4, "交通", "现金"),
    ("水果", 28, "购物", "微信"),
    ("外卖", 31, "餐饮", "支付宝"),
    ("零食", 18, "其他", "现金"),
]
# 当前月：每隔几天加的第三笔（note, amount, category, account）
_THIRD = [
    ("夜宵", 26, "餐饮", "微信"),
    ("停车", 15, "交通", "支付宝"),
    ("日用品", 39, "购物", "支付宝"),
    ("快递", 12, "其他", "微信"),
]

# 当前月：大额 / 补齐分类的固定笔（目标日, amount, category, account, note）。
# 目标日超过今天时会被钳到今天，保证一定落在当月。
_EXTRAS = [
    (9, 1500, "居住", "银行卡", "房租"),
    (5, 899, "数码", "银行卡", "降噪耳机"),
    (14, 268, "服饰", "支付宝", "运动鞋"),
    (4, 199, "服饰", "微信", "卫衣"),
    (12, 200, "人情", "微信", "随份子"),
    (7, 86, "医疗", "支付宝", "感冒药"),
    (8, 99, "学习", "支付宝", "网课"),
    (3, 39, "娱乐", "支付宝", "电影票"),
    (10, 60, "娱乐", "微信", "游戏充值"),
    (6, 120, "购物", "支付宝", "日用品"),
]
# 当前月收入（目标日, amount, account, note）
_INCOME = [
    (10, 8000, "银行卡", "工资"),
    (7, 600, "微信", "兼职"),
    (1, 200, "微信", "红包"),
]

# 近 5 个月每月基线（日, amount, category, account, note）
_PRIOR = [
    (3, 120, "餐饮", "微信", "聚餐"),
    (9, 95, "餐饮", "支付宝", "外卖"),
    (16, 90, "餐饮", "微信", "日常"),
    (6, 70, "交通", "支付宝", "打车"),
    (19, 55, "交通", "现金", "地铁"),
    (9, 1500, "居住", "银行卡", "房租"),
    (12, 160, "购物", "支付宝", "超市"),
    (20, 80, "娱乐", "微信", "电影"),
]
# 近 5 个月每月一笔特色支出，让各月有差异（delta -> (日, amount, category, account, note)）
_PRIOR_EXTRA = {
    1: (15, 320, "服饰", "支付宝", "外套"),
    2: (14, 150, "医疗", "微信", "体检"),
    3: (22, 480, "数码", "银行卡", "机械键盘"),
    4: (17, 260, "人情", "微信", "礼物"),
    5: (11, 90, "学习", "支付宝", "技术书"),
}

# 预算（category -> amount）；TOTAL_KEY 为总预算
_BUDGETS = {
    "餐饮": 800,
    "交通": 300,
    "娱乐": 200,
    "购物": 500,
    db.TOTAL_KEY: 6000,
}
# 记忆偏好（keyword -> category）
_PREFS = {
    "星巴克": "餐饮",
    "滴滴": "交通",
    "美团": "餐饮",
}


# ---------------- 清空 ----------------

def clear_all() -> None:
    conn = db._connect()
    try:
        conn.execute("DELETE FROM records")
        conn.execute("DELETE FROM budgets")
        conn.execute("DELETE FROM prefs")
        # 重置自增 id，让演示里的 id 从 1 开始
        if conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='sqlite_sequence'"
        ).fetchone():
            conn.execute("DELETE FROM sqlite_sequence WHERE name='records'")
        conn.commit()
    finally:
        conn.close()


# ---------------- 灌入 ----------------

def _add(amount, category, account, occurred_at, note) -> None:
    db.insert_record({
        "amount": amount,
        "category": category,
        "account": account,
        "occurred_at": occurred_at,
        "note": note,
        "source": "seed",
    })


def seed(today: date) -> int:
    n = 0
    y, m = today.year, today.month

    # —— 当前月：逐日铺设，填满热力图 ——
    for d in range(1, today.day + 1):
        day_iso = _iso(y, m, d)
        ln, la = _LUNCHES[(d - 1) % len(_LUNCHES)]
        _add(la, "餐饮", "微信", day_iso, ln)
        n += 1
        sn, sa, sc, sacct = _SECONDARY[(d - 1) % len(_SECONDARY)]
        _add(sa, sc, sacct, day_iso, sn)
        n += 1
        if d % 3 == 0:                                  # 每三天加密一笔
            tn, ta, tc, tacct = _THIRD[(d // 3 - 1) % len(_THIRD)]
            _add(ta, tc, tacct, day_iso, tn)
            n += 1

    # —— 当前月：大额 / 补齐分类 ——
    for target, amt, cat, acct, note in _EXTRAS:
        _add(amt, cat, acct, _iso(y, m, min(target, today.day)), note)
        n += 1

    # —— 当前月：收入 ——
    for target, amt, acct, note in _INCOME:
        _add(amt, "收入", acct, _iso(y, m, min(target, today.day)), note)
        n += 1

    # —— 近 5 个月：基线 + 特色，趋势略升至本月 ——
    for delta in range(1, 6):
        py, pm = _prior_month(today, delta)
        factor = 1.0 - 0.05 * delta                     # 越早的月份略少 → 看得出上升趋势
        for d, amt, cat, acct, note in _PRIOR:
            amount = amt if cat == "居住" else round(amt * factor)
            _add(amount, cat, acct, _iso(py, pm, d), note)
            n += 1
        ed, eamt, ecat, eacct, enote = _PRIOR_EXTRA[delta]
        _add(eamt, ecat, eacct, _iso(py, pm, ed), enote)
        n += 1
        # 每月工资
        _add(8000, "收入", "银行卡", _iso(py, pm, 10), "工资")
        n += 1

    # —— 预算 + 记忆偏好 ——
    for cat, amt in _BUDGETS.items():
        db.set_budget(cat, amt)
    for kw, cat in _PREFS.items():
        db.set_pref(kw, cat)

    return n


def main() -> None:
    parser = argparse.ArgumentParser(description="灌入 PennyPal 演示样例数据")
    parser.add_argument("--keep", action="store_true", help="不清空已有数据，只追加")
    args = parser.parse_args()

    db.init_db()
    today = date.today()

    print(f"目标数据库：{DB_PATH}")
    if not args.keep:
        clear_all()
        print("已清空：records / budgets / prefs")

    n = seed(today)

    s = db.summary(today.strftime("%Y-%m"))
    print(f"已灌入 {n} 条记录。")
    print(f"当前月 {s['month']}：支出 ¥{s['total']:.0f}，收入 ¥{s['income_total']:.0f}，"
          f"分类 {len(s['by_category'])} 个，账户 {len(s['by_account'])} 个，"
          f"覆盖 {len(s['daily'])} 天。")
    print(f"预算 {len(s['budgets'])} 项，记忆偏好 {len(s['prefs'])} 条。")
    print(f"洞察：{s['insight']}")
    print("完成。刷新 https://pennypal.orlando.ink 查看。")


if __name__ == "__main__":
    main()
