# Test Report

执行时间：2026-08-25

本地测试：

- Python 编译检查：通过
- API Key hash 不保存明文：通过
- 配额预留与结算、并发上限：通过
- 安全限流、Secret 阻断与审计：通过
- 用户统计隔离、周汇总、历史上限：通过

结果：14 tests passed。

生产加固本地验证：

- PBKDF2 管理员密码哈希与错误密码拒绝：通过
- 短密码策略：通过
- `restore_backup.py` 隔离恢复演练：恢复 4 个用户

服务器验证：

- Docker Compose 构建、启动和重启：通过
- `/healthz`：HTTP 200，`upstream_configured=true`
- 四个用户 `/api/me/usage`：全部 HTTP 200，数据按用户隔离
- 四个用户 `/api/me/history?limit=2`：全部返回限定条数
- `/admin/usage`：HTTP 200，返回 4 用户、总 Token 和总请求数
- `/dashboard`、`/admin/dashboard`：HTTP 200
- 启动后 SQLite 压缩备份：`/app/data/backup/usage_20260825.db.gz`
- 用户额度字段标记 `basis=estimated` 并返回 `last_sync_time`
- 重启后用户统计和备份仍存在
- `/status`：HTTP 200，Gateway/Proxy/数据库均为 `ok`，返回最近请求时间
- `/admin/dashboard` 未登录：HTTP 303 跳转 `/admin/login`
- 错误密码：HTTP 401
- 正确密码：HTTP 303，Cookie 带 `HttpOnly; Secure; SameSite=Lax`
- 注销后 Session：HTTP 401
- 数据库权限：600，备份目录：700
- Docker healthcheck、日志轮转配置：通过 Compose 校验

正式 DuckDNS 域名已配置，Caddy HTTPS profile 已启动并完成证书验收。

公网只读 Dashboard 验证：

- `PUBLIC_DASHBOARD_MODE=true`：已写入服务器环境
- 匿名 `/dashboard`：HTTP 200
- 匿名 `/api/public/dashboard`：HTTP 200，仅返回状态、估算百分比和 User A-D
- 无 Key `/v1/models`：HTTP 401
- 有 Key `/api/me/usage`：正常
- 匿名 `/admin/dashboard`：HTTP 303 跳转登录
- 页面源码敏感标记扫描：未发现 API Key、OAuth、Password、Bearer、Token
- Dashboard 访问审计记录：已写入数据库
- Docker 重启后 Dashboard、API、用户数据：通过

临时公网测试入口：

- `PUBLIC_TEST_MODE=true`、`PUBLIC_PORT=18080`：已配置
- 临时 Caddy profile：仅允许 Dashboard 只读路径，其他路径返回 404
- Gateway 8080：仍只绑定回环地址
- UFW 当前 inactive；Docker 仅发布测试端口
- 临时公网 `/v1/models` 无 Key：HTTP 401
- 临时公网 `/v1/models` 使用测试 Key：HTTP 200
- 临时公网 `/admin/dashboard`：HTTP 404
- 临时公网 `/dashboard`：HTTP 200

DuckDNS/Caddy HTTPS 验证：

- 域名：已脱敏（部署时由环境变量配置）
- DNS：已脱敏
- HTTPS Dashboard：HTTP 200
- `/v1/models` 无 Key：HTTP 401
- `/v1/models` 使用测试 Key：HTTP 200
- Admin 未登录：HTTP 303
- HTTP→HTTPS：HTTP 308
- Responses SSE：HTTP 200，收到 5583 字节 `data:` 流
- Caddy 重启后证书和 Dashboard：正常

统一 API 入口验收：

- API Base URL：`https://<your-domain>/v1`
- Caddy 路由：`/dashboard`、`/api/*`、`/v1/*` → `gateway:8080`
- 客户端禁止使用服务器 IP、宿主机 `8080` 或临时 `18080`
- OpenAI Compatible `models`、Responses 和 SSE 均通过上述域名验证
- Gateway 宿主机端口：`127.0.0.1:8080`；公网只发布 80/443
