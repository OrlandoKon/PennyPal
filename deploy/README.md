# 部署：systemd 常驻

把主应用和 OCR 服务交给 systemd：自动重启、开机自启、日志进 journald。

- `pennypal.service` —— 主应用，监听 `[::]:9527`（公网 IPv6）
- `pennypal-ocr.service` —— OCR 服务，监听 `127.0.0.1:8001`（仅本机）

## 前置条件

- `PennyPal` micromamba 环境已装好依赖（`requirements.txt` + `ocr_service/requirements.txt`）。
- 仓库根目录有 `.env`（至少含 `DEEPSEEK_API_KEY`）。unit 里 `WorkingDirectory` 指向仓库根，应用启动时自动读取它。
- 路径假设：仓库在 `/root/Repository/PennyPal`，env 在 `/root/.local/share/mamba/envs/PennyPal`。
  **若你的路径不同**，改两个 `.service` 里的 `WorkingDirectory` 与 `ExecStart`（含 `--app-dir`）。

## 安装并启动

```bash
sudo cp deploy/pennypal.service deploy/pennypal-ocr.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now pennypal-ocr.service   # 先起 OCR（内网 8001）
sudo systemctl enable --now pennypal.service       # 主应用（公网 :: 9527）
```

> `pennypal.service` 用 `Wants=pennypal-ocr.service`，所以 enable 主应用会顺带把 OCR 也拉起；单独 enable 一遍只是更直观。

## 查看状态 / 日志

```bash
systemctl status pennypal pennypal-ocr
journalctl -u pennypal -f          # 主应用实时日志
journalctl -u pennypal-ocr -f      # OCR 实时日志
```

## 改了代码 / .env 之后

```bash
sudo systemctl restart pennypal pennypal-ocr
```

## 停止 / 取消自启

```bash
sudo systemctl disable --now pennypal pennypal-ocr
```

## HTTPS（Caddy 反代）

语音功能需要安全上下文，故对外走 HTTPS。Caddy 监听 80/443，自动签发/续期 Let's Encrypt 证书，反代到本机 `127.0.0.1:9527`，http 自动跳 https。配置见 [Caddyfile](Caddyfile)。

```bash
# 安装 Caddy（Debian/Ubuntu 官方源）
apt-get install -y debian-keyring debian-archive-keyring apt-transport-https curl
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor --yes -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' > /etc/apt/sources.list.d/caddy-stable.list
apt-get update && apt-get install -y caddy
# 应用配置
cp deploy/Caddyfile /etc/caddy/Caddyfile && systemctl reload caddy
```

> `pennypal.service` 已把应用绑到 `127.0.0.1:9527`（仅本机），由 Caddy 对外。需公网 **80（签证书）+ 443** 可达。

## 访问与排错

- 浏览器：<https://pennypal.orlando.ink>（经 Caddy；语音需 HTTPS）
- 本机自测（zsh 给带 `[]` 的 URL 加引号、`--noproxy` 绕开本地代理）：
  ```bash
  curl -6 --noproxy '*' -s -o /dev/null -w 'HTTP %{http_code}\n' 'http://[::1]:9527/'
  ```
- 起不来先看 `journalctl -u pennypal -e`：
  - `Address already in use` → 9527 被占，换端口或停掉占用进程。
  - DeepSeek 超时/连不上 → 本机若需代理才能出网，按 `pennypal.service` 注释打开 `HTTPS_PROXY/HTTP_PROXY` + `NO_PROXY=localhost,127.0.0.1,::1` 三行后 `daemon-reload && restart`。
  - 截图记账无反应 → 看 `pennypal-ocr` 是否在跑、`OCR_URL` 是否为 `http://localhost:8001/ocr`。
- 防火墙：本机 nftables `input` 为 `accept`，无需额外放行；公网连不上多半是云安全组 / 上游 IPv6 路由，与本机防火墙无关。
