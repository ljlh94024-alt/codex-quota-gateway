# Codex Quota Gateway

外层、轻量的 OpenAI Compatible 配额网关。它不修改上游 Codex Proxy，也不保存 ChatGPT 密码或 OAuth；上游认证通过 `UPSTREAM_API_KEY` 或上游 Proxy 自己管理。

## 支持

- `POST /v1/chat/completions`
- `POST /v1/responses`
- streaming / SSE
- Bearer API Key（数据库只保存 SHA-256 hash）
- 四用户默认各 25% 周额度
- 全局并发 3、单用户并发 1、FIFO 等待
- 429/5xx 最多 3 次退避重试
- SQLite 使用和任务记录
- `/admin` 最小管理页面、`/admin/summary` 统计接口
- 确定性限流（默认每用户每分钟 10 次）和临时冻结
- 高置信度 Secret 检测（阻断请求，不记录 Secret 内容）
- `security_events` 审计日志和 `/admin/security-events`
- 用户统计：`/api/me/usage`、`/api/me/history`
- 管理员统计：`/admin/usage`、`/admin/dashboard`
- 周额度按 UTC 周一 00:00 自动切换，启动及每天执行 SQLite 备份并保留 7 份
- Caddy HTTPS 配置（通过 `--profile https` 启用）
- 管理员密码 PBKDF2 哈希与过期 Session Cookie
- 可配置的匿名只读 Dashboard（默认关闭）

## 启动

```bash
cp .env.example .env
# 在 .env 中设置已授权的上游 URL；不要使用容器内的 127.0.0.1
docker compose config >/dev/null
docker compose up -d --build gateway
curl -sS http://127.0.0.1:8080/healthz
```

镜像启动时会自动执行幂等 bootstrap。用户 Key 保存在 Docker volume 的 `/app/secrets/gateway_keys.json`，管理员 Key 在 `/app/secrets/admin_key.txt`。这些文件不要提交 Git 或公开发布。

默认 Compose 会自动创建项目管理的 `gateway-net` bridge 网络，Caddy 通过 `gateway:8080` 访问 Gateway；不需要预先手工创建网络。Gateway 在容器内监听 `0.0.0.0:8080`，宿主机仍只发布 `127.0.0.1:8080`，因此 8080 不对公网开放。

上游连接方式：同一 Docker 网络使用 `http://<service-name>:<container-port>/v1`；宿主机已发布且可从 bridge 访问的端口使用 `http://host.docker.internal:<host-port>/v1`；远程授权代理使用 `https://<authorized-upstream-host>/v1`。不要在容器内使用 `127.0.0.1` 连接外部上游。

跨 Compose 项目时，先确认外部网络存在，再使用可选 override：

```bash
UPSTREAM_DOCKER_NETWORK="${UPSTREAM_DOCKER_NETWORK:-codex-net}"
docker network inspect "$UPSTREAM_DOCKER_NETWORK"
docker compose -f docker-compose.yml -f docker-compose.upstream-network.yml up -d --build
```

## 安全边界

Gateway 容器的宿主机端口 `127.0.0.1:8080` 仅供本机/容器调试；客户端统一使用公网 HTTPS 入口：

```text
https://<your-domain>/v1
```

Caddy 将 `/dashboard`、`/api/*` 和 `/v1/*` 转发到 Docker 网络中的 `gateway:8080`。客户端不要使用服务器 IP、`8080` 或临时测试端口 `18080`。没有上游 URL 时 `/healthz` 会显示 `upstream_configured=false`，请求返回 `upstream_not_configured`，不会假装已经连通。

OpenAI Python SDK 示例：

```python
from openai import OpenAI

client = OpenAI(
    api_key="USER_KEY",
    base_url="https://<your-domain>/v1",
)

models = client.models.list()
print([model.id for model in models.data])

response = client.responses.create(
    model="gpt-5.6-sol",
    input="Reply with OK only.",
)
print(response.output_text)
```

流式 Responses 调用保持同一 `base_url` 和 `api_key`，并设置 `stream=True`；Bearer Key 仍由 Gateway 验证。

## 安全配置

`security_rules.yaml` 控制规则级别。默认只阻断高置信度 Secret 命中；普通开发、漏洞原理学习和 CTF 文本不会因关键词被拦截。

```env
RATE_LIMIT_PER_MINUTE=10
RATE_LIMIT_SUSPEND_SECONDS=300
RATE_LIMIT_VIOLATION_THRESHOLD=3
SECRET_SCAN=true
AUDIT_LOG=true
SECURITY_RULES_PATH=/app/security_rules.yaml
ABNORMAL_WINDOW_MINUTES=10
ABNORMAL_REQUESTS_10M=30
ABNORMAL_TOKENS_10M=100000
BACKUP_DIR=/app/data/backup
BACKUP_RETENTION=7
BACKUP_TARGET=s3://bucket/prefix
SECRET_KEY=replace-with-32-byte-random-secret
ADMIN_PASSWORD_HASH=
SESSION_COOKIE_SECURE=true
DOMAIN=
UPSTREAM_DOCKER_NETWORK=codex-net
PUBLIC_DASHBOARD_MODE=false
PUBLIC_TEST_MODE=false
PUBLIC_PORT=18080
BIND_HOST=0.0.0.0
PUBLIC_DASHBOARD_PASSWORD=
COOKIE_SECURE=true
```

管理员使用 `X-Admin-Key` 访问 `/admin/summary` 和 `/admin/security-events`。审计只保存用户、时间、IP、规则类型、动作和计数，不保存完整请求正文、Token、Cookie、密码或 OAuth 凭证。

## 恢复方法

- 限流/临时冻结：等待 `RATE_LIMIT_SUSPEND_SECONDS` 到期；服务重启会清空内存限流窗口。
- 误拦截：将对应规则在 `security_rules.yaml` 中设置 `enabled: false`，然后 `docker compose up -d --build`。
- 数据恢复：不要删除 `gateway-data` 和 `gateway-secrets` volume；升级只替换代码和镜像即可保留用户、额度、任务及审计记录。

## 使用统计与面板

用户使用自己的 Bearer Key 调用：

```text
GET /api/me/usage
GET /api/me/history?limit=100
GET /dashboard
```

统计只返回当前用户的数据，不保存完整 Prompt 或响应正文。管理员使用 `X-Admin-Key`：

```text
GET /admin/usage
GET /admin/dashboard
```

`weekly_usage` 按 UTC 周一到下周一汇总请求数和 Token；面板中的额度字段明确标记为 `estimated`，不会宣称为上游真实 Token 额度，并提供 `last_sync_time`（当前为本地/上游元数据最后更新时间）。`estimated_usage_percent` 达到 80%/100% 只提示，不自动封禁。SQLite 在线备份压缩写入 `BACKUP_DIR`，默认文件名为 `usage_YYYYMMDD.db.gz`，保留最近 7 份；配置 `BACKUP_TARGET=s3://bucket/prefix` 后会尝试同步到 S3 兼容存储。

## 生产加固

先生成管理员密码哈希（密码不会写入代码或日志）：

```bash
docker compose run --rm -it gateway python set_admin_password.py
```

启用 HTTPS 前，将 DNS 的 `DOMAIN` A/AAAA 记录指向服务器，并在 `.env` 设置域名、`SESSION_COOKIE_SECURE=true` 和 `COOKIE_SECURE=true`：

```bash
docker compose --profile https up -d
```

Caddy 会直接读取 `.env` 中的 `DOMAIN`，自动申请/续期证书，并将 HTTP 重定向到 HTTPS；不会改写受 Git 跟踪的 Caddyfile。没有真实域名和 DNS 指向时不要启用 `https` profile。

管理员浏览器入口为 `/admin/login`；`/admin/dashboard` 需要有效 Session。脚本恢复流程：

公开测试面板开启后，`/dashboard` 和 `/api/public/dashboard` 只返回 Gateway/Proxy 状态、估算百分比、最近请求时间和 `User A/B` 匿名周使用率，不接受或返回 API Key。`/v1/*` 仍始终要求 Bearer Key，`/admin/*` 仍始终需要管理员认证。

```bash
docker compose exec gateway python restore_backup.py list
docker compose stop gateway
docker compose run --rm gateway python restore_backup.py restore usage_YYYYMMDD.db.gz
docker compose up -d gateway
```

恢复前脚本会再次快照当前数据库，并执行 SQLite `quick_check`。
