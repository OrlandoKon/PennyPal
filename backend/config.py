"""集中读取环境变量，并构造 DeepSeek（OpenAI 兼容）客户端。

技术路线见 TechnicalRoadmap.md §2.2/§2.3：
- 大模型走 DeepSeek V4，OpenAI 兼容端点 + openai SDK；
- base_url、模型名、OCR 服务地址均可由环境变量配置。
"""
import os

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()  # 从项目根的 .env 读取（缺失也不报错）

# --- DeepSeek / 模型 ---
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
MODEL = os.environ.get("MODEL", "deepseek-v4-flash")

# --- 独立部署的 OCR 服务 ---
OCR_URL = os.environ.get("OCR_URL", "http://localhost:8001/ocr")

# --- 路径 ---
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT_DIR, "data")
DB_PATH = os.environ.get("DB_PATH", os.path.join(DATA_DIR, "expense.db"))
FRONTEND_DIR = os.path.join(ROOT_DIR, "frontend")

# --- 业务常量（分类枚举，见 Design.md §4.2）---
CATEGORIES = ["餐饮", "交通", "购物", "服饰", "数码", "娱乐", "居住", "医疗", "学习", "人情", "其他", "收入"]

# OpenAI 兼容客户端，base_url 指向 DeepSeek。
# 用占位 key 兜底，保证无 key 时应用仍能启动（仅 /api/chat 会在真正调用时报鉴权错）。
client = OpenAI(api_key=DEEPSEEK_API_KEY or "sk-missing", base_url=DEEPSEEK_BASE_URL)
