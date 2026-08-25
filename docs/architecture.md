# 香港入口 + 美国出口

## 线路

~~~text
Clash Meta
  ├─ HK-US → <hk-entry>:24443 (入口)
  │            └─ WireGuard → <us-exit>:51820
  │                               └─ NAT → 美国公网出口
  └─ US-Direct → <us-exit>:443 (备用直连)
~~~

香港只负责入口和转发；最终出口固定为美国 VPS。

## 已部署端口

| 主机 | 端口 | 用途 |
|---|---:|---|
| 香港 | 24443/tcp | VLESS + REALITY 入口 |
| 美国 | 51820/udp | WireGuard |
| 美国 | 443/tcp | VLESS + REALITY 直连 |

现有香港 Gateway、Caddy、Mihomo 端口未改动。香港的 18443 已占用，因此入口使用 24443。

## 重要配置

- 香港 WireGuard：10.66.0.2/30，策略路由表 51820
- 美国 WireGuard：10.66.0.1/30，开启 IPv4 转发和 MASQUERADE
- REALITY 目标：www.apple.com:443
- 可导入的实际 Clash 配置：C:\Users\风暴\Desktop\资料\clash-hk-us.yaml

实际配置包含 UUID 和 REALITY 公钥，不要提交到 Git 或公开粘贴。
