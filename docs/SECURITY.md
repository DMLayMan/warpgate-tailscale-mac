# 安全边界与维护

2026-10-27 基线：Warpgate 0.30.2。0.30.1 刚修复 HTTP ticket 授权混淆、登录保护计数清理等问题，0.30.2 加入撤销用户角色关闭会话。不得把旧版本沿用为本方案的已验证基线。

本次锁文件扫描仍匹配：crypto-bigint 0.5.5 的时序问题、quick-xml 0.36.2 的两条拒绝服务公告，以及 rsa 0.9.10/0.10 RC 的同一 Marvin 公告，共 5 个包版本匹配项、4 个不同公告。尚未证明这些记录在本部署路径全部可利用。关闭监听、选用 Ed25519 不会消除二进制中的所有依赖，也不是完整修复。

本项目没有 fork 或修复 Warpgate 上游依赖。需要跟进官方修复，不能用配置加固宣称“零漏洞”。

信任边界：VPS root、Warpgate 进程、最高管理员和可信 Mac 管理员能控制目标访问。静态加密保护分离的数据库泄漏，不能对抗掌握运行进程及密钥的 VPS root。运行数据和录像仍需受控备份。

公网入口首次启用即有强密码、强制 MFA 登记和登录保护。完成管理员 TOTP 并验证后才创建 Mac Target。不要将应用端口绕过 HTTPS 代理公开，也不要向普通用户授予管理员角色或任意编辑目标的权限。

关闭转发限制 SSH 协议功能；普通 Shell 仍可以执行其系统用户权限允许的命令，并可以自行发起网络连接。若需要限制 Mac 内部横向访问，需要额外的系统/网络策略，不能把 DisableForwarding 当成 Shell 沙箱。

外部资料：

- [Warpgate 0.30.2](https://github.com/warp-tech/warpgate/releases/tag/v0.30.2)
- [Warpgate 0.30.1 安全修复](https://github.com/warp-tech/warpgate/releases/tag/v0.30.1)
- [静态凭据加密](https://warpgate.null.page/encryption/)
- [生产部署检查](https://warpgate.null.page/production-checklist/)
- [反向代理](https://warpgate.null.page/reverse-proxy/)
- [Caddy reverse_proxy](https://caddyserver.com/docs/caddyfile/directives/reverse_proxy)
- [crypto-bigint 公告](https://rustsec.org/advisories/RUSTSEC-2026-0387.html)
- [quick-xml CPU 公告](https://rustsec.org/advisories/RUSTSEC-2026-0194.html)
- [quick-xml 内存公告](https://rustsec.org/advisories/RUSTSEC-2026-0195.html)
- [rsa 公告](https://rustsec.org/advisories/RUSTSEC-2023-0071.html)
