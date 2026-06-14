"""调用独立部署的 OCR 服务，把截图 base64 转成文本。

TechnicalRoadmap.md §5.3/§5.5：OCR 与主服务分离部署；
不可用/超时时抛 OCRUnavailable，由路由层降级为「请改用文字」。
"""
import httpx

from .config import OCR_URL


class OCRUnavailable(Exception):
    """OCR 服务不可用 / 超时。"""


def image_to_text(image_b64: str) -> str:
    try:
        resp = httpx.post(OCR_URL, json={"image": image_b64}, timeout=30.0)
        resp.raise_for_status()
        return (resp.json() or {}).get("text", "") or ""
    except Exception as exc:  # noqa: BLE001
        raise OCRUnavailable(str(exc)) from exc
