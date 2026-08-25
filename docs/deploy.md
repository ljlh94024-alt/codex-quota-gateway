# 部署与验证记录

## 服务

美国 VPS（Debian 12，地址由部署者配置）：

~~~text
systemctl enable --now wg-quick@wg0
systemctl enable --now xray
~~~

入口节点：

~~~text
systemctl enable --now wg-quick@wg0
systemctl enable --now wg-mark-route
systemctl enable --now hk-xray-firewall
systemctl enable --now xray
~~~

配置位置：

- /etc/wireguard/wg0.conf
- /usr/local/etc/xray/config.json
- /etc/systemd/system/wg-mark-route.service
- /etc/systemd/system/hk-xray-firewall.service

## Clash Meta

导入本机文件：

~~~text
C:\Users\风暴\Desktop\资料\clash-hk-us.yaml
~~~

策略组“美国出口”为 fallback，优先 HK-US，不可用时切换 US-Direct。

## 验证结果

- WireGuard 双端最新握手正常
- HK-US：经香港入口后出口 US，ASN AS2914 NTT America
- US-Direct：出口 US，ASN AS2914 NTT America
- YAML 结构解析通过，包含两个节点和 fallback 策略组
- 美国 VPS：470 MiB 总内存，实测已用 82 MiB；Xray RSS 约 35 MiB

## 项目出口分流

项目中的上游 Proxy 可绑定到入口节点的内部 HTTP 出口代理。该代理通过 WireGuard 隧道进入出口 VPS。

仅以下四类目标走美国出口：

1. GPT / OpenAI
2. Claude / Anthropic
3. Google
4. Microsoft

其余域名保持直连。旧节点池和旧监听不属于本模板运行路径。

## 项目出口优先级

香港服务器通过 `project-egress-qos.service` 对 WireGuard 出口进行无硬限速的优先级调度：

- 项目 HTTP 出口入口使用独立 fwmark，进入最高优先级队列。
- 其他 Xray 美国出口使用 fwmark 51820，进入低优先级队列。
- 空闲时各队列均可使用完整线路；只有发生拥塞时项目流量优先。
- 查看实时累计流量：`tc -s qdisc show dev wg0`。

生产配置备份应放在服务器私有目录，不提交到 Git。

## 回滚

只在明确需要时执行：停用新增的 WireGuard/Xray/策略路由服务，并恢复对应配置备份。不要停止香港现有 Gateway、Caddy 或 Mihomo。
