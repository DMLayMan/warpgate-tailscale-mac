# 验收、备份和维护

## 上线验收

部署人员逐项记录实际结果；任何一项未完成，不宣称全链路就绪：

- 域名解析到正确 VPS，浏览器证书有效，HTTPS 自动跳转及 WebSocket 连接正常。
- 普通用户只看到授权目标；密码后必须完成 TOTP，未登记 TOTP 被引导登记且不能直接绕过。
- VPS 实际进程到 Mac/Windows tailnet TCP 22 可达，来源地址与后端公钥 from 限制一致。
- 在网页运行 `whoami`，核对登录的是选择的现有系统账号，原文件/工具权限符合预期。Windows 管理员 Target 的网关公钥不能用来登录其他管理员账号。
- 验证该账号原有密钥路径保留、其他来源的 SSH 访问保持原设置；指定 VPS 来源只允许公钥且不能建立 SSH 转发。不通过降低已有多因素认证来接入。
- 两个网页账号同时操作；无权限账号拒绝；撤销一个用户的角色后，其已有网页终端关闭，另一用户继续工作。共用一个系统账号仍共享该账号的权限、文件及 Mac tmux 会话。
- 确认后端主机公钥已固定；未知公钥和公钥变化拒绝连接，旧算法关闭。
- 播放一次录制的终端会话。录像可能记录屏幕上的秘密，按敏感数据管理。
- 从外网确认 8888、2222、数据库和远程桌面端口没有暴露；管理 SSH 只有预期范围可达。
- VPS 与各后端分别重启，验证 Tailscale、证书、应用和 SSH 恢复；验证 Mac FileVault/Windows BitLocker 预启动解锁、锁屏/注销和休眠边界。Windows 不提供持久终端恢复。
- 在隔离临时环境恢复备份并确认解密与连接，再测试一次更新回退。

## 备份

运行数据：`/opt/warpgate-gateway/data`，包含 SQLite、网关 SSH 私钥、TLS 服务器私钥和录像；配置和公开 CA 在 config/public。
凭据加密密钥及内部 CA 私钥放在 secrets，必须单独备份到密码管理器或加密离线介质。仅复制数据库不能完成恢复。

安装可信发行渠道提供的 age，准备离线保存私钥的 age 接收方公钥，然后运行：

```sh
sudo bash scripts/backup-vps.sh --on-vps \
  age1REPLACE_WITH_REAL_PUBLIC_RECIPIENT /var/backups/warpgate-YYYYMMDD.age
```

脚本会暂停 Warpgate、加密打包 config/data/public，并在结束或失败时恢复原运行状态。不会将原始 tar 落盘，不包含 secrets。备份期间网页连接中断，提前安排窗口。目标文件必须是新的绝对路径且位于运行目录之外。失败的部分文件保留，不能当成有效备份。

小容量 VPS 建议每天离机备份，保留最近 7 个日备份和 4 个周备份，检查可恢复性。本方案设置 log.retention=3days、audit_retention=90days：Warpgate 会按已结束会话的结束时间清理过期录像和一般日志，审计日志单独保留 90 天。这是时间保留策略，并非磁盘字节配额；长期存活会话的录像仍可能增长，需要监控容量。备份副本有独立留存周期，也可能包含已在在线系统清理的录像。不要直接按 mtime 删除正在写入的文件，不要让 30 GiB 磁盘因录像或 journal 填满。

## 恢复

1. 在隔离的 Linux 临时主机安装备份对应的锁定版本，保持两个服务停止、无公网入口，不连接生产 tailnet；不要同时运行具有同一身份的克隆。
2. 用离线 age 私钥解密到权限 700 的临时目录，先查看归档内容，再将 config/data/public 恢复到同一绝对根目录。只使用自己生成且校验过的备份，拒绝包含绝对路径、`..` 或异常符号链接的归档。
3. 从独立秘密备份恢复 secrets/encryption.env 与 secrets/backend-ca.key，分别设置 600，secrets 目录 700、root 所有。
4. 数据及 config 归 wg-gateway，数据目录 700；public 的 CA/配置仅包含公开信息，其他秘密文件不能复制进去。
5. `warpgate check`、Caddy validate、隔离回环启动检查；确认没有“无法解密凭据”的日志，再验证授权、TOTP、录像和 SSH 登录。
6. 生产切换前安排独占节点身份和 Tailscale 注册/来源 IP 验收。VPS tailnet IP 改变时同步更新各后端的公钥 from、Match Address 及 Windows 防火墙来源限制。

## 更新与回退

不自动追 latest。每次检查 Warpgate/Caddy/Tailscale 官方安全公告，在隔离副本升级，更新 versions.lock.json 和 SHA256，验证后安排短维护窗口替换二进制。
升级前做可恢复备份并保存旧二进制。数据库迁移可能不可逆；回退应恢复升级前数据库、配置和匹配的加密密钥，不能只换回旧二进制。
可选静态安装的 Tailscale 不受 dnf 自动更新管理，需要主动更新；若已有包管理器安装，继续沿用其更新渠道，不混用。

公网证书由 Caddy 自动续期。内部 CA 有效 10 年，后端 TLS 叶证书 825 天；`check-vps.sh` 会检查叶证书至少剩余 30 天，应纳入现有监控并安排提前续签。续签保留 CA、重启 Warpgate；更换 CA 时需要同步更新 Caddy 的公开信任文件并重启它。本项目未自动调度续签。

## 故障定位

```sh
sudo journalctl -u warpgate-gateway -n 100 --no-pager
sudo journalctl -u warpgate-https -n 100 --no-pager
sudo systemctl status tailscaled --no-pager
df -h /opt/warpgate-gateway
```

避免用 debug/trace 日志长期记录真实会话。Caddy 未开启包含完整 URL 的访问日志，减少 ticket 或查询秘密进入日志的机会。
MFA 丢失时，从可信 VPS 管理通道使用匹配版本的 `warpgate recover-access`，按官方恢复说明完成操作后立即重新登记 MFA；不要长期启用管理员 Token 或取消全局 MFA。
