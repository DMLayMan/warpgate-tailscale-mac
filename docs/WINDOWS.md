# Windows 原生 PowerShell 后端 SOP

适用链路：浏览器 → VPS 上的 Warpgate → Tailscale → Windows OpenSSH → 现有本地账号的默认 Shell。普通或管理员账号均可；需要 PowerShell 时可在终端运行 `powershell.exe`。客户端不用安装 Tailscale，不用持有网关私钥。保留已有 Mac Target，新增独立 Windows Target。

本入口只配置网络、系统账号和 SSH。不安装业务应用，不导入其他用户配置或凭据。Windows 原生方案不安装 WSL/tmux，不提供断线后的交互会话恢复；关闭网页后任务是否继续运行不作保证。

## 前置准备

- 真正的远端 Windows 11 或 Windows Server 2019+，64 位 Windows PowerShell 5.1，以管理员身份运行。Windows 10 的系统安全支持情况需另行确认。
- Windows Update / Features on Demand 可下载 OpenSSH Server。企业 WSUS 禁用此能力时需管理员提供组件源；不要关闭组织策略。
- 先在 Windows 安装[官方 Tailscale](https://tailscale.com/download/windows)，授权进入 VPS 所在 tailnet。安装器不保存登录密钥，也不会绕过浏览器授权。
- 建议在 Tailscale 设置启用 **Run unattended**，并验证注销桌面用户后设备仍在线。[官方说明](https://tailscale.com/docs/how-to/run-unattended)
- 准备 VPS 的 Tailscale IPv4 和 Warpgate Ed25519 **公钥**；不需要 VPS root 密码或网关私钥。
- 直接复用现有本地账号，普通账号或管理员账号均可；不创建用户、不重设密码、不改变用户组。选择 `Get-LocalUser` 中已启用的账号，用 `$env:USERNAME` 查看当前短名，无需切换图形桌面。
- 大小写、点、连字符及中间空格可用，脚本会规范为小写。关联微软账号也使用本地短名。本安装器不自动配置 AD 域账号；Microsoft Entra ID 密钥认证不受 Windows OpenSSH 支持。

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
  "serverUser": "REPLACE_WITH_EXISTING_USERNAME",
  "vpsTailnetIp": "100.64.1.10",
  "serverTailnetIp": null,
  "gatewayPublicKey": "REPLACE_WITH_GATEWAY_ED25519_PUBLIC_KEY_WITHOUT_COMMENT"
}
```

- `gatewayPublicKey`：VPS `/opt/warpgate-gateway/public/gateway-ed25519.pub` 的两段公钥，不含末尾注释。
- `serverUser`：已有 Windows 本地账号短名，可填实际 `$env:USERNAME`，不能照抄占位符。管理员账号可用，但具有其现有系统权限，只向可信网页用户授权。
- `vpsTailnetIp`：VPS 当前私网地址，不能填写它的公网地址。
- `serverTailnetIp: null`：自动读取当前 Windows 的 Tailscale IP。也可指定预期 IP，安装器会与本机实际地址比较，防止装错机器。
- 不添加密码、token、项目目录等其他字段；未知字段会拒绝。

公钥不是秘密，但真实网络参数及回执是私有运维信息，只通过可信渠道转交。

## 一条命令配置 Windows

在上述管理员 PowerShell 中执行：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$repo\scripts\setup_windows.ps1" -OnServer -Setup "$work\setup.json" -RestartSshd
```

`Bypass` 只作用于这一个 PowerShell 进程，不永久降低系统执行策略；组织策略阻止执行时，按组织要求签名/批准脚本，不绕过 GPO。阅读源码后再执行，不从网络直接 `iex`。

`-RestartSshd` 明确允许应用配置时重启已有 Windows sshd，可能影响这台 Windows 的 SSH 连接，安排维护窗口并从本机终端执行。它不重启 VPS，不修改已有 Mac。若不允许重启可省略；检测到运行中的 sshd 时会停止，避免静默中断。

原 `-CreateUser` 参数已移除。账号不存在时停止并要求选择已有账号，不弹出新密码提示。

脚本会：

1. 在修改前校验平台、管理员权限、JSON、公钥、Tailscale 登录及本机地址。
2. 检查所选现有账号，按需通过 Windows 可选功能安装 OpenSSH Server；保留已有主机密钥，只补齐缺失类型。不创建用户、不重设密码、不改变组成员。
3. 在 `%ProgramData%\WarpgateSSH-账号SID` 保存绑定和回执。SYSTEM/Administrators 可写，普通目标账号对公钥仅有读权限；管理员账号不额外添加个人 SID 权限项。SID 命名避免账号空格影响路径。
4. 在首个既有 Match 之前插入 `Match User "账号" Address VPS_IP`，将单独网关公钥路径加在原有效 AuthorizedKeysFile 列表前。原列表保留；网关密钥不会写入所有管理员共用的 administrators_authorized_keys。这样管理员账号也有独立网关身份，不授权其他管理员账号使用它。
5. 仅该账号来自指定 VPS 的连接要求公钥、禁止密码和各种转发、允许 PTY；同来源既有连接也受限制，其他来源和其他账号保持原设置。不设置 ForceCommand，不修改 DefaultShell 或远端登录 profile；默认 Shell 保留，未设置时是 cmd.exe。遇到已有多因素 AuthenticationMethods 或 ForceCommand 会停止要求人工整合，不自动放宽。
6. 运行 `sshd -t/-T -C` 检查候选配置及原授权文件列表，并比较同账号回环来源的配置未变，再应用配置、启用服务自启动并检查 TCP 22。不能把这些检查等同于真实公钥登录验收。
7. 新增只允许 VPS 私网 IP → 本机 Tailscale IP TCP 22 的防火墙规则。若本次刚安装 OpenSSH，禁用它新建的宽泛默认规则；既有规则保留，仍需审查是否存在更宽放行。

看到 `WINDOWS_LOCAL_READY` 仅代表本机配置完成，**不是全链路验收通过**。回执位置：

```text
C:\ProgramData\WarpgateSSH-账号SID\server-receipt.json
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

先在网页终端运行 `whoami` 核对现有账号；默认 cmd 可先执行 `powershell.exe` 进入 PowerShell，再运行：

```powershell
whoami
$env:USERPROFILE
$PSVersionTable.PSVersion
Write-Output 'WINDOWS_CHAIN_READY'
```

应显示你选择的现有 Windows 账号，用户目录属于该账号；实际命令成功才算核心链路通过。已有工具、文件和账号权限可复用；钥匙串式凭据、网络共享等可能需要额外认证，公钥登录不会携带 Windows 密码进行出站认证。退出再连接检查仍是该账号，原 Mac Target 应仍可用。Windows 原生终端不恢复之前的进程、窗口或工作目录。

再单独验收重启、Tailscale 无人值守、锁屏/注销和休眠恢复；BitLocker 预启动 PIN 可能需要现场输入。保留磁盘加密；复用已有管理员账号不等于新增管理员权限。网关录制可见输入输出，不在网页中打印密码或私钥。

## 重复执行、失败和回退

- 相同绑定可重复执行；不重设已有账号密码、不替换主机密钥、不追加重复 SSH 块。运行中的服务仍需显式 `-RestartSshd`；配置未变时不重启。
- 来源 IP、公钥或所选账号改变属于迁移，遇到冲突停止；不要删除绑定文件强行覆盖。
- 应用配置前先备份 `sshd_config`。应用/启动失败时恢复本次配置、新防火墙规则和原服务运行状态；已安装 Windows 功能与补齐的主机密钥保留，服务自启动设置需管理员核对。没有账号创建或密码重置。
- OpenSSH 安装要求重启时脚本停止，不自动重启 Windows；完成现场重启后再执行。
- 旧版 `# BEGIN warpgate windows 账号 v1` 块会全局限制账号并强制 PowerShell，新版检测到就停止。在本机维护窗口先备份当前配置，仅移除该账号完整 v1 BEGIN/END 块，保留其余配置、密钥和旧绑定目录，运行 `sshd -t` 后再执行新版；新版创建 SID 目录和 v2 来源匹配块。新连接验收前保留旧备份，不整份覆盖他人后来的改动。
- 首次成功后不删除当前 SID 目录的 `binding.json`、托管公钥、v2 SSH 块或本项目防火墙规则。源码/输入副本在安全保存回执后可删除。
- 回退前撤销 Target 并关闭连接；现场备份配置，仅移除对应 v2 BEGIN/END 块及 `WarpgateSSH-用户名` 防火墙规则，再验证并安排重启。不删除账号、不改密码或其他服务。

## 验证边界与参考

仓库的 PowerShell 测试只用临时目录检查输入、冲突、重复配置和原生配置解析；不创建真实账号或启动测试服务。Windows 真机部署、网关公钥认证、NTFS 实际读取和重启均需在每台后端执行，不能用单元测试替代。

- [Microsoft：OpenSSH 安装和系统要求](https://learn.microsoft.com/en-us/windows-server/administration/openssh/openssh_install_firstuse)
- [Microsoft：密钥认证、SID 与权限](https://learn.microsoft.com/en-us/windows-server/administration/openssh/openssh_keymanagement)
- [Microsoft：Windows sshd 配置](https://learn.microsoft.com/en-us/windows-server/administration/openssh/openssh-server-configuration)
