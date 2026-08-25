# Public Dashboard

测试阶段开启匿名只读面板：

```env
PUBLIC_DASHBOARD_MODE=true
```

临时公网入口使用独立 HTTP Caddy profile：

```env
PUBLIC_TEST_MODE=true
PUBLIC_PORT=18080
BIND_HOST=0.0.0.0
COOKIE_SECURE=false
```

启动或关闭：

```bash
docker compose --profile public-test up -d
docker compose --profile public-test down
```

临时 Caddy 只转发 Dashboard 路径和 `/v1/*` OpenAI 兼容 API，其他路径返回 404。`/v1/*` 仍由 Gateway 强制校验 Bearer API Key。Gateway 的 8080 仍只绑定宿主机回环地址。设置 `PUBLIC_DASHBOARD_PASSWORD` 后，页面会先要求测试观察密码。

访问：

```text
/dashboard
/api/public/dashboard
```

公开内容只有：

- Gateway/Proxy 在线状态
- 最近请求时间
- 估算周使用率与剩余率
- 匿名用户编号及周使用率

页面不接收 API Key，也不调用 `/api/me/*`。OpenAI 兼容 API (`/v1/*`) 继续要求 Bearer Key；`/admin/*` 继续要求管理员 Session 或受控自动化凭据。

每次公开页面/API访问只记录时间、来源 IP 和路径，不记录请求内容或凭据。临时 HTTP 会明文传输 API Key 和请求内容，只适合短期测试；测试入口关闭后，将 `PUBLIC_TEST_MODE=false` 并停止 `public-test` profile。正式上线应通过 HTTPS Caddy profile 暴露，不要将 Gateway 8080 端口改为公网监听。
