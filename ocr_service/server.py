"""独立部署的 OCR 服务：POST /ocr 收 base64 图片，返回识别文本。

引擎用 RapidOCR（onnxruntime 版，CPU 即可、无 Paddle 依赖），
对应 TechnicalRoadmap.md §5.2/§5.3。与主应用分离部署：
    uvicorn ocr_service.server:app --port 8001
"""
import base64
import io

import numpy as np
from fastapi import FastAPI
from PIL import Image
from pydantic import BaseModel
from rapidocr_onnxruntime import RapidOCR

app = FastAPI(title="OCR 服务")
_engine = RapidOCR()


class OCRRequest(BaseModel):
    image: str  # base64（不含 data:URL 前缀）


@app.get("/health")
def health():
    return {"ok": True}


@app.post("/ocr")
def ocr(req: OCRRequest):
    raw = base64.b64decode(req.image)
    img = np.array(Image.open(io.BytesIO(raw)).convert("RGB"))
    result, _ = _engine(img)  # 返回 [[box, text, score], ...] 或 None
    lines = []
    for item in result or []:
        text, score = item[1], float(item[2])
        lines.append({"text": text, "score": score})
    return {"text": "\n".join(line["text"] for line in lines), "lines": lines}
