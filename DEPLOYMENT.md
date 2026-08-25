# Deployment Notes

远端目录：`/opt/codex-quota-gateway`

服务：

```bash
cd /opt/codex-quota-gateway
docker compose ps
docker compose logs --tail=100 gateway
```

正式公网入口由部署者配置为 `https://<your-domain>/v1`。宿主机 `127.0.0.1:8080` 只用于本机/容器内部调试，避免未经 TLS 保护的 Bearer Key 暴露到公网。配置授权的上游 Proxy 后，编辑 `.env`：

部分客户端会在 Base URL 后自行追加 `/v1`。Gateway 会把误拼的 `/v1/v1/*` 自动归一为 `/v1/*`，并提供需要 Bearer Key 的 `/v1/usage` 与 `/v1/user/balance` 兼容查询。推荐配置为 `https://<your-domain>/v1`；如果客户端明确要求“服务器地址/Host”而不是“Base URL”，填写 `https://<your-domain>`。

Gateway 为单实例运行。启动时会把上一次进程异常结束留下的 `pending/running` 任务标记为失败，并清零仅存在于旧进程的 `weekly_reserved`；这只回收僵尸预留，不修改用户的周额度比例或实际已用量。

配额准入按“实际已用达到个人比例后阻断”执行。请求的保守 token 估算只用于预留，预留最多占用当前剩余额度；不会因为“实际尚未到 100%，但本次估算可能越界”而提前返回 429。响应完成后仍按上游实际 usage 结算。

当前业主指定的估算基准为 `DEFAULT_TOTAL_QUOTA=1000000000`，单次上下文估算封顶为 `MAX_CONTEXT_TOKENS=270000`。四个用户仍按 20% / 20% / 25% / 35% 划分，对应估算上限 2亿 / 2亿 / 2.5亿 / 3.5亿 tokens。

客户打开你的域名根路径时会自动进入临时只读数据页 `/dashboard`；API 客户端使用你的域名 `/v1` Base URL。

```bash
UPSTREAM_BASE_URL=http://127.0.0.1:<proxy-port>/v1
UPSTREAM_API_KEY=
```

然后执行：

```bash
docker compose up -d --build
```

## Model discovery and verification

The Gateway authenticates the client key, then fetches the upstream
`GET /v1/models` catalog. Model IDs and metadata are returned unchanged; the
Gateway does not create aliases or maintain a fixed model allowlist. The
catalog is cached in memory for 60 seconds and is refreshed after restart.

通过公网域名调用（不要把服务器 IP、8080 或临时 18080 端口交给客户端）：

```text
https://<your-domain>/v1
```

OpenAI Python SDK：

```python
from openai import OpenAI

client = OpenAI(
    api_key="USER_KEY",
    base_url="https://<your-domain>/v1",
)

print([item.id for item in client.models.list().data])
response = client.responses.create(
    model="gpt-5.6-sol",
    input="Reply with OK only.",
)
print(response.output_text)
```

Responses 流式调用使用同一 Base URL，并传 `stream=True`；Caddy 会保持 SSE 长连接。

维护/调试时如已建立 SSH 隧道，也可以在服务器侧本地验证：

```powershell
curl http://127.0.0.1:8080/v1/models `
  -H "Authorization: Bearer <customer-key>"
```

To verify an actual model without renaming it:

```powershell
curl http://127.0.0.1:8080/v1/responses `
  -H "Authorization: Bearer <customer-key>" `
  -H "content-type: application/json" `
  -d '{"model":"gpt-5.6-sol","input":"Reply with OK only."}'
```

If the upstream account does not expose a requested model, the upstream error
is returned; the Gateway does not silently substitute a different model.

Key 文件位于 Docker volume 的 `/app/secrets/gateway_keys.json`，管理员 Key 位于 `/app/secrets/admin_key.txt`。不要提交 Git、不要放到公开 URL。

## 使用统计与额度面板

用户端（Bearer Key）：

```text
GET /api/me/usage
GET /api/me/history?limit=100
GET /dashboard
```

管理员端（`X-Admin-Key`）：

```text
GET /admin/usage
GET /admin/dashboard
```

统计按 UTC 周一 00:00 到下周一 00:00 汇总。启动时会先生成一次 SQLite 压缩快照，之后每 24 小时备份到 `/app/data/backup`，默认保留 7 份。配置 `BACKUP_TARGET=s3://bucket/prefix` 可启用 S3 兼容存储同步。检查方式：

```bash
docker compose exec gateway ls -lh /app/data/backup
docker compose exec gateway python restore_backup.py list
docker compose restart gateway
```

重启只重建容器，不删除 `gateway-data` volume；用户、历史、周统计和备份均应保留。

## 生产 HTTPS 与管理员登录

1. 设置 DNS `DOMAIN` 指向服务器，并在 `.env` 设置 `DOMAIN`、随机 `SECRET_KEY`、`SESSION_COOKIE_SECURE=true`。
2. 生成管理员密码哈希：

```bash
docker compose run --rm -it gateway python set_admin_password.py
```

3. 启动 Caddy：

```bash
docker compose --profile https up -d
```

Caddy 监听 80/443，自动申请和续期证书，并把 HTTP 跳转到 HTTPS。Gateway 的 8080 仍只绑定回环地址。SSH 隧道仅用于维护/调试，不是客户端 API 入口。

管理员页面：`/admin/login`。Session Cookie 为 HttpOnly、Secure、SameSite=Lax，并按 `ADMIN_SESSION_TTL_SECONDS` 过期。旧的 `X-Admin-Key` 只保留给受控自动化接口，不用于浏览器页面。

状态检查：

```text
GET /healthz
GET /status
```

## DuckDNS 免费 HTTPS 部署

服务器侧自动部署脚本：

```bash
export DUCKDNS_TOKEN='(只在当前 shell 设置)'
export DUCKDNS_SUBDOMAIN='<your-subdomain>'
export SERVER_IP='<server-ip>'
bash deploy_public.sh
```

脚本不会打印或写入 Git 的 Token；它会更新 DNS、等待解析、生成 `caddy/Caddyfile`、停止临时 HTTP profile，并启动 HTTPS profile。报告写入 `PUBLIC_DEPLOY_REPORT.md`。

## 公网只读 Dashboard

测试阶段可在 `.env` 设置：

```env
PUBLIC_DASHBOARD_MODE=true
```

然后通过 Caddy 访问 `/dashboard`。页面匿名可读，仅显示脱敏的系统状态、估算使用率和 `User A/B` 等匿名编号；数据接口为 `/api/public/dashboard`。它不会读取或返回完整 `users` 表、API Key、OAuth 或 Secret。

临时公网测试入口使用独立 HTTP profile（不与 HTTPS profile 同时启动）：

```env
PUBLIC_TEST_MODE=true
PUBLIC_PORT=18080
BIND_HOST=0.0.0.0
COOKIE_SECURE=false
```

```bash
docker compose --profile public-test up -d
```

当前临时只读数据页已恢复，可直接访问：

```text
http://<server-ip>:18080/dashboard
```

该页面是“Codex Gateway 状态”面板，显示 Gateway/Proxy 状态、估算周使用率、剩余率和匿名用户统计；它不是用户 Key 页面，也不是 Admin 页面。

该 profile 只转发 Dashboard 路径和 `/v1/*`，其他路径由临时 Caddy 返回 404。`/v1/*` 仍需要 Bearer API Key。由于这是 HTTP，Key 和请求内容未加密，只适合短期测试。防火墙只需开放 `18080/tcp`，Gateway `8080` 仍保持回环绑定。关闭：

```bash
docker compose --profile public-test down
```

验证边界：

```bash
curl -i https://<your-domain>/dashboard
curl -i https://<your-domain>/api/public/dashboard
curl -i https://<your-domain>/v1/models                 # 必须 401（无 Bearer Key）
curl -i https://<your-domain>/admin/dashboard           # 必须跳转登录
```

路由边界：Caddy 的 `/dashboard`、`/api/*`、`/v1/*` 统一 `reverse_proxy gateway:8080`；公网只发布 80/443，Gateway 的宿主机 8080 不接受公网连接。
