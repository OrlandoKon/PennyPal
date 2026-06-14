# PennyPal — 智能记账 Agent

用一句话记账：随口说「中午吃饭35，微信付的」，Agent 自动解析、补全、入账，并提供消费分析。

- 设计方案：[Design.md](Design.md)
- 技术路线：[TechnicalRoadmap.md](TechnicalRoadmap.md)

## 技术栈

| 层 | 选型 |
|---|---|
| 大模型 | **DeepSeek V4**（function calling，OpenAI 兼容接口，`openai` SDK） |
| 后端 | Python + FastAPI（单体，直接托管前端） |
| OCR | 独立部署 **RapidOCR**（截图转文字，本地化、不依赖云） |
| 存储 | SQLite（单文件，运行时生成于 `data/`） |
| 前端 | 原生 JS + Tailwind(CDN) + Chart.js(CDN)，无需打包 |
| 语音 | 浏览器 Web Speech API（纯前端转文字） |

## 目录

```
backend/        FastAPI 主应用（Agent 循环、工具、数据库、OCR 客户端）
ocr_service/    独立部署的 OCR 服务（RapidOCR + FastAPI，单独进程/容器）
frontend/       单页前端（记账 / 账本 / 分析 三视图）
data/           SQLite 数据库（运行时生成）
```

## 快速开始

### 1) 配置环境变量

```bash
cp .env.example .env
# 编辑 .env，至少填入 DEEPSEEK_API_KEY
```

### 2) 启动独立 OCR 服务（截图记账用；不需要截图可跳过）

```bash
pip install -r ocr_service/requirements.txt
uvicorn ocr_service.server:app --port 8001
```

> 首次启动 RapidOCR 会下载/加载模型，稍等片刻。也可用 `docker build -t pennypal-ocr ocr_service && docker run -p 8001:8001 pennypal-ocr`。

### 3) 启动主应用

```bash
pip install -r requirements.txt
uvicorn backend.main:app --reload --port 8000
```

浏览器打开 <http://localhost:8000>。

## 公网部署（HTTPS）

生产以 **systemd + Caddy** 跑（详见 [deploy/](deploy/)）：

- 两个应用由 systemd 托管、**只绑本机**：主应用 `127.0.0.1:9527`、OCR `127.0.0.1:8001`。
- **Caddy** 监听 443，自动签发/续期 Let's Encrypt 证书，反代到 `127.0.0.1:9527`，并把 http 自动跳转 https。
- 访问：<https://pennypal.orlando.ink>

> ⚠️ 语音（Web Speech API）需要**安全上下文**，必须走 HTTPS（或本机 `http://localhost:9527`）才能用麦克风。

一键概览（细节见 [deploy/README.md](deploy/README.md)）：

```bash
# 1) systemd 托管两个应用（只绑本机）
sudo cp deploy/pennypal.service deploy/pennypal-ocr.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now pennypal-ocr pennypal

# 2) Caddy 反代 + 自动 HTTPS
sudo cp deploy/Caddyfile /etc/caddy/Caddyfile && sudo systemctl reload caddy
```

前提：公网 **80 + 443** 可达（签证书与对外服务用）。

## 试一试（对应 Design.md §14 验收脚本）

1. 输入「中午吃饭35，微信付的」→ 餐饮 ¥35（微信）。
2. 输入「早餐12 打车18 超市买菜60」→ 自动拆成 3 笔。
3. 输入「35」→ Agent 追问这 35 是什么消费。
4. 输入「昨天买衣服好像两百多」→ 购物、待确认（pending）。
5. 点 📷 上传一张支付截图 → OCR 转文字 → 自动入账。
6. 点 🎤 语音说「打车 20」→ 转文字并入账（需 Chrome/Edge）。
7. 问「这个月花了多少？」→ 自然语言汇总回答。
8. 「分析」页查看分类饼图、环比、主动洞察。

## API

| 方法 | 路径 | 功能 |
|---|---|---|
| POST | `/api/chat` | 运行 Agent 循环，返回 `{reply, added_records}` |
| GET | `/api/records?month=YYYY-MM` | 列出某月记录 |
| PUT | `/api/records/{id}` | 编辑一笔 |
| DELETE | `/api/records/{id}` | 删除一笔 |
| GET | `/api/summary?month=YYYY-MM` | 仪表盘数据（分类聚合、环比、洞察） |
