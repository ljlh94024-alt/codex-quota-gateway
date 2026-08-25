# 故障排查

## 先看服务

~~~bash
systemctl status wg-quick@wg0 xray
wg show
ss -ltnup
~~~

香港还要检查：

~~~bash
systemctl status wg-mark-route hk-xray-firewall
ip rule
ip route show table 51820
~~~

## 没有握手

检查美国 UDP 51820、防火墙和 WireGuard peer 的 endpoint；不要修改香港现有 443/18443 服务。

## 节点握手失败

确认客户端的 uuid、REALITY 公钥、short-id、servername 与服务器配置配对。当前目标是 www.apple.com；不要改回 www.microsoft.com，Xray 26.3.27 在该目标上可能因证书记录过大而拒绝握手。

## 出口不是美国

优先检查香港策略路由：

~~~bash
ip rule | grep 51820
ip route show table 51820
~~~

以及美国的转发和 NAT：

~~~bash
sysctl net.ipv4.ip_forward
iptables -t nat -S POSTROUTING
~~~

## 官方参考

- [Xray REALITY 配置](https://xtls.github.io/en/config/transports/reality.html)
- [Xray REALITY 示例](https://github.com/XTLS/Xray-examples/tree/main/VLESS-TCP-XTLS-Vision-REALITY)
