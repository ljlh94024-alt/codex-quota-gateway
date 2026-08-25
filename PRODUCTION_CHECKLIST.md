# Production Checklist

- [ ] `.env` 不提交 Git；`SECRET_KEY` 使用随机值
- [ ] `ADMIN_PASSWORD_HASH` 或 `/app/secrets/admin_password_hash.txt` 已配置
- [ ] `SESSION_COOKIE_SECURE=true`
- [ ] DNS `DOMAIN` 已指向服务器
- [ ] `docker compose --profile https up -d` 后 `/healthz` 为 200
- [ ] HTTP 自动跳转 HTTPS，证书由 Caddy 管理
- [ ] `/admin/dashboard` 未登录时跳转 `/admin/login`
- [ ] `/app/data/gateway.db` 和 `/app/data/backup` 权限为 600/700
- [ ] `usage_*.db.gz` 本地备份存在且 `restore_backup.py list` 可读
- [ ] 如使用外部备份，`BACKUP_TARGET` 和 S3 凭据只放 `.env`
- [ ] Docker healthcheck、restart、日志轮转均生效
- [ ] `docker compose restart` 后用户、统计、OAuth 数据仍存在
