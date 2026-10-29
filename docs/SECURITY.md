# 安全边界与维护

2026-10-27 基线：Warpgate 0.30.2。0.30.1 刚修复 HTTP ticket 授权混淆、登录保护计数清理等问题，0.30.2 加入撤销用户角色关闭会话。不得把旧版本沿用为本方案的已验证基线。

本次锁文件扫描仍匹配：crypto-bigint 0.5.5 的时序问题、quick-xml 0.36.2 的两条拒绝服务公告，以及 rsa 0.9.10/0.10 RC 的同一 Marvin 公告，共 5 个包版本匹配项、4 个不同公告。尚未证明这些记录在本部署路径全部可利用。关闭监听、选用 Ed25519 不会消除二进制中的所有依赖，也不是完整修复。

本项目没有 fork 或修复 Warpgate 上游依赖。需要跟进官方修复，不能用配置加固宣称“零漏洞”。

信任边界：VPS root、Warpgate 进程、最高管理员和可信 Mac 管理员能控制目标访问。静态加密保护分离的数据库泄漏，不能对抗掌握运行进程及密钥的 VPS root。运行数据和录像仍需受控备份。

远端 Mac/Windows 复用指定已有账号，允许管理员账号，不新建用户、不重设密码。网关连接继承该账号的实际权限，网页角色不会隔离同一个系统账号。Windows 的网关密钥使用单账号文件，不加入全体管理员共享的授权文件。

来源匹配加固只作用于选定账号从指定 VPS IP 发起的连接；其他来源保留原认证方式和密钥入口。因此它限制新增网关身份，并非撤销该账号原有所有 SSH 访问。同一 VPS 来源下既有连接也会受到公钥认证和禁止转发的限制。已有更强的多因素 AuthenticationMethods 或自定义 ForceCommand 会停止自动安装，要求人工整合，不静默放宽。

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
# 提交前隐私检查

提交前先暂存需要发布的源码与文档，再执行 `python3 scripts/check_tracked.py --history`。检查对象是 Git 暂存区的实际 blob、所有可达历史和提交元数据；未暂存的修改不会替代暂存区内容。

检查拒绝运行回执、截图、日志、私钥/凭据字面值、完整 SSH 公钥、个人家目录路径及非示例 IP/邮箱。文档使用 example.com、RFC 文档网段和明确的合成 tailnet 测试段。初始历史中有一个合成邮箱注入测试样本，按文件限定兼容；新样本使用 example.com。

如需检查本次部署特有的域名、账号、主机名或指纹，将逐行列表放在仓库外私有文件中，并通过 `WG_PRIVACY_DENYLIST=/private/path/identifiers.txt` 传入。不要把真实值写进检测规则、测试数据或文档。检测只报告文件和规则，不输出匹配内容。Git 作者邮箱使用 GitHub noreply，避免默认 Git 配置泄露私人邮箱。

静态模式检查不能保证发现所有形式的敏感信息，推送前仍需人工查看暂存差异。安装回执、账号授权、备份与测试截图应留在各自私有位置。
