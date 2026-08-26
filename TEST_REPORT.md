# Test Report

执行时间：2026-08-26

## CODEX TASK 002 本地验收

- 基线：已审查基线 `863a960` 之后的本地后续提交；本轮补充了任务生命周期与额度结算保护，未改变认证、路由和配额比例策略。
- `git diff --check`：通过。
- `bash -n start.sh`、`deploy_public.sh`、`scripts/network_smoke_test.sh`、`scripts/deployment_config_smoke_test.sh`：通过。
- `python -m compileall -q app scripts`：通过。
- `python -m unittest discover -s tests -v`：42 tests passed。
- 基础 `docker compose config`：通过。
- `DOMAIN=example.invalid docker compose --profile https config`：通过。
- 真实 Caddy 容器 `validate --config /etc/caddy/Caddyfile --adapter caddyfile`：通过。
- `scripts/network_smoke_test.sh`：通过，输出 `NETWORK_SMOKE_OK`。
- `scripts/deployment_config_smoke_test.sh`：通过，输出 `DEPLOYMENT_CONFIG_SMOKE_OK`；未绑定宿主机 80/443，未启动正式 Caddy daemon。
- 配置冒烟干净环境：脚本在没有 `.env` 的独立工作树中自动创建临时 `.env`，校验后自动清理：通过。
- 端口边界断言：通过，输出 `PORT_BOUNDARY_OK`。
- 部署报告边界检查：通过，运行脚本包含 `--build`，不覆盖根目录报告，报告路径为被忽略的 `reports/`。
- 临时入口清理：脚本仅在 Compose 实际列出 `public_test` 时执行 stop/rm；清理命令失败会立即终止，清理后仍运行则明确失败。
- Compose 状态查询失败关闭：首次查询失败或清理后复查失败均返回非零；行为测试确认不会执行正式 `up --build`、不会输出成功标记、不会生成部署报告。
- DuckDNS 执行顺序：先完成基础 `.env` 准备，再更新并确认 DNS，最后写入 `DOMAIN`、`PUBLIC_TEST_MODE` 和安全 Cookie 字段；DNS 失败时不会写入正式公网字段。
- 任务生命周期：任务保存 `reserved_tokens`、`quota_state`、`actual_tokens` 和 `quota_finalized_at`；结算、usage log、周统计在单事务中恰好执行一次，重启恢复按任务释放 reservation。
- 流式保护：空闲超时和总时长超时进入 `timed_out` 终态；客户端取消显式处理 `CancelledError`，响应、HTTP 客户端和并发槽均在清理路径关闭/释放。
- 部署安全细节：脚本开头 `umask 077`；本机健康检查使用 `mktemp`，不再使用可预测的 `/tmp` 文件名。
- 敏感信息检查：通过；公开文件未发现真实 DuckDNS Token、API Key、OAuth、Cookie、凭据或服务器地址。

## 启动脚本验证

- `start.sh`：通过；首选 Python 实际执行失败时可回退到 `python`，两者均不可用时明确退出。
- `.env`：缺失、空值、占位符、已有值和缺少 `SECRET_KEY` 行均通过单元测试；重复执行保持有效值不变。
- `.env` 权限：脚本始终执行 `chmod 600`；Windows 本地文件系统不提供可等价读取的 POSIX mode，Linux 部署会按 600 生效。
- 本地实际执行 `start.sh`：通过，Gateway healthy，输出 `NETWORK_SMOKE_OK`。

## 本地运行状态

- Docker Desktop Server：29.7.2。
- Gateway：`127.0.0.1:8080`，healthcheck healthy。
- 正式 Caddy 端口配置：仅 80/443。
- Gateway 本次本地 health：HTTP 200；`upstream_configured=false`，因为本次未配置真实上游。

## 未执行项目

- 真实 DuckDNS API 更新。
- 真实域名证书签发和公网 HTTPS 端到端验收。
- 真实生产服务器部署、重启或数据卷操作。
- 真实 Codex 上游模型调用。

## 说明

根目录 `PUBLIC_DEPLOY_REPORT.md` 是脱敏模板。真实部署脚本运行报告只写入被 `.gitignore` 忽略的 `reports/public-deploy-*.md`，并使用受限权限；本地验收没有生成真实公网报告。
