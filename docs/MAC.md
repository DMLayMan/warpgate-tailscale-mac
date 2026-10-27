# 远端 Mac 一次性配置

只在真正提供 SSH 的远端 Mac 上执行。本地浏览器客户端无需安装 Tailscale、打开远程登录或修改 ~/.ssh。

## 1. 准备账号与网络

1. 在系统设置 → 用户与群组创建专用的“标准”账号，例如 remoteoperator。不要给它管理员权限。每个不互信的人需要单独系统账号和 Target。
2. 远端 Mac 安装官方 Tailscale，加入与 VPS 相同的 tailnet，配置启动行为和设备标签；记录 VPS 与 Mac 的 Tailscale IPv4。
3. 系统设置 → 通用 → 共享 → 远程登录，访问范围选择“仅这些用户”，加入专用账号。不要开启“允许远程用户完全访问磁盘”，除非具体工作确实需要。
4. 在 Mac 本机确认有 `/etc/ssh/ssh_host_ed25519_key.pub`。部分系统首次开启远程登录后才会生成；安装器不会替换系统主机密钥。

远程登录的系统设置可能需要管理员确认和系统隐私权限，应由 Mac 现场管理员完成。脚本不会关闭 FileVault、修改全局防火墙或改掉其他账号的 SSH 配置。

## 2. 授权网关公钥

从 VPS 取得 `/opt/warpgate-gateway/public/gateway-ed25519.pub`，通过可信渠道带到远端 Mac。只传公钥，不导出 Warpgate 数据库或私钥。

在远端 Mac 的管理员终端执行，将以下示例的两个 100.x 地址换为实际值：

```sh
sudo python3 scripts/setup_mac.py --on-server \
  --user remoteoperator \
  --gateway-key /private/path/gateway-ed25519.pub \
  --vps-tailnet-ip 100.64.1.10 \
  --mac-tailnet-ip 100.64.1.20 \
  --receipt /private/path/mac-receipt.json
```

安装器会拒绝管理员/系统账号、非法密钥、非 tailnet IPv4、已有托管文件或覆盖操作。第一次安装写入：

- `/etc/ssh/warpgate_authorized_keys/用户名`，root 所有、权限 644，仅包含公钥。普通账号不能自行替换来源限制或添加其他身份。
- `/etc/ssh/sshd_config.d/999-warpgate-用户名.conf`，排序在 macOS 的全局 UsePAM/Subsystem 配置后，仅针对这个用户。

公钥限制包含 `from="VPS_TAILNET_IP",restrict,pty`。这个账号只使用独立的系统托管公钥文件；原 ~/.ssh/authorized_keys 文件保留，但不再用于此专用账号的 SSH 认证。因此应使用新建专用账号，不要把已有日常账号直接改作网关账号。

此账号仅允许公钥认证、禁止密码/键盘交互认证，禁止 TCP、Unix socket、代理、X11 等转发，允许交互终端，禁止用户 SSH rc。其他系统账号的设置不变。

脚本运行 `sshd -t` 和针对指定账号的 `sshd -T -C` 验证实际生效配置。已有 Match/Include 导致设置未生效时会回滚新增文件并停止；应审查已有配置，不应靠更宽松策略强行通过。

不会强行重启 sshd，避免中断其他用户。macOS 的 launchd 通常按新连接读取配置；以实际新登录和有效配置为准，必要的服务操作由管理员安排。

## 3. 交接回执

mac-receipt.json 包含账号、双方 tailnet IP 和 Mac Ed25519 主机公钥，不包含私钥。文件权限 600，经可信渠道交回 VPS。Mac 本机另行查看指纹并核对：

```sh
ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub -E sha256
```

随后按 VPS.md 先登记主机公钥，再建立目标。不要将回执提交到仓库。

## 4. 无人值守注意事项

- 打开适合该机型的“显示器关闭时防止自动睡眠”选项；保持电源与网络。不要假设所有 Mac 机型或合盖状态都能可靠唤醒。
- 保留 FileVault。断电/重启后的预启动解锁可能需要现场操作，未解锁时不能依靠普通 SSH 连接。
- Tailscale 启动、Remote Login、FileVault 解锁和网络恢复是不同条件。必须做一次真实重启/休眠恢复验收。
- 浏览器断开后网关约 60 秒清理未附着的网页会话，需要持续工作时可在 Mac 使用 tmux。管理员撤权关闭连接不会自动终止脱离会话的后台任务。

## 5. 回退与轮换

删除前先在 Warpgate 撤销角色并终止该目标的存活连接。现场管理员确认不再需要后，移走本项目新增的 drop-in 与系统托管公钥文件，再运行 `sshd -t`，其他配置保持不动。

更换 VPS tailnet IP 或网关公钥属于显式迁移：保留现场管理通道，核对新来源/公钥，安排更新托管文件并做新连接验收。安装器不会自动覆盖旧绑定。
