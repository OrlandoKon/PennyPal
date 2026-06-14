# Bug 记录

开发过程中遇到的 bug 与修复记录,最新在上。格式:现象 / 根因 / 修复 / 验证 / 涉及文件。

---

## #7 网页语音点击无反应（Web Speech 需安全上下文）

- **现象**：网页上点 🎤 语音按钮"根本没用"，毫无反应。
- **根因**：浏览器 Web Speech API 只在**安全上下文**（HTTPS 或 `http://localhost`）下放行麦克风；站点当时是 `http://pennypal.orlando.ink:9527`（明文 HTTP 公网），`recognition.start()` 直接报 `not-allowed`，而代码 `onerror = () => {}` 把错误**吞掉了** → 表现成"点了没反应"。
- **修复**：
  1. 前端不再吞错：检测 `window.isSecureContext`，不安全时点按给出明确提示；各类 `onerror`（not-allowed / no-speech / …）都有中文提示。
  2. 部署上 **HTTPS**：Caddy 监听 443、自动签 Let's Encrypt 证书、反代到 `127.0.0.1:9527`，http 自动跳 https；页面变安全上下文后语音可用。
- **验证**：`https://pennypal.orlando.ink` 返回 200（有效证书），app.js 含新语音逻辑；直连公网 `:9527` 已收回内网。
- **涉及文件**：[frontend/app.js](frontend/app.js)、[deploy/Caddyfile](deploy/Caddyfile)、[deploy/pennypal.service](deploy/pennypal.service)。

## #4 分类"购物"过宽（数据模型细化）

- **现象**:买衣服、买电脑摄像头都归到"购物",太笼统。
- **修复**:拆出 `服饰`(衣服/鞋/包)与 `数码`(手机/电脑/摄像头/数码配件),`购物` 留作其它日用百货;同步更新分类枚举、prompt 分类提示、前端编辑下拉与图标/颜色。
- **验证**:买衣服→服饰、买电脑摄像头→数码、超市日用→购物。
- **涉及文件**:[backend/config.py](backend/config.py)、[backend/agent.py](backend/agent.py)、[frontend/app.js](frontend/app.js)。

## #5 饼图颜色随占比排名变化（图表 bug）

- **现象**:饼图里每个分类的颜色会随占比变化,占比最高的永远是红色。
- **根因**:`drawPie` 用 `PALETTE[i]` 按**排序后的排名**取色(byCat 按金额降序),第一名总是 PALETTE[0]=红,排名一变颜色就变。
- **修复**:改为**按分类名固定取色**——新增 `CATCOLOR` 映射,`backgroundColor: byCat.map(c => CATCOLOR[c.category])`。
- **验证**:同一分类无论占比多少,颜色恒定。
- **涉及文件**:[frontend/app.js](frontend/app.js)。

---

## #1 大模型只回"已记"却不真正记账（核心功能 bug）

- **现象**:刷新后往往只有第一笔能记上,后续输入"记不进去",要再刷新才行;连续记账经常不写库。
- **根因**:DeepSeek `v4-flash`(非思考)在原 prompt 下经常**只输出"已记…"文字而不调用 `add_record` 工具**(实测仅 **2/5** 真正写库);前端又把"已记…"塞进对话历史,后续轮模型照历史里的文字回执模仿,更不调用工具 → 表现为"第一笔行、后面不行"。
- **修复**:
  1. 后端强化 `SYSTEM_PROMPT`:明确"记账的唯一方式是调用 add_record""严禁不调用工具就回复'已记'""历史里的'已记'不代表本轮已记"。
  2. 前端**成功入账后清空对话历史**(一笔记完即线程结束),只在信息不全、模型追问时保留上下文。
- **验证**:连记 5 笔 **5/5** 全部写库(改前 2/5)。
- **涉及文件**:[backend/agent.py](backend/agent.py)(SYSTEM_PROMPT)、[frontend/app.js](frontend/app.js)(sendMessage 历史处理)。

## #2 DeepSeek V4 不支持图片输入（设计约束修正）

- **现象**:Design 原方案"截图免 OCR、多模态模型直读图"在换用 DeepSeek V4 后不成立。
- **根因**:官方 `deepseek-v4-flash` / `deepseek-v4-pro` 是**纯文本模型**,API 不接受图片输入("V4 Vision"仅见于第三方聚合站,非官方平台)。
- **修复**:改为**独立部署 OCR 服务**(RapidOCR),截图先转文字、再交 V4 解析入账;OCR 只做"图→文",结构化仍由模型完成。
- **验证**:合成小票图 → OCR 识别 → V4 解析 → 入账,端到端打通。
- **涉及文件**:[backend/ocr_client.py](backend/ocr_client.py)、[ocr_service/server.py](ocr_service/server.py);文档同步 Design.md / TechnicalRoadmap.md。

## #3 前端在桌面端像手机页面（UI）

- **现象**:布局是窄列居中(`max-w-md`),桌面浏览器上像中间贴了一张手机屏。
- **修复**:改为响应式——全宽顶栏、加宽内容区、分析页图表并排;随后进一步改为**单页三块同屏仪表盘**(左记账、右上分析、右下账本),记账后右侧自动刷新;窄屏自动堆叠。
- **验证**:桌面端三块同屏、联动刷新正常;窄屏单列可用。
- **涉及文件**:[frontend/index.html](frontend/index.html)、[frontend/app.js](frontend/app.js)。

---

## #6 新增/编辑缺统一校验，可写入脏数据（G1–G4，已修复）

由测试发现（[tests/test_validation.py](tests/test_validation.py)，曾以 xfail 固定，现已转为回归护栏）。

- **现象/根因**：
  - **G1 occurred_at 未校验**：模型若没把"昨天"换算成日期、原样写入，记录因按 `substr(occurred_at,1,7)` 取不到 `YYYY-MM`，在账本/汇总里**全部不可见——静默丢数据**。
  - **G2 编辑（PUT /api/records）零校验**：可写入枚举外分类、负金额、`expense/income` 以外的 type，污染饼图/汇总/环比（新增路径有兜底，编辑路径完全绕过）。
  - **G3 `tools._coerce_record` 的 `abs()` 是死代码**：add 在 coerce 前已拒 `amount<=0`，负数到不了 abs。
  - **G4 type 与 category 可不一致**：如 `type=income` 配 `category=餐饮`。
- **修复**：抽出统一的 [backend/validation.py](backend/validation.py) `normalize_fields()`，**所有写库（insert + update）都过一遍**——occurred_at 非法→今天；category 越界→其他；amount→取绝对值；status/account 规整；**type 由 category 决定**（杜绝不一致）。`tools._coerce_record` 不再各自兜底，`abs()` 死代码删除。
- **验证**：`tests/test_validation.py` 5 项全绿；确定性套件 32 项、Agent 套件 8 项全过。
- **涉及文件**：[backend/validation.py](backend/validation.py)、[backend/db.py](backend/db.py)、[backend/tools.py](backend/tools.py)。

## 部署 / 排错坑（环境相关,非代码 bug）

- **http_proxy 干扰自测**:本机 shell 设了 `http_proxy=127.0.0.1:15732`,`curl` 默认走代理,自测本机服务要加 `--noproxy '*'`;systemd 启动的进程不继承该变量(本机直连能出网,故无需在 unit 里配代理)。
- **zsh 把 `[::1]` 当通配符**:带 `[]` 的 IPv6 URL 必须加引号,如 `curl '... http://[::1]:9527/'`,否则报 `no matches found`。
- **端口要"浏览器允许"**:对外端口用 `9527`(不常用且不在浏览器 unsafe-ports 黑名单);`--host ::` 才能让解析到公网 IPv6 的域名访问到(默认 `127.0.0.1` 仅本机)。
- **本机防火墙无需放行**:nftables `input` 策略为 `accept` 且无拦截规则;公网连不通应查云安全组 / 上游 v6 路由。

## 已知无害提示（非 bug,无需处理）

- `GET /favicon.ico 404`:未放站点图标,无害。
- OCR 启动时 onnxruntime 打印 GPU 探测 / `pthread_setaffinity_np` 告警:容器环境噪音,不影响识别。
