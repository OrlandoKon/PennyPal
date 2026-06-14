"""FastAPI 入口：托管前端 + 业务路由（Design.md §6）。

一条命令启动：uvicorn backend.main:app --reload --port 8000
"""
import os
from datetime import date
from typing import List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import db
from .agent import SYSTEM_PROMPT, generate_insight, run_agent
from .config import FRONTEND_DIR
from .ocr_client import OCRUnavailable, image_to_text

app = FastAPI(title="智能记账 Agent")

db.init_db()  # 启动即建表（幂等）


# ---------- 请求/响应模型 ----------

class ChatMessage(BaseModel):
    role: str
    content: str


class ImagePayload(BaseModel):
    data: str
    media_type: Optional[str] = None


class ChatRequest(BaseModel):
    message: Optional[str] = None
    image: Optional[ImagePayload] = None
    source: Optional[str] = "text"
    history: Optional[List[ChatMessage]] = None


class RecordUpdate(BaseModel):
    amount: Optional[float] = None
    type: Optional[str] = None
    category: Optional[str] = None
    account: Optional[str] = None
    occurred_at: Optional[str] = None
    note: Optional[str] = None
    status: Optional[str] = None


class BudgetSet(BaseModel):
    category: str
    amount: float


# ---------- 业务路由 ----------

@app.post("/api/chat")
def chat(req: ChatRequest):
    source = req.source or "text"
    ocr_text = ""
    if req.image and req.image.data:
        source = "image"
        try:
            ocr_text = image_to_text(req.image.data)
        except OCRUnavailable:
            return {"reply": "截图识别服务暂不可用，请改用文字描述这笔消费。", "added_records": []}

    if not req.message and not ocr_text:
        raise HTTPException(status_code=400, detail="message 或 image 至少提供一个")

    # 上下文注入首条 user 消息（不写进 system，利于缓存命中，见 TechnicalRoadmap §3.4）
    today = date.today().isoformat()
    top = db.top_account()
    parts = [f"【上下文】今天是 {today}；用户最常用账户：{top or '无历史'}。"]
    prefs = db.get_prefs()
    if prefs:
        parts.append("【记忆/偏好】" + "；".join(f"{k}→{v}" for k, v in prefs.items()) + "（遇到这些关键词优先用对应分类）")
    if req.message:
        parts.append(req.message)
    if ocr_text:
        parts.append("【以下为截图识别文本，可能排版乱、含多笔】\n" + ocr_text)
    user_content = "\n\n".join(parts)

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    for m in req.history or []:
        if m.role in ("user", "assistant") and m.content:
            messages.append({"role": m.role, "content": m.content})
    messages.append({"role": "user", "content": user_content})

    ctx = {"raw_input": (req.message or ocr_text[:200] or "[截图]"), "source": source}
    reply, added, dirty = run_agent(messages, ctx)
    return {"reply": reply, "added_records": added, "dirty": dirty}


@app.get("/api/records")
def get_records(month: Optional[str] = None):
    month = month or date.today().strftime("%Y-%m")
    return {"month": month, "records": db.list_by_month(month)}


@app.put("/api/records/{record_id}")
def put_record(record_id: int, body: RecordUpdate):
    updated = db.update_record(record_id, body.model_dump(exclude_none=True))
    if not updated:
        raise HTTPException(status_code=404, detail="记录不存在")
    return updated


@app.delete("/api/records/{record_id}")
def remove_record(record_id: int):
    if not db.delete_record(record_id):
        raise HTTPException(status_code=404, detail="记录不存在")
    return {"ok": True}


@app.get("/api/summary")
def get_summary(month: Optional[str] = None):
    month = month or date.today().strftime("%Y-%m")
    return db.summary(month)


@app.get("/api/budgets")
def get_budgets():
    return {"budgets": db.get_budgets()}


@app.put("/api/budgets")
def put_budget(body: BudgetSet):
    cat = body.category.strip()
    if cat in ("总", "总预算", "全部", "总额", "整体"):
        cat = db.TOTAL_KEY
    db.set_budget(cat, body.amount)
    return {"budgets": db.get_budgets()}


@app.delete("/api/prefs/{keyword}")
def remove_pref(keyword: str):
    db.delete_pref(keyword)
    return {"prefs": db.get_prefs()}


@app.get("/api/insight")
def api_insight(month: Optional[str] = None):
    month = month or date.today().strftime("%Y-%m")
    return {"insight": generate_insight(db.summary(month))}


# ---------- 托管前端 ----------

@app.get("/")
def index():
    return FileResponse(os.path.join(FRONTEND_DIR, "index.html"))


app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")
