# 持久终端：安装、使用与迁移

## 新后端默认行为

按 [MAC.md](MAC.md) 准备输入并执行 `setup_mac.py --on-server ...`，默认一并安装 tmux。无需 Homebrew，不运行 `curl | sh`，不读取其他账号的 shell 配置。安装入口必须是远端 Mac、root 和明确指定的已有账号（普通或管理员均可）；下载、版本执行及用户文件写入会先降权到该账号。

固定版本和 SHA256 在 `config/versions.lock.json` 的 `tmux_macos`。来源为 [tmux 官方预编译仓库](https://github.com/tmux/tmux-builds/releases/tag/v3.7c)，同时校验压缩包和二进制，拒绝非预期归档项；保留上游许可证归档。该发布物要求 macOS 15+。用户已安装的其他 tmux 不升级、不卸载。

新增文件均在指定账号家目录下：

| 文件 | 用途 |
| --- | --- |
| `~/.local/share/warpgate-tmux/版本/tmux` | 经过校验的专用二进制 |
| 同目录 `LICENSES.tar.gz` | 上游许可证归档 |
| `~/.local/bin/remote-resume` | 手动恢复/查看会话 |
| `~/.config/warpgate-session/tmux.conf` | 专用 tmux 配置，不覆盖默认 `.tmux.conf` |
| `~/.zshrc` 内标记区块 | 交互式 SSH 自动恢复，并保留原内容 |
| `~/.local/state/warpgate-session/backup-*` | 改动前文件及恢复清单，权限受限 |

同一账号使用独立 socket `warpgate-work` 和固定会话 `work`。配置重复执行不追加第二个块，也不关闭已有会话。非交互 SSH 命令、本机普通终端、已经位于 tmux 内的 shell 不会自动嵌套进入。自动恢复失败时保留普通 shell 作为修复入口。

## 日常操作

1. 登录部署者提供的网关 HTTPS 地址，完成密码及 MFA。
2. 点击要使用的 Target。看到底部 `[work]` 后，在此启动任务。
3. 暂时离开可关闭网页、点 Disconnect，或按 Control+b，松开后按 d。
4. 下次登录并点击同一 Target，自动回到原窗口、工作目录和任务；无需重新启动任务。

手动命令：

```sh
remote-resume
remote-resume status
```

快捷键：Control+b 后按 c 新建窗口，n/p 切换窗口，w 选择窗口。浏览器截获快捷键时，可使用 Disconnect 离开。`exit` 会结束当前 shell；若最后一个窗口也结束，下一次连接将新建会话。

网页底部的 `+` 创建新的 SSH shell，仍会进入该账号的同一个 `work`。要并行独立任务，使用 tmux 窗口。不同 Mac、不同系统账号各自独立；共用一个 Mac 系统账号的网页用户会共享会话，互不信任的用户必须分配不同系统账号及 Target。

## 既有环境与迁移

- 使用 `--sessions-only` 可以补装持久终端，无需重新提交 SSH 公钥或网络参数。
- 使用 `--without-tmux` 可以只配置 SSH；该选项不会删除或禁用已配置的持久终端。
- 同名 `remote-resume` 或专用配置文件不是本安装器管理时，停止并要求显式迁移，不覆盖它。先核实原 socket、活跃任务和备份；在原任务结束后迁移旧入口及 shell 标记，再执行安装。运行中的进程不能从旧 socket 自动搬到新 socket。
- 发现 `ZDOTDIR`、非 zsh 账号或符号链接会停止，避免写错配置位置。管理员应审查实际启动文件；不要为了通过检查直接删除个人配置。
- 首次下载或版本校验失败时，不发布自动恢复配置；相同完整部署命令可在修复网络后重试。已成功下载的受校验二进制可能保留，重复执行会重新校验而非盲目覆盖。
- 用户配置写入失败时恢复本次已改的文件；完整部署失败时只回滚本次新建的 SSH 授权文件，保留此前已经存在且验证相符的绑定。没有完整成功标记时应先排错，不把部分安装视为链路验收通过。

## 暂停和回退

在远端目标账号下暂停新连接自动恢复：

```sh
touch "$HOME/.config/warpgate-session/no-auto-attach"
```

原 tmux 任务继续运行，手动 `remote-resume` 仍可使用。恢复自动接入时移走这个标记文件。一次性普通交互 shell 可运行 `REMOTE_SESSION_NO_AUTO=1 /bin/zsh`。

永久回退时先处理原任务，然后只移除 `.zshrc` 中 `BEGIN warpgate tmux v1` 到 `END warpgate tmux v1` 的块，或依据备份清单恢复并保留后续用户改动。确认没有活动任务后再清理专用入口、配置和二进制。不要用 `kill-server` 代替普通离开操作；它会终止该 socket 下的所有窗口。

## 验收与边界

在新的网页会话执行 `echo "$TMUX"`，应非空。可启动一个限时计数任务，关闭终端页超过 60 秒，再从首页点击目标。核对原进程 PID、开始时间及计数增长；另测 Disconnect 和 Control+b、d。不要仅凭出现绿色状态栏判定任务续跑成功。

保持 Mac 接电、清醒和联网；不更改 FileVault、休眠或唤醒策略。tmux 不能让关机、重启、断电或被杀死的进程自动复活，也不能代替任务自己的网络重试、检查点和日志。网关录像记录连接期间的终端流，不是断线期间后台任务的完整日志。撤销网页权限/关闭连接也不会自动结束已脱离连接的任务。

每台新增后端都应独立进行真实跨网验收。仓库测试只使用临时家目录、独立 socket、伪终端和合成参数，不操作真实系统账号或系统服务。
