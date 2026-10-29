# Tailscale 网络与账号边界

VPS 与远端 Mac/Windows 加入同一个 tailnet。浏览器用户由 Warpgate 管理，无需每个人加入 tailnet。使用普通 OpenSSH，不开启 Tailscale SSH。两种后端都复用现有系统账号。

## 标签与规则

`config/tailscale-policy.example.json` 是网关与后端节点的最小策略示例：

- tag:warpgate-vps → tag:warpgate-mac / tag:warpgate-windows：TCP 22。
- 不授予后端访问 VPS 8888，也不授予 VPS 访问后端的远程桌面、其他端口。
- 只有 tailnet 管理员可以分配标签。为实际设备分配对应标签，保留节点审批与其他现有设备的正常使用。

在已有 tailnet 中应人工合并 tagOwners、grants 和 tests，不能整体替换原策略。Tailscale 规则是叠加放行；现有 `*` 到 `*:*`、autogroup 或其他宽泛规则可能让本示例失去隔离作用。需要调整重叠规则，使 deny 测试通过；只添加一条窄规则并不能撤销旧权限。

确认实际设备获得正确标签后保存策略。不要在首次登录命令中放可复用登录密钥；如使用 auth key，选择短期、一次性、仅目标标签，使用完及时撤销，不放入 Git 或命令历史。

## 固定地址与授权来源

记录 VPS 与 Mac/Windows 的 tailnet IPv4。新增网关公钥带 `from="VPS_TAILNET_IP"`，仅该来源可用；账号的原 SSH 授权按原配置保留。删除并重新注册节点可能改 IP，届时应明确迁移授权和 Address 匹配，不删除来源限制绕过。

VPS 宿主机原生 Warpgate 直接连接 Mac 的 tailnet IPv4，不使用 exit node 或另一个指回自身的 SSH 跳板。确认 Mac sshd 实际日志看到的来源就是 VPS tailnet IP。

## 运行与恢复

- VPS 确认 tailscaled systemd 服务已启用。系统更新后检查服务与登录状态。
- 远端 Mac 使用官方 Tailscale 客户端，配置登录时启动，保持节点在线。不同 macOS 分发方式的后台运行条件不同，必须验证无人值守重启后的实际表现。
- 按节点身份确认密钥到期策略。若为无人值守设备调整到期策略，应记录设备负责人、轮换和撤销方式；不要为所有设备全局关闭到期。
- Direct/DERP 路径都可能工作，DERP 的延迟和带宽需要现场测试。Tailscale ping 成功不代表 TCP 22、Mac 账号或 SSH 公钥授权成功。

官方资料：

- [策略语法](https://tailscale.com/docs/reference/syntax/policy-file)
- [访问控制](https://tailscale.com/kb/1018/acls)
- [macOS 安装](https://tailscale.com/kb/1016/install-mac)
- [Linux 静态二进制](https://tailscale.com/kb/1053/install-static)
