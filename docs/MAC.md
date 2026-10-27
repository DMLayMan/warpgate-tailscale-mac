# 远端 Mac 一次性配置

只在真正提供 SSH 的远端 Mac 上执行。本地浏览器客户端无需安装 Tailscale、打开远程登录或修改 ~/.ssh。

## 1. 准备账号与网络

1. 在系统设置 → 用户与群组创建专用的“标准”账号，例如 remoteoperator。不要给它管理员权限。每个不互信的人需要单独系统账号和 Target。
2. 远端 Mac 安装官方 Tailscale，加入与 VPS 相同的 tailnet，配置启动行为和设备标签；记录 VPS 与 Mac 的 Tailscale IPv4。
3. 系统设置 → 通用 → 共享 → 远程登录，访问范围选择“仅这些用户”，加入专用账号。不要开启“允许远程用户完全访问磁盘”，除非具体工作确实需要。
4. 在 Mac 本机确认有 `/etc/ssh/ssh_host_ed25519_key.pub`。部分系统首次开启远程登录后才会生成；安装器不会替换系统主机密钥。

远程登录的系统设置可能需要管理员确认和系统隐私权限，应由 Mac 现场管理员完成。脚本不会关闭 FileVault、修改全局防火墙或改掉其他账号的 SSH 配置。

## 2. 一条命令配置 SSH 与持久终端

从 VPS 取得 `/opt/warpgate-gateway/public/gateway-ed25519.pub`，通过可信渠道带到远端 Mac。只传公钥，不导出 Warpgate 数据库或私钥。

提前准备以下输入，实际值只留在服务器或仓库外的私有目录：

| 输入 | 获取方式 |
| --- | --- |
| `--user` | 已创建、允许远程登录的标准账号短名称；不是网页账号 |
| `--gateway-key` | 从 VPS 带来的网关 Ed25519 公钥文件路径 |
| `--vps-tailnet-ip` | VPS 的 Tailscale IPv4 |
| `--mac-tailnet-ip` | 当前待新增 Mac 的 Tailscale IPv4 |
| `--receipt` | 已存在的私有目录内、用于保存回执的完整文件路径 |

Mac 需有可用的 Python 3.9+，并允许 Python 通过正常 HTTPS 访问 GitHub Release。默认持久终端要求 macOS 15+、目标账号 shell 为 `/bin/zsh`，不要求 Homebrew。系统账号创建、Tailscale 登录和 macOS 远程登录授权仍是前置准备，不会被脚本绕过。

在远端 Mac 的管理员终端执行，将以下账号、路径和两个示例地址换为实际输入：

```sh
sudo python3 scripts/setup_mac.py --on-server \
  --user remoteoperator \
  --gateway-key /private/path/gateway-ed25519.pub \
  --vps-tailnet-ip 100.64.1.10 \
  --mac-tailnet-ip 100.64.1.20 \
  --receipt /private/path/mac-receipt.json
```

命令会验证账号和 SSH 配置，下载并验证固定版本的 tmux，再在指定标准账号下配置自动恢复。看到 `MAC_SESSIONS_CONFIGURED` 和 `MAC_SSH_CONFIGURED` 表示两部分初始化完成；真实链路仍需按下文验收。

安装器拒绝管理员/系统账号、非法密钥、非 tailnet IPv4、符号链接以及有冲突的托管文件。相同 SSH 绑定可以重复运行；不会覆盖不同的公钥、来源 IP 或账号绑定。相同回执保持不变，增加持久终端字段时先保存旧回执副本。第一次安装写入：

- `/etc/ssh/warpgate_authorized_keys/用户名`，root 所有、权限 644，仅包含公钥。普通账号不能自行替换来源限制或添加其他身份。
- `/etc/ssh/sshd_config.d/999-warpgate-用户名.conf`，排序在 macOS 的全局 UsePAM/Subsystem 配置后，仅针对这个用户。
- 指定账号的 `~/.local/bin/remote-resume`、独立 tmux 配置、经校验的用户级 tmux 二进制，以及 `.zshrc` 的带标记自动恢复块；原文件先备份。详见 [SESSIONS.md](SESSIONS.md)。

公钥目录显式设为 755，避免调用方的 `umask 077` 使标准账号无法读取公钥。目录仍由 root 管理，普通账号无写权限。

只需要 SSH 或已自行配置终端时，可在原命令后加 `--without-tmux`；这不会卸载或停掉原有会话。已经配置好 SSH 的后端可以单独补上持久终端：

```sh
sudo python3 scripts/setup_mac.py --on-server --user remoteoperator --sessions-only
```

旧版手工安装存在同名 `remote-resume`、不同的自动恢复块或 `ZDOTDIR` 时，先按迁移说明处理；不要删除仍承载任务的 tmux 服务来强行重装。

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
- 默认安装的 tmux 会在新交互式 SSH 连接中自动恢复 `work` 会话，浏览器关闭后仍保留任务。配置前打开的终端需重新连接；原有进程不会自动迁入 tmux。管理员撤权关闭连接不会自动终止脱离会话的后台任务。

## 5. 回退与轮换

删除前先在 Warpgate 撤销角色并终止该目标的存活连接。现场管理员确认不再需要后，移走本项目新增的 drop-in 与系统托管公钥文件，再运行 `sshd -t`，其他配置保持不动。

更换 VPS tailnet IP 或网关公钥属于显式迁移：保留现场管理通道，核对新来源/公钥，安排更新托管文件并做新连接验收。安装器不会自动覆盖旧绑定。
