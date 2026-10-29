# VPS 安装

支持带 systemd 的 Linux x86_64/aarch64。需要 Python 3.9+、OpenSSL、useradd、curl 和 TUN。阿里云 Linux 4 可使用系统 Python；若缺少依赖，先通过系统包管理器安装：

```sh
sudo dnf install -y python3 openssl curl tar
```

配置面向 2 核 / 1 GiB 的小规模实例，Warpgate 内存上限 512 MiB、Caddy 128 MiB。并发容量与录像占用需要现场观察，超过内存上限会被系统终止，不承诺容量。

## 1. 域名与私有参数

创建一个子域名 A 记录指向 VPS 公网 IPv4，TTL 可用 600。没有可用 IPv6 时不要添加 AAAA；保留其他业务记录。不需要为 TLS 提供阿里云 AccessKey。

将本仓库安全地复制到 VPS，在 VPS 中创建只供管理员读取的参数：

```sh
mkdir -m 700 -p local
cp config/site.example.json local/site.json
chmod 600 local/site.json
```

填写 domain 与 public_ip，acme_email 可留空；backend_port 默认 8888。`local/` 不会 push 到 Git。运行下面的命令只渲染配置，不安装服务：

```sh
python3 scripts/wgkit.py render --site local/site.json --output local/rendered
```

安装器会拒绝已有 `/opt/warpgate-gateway`、服务账号或冲突端口，不会接管现有 Caddy/Nginx/Termix。若 80/443 已被占用，应人工整合现有 HTTPS 入口，不要先停掉其他业务。

## 2. Tailscale

已有 Tailscale 的服务器保留现有安装和节点状态，检查 `tailscale status`。没有安装时，可选用锁定 SHA256 的官方静态包：

```sh
sudo python3 scripts/install_tailscale.py --on-vps
sudo tailscale up --advertise-tags=tag:warpgate-vps --ssh=false --accept-routes=false
tailscale ip -4
```

需要先在 tailnet policy 中定义 tagOwners，然后完成浏览器登录和设备授权。已有节点优先通过管理控制台分配标签；不要为了改标签重置现有网络设置。详见 [TAILSCALE.md](TAILSCALE.md)。不会在客户端 Mac 上安装 tailscaled。

## 3. 安装并初始化

```sh
sudo python3 scripts/wgkit.py install --on-vps --site local/site.json
```

安装器执行：

- 检查 Tailscale 已登录、所需命令和端口；交互输入初始 admin 密码，不把密码写入参数、日志或仓库。
- 校验锁定的官方发布 SHA256，安装到 `/opt/warpgate-gateway/bin`。
- 新建非登录账号 wg-gateway、wg-https，分别运行应用与 HTTPS。
- 在 `secrets/encryption.env` 生成加密密钥；生成仅供回环 TLS 的内部 CA 和服务器证书。
- 在回环地址短暂启动受随机 Token 保护的管理员初始化进程，写入并逐项读回 `config/hardening.json`，随后终止该进程。
- 保存公钥 `public/gateway-ed25519.pub` 和公钥对应 ID `public/gateway-key-id.txt`；安装尚未启动的 systemd 单元。

若中途失败，保持服务未启动，检查 root-only `secrets/bootstrap.log`，人工处理部分安装；不要盲目删除运行数据后重跑。

首次启用前，将以下内容复制到受控密码管理器/加密离线备份，分别与常规备份保存：

- `/opt/warpgate-gateway/secrets/encryption.env`
- `/opt/warpgate-gateway/secrets/backend-ca.key`
- 初始 admin 密码（之后为每个管理员使用独立账号）

## 4. 启用 HTTPS 并登记 TOTP

阿里云防火墙与主机防火墙需要允许公网 TCP 80、443；80 用于 HTTP-01/跳转，443 用于 HTTPS/WebSocket。不要开放 8888、2222、数据库或远程桌面端口。现有宿主机管理 SSH 保留，限制来源前先验证备用管理通道，避免锁死。

```sh
sudo systemctl enable --now warpgate-gateway
sudo systemctl enable --now warpgate-https
sudo journalctl -u warpgate-https -n 50 --no-pager
```

通过已配置的 HTTPS 域名登录 admin，按强制 MFA 流程登记 TOTP，退出后确认密码与 TOTP 均被要求。公网证书由 Caddy 自动申请续期，不能跳过浏览器证书警告。证书错误时检查 DNS、80/443、时间和 Caddy 日志。

此时还没有 Mac 目标。先完成 TOTP，再继续授权用户和目标。生产单元没有 `--enable-admin-token`，初始化 Token 不保留。

## 5. 登记 Mac 目标

Windows 后端先执行 [Windows SOP](WINDOWS.md)，使用 `server-receipt.json`；下面生成命令可加 `--name remote-windows`。Mac 旧回执继续兼容，新增后端不要覆盖现有 Target。

把 `public/gateway-ed25519.pub` 交给真正远端 Mac 的管理员；私钥始终在网关数据库。按 MAC.md 完成配置，通过可信渠道取得 Mac 回执。

```sh
python3 scripts/wgkit.py prepare-target \
  --receipt /private/path/mac-receipt.json \
  --key-id /opt/warpgate-gateway/public/gateway-key-id.txt \
  --output local/mac-target
```

此命令生成要录入的精确字段，不会调用线上管理员 API：

1. Config → Targets → 新建 SSH，先不分配日常访问角色。按 target.json 填 host、port、username、PublicKey；明确选择 gateway-key-id 对应的 Ed25519 公钥；Allow insecure algorithms 关闭；ticket 请求关闭。
2. 若 Known hosts 页面支持新增，可录入 known-host.json；0.30.2 的另一种 UI 流程是在目标保存后执行 Check host key，将结果与服务器本机指纹和可信回执核对后再 Trust。不能仅凭网络扫描结果建立初始信任。全局关闭自助 ticket 时目标级设置可能隐藏；保持全局关闭，未来开启前先检查各目标的票据策略。
3. 创建例如 mac-operators 的普通访问角色，将 Target 分配给该角色；创建每个人自己的账号并授予角色。普通用户不授予任何管理员角色。
4. 用户首次登录登记 TOTP，再进入 Mac 网页终端。未知公钥默认 AutoReject，未登记正确主机公钥会连接失败，这是预期保护。

自助 ticket 关闭不等于管理员被禁止创建 ticket；本方案日常不创建访问 ticket。管理员属于信任边界。

Mac/Windows Target 的 username 使用现有系统账号，普通或管理员均可，不要求新建。多个网页用户共用该账号时会共享其权限和文件；需要隔离时为不同的已有系统账号建立不同 Target。管理员账号 Target 只授予可信用户。

## 6. 检查

```sh
sudo bash scripts/check-vps.sh --on-vps YOUR_DOMAIN
tailscale ping MAC_TAILNET_IP
nc -vz MAC_TAILNET_IP 22
```

ss 输出必须显示后端只绑定 127.0.0.1，公网服务只有预期的 80/443 和原有管理入口。check-vps 只做基本检查，不代表业务链路全部就绪。

SELinux 开启时保留保护；若系统策略拒绝 `/opt` 中程序或端口，先检查 journal/audit 日志并按该发行版配置文件标签/服务策略，不要直接关闭 SELinux。
