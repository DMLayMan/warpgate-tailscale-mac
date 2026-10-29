# 验证范围

## 现有账号复用（2026-10-29）

Mac/Windows 安装器改为复用已有普通或管理员账号，移除 Windows 新建用户入口。网关公钥放在单账号托管文件中，来源限定为指定 VPS；保留原 AuthorizedKeysFile 列表和其他来源的配置。Windows 不再强制 PowerShell，保留 DefaultShell/profile。遇到原有更强认证或 ForceCommand 时停止，旧账号级规则需按平台文档显式迁移。

本地 macOS arm64 执行 44 项 Python 测试全部通过，无跳过：包含真实 Warpgate 初始化/Caddy 校验、TLS/私钥加密、原生 sshd 来源匹配解析、临时家目录 tmux 首次安装和真实 PTY 断线恢复。PowerShell 7.6.9 执行 40 项隔离断言通过；Windows NTFS/原生 sshd 检查需由 Windows runner 完成。

新增回归覆盖管理员账号输入、保留原密钥路径、其他来源配置不变、更强认证/ForceCommand 拒绝降级、旧版账号全局规则拒绝静默替换，以及异常时回滚新增 SSH 文件。云端结果在本提交推送后补充。此改动未运行任何真实服务器安装器，未创建账号、修改客户端 SSH 或操作在线后端。

## 上一版云端基线（2026-10-28）

提交 `397b10d` 的 [Actions 40709211660](https://github.com/DMLayMan/warpgate-tailscale-mac/actions/runs/40709211660) 三组任务全部通过：

- Windows Server 2022：Windows PowerShell 5.1 和 PowerShell 7 的输入/配置测试、真实临时文件 NTFS ACL，以及 Windows 原生 sshd 配置解析。
- Ubuntu 24.04：Python 测试、真实 PTY 断线/恢复、隐私历史扫描、锁定的官方 Warpgate/Caddy 二进制回环初始化、TLS/加密检查、systemd 单元校验。
- macOS 15：下载并校验官方 tmux，临时目录首次安装/重复执行，以及真实 PTY 断线/恢复。

Windows sshd 解析使用官方 Win32-OpenSSH 10.4.0.0p1-Preview 的校验 SHA256 便携测试文件，未安装其服务。生产安装器使用 Windows 自带的 OpenSSH 可选功能，并在目标机上再次运行 `sshd -t/-T`；云端夹具不代表所有 Windows 内置版本已实机部署。测试不创建真实系统账号或修改现有后端。

Linux PTY 测试曾被 runner 的全局 zsh 补全提示打断，现通过临时家目录的 `.zshenv` 隔离全局启动配置，同时保留待测 `.zshrc` 和真实 tmux 进程。没有改动用户机器的 shell 配置或放宽安装检查。

这些结果补齐下述早期记录中的云端空缺，仍不替代新 Windows 的管理员安装、网关公钥认证、浏览器操作及重启/休眠验收。

## 持久终端整合（2026-10-27）

当前测试集共 37 项。本轮 macOS arm64 本地执行：36 项通过，原有 Warpgate 完整初始化集成用例因未提供 `WG_TEST_BINARY` 跳过，没有将其列为本轮通过。

- 官方 tmux 3.7c 两种 macOS 架构发布物的压缩包与二进制 SHA256 已核对；两种 Mach-O 均声明最低 macOS 15。arm64 二进制实际执行通过，Intel 二进制未在 Intel 实机执行。
- 临时家目录完成真实二进制首次安装、版本执行、配置生成和重复执行；没有安装到客户端真实家目录或系统目录。
- 真实 PTY：从新交互 shell 自动附着，在任务运行时关闭 PTY 模拟突发连接丢失，确认无人附着期间计数仍增长，再次连接保持同一个 server/session/pane/shell。Control+b、d 分离和非交互 shell 保护通过。
- 完整 Mac 命令的编排使用临时 SSH 文件树及替身系统调用：同一绑定可重复运行、不同绑定拒绝替换、下载失败只回滚新建 SSH 文件、SSH-only 模式不碰终端配置。
- 原内容备份、符号链接/硬链接/配置冲突拒绝、写入失败回滚、降权运行和清除继承环境通过。`umask 077` 下公钥目录仍为 755。
- Python/Bash 语法、差异空白检查，以及暂存 blob/全部可达历史/提交元数据的隐私扫描通过。环境专用排除词文件仅存在仓库外；检查输出不显示匹配的私人值。

复现新安装与 PTY 用例：

```sh
python3 tests/ci_tmux.py
TMUX_TEST_BINARY="$PWD/.ci-bin/tmux" \
TMUX_TEST_ASSETS="$PWD/.ci-bin/tmux-assets" \
python3 -m unittest discover -s tests -v
python3 scripts/check_tracked.py --history
```

仓库新增 macOS 原生终端测试任务，Linux 任务补充 tmux/zsh 用于临时终端测试。2026-10-27 当轮未完成云端执行；2026-10-28 的后续结果见本文顶部。

当前在线后端的手工版本此前完成过网页关闭、Disconnect、恢复同一进程及五分钟持续运行验收，私有证据保存在仓库外。该结果不能替代本次通用安装器在另一台全新 Mac 上的完整管理员/账号/Tailscale/网页链路验收；本轮未改动在线后端。

## 初始网关部署基线记录

2026-10-27，部署包在 macOS arm64 的隔离环境验证：

- 真实 Warpgate 0.30.2 从源码完整构建；Caddy 2.11.7 为校验 SHA256 的官方 macOS 发布物。
- 安全参数模板、输入校验、仅回环监听、服务权限、客户端执行保护、Mac 公钥格式和受限账号配置、目标回执转换检查。
- 真实运行 unattended-setup + 短暂回环初始化服务，逐项读回安全参数。
- 使用私有 CA 验证内部 TLS，检查 SQLite 网关私钥均为加密信封；Caddy validate 成功。
- 正常生产模式不启用管理员 Token，只有密码、未完成强制 TOTP 登记的管理员无法调用目标管理 API。
- Python 编译检查、Bash 语法检查、提交内容扫描。

部署包共 12 个测试用例，包括 1 个实际初始化集成用例和本机原生 macOS sshd 的配置解析检查（不启动服务、不修改系统）。最初的内部证书生成兼容问题已修正：显式使用命名椭圆曲线，私有 CA 与 TLS 叶证书分开，保持证书校验。

仓库包含 GitHub Actions：使用锁定的官方 Linux 二进制重复真实初始化及 Caddy 验证，并检查 systemd 单元。首次运行未启动步骤；2026-10-28 已补齐这些检查，见本文顶部。

此前对 Warpgate 0.30.2 的独立研究另完成 140 项单元检查、31 项集成检查，包括多用户隔离、撤权、未知/变化的主机公钥、TOTP、ticket 隔离和原生 macOS OpenSSH 互通。这些不等于本部署包已经在真实服务器安装。

尚未声称完成：VPS 的 systemd 实际运行、阿里云防火墙、管理员 TOTP 的人工登记、tailnet 策略保存、远端 Mac 系统设置与安装、真实跨网链路、生产容量、重启/休眠、完整恢复演练。需要按 OPERATIONS.md 的清单现场验收。

## 早期 Windows 原生后端检查

早期新增 `setup_windows.ps1` 和 `windows-config` CI 任务。该版本 PowerShell 5.1/7 的隔离测试覆盖输入校验、配置追加/重复执行、冲突拒绝和当时的强制终端设置；Windows runner 另检查临时文件的 NTFS ACL，并下载固定版本的官方便携 sshd 检查原生配置解析。非 Windows 本地测试另验证错误平台拒绝。测试不创建真实系统账号、不安装系统功能、不启动服务。本节是历史记录，现有账号版本不再强制终端，见本文顶部。

Mac 上运行 PowerShell 7 的 31 项断言通过；Python 共 39 项测试，37 项通过，2 项因未提供真实 Warpgate 二进制和 tmux 下载夹具跳过。Windows 回执可生成独立目标，旧 Mac 回执兼容。实际 Windows 管理员安装、网关认证和重启验收仍需目标机器参与；不把跨平台单元测试称为 Windows 全链路验收。
