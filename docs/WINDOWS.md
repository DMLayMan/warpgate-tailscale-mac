# Windows 原生 PowerShell 后端 SOP

适用链路：浏览器 → VPS 上的 Warpgate → Tailscale → Windows OpenSSH → 专用标准账号的 PowerShell。客户端不用安装 Tailscale，不用持有网关私钥。保留已有 Mac Target，新增独立 Windows Target。

本入口只配置网络、系统账号和 SSH。不安装业务应用，不导入其他用户配置或凭据。Windows 原生方案不安装 WSL/tmux，不提供断线后的交互会话恢复；关闭网页后任务是否继续运行不作保证。

## 前置准备

- 真正的远端 Windows 11 或 Windows Server 2019+，64 位 Windows PowerShell 5.1，以管理员身份运行。Windows 10 的系统安全支持情况需另行确认。
- Windows Update / Features on Demand 可下载 OpenSSH Server。企业 WSUS 禁用此能力时需管理员提供组件源；不要关闭组织策略。
- 先在 Windows 安装[官方 Tailscale](https://tailscale.com/download/windows)，授权进入 VPS 所在 tailnet。安装器不保存登录密钥，也不会绕过浏览器授权。
- 建议在 Tailscale 设置启用 **Run unattended**，并验证注销桌面用户后设备仍在线。[官方说明](https://tailscale.com/docs/how-to/run-unattended)
- 准备 VPS 的 Tailscale IPv4 和 Warpgate Ed25519 **公钥**；不需要 VPS root 密码或网关私钥。
- 专用本地标准账号默认 `remoteoperator`。脚本的 `-CreateUser` 只在账号不存在时通过安全密码提示创建；已有账号不重设密码，管理员账号或禁用账号直接拒绝。无需切换 Windows 当前桌面用户。

当前安装器依赖官方 Tailscale 的默认路径 `%ProgramFiles%\Tailscale\tailscale.exe` 和 Windows 内置 OpenSSH 的默认服务。自定义路径、端口、服务启动参数需先人工整合。

## 获取代码

仓库：[DMLayMan/warpgate-tailscale-mac](https://github.com/DMLayMan/warpgate-tailscale-mac)。仓库名保留，现同时支持两种后端。公开后可以匿名下载，不需要在远端登录 GitHub。

在 Windows 的管理员 PowerShell 中：

```powershell
$work = Join-Path $env:USERPROFILE ('Downloads\warpgate-setup-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $work | Out-Null
$zip = Join-Path $work 'source.zip'
Invoke-WebRequest 'https://github.com/DMLayMan/warpgate-tailscale-mac/archive/refs/heads/main.zip' -OutFile $zip
Expand-Archive -LiteralPath $zip -DestinationPath $work
$repo = Join-Path $work 'warpgate-tailscale-mac-main'
```

上面下载最新 main。可复现部署应使用运维提供的完整提交 SHA：将 URL 中的 `refs/heads/main` 换成 SHA，解压目录相应变为 `warpgate-tailscale-mac-SHA`，并核对运维提供的 ZIP SHA256。也可以通过 Git 拉取并 checkout 审查过的提交。

## 准备一个 setup.json

配置必须放在仓库外，例如 `$work\setup.json`。从 `config/windows.example.json` 复制并替换真实输入；不要把示例公钥占位符直接交给安装器。

```json
{
  "schemaVersion": 1,
  "serverUser": "remoteoperator",
  "vpsTailnetIp": "100.64.1.10",
  "serverTailnetIp": null,
  "gatewayPublicKey": "REPLACE_WITH_GATEWAY_ED25519_PUBLIC_KEY_WITHOUT_COMMENT"
}
```

- `gatewayPublicKey`：VPS `/opt/warpgate-gateway/public/gateway-ed25519.pub` 的两段公钥，不含末尾注释。
- `vpsTailnetIp`：VPS 当前私网地址，不能填写它的公网地址。
- `serverTailnetIp: null`：自动读取当前 Windows 的 Tailscale IP。也可指定预期 IP，安装器会与本机实际地址比较，防止装错机器。
- 不添加密码、token、项目目录等其他字段；未知字段会拒绝。

公钥不是秘密，但真实网络参数及回执是私有运维信息，只通过可信渠道转交。

## 一条命令配置 Windows

在上述管理员 PowerShell 中执行：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$repo\scripts\setup_windows.ps1" -OnServer -Setup "$work\setup.json" -CreateUser -RestartSshd
```

`Bypass` 只作用于这一个 PowerShell 进程，不永久降低系统执行策略；组织策略阻止执行时，按组织要求签名/批准脚本，不绕过 GPO。阅读源码后再执行，不从网络直接 `iex`。

`-RestartSshd` 明确允许应用配置时重启已有 Windows sshd，可能影响这台 Windows 的 SSH 连接，安排维护窗口并从本机终端执行。它不重启 VPS，不修改已有 Mac。若不允许重启可省略；检测到运行中的 sshd 时会停止，避免静默中断。

脚本会：

1. 在修改前校验平台、管理员权限、JSON、公钥、Tailscale 登录及本机地址。
2. 检查/创建专用标准账号，按需通过 Windows 可选功能安装 OpenSSH Server；保留已有主机密钥，只补齐缺失类型。
3. 在 `%ProgramData%\WarpgateSSH-用户名` 保存管理员管理的公钥绑定和回执。SYSTEM/Administrators 可写，目标账号对公钥只有读权限。采用 SID，兼容中文系统组名。
4. 在 `sshd_config` 追加带标记、仅针对这个用户的块：只接受公钥、来源限定 VPS IP、禁止各类转发、允许终端。其他账号原配置保留。
5. 为该账号强制启动 Windows PowerShell `-NoLogo -NoProfile`，不修改系统全局 DefaultShell。这个入口限定交互终端，不提供该账号的 SFTP/scp/任意 SSH exec 命令；其他账号保持原行为。
6. 运行 `sshd -t` 和 `sshd -T -C` 校验语法及实际生效设置，再应用配置、启用服务自启动、检查 TCP 22 监听。
7. 新增只允许 VPS 私网 IP → 本机 Tailscale IP TCP 22 的防火墙规则。若本次刚安装 OpenSSH，禁用它新建的宽泛默认规则；既有规则保留，仍需审查是否存在更宽放行。

看到 `WINDOWS_LOCAL_READY` 仅代表本机配置完成，**不是全链路验收通过**。回执位置：

```text
C:\ProgramData\WarpgateSSH-remoteoperator\server-receipt.json
```

在管理员终端核对主机指纹：

```powershell
& "$env:WINDIR\System32\OpenSSH\ssh-keygen.exe" -lf "$env:ProgramData\ssh\ssh_host_ed25519_key.pub" -E sha256
```

回执只含主机公钥、账号、IP、端口和能力标记，不含密码或私钥。用可信渠道交给网关管理员。不要交出整个 `%ProgramData%\ssh` 目录，其中有主机私钥。

## VPS 与 Warpgate 接入

先确认 tailnet 允许 VPS → 新 Windows 的 TCP 22。同一个 tailnet 不等于已经放行；保留现有 Mac 规则，审查重叠宽泛规则。跨 tailnet 需单独验证共享方向与设备身份，不将共享 VPS 当成 VPS 可以主动连接对方的证明。

在已有可信 VPS 管理会话中：

```sh
tailscale ping NEW_WINDOWS_TAILNET_IP
nc -vz -w 5 NEW_WINDOWS_TAILNET_IP 22
```

将回执与 `gateway-key-id.txt` 放到客户端/管理机的私有目录，使用本仓库工具生成字段（Python 3.9+；在现有 Mac 管理客户端或 Linux VPS 执行）：

```sh
python3 scripts/wgkit.py prepare-target \
  --receipt /private/path/server-receipt.json \
  --key-id /private/path/gateway-key-id.txt \
  --name remote-windows \
  --output /private/path/windows-target
```

输出目录必须是新路径。工具同时兼容旧 Mac 回执，不会修改线上目标。

1. 以管理员进入 Warpgate → Config → Targets，**新建** `remote-windows`，暂不分配普通访问角色。
2. 填入生成文件中的 host、port、username，认证选 PublicKey，并明确选择 `gateway-key-id.txt` 对应的 Ed25519 身份；保持不安全算法关闭。
3. 当前 0.30.2 UI 可在目标保存后执行 Check host key。将结果与 Windows 本机显示的指纹、可信回执完整核对，一致后才 Trust。不能只凭网络扫描结果信任新主机。
4. 创建/选择普通角色，例如 `windows-operators`，将目标及指定网页用户关联。不要给日常用户管理员角色，不改变已有 Mac 目标和角色。
5. 保持强制 MFA、自助票据关闭；普通用户登录后应只看到自己被授权的目标。

## 客户端使用和验收

客户端打开自己的网关 HTTPS 地址 → 网页账号和 TOTP → 点击 `remote-windows`。不需要安装本仓库、不需要客户端 SSH 密钥，也不需要开启客户端 Tailscale。SSH 私钥由 VPS 保管。

在网页终端运行：

```powershell
whoami
$env:USERPROFILE
$PSVersionTable.PSVersion
Write-Output 'WINDOWS_CHAIN_READY'
```

应显示新 Windows 的 `机器名\remoteoperator`，且用户目录属于该账号；实际命令执行成功才算核心链路通过。退出再连接，检查仍是相同账号；原 Mac Target 应仍可使用。原生 PowerShell 不恢复之前的进程、窗口或工作目录。

再单独验收 Windows 重启、Tailscale 无人值守、锁屏/注销、休眠恢复；BitLocker 预启动 PIN 等可能需要现场输入。不要为无人值守关闭磁盘加密或把账号升级为管理员。网关终端会录制可见输入输出，不在网页中打印密钥/密码。

## 重复执行、失败和回退

- 相同绑定可重复执行；不重设已有账号密码、不替换主机密钥、不追加重复 SSH 块。运行中的服务仍需显式 `-RestartSshd`；配置未变时不重启。
- 来源 IP、公钥或专用账号改变属于迁移，遇到冲突停止；不要删除绑定文件强行覆盖。
- 应用配置前先备份 `sshd_config`。应用/启动失败时恢复本次配置和新防火墙规则，恢复原服务运行状态。已新建账号、已安装 Windows 功能、生成的缺失主机密钥会保留，不盲目删除；服务自启动设置也需管理员核对。
- OpenSSH 安装要求重启时脚本停止，不自动重启 Windows；完成现场重启后再执行。
- 首次成功后不删除 `binding.json`、系统托管公钥、托管 SSH 块或防火墙规则。下载 ZIP、解压源码和输入副本在安全保存回执后可删除。
- 回退前先撤销该 Target 的角色并关闭其连接。现场管理员备份当前配置，仅移除对应的 BEGIN/END 块与 `WarpgateSSH-用户名` 防火墙规则，再 `sshd -t` 并安排服务重启。不要整份覆盖后来有他人修改的配置，不删除其他用户或服务。

## 验证边界与参考

仓库的 PowerShell 测试只用临时目录检查输入、冲突、重复配置和原生配置解析；不创建真实账号或启动测试服务。Windows 真机部署、网关公钥认证、NTFS 实际读取和重启均需在每台后端执行，不能用单元测试替代。

- [Microsoft：OpenSSH 安装和系统要求](https://learn.microsoft.com/en-us/windows-server/administration/openssh/openssh_install_firstuse)
- [Microsoft：密钥认证、SID 与权限](https://learn.microsoft.com/en-us/windows-server/administration/openssh/openssh_keymanagement)
- [Microsoft：Windows sshd 配置](https://learn.microsoft.com/en-us/windows-server/administration/openssh/openssh-server-configuration)
