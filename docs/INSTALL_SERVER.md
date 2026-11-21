# 在一台常开的电脑上装 VortoOne

家放在一台一直开着的电脑上（家里的小主机、NAS、一台不关的 Mac）时，用这里的一条安装命令。
放在「这台电脑」上不用看这一页：Desktop 首次引导里选「这台电脑」就装好了。设计见 `HOME_AND_MEMBERS_DESIGN.md` 第 3 节。

## 一条命令（Linux、macOS）

```sh
curl -fsSL https://github.com/DingHappy/VortoOne/releases/download/server-latest/install.sh | sh
```

想先看它要做什么：在后面加 `-s -- --plan`，只打印、不做任何事。想先读脚本：把它下载下来读完再 `sh install.sh`。

它会：

1. 没有 [uv](https://github.com/astral-sh/uv) 就下载一份固定版本，核对脚本里写死的 SHA-256。
2. 从 GitHub Releases 下载这一版的安装包、界面和依赖锁，按同一版的 `SHA256SUMS` 核对；对不上就停下。
3. 装进 `~/.local/share/vortoone/versions/<版本>/`，`current` 指向它。Python 由 uv 准备，不动系统里的 Python。
4. 第一次装时生成所有者令牌，只写在 `~/.local/share/vortoone/server.env`（0600）。
5. 登记成用户服务并启动：Linux 是 `systemd --user`（要在没登录时也运行，跑一次 `sudo loginctl enable-linger $USER`），macOS 是 launchd。服务只听 `127.0.0.1`。另登记每天 3:30 的备份。
6. 打印一次性的**所有者认领链接**。

不用 sudo，不改防火墙，不对外开端口。

选项：`--version <版本>`、`--dir <目录>`、`--port <端口>`（默认 8080）、`--no-service`（只装，不登记服务，之后自己跑 `<目录>/run.sh`）、`--background`（不登记服务，直接在后台启动；没有 systemd 的机器用，不登记每天的备份）、`--from <目录>`（从本地目录取安装文件，离线安装用）。

## 备份

- 每天 3:30 自动备份到 `~/.local/share/vortoone/backups`，只留最新 7 份；升级前另备份一份。手动备份：`~/.local/share/vortoone/backup.sh manual`。
- 备份里有聊天、记忆、agent、成员，也有手机配对、IM 凭据、AI 应用的连接：它和家里的数据一样要紧，只有你这个用户能读（0600）。要放到别的机器上，用加密的 `scripts/assistant-backup.py`。
- 恢复：先停服务，`cd ~/.local/share/vortoone/home && ../current/bin/vc home-restore <备份文件>`。原来的数据挪到旁边（`.vortoone.before-restore-<时间>`）留着。

## 升级与失败退回

再跑一次安装命令就是升级（要装指定版本加 `--version`）：

1. 停服务，备份一份；
2. 换到新版本启动，在限定时间里自检：要应答，报的版本要是新版本；
3. 自检不过，就退回旧版本和升级前的数据，重新启动，并把原因写进 `~/.local/share/vortoone/upgrade.json`。

成功了只留新版本和刚才那一个旧版本。`scripts/check-server-upgrade.sh` 在发布流程里用一个故意坏掉的版本验证这一套。

## 认领

安装结束时打印的链接 24 小时内有效、只能用一次。第一个打开它、填好名字、邮箱和密码的人成为所有者；之后用这个邮箱和密码登录。
在认领之前，这个家不收任何成员登录和注册。链接只该出现在你自己的终端里，别发给别人。

- 链接过期了：`cd ~/.local/share/vortoone/home && ../current/bin/vc owner-claim` 再开一个。
- 忘了所有者密码：在这台电脑上 `../current/bin/vc owner-claim --reset`，重新认领。

## 让手机和别的电脑连进来

推荐 Tailscale：只在你自己的设备之间，不需要公网地址和证书。

1. 在这台电脑上装好 Tailscale 并登录。在 Tailscale 里把它改名为 `vortoone`（或 `vortoone-<名字>`），Desktop 的「连接」页就能自己找到它。
2. `tailscale serve --bg http://127.0.0.1:8080`（tailnet 里没开 HTTPS 时，它会给一个管理页地址，去打开）。
3. 在 `server.env` 里填 `VORTOONE_PUBLIC_URL=https://vortoone.<你的 tailnet>.ts.net`，重启服务（Linux：`systemctl --user restart vortoone`；macOS：`launchctl kickstart -k gui/$(id -u)/com.vorto.vortoone.server`）。

## Docker

```sh
curl -fsSLO https://raw.githubusercontent.com/DingHappy/VortoOne/main/deploy/docker-compose.yml
docker compose up -d
docker compose logs vortoone      # 第一次启动时这里有认领链接
```

镜像是 `ghcr.io/dinghappy/vortoone`，和安装命令装的是同一份发布文件。数据在卷 `vortoone-data` 的 `/data` 里（`home/` 和 `server.env`）。
端口只映射到宿主机的 `127.0.0.1:8080`，别的设备照上面的办法经 Tailscale 连进来。
容器里用 uid 10001 运行：要把 `/data` 换成宿主机目录，先把那个目录交给 10001。

## 模型

在 `server.env` 里填 OpenAI 兼容的 `OPENAI_BASE_URL` 和 `OPENAI_API_KEY`，重启服务。

## 发布（维护者）

发布只在主人说要发时做。改 `pyproject.toml` 的版本、合进 main，然后推 `server-v<版本>` 标签：
`.github/workflows/server-release.yml` 构建、装一遍并起容器冒烟，通过后发 GitHub Release、推镜像，并更新安装命令取版本用的 `server-latest`。
服务版的发布不标成仓库的「最新发布」，免得和 Desktop 的发布混在一起。

## 还没有

- 升级失败的原因还只写在 `upgrade.json` 和安装命令的输出里，Desktop 里还不显示。
- Docker 的升级就是换镜像，没有自动退回；备份用 `docker compose exec -w /data/home vortoone /opt/vortoone/venv/bin/vc home-backup --dir /data/backups --reason manual`。
- Windows。
